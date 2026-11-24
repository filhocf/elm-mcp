"""Shared tenant-neutral fixtures for the W6 tests (QM write: create, update, delete, link, unlink).

Neutral values only: host https://elm.example.com, project area _PA1, user jdoe, project "Project One".
Everything is served from a private respx router (``w2_router`` in conftest.py): a request without a
route raises, so nothing can reach a real server.

``QmWriteServer`` extends the W3 ``QmServer`` (discovery + query endpoints) into a small STATEFUL fake of
the ETM write surface. Like the real ETM it only speaks RDF/XML (a request that asks for JSON gets 406):

- ``services.xml`` also publishes one ``oslc:CreationFactory`` per type, built from the real sanitized
  fixture ``tests/fixtures/qm_creation_factories.xml`` (resourceType in TWO namespaces, TestExecutionRecord
  FIRST, creation URL of TestPlan/TestScript/TestSuite different from their queryBase);
- ``POST <creation URL>``: validates that the body holds ONE resource with only ``dcterms:title`` (and
  ``dcterms:description``), stores it with a server-assigned id and answers ``201`` + ``Location`` with an
  EMPTY body (the id can only be learned by re-reading);
- ``GET <resource>``: the stored RDF/XML + ``ETag`` (406 when only JSON is accepted);
- ``PUT <resource>``: needs ``If-Match`` equal to the current ETag (412 if stale, 428 if missing);
- ``DELETE <resource>``: removes it; later reads answer ``after_delete_status`` (404 by default);
- the query endpoints of W3 list what is stored, so the duplicate check sees created resources.

The query fake ignores ``oslc.where`` (like W3): the code under test must compare titles itself.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from xml.etree import ElementTree as ET
from xml.sax.saxutils import escape

import httpx
import respx

from . import w3_fixtures as w3
from .w3_fixtures import DCTERMS, PA, QM_CTX, QM_NS, RDF, RQM, fixture_text

EVIL = "https://evil.example.net"
CORE = w3.CORE

SUPPORTED = ["TestCase", "TestPlan", "TestSuite", "TestScript"]
DEFERRED = ["TestExecutionRecord", "TestResult"]
LINK_TYPES = ["validatesRequirement", "testsChangeRequest"]

# The real fixture of the 12 creation factories, parsed into {type local name: (creation URL, shape URL)}.
_FACTORY_RE = re.compile(
    r'<oslc:creation rdf:resource="([^"]+)"/>\s*<oslc:resourceShape rdf:resource="([^"]+)"/>\s*'
    r'<oslc:resourceType rdf:resource="([^"]+)"/>'
)
FACTORIES: dict[str, tuple[str, str]] = {}
FACTORY_ORDER: list[str] = []
for _creation, _shape, _rt in _FACTORY_RE.findall(fixture_text("qm_creation_factories.xml")):
    _name = _rt.rsplit("#", 1)[-1]
    FACTORIES[_name] = (_creation, _shape)
    FACTORY_ORDER.append(_name)

TESTCASE_FIXTURE_URI = f"{QM_CTX}/resources/com.ibm.rqm.planning.VersionedTestCase/_ID001"
FIXTURE_REQUIREMENT = "https://elm.example.com/rm/resources/TX_hDQNkB3GEfGD-fsEtTDLSg"
FIXTURE_WORKITEM = "https://elm.example.com/ccm/resource/itemName/com.ibm.team.workitem.WorkItem/610669"
FIXTURE_SCRIPT = f"{QM_CTX}/resources/com.ibm.rqm.planning.VersionedExecutionScript/_ID002"


# Qualified element name the live ETM requires for the POST node of each supported type
# (``oslc_qm:TestSuite`` is refused with 400; TestSuite lives in the ``rqm`` namespace).
TYPED = {
    "TestCase": f"{{{QM_NS}}}TestCase",
    "TestPlan": f"{{{QM_NS}}}TestPlan",
    "TestScript": f"{{{QM_NS}}}TestScript",
    "TestSuite": f"{{{RQM}}}TestSuite",
}


def creation_url(type_name: str) -> str:
    return FACTORIES[type_name][0]


def factory_block(type_name: str, title_name: str | None = None, url: str | None = None) -> str:
    """One ``oslc:CreationFactory`` for ``type_name`` (resourceType in the REAL namespace of the type)."""
    creation, shape = FACTORIES[type_name]
    return f"""
        <oslc:creationFactory>
            <oslc:CreationFactory>
                <dcterms:title>Default creation factory for {title_name or type_name}</dcterms:title>
                <oslc:creation rdf:resource="{url or creation}"/>
                <oslc:resourceShape rdf:resource="{shape}"/>
                <oslc:resourceType rdf:resource="{w3.type_ns(type_name)}{type_name}"/>
            </oslc:CreationFactory>
        </oslc:creationFactory>"""


def services_with_factories(
    factory_types: list[str] | None = None,
    *,
    swap_titles: bool = False,
    duplicate_for: str | None = None,
    foreign_for: str | None = None,
) -> str:
    """The W3 services.xml (14 query capabilities) plus creation factories in one extra oslc:Service."""
    names = list(FACTORY_ORDER if factory_types is None else factory_types)
    blocks = []
    for i, name in enumerate(names):
        title = names[(i + 1) % len(names)] if swap_titles else None  # a factory TITLED after another type
        url = f"{EVIL}/qm/oslc_qm/contexts/{PA}/resources/stolen" if name == foreign_for else None
        blocks.append(factory_block(name, title, url))
    if duplicate_for:
        blocks.append(factory_block(duplicate_for, url=f"{QM_CTX}/resources/com.example.other.{duplicate_for}"))
    head, tail = w3.qm_services_xml().rsplit("</oslc:ServiceProvider>", 1)
    extra = "<oslc:service><oslc:Service>" + "".join(blocks) + "\n        </oslc:Service></oslc:service>\n    "
    return head + extra + "</oslc:ServiceProvider>" + tail


# ----------------------------------------------------------------------------------------------
# RDF/XML helpers used by the tests (namespace-aware: a link in another namespace is not the same link)
# ----------------------------------------------------------------------------------------------
def q(ns: str, local: str) -> str:
    return f"{{{ns}}}{local}"


def local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def resource_nodes(xml_text: str) -> list[ET.Element]:
    """The resource nodes of an RDF/XML document (children of rdf:RDF, or the root itself)."""
    root = ET.fromstring(xml_text)
    return list(root) if root.tag == q(RDF, "RDF") else [root]


def subject(xml_text: str, uri: str) -> ET.Element:
    found = [n for n in resource_nodes(xml_text) if n.get(q(RDF, "about")) == uri]
    assert len(found) == 1, f"expected exactly one node about {uri}, found {len(found)}"
    return found[0]


def link_targets(xml_text: str, uri: str, link: str) -> list[str]:
    """Targets of ``oslc_qm:<link>`` (QM namespace only) of the subject, with duplicates kept."""
    return [c.get(q(RDF, "resource")) for c in subject(xml_text, uri) if c.tag == q(QM_NS, link)]


def literal(xml_text: str, uri: str, ns: str, name: str) -> str | None:
    found = [c for c in subject(xml_text, uri) if c.tag == q(ns, name)]
    assert len(found) <= 1, f"{name} appears {len(found)} times"
    return (found[0].text or "") if found else None


def triples(xml_text: str, *, skip: tuple[str, ...] = ()) -> set[tuple[str, str, str]]:
    """(about, property, value) of every NAMED node (rdf:about) and each property (skip = full property names)."""
    out: set[tuple[str, str, str]] = set()
    for node in resource_nodes(xml_text):
        about = node.get(q(RDF, "about"))
        if not about:
            continue
        for child in node:
            if child.tag in skip:
                continue
            value = child.get(q(RDF, "resource")) or (child.text or "").strip()
            out.add((about, child.tag, value))
    return out


def body_properties(request: httpx.Request) -> list[tuple[str, str | None, str]]:
    """(property, rdf:resource, text) of the ONE resource node of a request body."""
    nodes = resource_nodes(request.content.decode("utf-8"))
    assert len(nodes) == 1, f"the body must hold exactly one resource, found {len(nodes)}"
    return [(c.tag, c.get(q(RDF, "resource")), (c.text or "")) for c in nodes[0]]


def _meta(doc: str, uri: str) -> dict:
    node = subject(doc, uri)
    rdf_types = [c.get(q(RDF, "resource"), "") for c in node if c.tag == q(RDF, "type")]
    type_name = ""
    for t in rdf_types:
        for ns in (QM_NS, RQM):
            if t.startswith(ns) and not t.endswith("Query"):
                type_name = t[len(ns) :]
    if node.tag != q(RDF, "Description"):
        type_name = local_name(node.tag)
    title = next((c.text or "" for c in node if c.tag == q(DCTERMS, "title")), "")
    short = next((c.text or "" for c in node if c.tag == q(RQM, "shortIdentifier")), "")
    return {"type": type_name, "title": title, "short_id": short}


class QmWriteServer(w3.QmServer):
    """Stateful fake of the ETM: discovery + queries (W3) + POST/GET/PUT/DELETE on QM resources."""

    def __init__(
        self,
        router: respx.MockRouter,
        *,
        factory_types: list[str] | None = None,
        swap_titles: bool = False,
        duplicate_factory_for: str | None = None,
        foreign_factory_for: str | None = None,
        post_status: int | None = None,
        post_body: str = "",
        post_headers: dict[str, str] | None = None,
        weak_etag: bool = False,
        post_timeout: str | None = None,  # "stored": the server creates it then never answers; "lost": never creates it
        put_status: int | None = None,
        put_body: str = "",
        delete_status: int | None = None,
        delete_body: str = "",
        after_delete_status: int = 404,
        get_status: dict[str, int] | None = None,
        hide_etag: bool = False,
        change_after_get: bool = False,
        put_transform: Callable[[str, str], str] | None = None,
        query_status_after_post: int | None = None,
        **kwargs,
    ) -> None:
        super().__init__(
            router,
            services_xml=services_with_factories(
                factory_types,
                swap_titles=swap_titles,
                duplicate_for=duplicate_factory_for,
                foreign_for=foreign_factory_for,
            ),
            **kwargs,
        )
        self.post_status, self.post_body, self.post_timeout = post_status, post_body, post_timeout
        self.post_headers, self.weak_etag = post_headers or {}, weak_etag
        self.put_status, self.put_body = put_status, put_body
        self.delete_status, self.delete_body = delete_status, delete_body
        self.after_delete_status = after_delete_status
        self.get_status = get_status or {}
        self.hide_etag, self.change_after_get = hide_etag, change_after_get
        self.put_transform = put_transform
        self.query_status_after_post = query_status_after_post
        self.store: dict[str, dict] = {}  # uri -> {"doc", "version"}
        self.deleted: set[str] = set()
        self.posts: list[httpx.Request] = []
        self.puts: list[httpx.Request] = []
        self.deletes: list[httpx.Request] = []
        self.resource_gets: list[httpx.Request] = []
        self.order: list[str] = []  # "POST", "GET <uri>", "PUT", "DELETE" in arrival order
        self.created = 0
        self.evil = router.route(host="evil.example.net").mock(return_value=httpx.Response(200, text="<x/>"))
        for name in FACTORIES:
            router.post(creation_url(name)).mock(side_effect=lambda request, name=name: self._post(request, name))
        pattern = rf"^{re.escape(QM_CTX)}/resources/[^/?#]+/[^/?#]+$"
        router.route(method="GET", url__regex=pattern).mock(side_effect=self._get_resource)
        router.route(method="PUT", url__regex=pattern).mock(side_effect=self._put_resource)
        router.route(method="DELETE", url__regex=pattern).mock(side_effect=self._delete_resource)

    # -- seeding / inspecting -----------------------------------------------------------------
    def add_raw(self, uri: str, doc: str) -> str:
        self.store[uri] = {"doc": doc, "version": 1}
        return uri

    def add(
        self,
        type_name: str,
        ident: str,
        title: str,
        *,
        short_id: str = "9001",
        links: dict[str, list[str]] | None = None,
        literals: dict[str, str] | None = None,
    ) -> str:
        uri = f"{creation_url(type_name)}/{ident}"
        member = w3.member_xml(type_name, uri, escape(title), short_id, links=links, literals=literals)
        return self.add_raw(uri, w3.single_xml(member))

    def add_fixture_testcase(self) -> str:
        return self.add_raw(TESTCASE_FIXTURE_URI, fixture_text("qm_testcase_one.xml"))

    def doc(self, uri: str) -> str:
        return self.store[uri]["doc"]

    def etag(self, uri: str) -> str:
        return f'"{self.store[uri]["version"]}"'

    def touch(self, uri: str) -> None:
        """Someone else changed the resource: the ETag moves on, the content stays."""
        self.store[uri]["version"] += 1

    def writes(self) -> list[httpx.Request]:
        return [*self.posts, *self.puts, *self.deletes]

    # -- the query endpoints list what is stored ------------------------------------------------
    def _query(self, request: httpx.Request, type_name: str) -> httpx.Response:
        if self.query_status_after_post is not None and self.posts:
            self.requests.append(request)
            self.query_requests[type_name].append(request)
            return httpx.Response(self.query_status_after_post, text="")
        self.data = {}
        for uri, entry in self.store.items():
            meta = _meta(entry["doc"], uri)
            if meta["type"] in w3.QM_TYPES and uri not in self.deleted:
                self.data.setdefault(meta["type"], []).append(
                    (uri, w3.member_xml(meta["type"], uri, escape(meta["title"]), meta["short_id"] or "0"))
                )
        return super()._query(request, type_name)

    # -- POST ---------------------------------------------------------------------------------
    @staticmethod
    def _rdf(status: int, text: str = "", headers: dict | None = None) -> httpx.Response:
        return httpx.Response(status, text=text, headers={"Content-Type": "application/rdf+xml", **(headers or {})})

    def _post(self, request: httpx.Request, type_name: str) -> httpx.Response:
        self.requests.append(request)
        self.posts.append(request)
        self.order.append("POST")
        accept = request.headers.get("accept", "").lower()
        if "json" in accept and "rdf+xml" not in accept:
            return httpx.Response(406, text="Not Acceptable")
        if self.post_status is not None:
            return httpx.Response(self.post_status, text=self.post_body, headers=self.post_headers)
        if "rdf+xml" not in request.headers.get("content-type", "").lower():
            return httpx.Response(415, text="Only RDF/XML is accepted")
        try:
            nodes = resource_nodes(request.content.decode("utf-8"))
        except (ET.ParseError, UnicodeDecodeError) as exc:
            return httpx.Response(400, text=f"not well-formed: {exc}")
        if len(nodes) != 1:
            return httpx.Response(400, text="exactly one resource expected")
        if nodes[0].tag != TYPED.get(type_name):  # like the live ETM (issue #78): exact typed node
            return httpx.Response(400, text="resource node must be typed")
        props = list(nodes[0])
        titles = [p for p in props if p.tag == q(DCTERMS, "title")]
        descriptions = [p for p in props if p.tag == q(DCTERMS, "description")]
        others = [p for p in props if p.tag not in (q(DCTERMS, "title"), q(DCTERMS, "description"), q(RDF, "type"))]
        if len(titles) != 1 or not (titles[0].text or "").strip() or len(descriptions) > 1 or others:
            return httpx.Response(400, text="only a title (and a description) are accepted")
        declared = [p.get(q(RDF, "resource"), "") for p in props if p.tag == q(RDF, "type")]
        if nodes[0].tag != q(RDF, "Description"):
            declared.append(f"{nodes[0].tag[1:].replace('}', '')}")
        if any(not d.endswith(type_name) for d in declared):
            return httpx.Response(400, text="the declared type does not match the factory")
        if self.post_timeout == "lost":
            raise httpx.ReadTimeout("timed out after the request was sent")
        self.created += 1
        uri = f"{creation_url(type_name)}/_N{self.created:03d}"
        literals = {"dcterms:description": escape(descriptions[0].text or "")} if descriptions else None
        member = w3.member_xml(
            type_name, uri, escape(titles[0].text or ""), str(5000 + self.created), literals=literals
        )
        self.add_raw(uri, w3.single_xml(member))
        if self.post_timeout == "stored":
            raise httpx.ReadTimeout("timed out after the request was sent")
        return httpx.Response(201, text="", headers={"Location": uri})

    # -- GET / PUT / DELETE on one resource -----------------------------------------------------
    @staticmethod
    def _uri(request: httpx.Request) -> str:
        return str(request.url).split("?", 1)[0]

    def _get_resource(self, request: httpx.Request) -> httpx.Response:
        uri = self._uri(request)
        self.requests.append(request)
        self.resource_gets.append(request)
        self.order.append(f"GET {uri}")
        accept = request.headers.get("accept", "").lower()
        if "json" in accept and "rdf+xml" not in accept:
            return httpx.Response(406, text="Not Acceptable")
        if uri in self.get_status:
            return httpx.Response(self.get_status[uri], text="")
        if uri in self.deleted:
            return httpx.Response(self.after_delete_status, text="")
        if uri not in self.store:
            return httpx.Response(404, text="")
        entry = self.store[uri]
        etag = f'"{entry["version"]}"'
        headers = {} if self.hide_etag else {"ETag": f"W/{etag}" if self.weak_etag else etag}
        response = self._rdf(200, entry["doc"], headers)
        if self.change_after_get:
            entry["version"] += 1  # a concurrent edit lands right after this read
        return response

    def _put_resource(self, request: httpx.Request) -> httpx.Response:
        uri = self._uri(request)
        self.requests.append(request)
        self.puts.append(request)
        self.order.append("PUT")
        if self.put_status is not None:
            return httpx.Response(self.put_status, text=self.put_body)
        if uri not in self.store or uri in self.deleted:
            return httpx.Response(404, text="")
        if "rdf+xml" not in request.headers.get("content-type", "").lower():
            return httpx.Response(415, text="Only RDF/XML is accepted")
        match = request.headers.get("if-match")
        if match is None:
            return httpx.Response(428, text="If-Match required")
        if match != self.etag(uri):
            return httpx.Response(412, text="stale ETag")
        new_doc = request.content.decode("utf-8")
        try:
            _meta(new_doc, uri)
        except (ET.ParseError, AssertionError) as exc:
            return httpx.Response(400, text=f"bad body: {exc}")
        old_doc = self.store[uri]["doc"]
        stored = self.put_transform(old_doc, new_doc) if self.put_transform else new_doc
        self.store[uri] = {"doc": stored, "version": self.store[uri]["version"] + 1}
        return self._rdf(200, "", {"ETag": self.etag(uri)})

    def _delete_resource(self, request: httpx.Request) -> httpx.Response:
        uri = self._uri(request)
        self.requests.append(request)
        self.deletes.append(request)
        self.order.append("DELETE")
        if self.delete_status is not None:
            return httpx.Response(self.delete_status, text=self.delete_body)
        if uri not in self.store or uri in self.deleted:
            return httpx.Response(404, text="")
        self.deleted.add(uri)
        return httpx.Response(200, text="")
