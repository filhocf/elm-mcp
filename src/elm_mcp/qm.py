"""ETM (QM) reading: query capabilities by resource type, RDF/XML pages, coverage and results.

The ETM only speaks RDF/XML (JSON gets 406). A query answer holds the members INLINE as typed
``rdf:Description`` elements, next to the query resource itself (type ``...Query``) and an
``oslc:ResponseInfo`` (``oslc:totalCount``, ``oslc:nextPage``), which are not members.

Premises not verified live (to be confirmed in live acceptance): the property names of a TestResult
(``verdict``, ``pointsPassed``/``pointsFailed``/``pointsBlocked``, ``reportsOnTestCase``,
``producedByTestExecutionRecord``) and the ``rdf:type`` of Keyword, BuildRecord and TestEnvironment members.
"""

import logging
from xml.etree import ElementTree as ET

import httpx

from . import oslc
from .auth import get_client

logger = logging.getLogger(__name__)

QM_TYPES = (
    "TestPlan",
    "TestCase",
    "TestScript",
    "TestScriptStep",
    "TestSuite",
    "TestExecutionRecord",
    "TestSuiteExecutionRecord",
    "TestResult",
    "TestSuiteResult",
    "TestData",
    "Keyword",
    "TestEnvironment",
    "BuildRecord",
    "BuildDefinition",
)

_RDF = oslc.NS["rdf"]
_CORE = oslc.NS["oslc"]
_DCTERMS = oslc.NS["dcterms"]
_RQM = "http://jazz.net/ns/qm/rqm#"
_RQM_PROCESS = "http://jazz.net/xmlns/prod/jazz/rqm/process/1.0/"
# Namespaces of the QM domain types. Members are recognised by an rdf:type in one of them
# (the rqm namespace for types the ETM may not publish as oslc_qm: to be confirmed in live acceptance).
_QM_TYPE_NAMESPACES = (oslc.NS["oslc_qm"], _RQM)
_HEADERS = {"Accept": "application/rdf+xml", "OSLC-Core-Version": "2.0"}

# Link property (local name) -> group of the "links" of get_qm_resource.
_LINK_GROUPS = {
    "validatesRequirement": "validates_requirement",
    "testsChangeRequest": "tests_change_request",
    "usesTestScript": "uses_test_script",
    "runsTestCase": "runs_test_case",
    "executesTestScript": "executes_test_script",
    "producesTestResult": "test_results",
    "currentTestResult": "test_results",
    "reportsOnTestCase": "reports_on_test_case",
    "producedByTestExecutionRecord": "produced_by_execution_record",
}


def resolve_type(type_arg: str) -> str:
    """Canonical QM type name (case-insensitive); ValueError listing the valid ones otherwise."""
    wanted = (type_arg or "").strip().lower()
    for name in QM_TYPES:
        if name.lower() == wanted:
            return name
    raise ValueError(f"Unknown QM type '{type_arg}'. Valid types: {', '.join(QM_TYPES)}")


def _parse(xml_text: str) -> ET.Element:
    try:
        return ET.fromstring(xml_text)
    except ET.ParseError as exc:
        raise ValueError(f"The ETM answer is not valid RDF/XML ({exc}).") from exc


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _text(desc: ET.Element, ns: str, name: str) -> str | None:
    el = desc.find(f"{{{ns}}}{name}")
    return el.text.strip() if el is not None and el.text and el.text.strip() else None


def _types(desc: ET.Element) -> list[str]:
    return [t.get(f"{{{_RDF}}}resource", "") for t in desc.findall(f"{{{_RDF}}}type")]


def _member_type(desc: ET.Element) -> str | None:
    """Plain name of the QM domain type of a Description (not a ``*Query``), or None (not a member)."""
    for uri in _types(desc):
        for ns in _QM_TYPE_NAMESPACES:
            local = uri[len(ns) :] if uri.startswith(ns) else ""
            if local and not local.endswith("Query"):
                return local
    return None


def _describe(desc: ET.Element, type_name: str) -> dict:
    """Normalized view of one QM Description (raw links and literals by local name kept for the tools)."""
    links: dict[str, list[str]] = {}
    literals: dict[str, str] = {}
    for child in desc:
        local = _local(child.tag)
        target = child.get(f"{{{_RDF}}}resource")
        if target:
            if target not in links.setdefault(local, []):
                links[local].append(target)
        elif child.text and child.text.strip():
            literals.setdefault(local, child.text.strip())
    state = desc.find(f"{{{_RQM_PROCESS}}}hasWorkflowState")
    return {
        "uri": desc.get(f"{{{_RDF}}}about"),
        "type": type_name,
        "title": _text(desc, _DCTERMS, "title"),
        "identifier": _text(desc, _DCTERMS, "identifier"),
        "short_id": _text(desc, _RQM, "shortIdentifier") or _text(desc, _CORE, "shortId"),
        "state": state.get(f"{{{_RDF}}}resource") if state is not None else None,
        "_links": links,
        "_literals": literals,
    }


def _public(member: dict) -> dict:
    return {k: v for k, v in member.items() if not k.startswith("_")}


def _grouped_links(member: dict) -> dict[str, list[str]]:
    groups: dict[str, list[str]] = {}
    for local, group in _LINK_GROUPS.items():
        for target in member["_links"].get(local, []):
            if target not in groups.setdefault(group, []):
                groups[group].append(target)
    return groups


def _page(xml_text: str) -> tuple[list[dict], int | None, str | None]:
    """(members, totalCount, nextPage) of a query answer."""
    members: list[dict] = []
    total: int | None = None
    next_url: str | None = None
    for desc in _parse(xml_text).findall(f"{{{_RDF}}}Description"):
        if f"{_CORE}ResponseInfo" in _types(desc):
            nxt = desc.find(f"{{{_CORE}}}nextPage")
            if nxt is not None and nxt.get(f"{{{_RDF}}}resource"):
                next_url = nxt.get(f"{{{_RDF}}}resource")
            count = _text(desc, _CORE, "totalCount")
            if count is not None and count.lstrip("-").isdigit():
                total = int(count)
            continue
        type_name = _member_type(desc)
        if type_name is not None and desc.get(f"{{{_RDF}}}about"):
            members.append(_describe(desc, type_name))
    return members, total, next_url


async def _query_base(project_url: str, type_name: str) -> str:
    """queryBase of the capability whose oslc:resourceType is ``qm#<type>Query`` (never by position)."""
    client = await get_client()
    wanted = f"{oslc.NS['oslc_qm']}{type_name}Query"
    if project_url.endswith("services.xml"):
        candidates = [project_url.replace("/services.xml", "/services"), project_url]
    else:
        candidates = [project_url.rstrip("/") + "/services", project_url]
    last_error: ValueError | None = None
    for url in candidates:
        try:
            root = await oslc._get_xml(client, url)
        except (httpx.HTTPError, ValueError, ET.ParseError) as exc:
            if isinstance(exc, ValueError) and not isinstance(exc, ET.ParseError):
                last_error = exc
            continue
        for qc in root.iter(f"{{{_CORE}}}QueryCapability"):
            rt = qc.find(f"{{{_CORE}}}resourceType")
            base = qc.find(f"{{{_CORE}}}queryBase")
            if rt is not None and base is not None and rt.get(f"{{{_RDF}}}resource") == wanted:
                return base.get(f"{{{_RDF}}}resource")
        last_error = None  # a document was read: the type is simply not offered (no other capability is used)
        break
    if last_error is not None:
        raise last_error
    raise ValueError(f"The project '{project_url}' has no query capability for QM type {type_name} ({wanted}).")


async def query_members(
    project: str, type_name: str, where: str | None = None, limit: int | None = None
) -> tuple[list[dict], int | None, bool]:
    """Members of a QM type, following ``oslc:nextPage`` as it came until ``limit`` (default 50, max 1000).

    Returns (members, totalCount or None, truncated). Same guards as ``oslc.query_resources``: same origin,
    repeated next page, page without new member and a page ceiling stop the walk (truncated).
    """
    type_name = resolve_type(type_name)
    if limit is not None and limit <= 0:
        raise ValueError(
            f"limit must be a positive number (got {limit}); omit it for the default of {oslc.DEFAULT_QUERY_LIMIT}."
        )
    cap = min(limit if limit else oslc.DEFAULT_QUERY_LIMIT, oslc.MAX_QUERY_LIMIT)
    project_url = await oslc._resolve_project_url("qm", project)
    url = await _query_base(project_url, type_name)
    client = await get_client()
    params = {"oslc_qm.pageSize": str(min(cap, oslc.DEFAULT_QUERY_LIMIT))}
    if where:
        params["oslc.where"] = where
    resp = await oslc.fetch(client, "GET", url, "query_qm", params=params, headers=_HEADERS)

    results: list[dict] = []
    seen: set[str] = set()
    visited = {url}
    total: int | None = None
    pages = 1
    while True:
        page_members, page_total, next_url = _page(resp.text)
        if pages == 1:
            total = page_total
        new_on_page = 0
        for member in page_members:
            if member["uri"] in seen:
                continue
            seen.add(member["uri"])
            if len(results) >= cap:
                return results, total, True
            results.append(member)
            new_on_page += 1
        if next_url is None:
            return results, total, False
        if len(results) >= cap:
            return results, total, True
        if next_url in visited or new_on_page == 0 or pages >= oslc.MAX_QUERY_PAGES:
            logger.warning("query_members: stopped at page %d (repeated next page, no new item or ceiling)", pages)
            return results, total, True
        visited.add(next_url)
        pages += 1
        try:
            resp = await oslc.fetch(client, "GET", next_url, "query_qm", headers=_HEADERS)
        except (ValueError, RuntimeError) as exc:
            raise ValueError(f"{exc} ({len(results)} items had been collected before this page failed.)") from exc


async def list_resources(project: str, type_name: str, query: str | None = None, limit: int | None = None) -> dict:
    members, total, truncated = await query_members(project, type_name, query, limit)
    return {"items": [_public(m) for m in members], "truncated": truncated, "total": total}


async def search_testcases(project: str, query: str | None = None) -> dict:
    members, _, truncated = await query_members(project, "TestCase", query, oslc.MAX_QUERY_LIMIT)
    return {"items": [_public(m) for m in members], "truncated": truncated}


async def get_resource(uri: str) -> dict:
    """Normalized properties and links of one QM resource (the Description whose rdf:about is ``uri``)."""
    client = await get_client()
    resp = await oslc.fetch(client, "GET", uri, "get_qm_resource", headers=_HEADERS)
    descriptions = _parse(resp.text).findall(f"{{{_RDF}}}Description")
    chosen = [d for d in descriptions if d.get(f"{{{_RDF}}}about") == uri]
    if not chosen and len(descriptions) == 1:
        chosen = descriptions
    if not chosen:
        raise ValueError(f"The ETM answer for {uri} does not describe that resource.")
    member = _describe(chosen[0], _member_type(chosen[0]) or "")
    return {**_public(member), "uri": uri, "links": _grouped_links(member)}


async def get_test_coverage(project: str) -> dict:
    """Per test case: validated requirements and tested work items, from the links inline in the listing."""
    members, _, truncated = await query_members(project, "TestCase", None, oslc.MAX_QUERY_LIMIT)
    items = []
    for m in members:
        groups = _grouped_links(m)
        items.append(
            {
                "uri": m["uri"],
                "title": m["title"],
                "short_id": m["short_id"],
                "requirements": groups.get("validates_requirement", []),
                "work_items": groups.get("tests_change_request", []),
            }
        )
    return {"items": items, "truncated": truncated}


def _points(member: dict, name: str) -> int | None:
    value = member["_literals"].get(name)
    return int(value) if value is not None and value.lstrip("-").isdigit() else None


async def get_test_results(project: str, test_case: str | None = None) -> dict:
    """Test results with verdict, points and originating execution record; ``test_case`` filters client-side."""
    members, _, truncated = await query_members(project, "TestResult", None, oslc.MAX_QUERY_LIMIT)
    items = []
    for m in members:
        groups = _grouped_links(m)
        cases = groups.get("reports_on_test_case", [])
        if test_case and test_case not in cases:
            continue
        verdicts = m["_links"].get("verdict", [])
        records = groups.get("produced_by_execution_record", [])
        items.append(
            {
                "uri": m["uri"],
                "title": m["title"],
                "short_id": m["short_id"],
                "verdict": verdicts[0] if verdicts else None,
                "points": {
                    "passed": _points(m, "pointsPassed"),
                    "failed": _points(m, "pointsFailed"),
                    "blocked": _points(m, "pointsBlocked"),
                },
                "execution_record": records[0] if records else None,
                "test_case": cases[0] if cases else None,
            }
        )
    return {"items": items, "truncated": truncated}
