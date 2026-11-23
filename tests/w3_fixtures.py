"""Shared tenant-neutral fixtures for the W3 tests (QM reading and cross-domain links).

Neutral values only: host https://elm.example.com, project area _PA1, user jdoe, project "Project One".
Everything is served from a private respx router (``w2_router`` in conftest.py): a request without a
route raises, so nothing can reach a real server.

``QmServer`` is a small stateful fake of the ETM (QM) OSLC endpoints. It only speaks RDF/XML (a request
that asks for JSON gets 406, like the real ETM), serves the 14 query capabilities of a project with a
DISTINCT queryBase per type (TestExecutionRecord first, like the real services.xml), and pages the
members the way the real server does:

- page size comes from ``oslc_qm.pageSize`` (first request) or ``oslc.pageSize`` (inside nextPage), and is
  clamped by ``server_page_cap``;
- ``oslc:nextPage`` carries a token and must be followed as it came;
- the page holds the members INLINE, plus the query-resource Description (type ``...Query``, lists the
  members) and the ``oslc:ResponseInfo`` Description (``totalCount``, ``nextPage``), which are NOT members.
"""

from __future__ import annotations

import re
from pathlib import Path

import httpx
import respx

from . import f1_fixtures as f

BASE = f.BASE
PA = f.PA
PROJECT = f.PROJECT
OTHER_PROJECT = "Project Two"
FIXTURES = Path(__file__).parent / "fixtures"

QM_NS = "http://open-services.net/ns/qm#"
CORE = "http://open-services.net/ns/core#"
DCTERMS = "http://purl.org/dc/terms/"
RDF = "http://www.w3.org/1999/02/22-rdf-syntax-ns#"
RQM = "http://jazz.net/ns/qm/rqm#"
QM_CTX = f"{BASE}/qm/oslc_qm/contexts/{PA}"
QM_SERVICES_XML_URL = f"{QM_CTX}/services.xml"
QM_CATALOG_URL = f"{BASE}/qm/oslc_qm/catalog"

# The 14 types of the SPEC, in the order of the real services.xml (TestExecutionRecord FIRST: that is
# what makes a "first capability wins" implementation consult executions instead of test cases).
QM_TYPES = [
    "TestExecutionRecord",
    "TestPlan",
    "TestCase",
    "TestScript",
    "TestScriptStep",
    "TestSuite",
    "TestSuiteExecutionRecord",
    "TestResult",
    "TestSuiteResult",
    "TestData",
    "Keyword",
    "TestEnvironment",
    "BuildRecord",
    "BuildDefinition",
]

# Internal path segment of each queryBase. The three real ones come from tests/fixtures/*.xml; the
# others are neutral. The code must never hard-code these: it reads them from services.xml.
_INTERNAL = {
    "TestCase": "com.ibm.rqm.planning.VersionedTestCase",
    "TestExecutionRecord": "com.ibm.rqm.execution.TestcaseExecutionRecord",
    "TestResult": "com.ibm.rqm.execution.ExecutionResult",
}


def query_base(type_name: str) -> str:
    return f"{QM_CTX}/resources/{_INTERNAL.get(type_name, 'com.example.qm.' + type_name)}"


def member_uri(type_name: str, ident: str) -> str:
    return f"{query_base(type_name)}/{ident}"


def rm_uri(ident: str = "_REQ1") -> str:
    return f"{BASE}/rm/resources/{ident}"


def wi_uri(wid: str = "100") -> str:
    return f"{BASE}/ccm/resource/itemName/com.ibm.team.workitem.WorkItem/{wid}"


# ----------------------------------------------------------------------------------------------
# Discovery documents
# ----------------------------------------------------------------------------------------------
QM_ROOTSERVICES_XML = f"""<?xml version="1.0" encoding="UTF-8"?>
<rdf:Description xmlns:rdf="{RDF}" xmlns:oslc_qm="http://open-services.net/xmlns/qm/1.0/">
    <oslc_qm:qmServiceProviders rdf:resource="{QM_CATALOG_URL}"/>
</rdf:Description>
"""

# Service providers carry EXTRA attributes on the element (and on the title): a naive parser that
# expects a bare <oslc:ServiceProvider rdf:about="..."> / <dcterms:title> finds no project.
QM_CATALOG_XML = f"""<?xml version="1.0" encoding="UTF-8"?>
<oslc:ServiceProviderCatalog
    xmlns:rdf="{RDF}"
    xmlns:dcterms="{DCTERMS}"
    xmlns:oslc="{CORE}"
    xmlns:jp06="http://jazz.net/xmlns/prod/jazz/process/0.6/"
    rdf:about="{QM_CATALOG_URL}">
    <dcterms:title>Quality Management Service Provider Catalog</dcterms:title>
    <oslc:serviceProvider>
        <oslc:ServiceProvider rdf:about="{QM_SERVICES_XML_URL}" jp06:consumerRegistry="{BASE}/qm/reg" xml:lang="en">
            <dcterms:title rdf:datatype="http://www.w3.org/2001/XMLSchema#string" xml:lang="en">{PROJECT}</dcterms:title>
            <jp06:details rdf:resource="{BASE}/qm/process/project-areas/{PA}"/>
        </oslc:ServiceProvider>
    </oslc:serviceProvider>
    <oslc:serviceProvider>
        <oslc:ServiceProvider rdf:about="{BASE}/qm/oslc_qm/contexts/_PA2/services.xml" jp06:consumerRegistry="{BASE}/qm/reg">
            <dcterms:title rdf:datatype="http://www.w3.org/2001/XMLSchema#string">{OTHER_PROJECT}</dcterms:title>
        </oslc:ServiceProvider>
    </oslc:serviceProvider>
</oslc:ServiceProviderCatalog>
"""


def qm_services_xml(types: list[str] | None = None) -> str:
    """services.xml with one QueryCapability per type (resourceType ``qm#<Type>Query``, distinct queryBase)."""
    chosen = QM_TYPES if types is None else types
    blocks = []
    for i, t in enumerate(chosen):
        blocks.append(
            f"""
        <oslc:queryCapability>
            <oslc:QueryCapability jp06:extra="{i}" rdf:ID="qc{i}">
                <dcterms:title>{t} Query Capability</dcterms:title>
                <oslc:label>{t}</oslc:label>
                <oslc:resourceType rdf:resource="{QM_NS}{t}Query"/>
                <oslc:queryBase rdf:resource="{query_base(t)}"/>
            </oslc:QueryCapability>
        </oslc:queryCapability>"""
        )
    half = (len(blocks) + 1) // 2
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<rdf:RDF xmlns:rdf="{RDF}"
    xmlns:dcterms="{DCTERMS}"
    xmlns:jp06="http://jazz.net/xmlns/prod/jazz/process/0.6/"
    xmlns:oslc="{CORE}">
    <oslc:ServiceProvider rdf:about="{QM_SERVICES_XML_URL}">
        <oslc:service>
            <oslc:Service>{"".join(blocks[:half])}
            </oslc:Service>
        </oslc:service>
        <oslc:service>
            <oslc:Service>{"".join(blocks[half:])}
            </oslc:Service>
        </oslc:service>
    </oslc:ServiceProvider>
</rdf:RDF>
"""


# ----------------------------------------------------------------------------------------------
# RDF/XML builders (members inline, query resource, ResponseInfo)
# ----------------------------------------------------------------------------------------------
_XMLNS = (
    f'xmlns:rdf="{RDF}" xmlns:dcterms="{DCTERMS}" xmlns:oslc="{CORE}" xmlns:oslc_qm="{QM_NS}" '
    f'xmlns:rqm_qm="{RQM}" xmlns:oslc_cm="http://open-services.net/ns/cm#" '
    'xmlns:rqm_process="http://jazz.net/xmlns/prod/jazz/rqm/process/1.0/"'
)
_STRING = 'rdf:datatype="http://www.w3.org/2001/XMLSchema#string"'
_INT = 'rdf:datatype="http://www.w3.org/2001/XMLSchema#integer"'


def _res(qname: str, uri: str) -> str:
    return f'  <{qname} rdf:resource="{uri}"/>\n'


def member_xml(
    type_name: str,
    uri: str,
    title: str,
    short_id: str,
    *,
    state: str | None = "com.ibm.rqm.planning.common.new",
    links: dict[str, list[str]] | None = None,
    literals: dict[str, str] | None = None,
) -> str:
    """One inline member: typed rdf:Description with title, identifiers, state, links and extra literals."""
    out = f' <rdf:Description rdf:about="{uri}">\n'
    out += _res("rdf:type", f"{QM_NS}{type_name}")
    out += f"  <dcterms:title {_STRING}>{title}</dcterms:title>\n"
    out += f"  <dcterms:identifier {_STRING}>{short_id}</dcterms:identifier>\n"
    out += f"  <rqm_qm:shortIdentifier {_STRING}>{short_id}</rqm_qm:shortIdentifier>\n"
    out += f'  <oslc:shortId rdf:datatype="http://www.w3.org/2001/XMLSchema#int">{short_id}</oslc:shortId>\n'
    out += '  <dcterms:created rdf:datatype="http://www.w3.org/2001/XMLSchema#dateTime">2026-01-01T10:00:00.000Z</dcterms:created>\n'
    out += _res("dcterms:creator", f"{BASE}/jts/users/jdoe")
    if state:
        out += _res(
            "rqm_process:hasWorkflowState",
            f"{BASE}/qm/service/com.ibm.rqm.integration.service.IIntegrationService/process-info/{PA}"
            f"/workflowstate/com.ibm.rqm.process.testcase.workflow/{state}",
        )
    for qname, targets in (links or {}).items():
        for target in targets:
            out += _res(qname, target)
    for qname, value in (literals or {}).items():
        out += f"  <{qname} {_INT if value.lstrip('-').isdigit() else _STRING}>{value}</{qname}>\n"
    return out + " </rdf:Description>\n"


def _lower_first(text: str) -> str:
    return text[:1].lower() + text[1:]


def page_xml(
    type_name: str,
    members: list[str],
    member_uris: list[str],
    *,
    total: int | None,
    next_page: str | None,
    self_url: str | None = None,
) -> str:
    """A query answer: ResponseInfo + members inline + the query-resource Description (scrambled order)."""
    base = query_base(type_name)
    info = f' <rdf:Description rdf:about="{self_url or base}?oslc_qm.pageSize=2">\n'
    info += f"  <dcterms:title {_STRING}>Response Info Title</dcterms:title>\n"
    if next_page:
        info += _res("oslc:nextPage", next_page.replace("&", "&amp;"))
    if total is not None:
        info += f"  <oslc:totalCount {_INT}>{total}</oslc:totalCount>\n"
    info += _res("rdf:type", f"{CORE}ResponseInfo") + " </rdf:Description>\n"
    query = f' <rdf:Description rdf:about="{base}">\n'
    for uri in member_uris:
        query += _res(f"oslc_qm:{_lower_first(type_name)}", uri)
    query += _res("rdf:type", f"{QM_NS}{type_name}Query") + " </rdf:Description>\n"
    first, rest = (members[:1], members[1:])
    return (
        f'<?xml version="1.0" encoding="UTF-8"?>\n<rdf:RDF {_XMLNS}>\n'
        + "".join(first)
        + info
        + "".join(rest[:1])
        + query
        + "".join(rest[1:])
        + "</rdf:RDF>\n"
    )


def single_xml(member: str) -> str:
    """A GET on one resource: the document holds just that Description."""
    return f'<?xml version="1.0" encoding="UTF-8"?>\n<rdf:RDF {_XMLNS}>\n{member}</rdf:RDF>\n'


def simple_member(type_name: str, n: int, **kwargs) -> tuple[str, str]:
    """(uri, member xml) of the n-th synthetic member of a type."""
    uri = member_uri(type_name, f"_M{n:03d}")
    return uri, member_xml(type_name, uri, f"{type_name} {n}", str(1000 + n), **kwargs)


# ----------------------------------------------------------------------------------------------
# Stateful fake ETM
# ----------------------------------------------------------------------------------------------
class QmServer:
    """Discovery + the 14 query endpoints. ``data`` maps type -> list of (uri, member xml)."""

    def __init__(
        self,
        router: respx.MockRouter,
        data: dict[str, list[tuple[str, str]]] | None = None,
        *,
        server_page_cap: int = 2,
        types_in_services: list[str] | None = None,
        total: dict[str, int | None] | None = None,
        raw_pages: dict[str, str] | None = None,
        loop_forever: bool = False,
        foreign_next_page: str | None = None,
        query_status: int | None = None,
    ) -> None:
        self.router = router
        self.data = data or {}
        self.cap = server_page_cap
        self.total = total or {}
        self.raw_pages = raw_pages or {}
        self.loop_forever = loop_forever
        self.foreign_next_page = foreign_next_page
        self.query_status = query_status
        self.requests: list[httpx.Request] = []
        self.query_requests: dict[str, list[httpx.Request]] = {t: [] for t in QM_TYPES}
        router.post(f"{BASE}/jts/j_security_check").mock(return_value=httpx.Response(200))
        self._get(f"{BASE}/qm/rootservices", QM_ROOTSERVICES_XML)
        self._get(QM_CATALOG_URL, QM_CATALOG_XML)
        services = qm_services_xml(types_in_services)
        self._get(QM_SERVICES_XML_URL, services)
        router.get(f"{QM_CTX}/services").mock(return_value=httpx.Response(404))
        for t in QM_TYPES:
            router.get(query_base(t)).mock(side_effect=lambda request, t=t: self._query(request, t))

    def _get(self, url: str, text: str) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            self.requests.append(request)
            return httpx.Response(200, text=text, headers={"Content-Type": "application/rdf+xml"})

        self.router.get(url).mock(side_effect=handler)

    # -- the query endpoint ------------------------------------------------------------------
    def _query(self, request: httpx.Request, type_name: str) -> httpx.Response:
        self.requests.append(request)
        self.query_requests[type_name].append(request)
        accept = request.headers.get("accept", "")
        if "json" in accept.lower() and "rdf+xml" not in accept.lower():
            return httpx.Response(406, text="Not Acceptable")
        if self.query_status is not None:
            return httpx.Response(self.query_status, text="")
        if type_name in self.raw_pages:
            return self._xml(self.raw_pages[type_name])
        entries = self.data.get(type_name, [])
        params = request.url.params
        page = int(params.get("rqm_qm.pageNum", "0"))
        asked = params.get("oslc_qm.pageSize") or params.get("oslc.pageSize") or str(self.cap)
        size = max(1, min(int(asked), self.cap))
        if self.loop_forever:
            page = 0
        chunk = entries[page * size : (page + 1) * size]
        more = (page + 1) * size < len(entries) or (self.loop_forever and bool(entries))
        next_page = None
        if more:
            next_page = (
                f"{query_base(type_name)}?oslc.paging=true&rqm_qm.queryToken=_TOKEN"
                f"&oslc.pageSize={size}&rqm_qm.pageNum={page + 1}"
            )
            if self.foreign_next_page:
                next_page = self.foreign_next_page
        total = self.total.get(type_name, len(entries)) if type_name in self.total or entries else 0
        xml = page_xml(
            type_name,
            [m for _, m in chunk],
            [u for u, _ in chunk],
            total=total,
            next_page=next_page,
        )
        return self._xml(xml)

    @staticmethod
    def _xml(text: str) -> httpx.Response:
        return httpx.Response(200, text=text, headers={"Content-Type": "application/rdf+xml"})

    # -- assertions helpers ------------------------------------------------------------------
    def queried_types(self) -> set[str]:
        return {t for t, reqs in self.query_requests.items() if reqs}

    def json_requests(self) -> list[httpx.Request]:
        """Every request to /qm/ that asked for JSON (must be empty: the ETM only speaks RDF/XML)."""
        return [r for r in self.requests if r.url.path.startswith("/qm/") and "json" in r.headers.get("accept", "")]


def mock_get_resource(router: respx.MockRouter, uri: str, member: str, calls: list | None = None) -> None:
    """GET on one QM resource -> a document with its Description."""

    def handler(request: httpx.Request) -> httpx.Response:
        if calls is not None:
            calls.append(request)
        accept = request.headers.get("accept", "")
        if "json" in accept.lower() and "rdf+xml" not in accept.lower():
            return httpx.Response(406, text="Not Acceptable")
        return httpx.Response(200, text=single_xml(member), headers={"Content-Type": "application/rdf+xml"})

    router.get(uri).mock(side_effect=handler)


def fixture_text(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


def page_param(request: httpx.Request, name: str) -> str | None:
    return request.url.params.get(name)


_ID_RE = re.compile(r"_M(\d+)$")


def numbers_of(items: list[dict]) -> list[int]:
    """The synthetic member numbers (``_M007`` -> 7) of a list of items, in order."""
    return [int(_ID_RE.search(i["uri"]).group(1)) for i in items]
