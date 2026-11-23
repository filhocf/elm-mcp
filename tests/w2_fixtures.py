"""Shared tenant-neutral fixtures for the W2 tests (collaboration, custom types, pagination).

Neutral values only: host https://elm.example.com, project area _PA1, user jdoe,
project "MY PROJECT (KEY)". Every route is registered on a private respx router (see
``w2_router`` in conftest.py): a request without a route raises, so nothing can reach a server.

The fakes below are small stateful servers (an item with an ETag, a comment thread, a paged
query). They answer from their STATE instead of from a scripted sequence of responses, so a
test does not depend on how many times the implementation re-reads a resource.
"""

import itertools
import json
import re

import httpx
import respx

from . import f1_fixtures as f

BASE = f.BASE
PA = f.PA
PROJECT = "MY PROJECT (KEY)"
CTX = f"{BASE}/ccm/oslc/contexts/{PA}"
QBASE = f"{BASE}/ccm/oslc/query/{PA}/workitems"
CORE = "http://open-services.net/ns/core#"
DCTERMS = "http://purl.org/dc/terms/"
AUTH_FAIL_HEADER = "X-com-ibm-team-repository-web-auth-msg"

PREFIXES = {
    "rtc_cm": f.RTC_CM,
    "dcterms": DCTERMS,
    "oslc": CORE,
    "oslc_cm": "http://open-services.net/ns/cm#",
    "rdf": "http://www.w3.org/1999/02/22-rdf-syntax-ns#",
}


def item_uri(wid: str | int) -> str:
    return f"{BASE}/ccm/resource/itemName/com.ibm.team.workitem.WorkItem/{wid}"


def item_alias_uri(wid: str | int) -> str:
    """Second URL form of the same work item (OSLC workitems collection)."""
    return f"{BASE}/ccm/oslc/workitems/{wid}"


def user_uri(name: str = "jdoe") -> str:
    return f"{BASE}/jts/users/{name}"


# ----------------------------------------------------------------------------------------------
# Discovery (rootservices -> catalog -> service provider with typed factories + query capability)
# ----------------------------------------------------------------------------------------------
def _catalog_xml() -> str:
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<oslc:ServiceProviderCatalog
    xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#"
    xmlns:dcterms="{DCTERMS}"
    xmlns:oslc="{CORE}">
    <oslc:serviceProvider>
        <oslc:ServiceProvider rdf:about="{CTX}">
            <dcterms:title>{PROJECT}</dcterms:title>
        </oslc:ServiceProvider>
    </oslc:serviceProvider>
</oslc:ServiceProviderCatalog>
"""


def services_xml(factories: list[tuple[str, str]] | None = None) -> str:
    """Service provider: one typed factory per (type id, raw factory title) + the work item query capability."""
    blocks = "".join(
        f"""
        <oslc:CreationFactory>
            <dcterms:title>{title}</dcterms:title>
            <oslc:resourceType rdf:resource="{f.CR_TYPE}"/>
            <oslc:creation rdf:resource="{f.factory_uri(type_id)}"/>
            <oslc:resourceShape rdf:resource="{f.shape_uri(type_id)}"/>
        </oslc:CreationFactory>"""
        for type_id, title in (factories if factories is not None else f.TYPED_FACTORIES)
    )
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#"
    xmlns:dcterms="{DCTERMS}"
    xmlns:oslc="{CORE}">
    <oslc:Service>
        <oslc:creationFactory>{blocks}
        </oslc:creationFactory>
    </oslc:Service>
    <oslc:Service>
        <oslc:queryCapability>
            <oslc:QueryCapability>
                <dcterms:title>Work item query</dcterms:title>
                <oslc:resourceType rdf:resource="{f.CR_TYPE}"/>
                <oslc:queryBase rdf:resource="{QBASE}"/>
            </oslc:QueryCapability>
        </oslc:queryCapability>
    </oslc:Service>
</rdf:RDF>
"""


def mock_login(router: respx.MockRouter) -> respx.Route:
    """Mock only the form login (for tools that need no discovery)."""
    return router.post(f"{BASE}/jts/j_security_check").mock(return_value=httpx.Response(200))


def mock_ccm(router: respx.MockRouter, factories: list[tuple[str, str]] | None = None) -> respx.Route:
    """Mock login + CCM discovery. Returns the j_security_check route (count logins with ``call_count``)."""
    auth = router.post(f"{BASE}/jts/j_security_check").mock(return_value=httpx.Response(200))
    router.get(f"{BASE}/ccm/rootservices").mock(return_value=httpx.Response(200, text=f.ROOTSERVICES_XML))
    router.get(f"{BASE}/ccm/oslc/workitems/catalog").mock(return_value=httpx.Response(200, text=_catalog_xml()))
    xml = services_xml(factories)
    router.get(f"{CTX}/services").mock(return_value=httpx.Response(200, text=xml))
    router.get(CTX).mock(return_value=httpx.Response(200, text=xml))
    return auth


# ----------------------------------------------------------------------------------------------
# Work item with an ETag (GET + partial PUT), used by link/unlink/set_field
# ----------------------------------------------------------------------------------------------
def item_json(wid: str | int, shape: str = "task") -> dict:
    return {
        "rdf:about": item_uri(wid),
        "dcterms:identifier": str(wid),
        "dcterms:title": f"Item {wid}",
        "oslc:instanceShape": {"rdf:resource": f.shape_uri(shape)},
        "prefixes": dict(PREFIXES),
    }


class FakeItem:
    """A work item served at both URL forms; a PUT applies only the properties named in ?oslc.properties."""

    def __init__(
        self,
        router: respx.MockRouter,
        wid: str | int,
        *,
        data: dict | None = None,
        shape: str = "task",
        etag: str | None = '"etag1"',
        apply_put: bool = True,
        put_response: httpx.Response | None = None,
        get_response: httpx.Response | None = None,
    ) -> None:
        self.wid = str(wid)
        self.uri = item_uri(wid)
        self.data = item_json(wid, shape)
        self.data.update(data or {})
        self.etag = etag
        self.apply_put = apply_put
        self.put_response = put_response
        self.get_response = get_response
        self.puts: list[httpx.Request] = []
        self.gets: list[httpx.Request] = []
        self._version = itertools.count(2)
        for url in (self.uri, item_alias_uri(wid)):
            router.get(url).mock(side_effect=self._get)
            router.put(url).mock(side_effect=self._put)

    def _get(self, request: httpx.Request) -> httpx.Response:
        self.gets.append(request)
        if self.get_response is not None:
            return self.get_response
        headers = {"ETag": self.etag} if self.etag else {}
        return httpx.Response(200, json=self.data, headers=headers)

    def _put(self, request: httpx.Request) -> httpx.Response:
        self.puts.append(request)
        if self.put_response is not None:
            return self.put_response
        if self.apply_put:
            body = json.loads(request.content)
            for prop in request.url.params.get("oslc.properties", "").split(","):
                if prop in body:
                    self.data[prop] = body[prop]
            self.etag = f'"etag{next(self._version)}"'
        headers = {"ETag": self.etag} if self.etag else {}
        return httpx.Response(200, json=self.data, headers=headers)

    # --- helpers for assertions -------------------------------------------------------------
    def put_body(self, index: int = 0) -> dict:
        return json.loads(self.puts[index].content)

    def put_properties(self, index: int = 0) -> str:
        return self.puts[index].url.params.get("oslc.properties", "")


# ----------------------------------------------------------------------------------------------
# Comment thread (OSLC CM 1.0 POST, OSLC 2.0 read through oslc:discussedBy -> oslc:comment)
# ----------------------------------------------------------------------------------------------
def discussion_uri(wid: str | int) -> str:
    return f"{BASE}/ccm/oslc/workitems/{wid}/rtc_cm:comments"


class FakeComments:
    """Comment thread of one work item. A successful POST appends the comment unless persist=False."""

    def __init__(
        self,
        router: respx.MockRouter,
        wid: str | int,
        *,
        existing: tuple[tuple[str, str], ...] = (("1", "First comment"),),
        mode: str = "inline",
        post_response: httpx.Response | None = None,
        persist: bool = True,
    ) -> None:
        self.wid = str(wid)
        self.url = discussion_uri(wid)
        self.mode = mode
        self.post_response = post_response
        self.persist = persist
        self.comments: list[tuple[str, str]] = list(existing)
        self.posts: list[httpx.Request] = []
        item = {
            "rdf:about": item_uri(wid),
            "dcterms:identifier": self.wid,
            "dcterms:title": f"Item {wid}",
            "oslc:discussedBy": {"rdf:resource": self.url},
            "prefixes": dict(PREFIXES),
        }
        for url in (item_uri(wid), item_alias_uri(wid)):
            router.get(url).mock(return_value=httpx.Response(200, json=item, headers={"ETag": '"etag1"'}))
        router.get(self.url).mock(side_effect=self._get_discussion)
        router.post(self.url).mock(side_effect=self._post)
        router.get(url__regex=rf"^{re.escape(self.url)}/[^/]+$").mock(side_effect=self._get_comment)

    def comment_uri(self, cid: str) -> str:
        return f"{self.url}/{cid}"

    def _comment_json(self, cid: str, text: str) -> dict:
        return {
            "rdf:about": self.comment_uri(cid),
            "dcterms:identifier": cid,
            "dcterms:creator": {"rdf:resource": user_uri("jdoe")},
            "dcterms:created": "2026-01-01T10:00:00.000Z",
            "dcterms:description": text,
        }

    def _get_discussion(self, request: httpx.Request) -> httpx.Response:
        if self.mode == "inline":
            members = [self._comment_json(cid, text) for cid, text in self.comments]
        else:
            members = [{"rdf:resource": self.comment_uri(cid)} for cid, _ in self.comments]
        return httpx.Response(200, json={"rdf:about": self.url, "oslc:comment": members})

    def _get_comment(self, request: httpx.Request) -> httpx.Response:
        cid = request.url.path.rsplit("/", 1)[-1]
        text = dict(self.comments).get(cid)
        if text is None:
            return httpx.Response(404)
        return httpx.Response(200, json=self._comment_json(cid, text))

    def _post(self, request: httpx.Request) -> httpx.Response:
        self.posts.append(request)
        if self.post_response is not None:
            return self.post_response
        text = json.loads(request.content)["dc:description"]
        cid = str(len(self.comments) + 1)
        if self.persist:
            self.comments.append((cid, text))
        return httpx.Response(201, json={"rdf:about": self.comment_uri(cid), "dc:description": text})


# ----------------------------------------------------------------------------------------------
# Enumerations and shapes (set_field, list_workflow)
# ----------------------------------------------------------------------------------------------
def enum_url(enum: str) -> str:
    return f"{BASE}/ccm/oslc/enumerations/{PA}/{enum}"


def literal_uri(enum: str, literal_id: str) -> str:
    return f"{enum_url(enum)}/{literal_id}"


def enum_xml(enum: str, literals: list[tuple[str, str]]) -> str:
    """Enumeration document: allowedValue references + one titled Description per literal (id, title)."""
    refs = "".join(f'<oslc:allowedValue rdf:resource="{literal_uri(enum, i)}"/>' for i, _ in literals)
    descs = "".join(
        f'<rdf:Description rdf:about="{literal_uri(enum, i)}"><dcterms:title>{t}</dcterms:title></rdf:Description>'
        for i, t in literals
    )
    return (
        '<rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#" '
        f'xmlns:dcterms="{DCTERMS}" xmlns:oslc="{CORE}">'
        f'<rdf:Description rdf:about="{enum_url(enum)}">{refs}</rdf:Description>{descs}</rdf:RDF>'
    )


POINTS_ENUM = "storyPoints"
POINTS_LITERALS = [
    (f"{POINTS_ENUM}.literal.l1", "Unassigned"),
    (f"{POINTS_ENUM}.literal.l2", "3 pts"),
    (f"{POINTS_ENUM}.literal.l3", "5 pts"),
    (f"{POINTS_ENUM}.literal.l4", "8 pts"),
]
SEVERITY_ENUM = "severity"
SEVERITY_LITERALS = [
    (f"{SEVERITY_ENUM}.literal.l1", "Unassigned"),
    (f"{SEVERITY_ENUM}.literal.l2", "Normal"),
    (f"{SEVERITY_ENUM}.literal.l3", "Critical"),
]


def task_shape_props() -> list[dict]:
    """Optional enum (storyPoints), REQUIRED enum (severity) and a plain required string (title)."""
    return [
        f.prop("storyPoints", "Story Points", propdef=f"{f.RTC_CM}storyPoints", allowed=enum_url(POINTS_ENUM)),
        f.prop("severity", "Severity", "Exactly-one", propdef=f"{f.RTC_CM}severity", allowed=enum_url(SEVERITY_ENUM)),
        f.prop("title", "Summary", "Exactly-one", propdef=f"{DCTERMS}title"),
    ]


def mock_enum_shape(router: respx.MockRouter, shape: str = "task") -> None:
    """Task shape in the real format + the two enumerations it references."""
    router.get(f.shape_uri(shape)).mock(
        return_value=httpx.Response(200, text=f.real_shape_xml(shape, task_shape_props()))
    )
    router.get(enum_url(POINTS_ENUM)).mock(
        return_value=httpx.Response(200, text=enum_xml(POINTS_ENUM, POINTS_LITERALS))
    )
    router.get(enum_url(SEVERITY_ENUM)).mock(
        return_value=httpx.Response(200, text=enum_xml(SEVERITY_ENUM, SEVERITY_LITERALS))
    )


# ----------------------------------------------------------------------------------------------
# Creation context (create_workitem)
# ----------------------------------------------------------------------------------------------
CREATED = {
    "dcterms:title": "New",
    "dcterms:identifier": "42",
    "rdf:about": item_uri(42),
}


def mock_creation(router: respx.MockRouter, factories: list[tuple[str, str]] | None = None) -> dict[str, respx.Route]:
    """Session-refresh GETs, shapes and one POST route per typed factory (+ the generic one under key '').

    Returns {type id: POST route}. The generic /workitems factory is registered under the key ''.
    """
    factories = factories if factories is not None else f.TYPED_FACTORIES
    router.get(f"{BASE}/ccm/oslc/iterations").mock(return_value=httpx.Response(200, json={"oslc:results": []}))
    router.get(f.GENERIC_FACTORY).mock(return_value=httpx.Response(200, json={}))
    router.get(f.allowed_values_url("task")).mock(
        return_value=httpx.Response(200, text=f.allowed_values_xml("task", [f.CAT_DEFAULT, f.CAT_2]))
    )
    posts: dict[str, respx.Route] = {"": router.post(f.GENERIC_FACTORY).mock(return_value=_created())}
    for type_id, _title in factories:
        router.get(f.factory_uri(type_id)).mock(return_value=httpx.Response(200, json={}))
        router.get(f.shape_uri(type_id)).mock(
            return_value=httpx.Response(200, text=f.real_shape_xml(type_id, f.default_task_props()))
        )
        posts[type_id] = router.post(f.factory_uri(type_id)).mock(return_value=_created())
    return posts


def _created() -> httpx.Response:
    return httpx.Response(201, json=CREATED)


# ----------------------------------------------------------------------------------------------
# Paged query (list_workitems / query_resources)
# ----------------------------------------------------------------------------------------------
def wi(n: int) -> dict:
    """Query member for work item n."""
    return {"rdf:about": item_uri(n), "dcterms:identifier": str(n), "dcterms:title": f"Item {n}"}


def page_url(page: int) -> str:
    return f"{QBASE}?page={page}"


class FakePages:
    """The work item query endpoint, served in pages of the given members.

    ``form`` selects how the next page is announced (all are valid OSLC 2.0 JSON shapes):
    - "info":   {"oslc:results": [...], "oslc:responseInfo": {"oslc:nextPage": {"rdf:resource": url}}}
    - "top":    {"oslc:results": [...], "oslc:nextPage": {"rdf:resource": url}}
    - "member": {"rdfs:member": [...], "oslc:responseInfo": {"oslc:nextPage": url}}  (plain string)
    ``failures`` maps a page number to the responses its FIRST requests get (in order); with
    ``sticky`` the last failure repeats forever, otherwise later requests are served normally.
    """

    def __init__(
        self,
        router: respx.MockRouter,
        pages: list[list[dict]],
        *,
        form: str = "info",
        failures: dict[int, list[httpx.Response]] | None = None,
        sticky: bool = False,
    ) -> None:
        self.pages = pages
        self.form = form
        self.failures = {k: list(v) for k, v in (failures or {}).items()}
        self.sticky = sticky
        self.requests: list[tuple[int, httpx.Request]] = []
        self.route = router.get(QBASE).mock(side_effect=self._serve)

    def requested_pages(self) -> list[int]:
        return [page for page, _ in self.requests]

    def _serve(self, request: httpx.Request) -> httpx.Response:
        page = int(request.url.params.get("page", "1"))
        self.requests.append((page, request))
        pending = self.failures.get(page)
        if pending:
            return pending[0] if (self.sticky and len(pending) == 1) else pending.pop(0)
        members = self.pages[page - 1]
        has_next = page < len(self.pages)
        nxt = page_url(page + 1)
        if self.form == "top":
            body: dict = {"oslc:results": members}
            if has_next:
                body["oslc:nextPage"] = {"rdf:resource": nxt}
        elif self.form == "member":
            body = {"rdfs:member": members, "oslc:responseInfo": {"oslc:totalCount": 99999}}
            if has_next:
                body["oslc:responseInfo"]["oslc:nextPage"] = nxt
        else:
            body = {"oslc:results": members, "oslc:responseInfo": {"rdf:about": str(request.url)}}
            if has_next:
                body["oslc:responseInfo"]["oslc:nextPage"] = {"rdf:resource": nxt}
        return httpx.Response(200, json=body)

    def where_of_first_request(self) -> str | None:
        return self.requests[0][1].url.params.get("oslc.where")


def paginate(start: int, sizes: list[int]) -> list[list[dict]]:
    """Consecutive work items split in pages of the given sizes, numbered from ``start``."""
    pages, n = [], start
    for size in sizes:
        pages.append([wi(n + i) for i in range(size)])
        n += size
    return pages


def ids_of(items: list[dict]) -> list[str]:
    """Identifiers of query members (raw JSON) or of list_workitems entries."""
    return [str(i.get("dcterms:identifier", i.get("id"))) for i in items]
