"""G3 (RED) tests for SPEC-qm-leitura (wave W3, release v0.6.0).

Tools under test (names fixed by the plan):
    list_qm_resources(project, type, query=None, limit=None)
    get_qm_resource(uri)
    get_test_coverage(project)
    get_test_results(project, test_case=None)
    search_testcases(project, query=None)          (D12, fixed)

The ETM is a stateful fake (tests/w3_fixtures.py::QmServer) on a private respx router: RDF/XML only
(JSON gets 406), 14 query capabilities with a distinct queryBase each, TestExecutionRecord FIRST in the
services.xml, members INLINE, query-resource and ResponseInfo Descriptions that are NOT members, paging by
``oslc_qm.pageSize`` + a tokenised ``oslc:nextPage``. Two real, sanitized pages are served as they are in
tests/fixtures/.

Contract pinned here (the developer validates it, see the G3 report):

- every tool returns ``{"items": [...], "truncated": bool, ...}``; ``list_qm_resources`` also ``total``
  (``oslc:totalCount`` or None); an item is ``{uri, title, identifier, short_id, state, type, ...}`` with
  ``type`` the plain type name ("TestCase") and ``short_id`` a string;
- ``get_qm_resource`` -> ``{uri, type, title, identifier, short_id, state, links: {<group>: [uri, ...]}}``;
- ``get_test_coverage`` -> ``{"items": [{uri, title, short_id, requirements: [uri], work_items: [uri]}], "truncated"}``;
- ``get_test_results`` -> ``{"items": [{uri, title, verdict, points: {passed, failed, blocked},
  execution_record, test_case}], "truncated"}``, ``test_case`` filter is a TestCase URI applied on the
  client (the fake ignores any server-side filter).

New symbols are reached by attribute INSIDE each test so each test fails by itself.
"""

import httpx
import pytest
from mcp.server.mcpserver.exceptions import ToolError

from elm_mcp import server
from elm_mcp.auth import close_client

from .w3_fixtures import (
    BASE,
    OTHER_PROJECT,
    PROJECT,
    QM_TYPES,
    QmServer,
    fixture_text,
    member_uri,
    member_xml,
    mock_get_resource,
    numbers_of,
    query_base,
    rm_uri,
    simple_member,
    wi_uri,
)


@pytest.fixture(autouse=True)
async def _cfg(monkeypatch):
    monkeypatch.setattr("elm_mcp.config.settings.elm_url", BASE)
    yield
    await close_client()


def _members(type_name: str, count: int, **kwargs) -> list[tuple[str, str]]:
    return [simple_member(type_name, n, **kwargs) for n in range(1, count + 1)]


# ======================================================================
# Contract: tools registered with the agreed parameters and hints
# ======================================================================
async def _registered() -> dict:
    return {t.name: t for t in await server.mcp.list_tools()}


@pytest.mark.parametrize(
    ("name", "params", "required"),
    [
        ("list_qm_resources", ["project", "type", "query", "limit"], {"project", "type"}),
        ("get_qm_resource", ["uri"], {"uri"}),
        ("get_test_coverage", ["project"], {"project"}),
        ("get_test_results", ["project", "test_case"], {"project"}),
        ("search_testcases", ["project", "query"], {"project"}),
    ],
)
async def test_qm_read_tool_is_registered_read_only_with_the_agreed_parameters(name, params, required):
    """FAILS today: the four new tools are not registered. All QM tools are read-only (readOnlyHint True)."""
    tools = await _registered()
    assert name in tools, f"tool not registered: {name}"
    schema = tools[name].input_schema or {}
    assert list(schema.get("properties", {})) == params
    assert set(schema.get("required", [])) == required
    assert tools[name].annotations is not None
    assert tools[name].annotations.read_only_hint is True


# ======================================================================
# Real, sanitized pages (members inline; query resource and ResponseInfo are NOT members)
# ======================================================================
async def test_real_testcase_page_yields_one_item_per_member_and_no_pseudo_members(w2_router):
    """EARS-1: 3 members + query Description + ResponseInfo in the page -> exactly the 3 members."""
    qm = QmServer(w2_router, raw_pages={"TestCase": fixture_text("qm_testcase_page.xml")})
    result = await server.list_qm_resources(PROJECT, "TestCase", limit=3)
    items = result["items"]
    assert len(items) == 3
    # "Item 2" is the dcterms:title of the ResponseInfo Description: it must not leak in as a member.
    assert {i["title"] for i in items} == {"Item 1", "Item 3", "Item 4"}
    assert {i["uri"].rsplit("/", 1)[-1] for i in items} == {"_ID001", "_ID002", "_ID003"}
    assert all(i["uri"].startswith(query_base("TestCase") + "/") for i in items)
    assert qm.json_requests() == []


async def test_real_testcase_page_item_has_identifiers_state_and_type(w2_router):
    """EARS-1: URI, title and identifiers (dcterms:identifier, rqm_qm:shortIdentifier), state and type per item."""
    QmServer(w2_router, raw_pages={"TestCase": fixture_text("qm_testcase_page.xml")})
    result = await server.list_qm_resources(PROJECT, "TestCase", limit=3)
    by_title = {i["title"]: i for i in result["items"]}
    one = by_title["Item 1"]
    assert one["uri"] == member_uri("TestCase", "_ID001")
    assert one["short_id"] == "88236"
    assert one["identifier"] == member_uri("TestCase", "_ID001")  # sanitized fixture: dcterms:identifier == URI
    assert one["type"] == "TestCase"
    assert one["state"].endswith("common.new")
    assert by_title["Item 3"]["short_id"] == "88242"
    assert by_title["Item 4"]["short_id"] == "88237"


async def test_real_testcase_page_reports_total_count_and_truncation(w2_router):
    """EARS-5 / aceitação viva: total comes from oslc:totalCount (187); nextPage present at the limit -> truncated."""
    QmServer(w2_router, raw_pages={"TestCase": fixture_text("qm_testcase_page.xml")})
    result = await server.list_qm_resources(PROJECT, "TestCase", limit=3)
    assert result["total"] == 187
    assert result["truncated"] is True


async def test_real_tcer_page_yields_executions(w2_router):
    """EARS-1: the TestExecutionRecord page (2 members + query + ResponseInfo titled 'Item 1') -> 2 items."""
    QmServer(w2_router, raw_pages={"TestExecutionRecord": fixture_text("qm_tcer_page.xml")})
    result = await server.list_qm_resources(PROJECT, "TestExecutionRecord", limit=2)
    assert {i["title"] for i in result["items"]} == {"Item 2", "Item 3"}
    assert {i["short_id"] for i in result["items"]} == {"68157", "68404"}
    assert {i["type"] for i in result["items"]} == {"TestExecutionRecord"}
    assert result["total"] == 4


# ======================================================================
# Capability chosen by oslc:resourceType, never by position
# ======================================================================
@pytest.mark.parametrize("type_name", QM_TYPES)
async def test_each_of_the_14_types_uses_its_own_query_capability(w2_router, type_name):
    """EARS-1 + evidence 1/2: type -> capability whose resourceType is qm#<Type>Query; nothing else is queried."""
    qm = QmServer(w2_router, {type_name: _members(type_name, 1)})
    result = await server.list_qm_resources(PROJECT, type_name)
    assert [i["uri"] for i in result["items"]] == [member_uri(type_name, "_M001")]
    assert result["items"][0]["type"] == type_name
    assert qm.queried_types() == {type_name}


@pytest.mark.parametrize("spelling", ["TestCase", "testcase", "TESTCASE", "tEsTcAsE"])
async def test_type_is_case_insensitive(w2_router, spelling):
    """The type argument accepts any casing and still queries the TestCase capability."""
    qm = QmServer(w2_router, {"TestCase": _members("TestCase", 2)})
    result = await server.list_qm_resources(PROJECT, spelling)
    assert len(result["items"]) == 2
    assert qm.queried_types() == {"TestCase"}


async def test_first_capability_of_the_services_xml_is_never_picked_by_position(w2_router):
    """Bug D12 root cause: TestExecutionRecord is FIRST in services.xml; asking for TestCase must not query it."""
    qm = QmServer(
        w2_router, {"TestCase": _members("TestCase", 2), "TestExecutionRecord": _members("TestExecutionRecord", 5)}
    )
    result = await server.list_qm_resources(PROJECT, "TestCase")
    assert {i["type"] for i in result["items"]} == {"TestCase"}
    assert len(result["items"]) == 2
    assert qm.query_requests["TestExecutionRecord"] == []


async def test_missing_capability_is_an_error_not_a_fallback_to_another_one(w2_router):
    """services.xml without TestScriptStepQuery: ToolError naming the type; no other capability is queried."""
    others = [t for t in QM_TYPES if t != "TestScriptStep"]
    qm = QmServer(w2_router, {"TestExecutionRecord": _members("TestExecutionRecord", 2)}, types_in_services=others)
    with pytest.raises(ToolError, match="TestScriptStep"):
        await server.list_qm_resources(PROJECT, "TestScriptStep")
    assert qm.queried_types() == set()


async def test_unknown_type_lists_the_14_valid_types_before_any_request(w2_router):
    """Invalid type -> ToolError listing the 14 valid ones, decided without touching the network."""
    qm = QmServer(w2_router)
    with pytest.raises(ToolError) as exc:
        await server.list_qm_resources(PROJECT, "Banana")
    for t in QM_TYPES:
        assert t in str(exc.value)
    assert qm.requests == []


async def test_catalog_with_extra_attributes_still_finds_the_projects(w2_router):
    """EARS-3: ServiceProvider/title/QueryCapability with extra attributes -> project resolved, 2nd project ignored."""
    QmServer(w2_router, {"TestCase": _members("TestCase", 1)})
    result = await server.list_qm_resources(PROJECT.upper(), "TestCase")  # project match is case-insensitive
    assert len(result["items"]) == 1


async def test_unknown_project_lists_the_available_ones(w2_router):
    """Unknown project -> ToolError that names the projects of the (extra-attribute) catalog."""
    QmServer(w2_router)
    with pytest.raises(ToolError) as exc:
        await server.list_qm_resources("No Such Project", "TestCase")
    assert PROJECT in str(exc.value)
    assert OTHER_PROJECT in str(exc.value)


# ======================================================================
# RDF/XML only
# ======================================================================
async def test_qm_is_asked_for_rdf_xml_and_never_for_json(w2_router):
    """EARS-2: every QM request (discovery and query) sends Accept containing application/rdf+xml, none asks JSON."""
    qm = QmServer(w2_router, {"TestCase": _members("TestCase", 3)})
    await server.list_qm_resources(PROJECT, "TestCase")
    assert qm.query_requests["TestCase"], "the query endpoint was never called"
    assert qm.json_requests() == []
    for request in qm.query_requests["TestCase"]:
        assert "application/rdf+xml" in request.headers["accept"]


async def test_406_is_diagnosed_as_a_format_gate_not_hidden_by_a_fallback(w2_router):
    """A 406 on the RDF/XML request is reported with the [formato] gate (no silent raw_xml fallback)."""
    QmServer(w2_router, query_status=406)
    with pytest.raises(ToolError, match=r"\[formato\]"):
        await server.list_qm_resources(PROJECT, "TestCase")


async def test_404_on_the_query_names_the_gate(w2_router):
    QmServer(w2_router, query_status=404)
    with pytest.raises(ToolError, match=r"\[recurso-inexistente-ou-invisivel\]"):
        await server.list_qm_resources(PROJECT, "TestCase")


# ======================================================================
# Paging: oslc_qm.pageSize, follow nextPage as it came, up to the limit
# ======================================================================
async def test_first_request_uses_oslc_qm_page_size_not_oslc_page_size(w2_router):
    """Evidence 4: the page-size parameter of the ETM is oslc_qm.pageSize."""
    qm = QmServer(w2_router, {"TestCase": _members("TestCase", 3)})
    await server.list_qm_resources(PROJECT, "TestCase")
    first = qm.query_requests["TestCase"][0]
    assert int(first.url.params["oslc_qm.pageSize"]) > 0
    assert "oslc.pageSize" not in first.url.params


async def test_follows_next_page_as_it_came_until_the_limit(w2_router):
    """EARS-5: 7 members, server pages of 2, limit 5 -> 3 requests, members 1..5, truncated, total 7."""
    qm = QmServer(w2_router, {"TestCase": _members("TestCase", 7)})
    result = await server.list_qm_resources(PROJECT, "TestCase", limit=5)
    assert numbers_of(result["items"]) == [1, 2, 3, 4, 5]
    assert result["truncated"] is True
    assert result["total"] == 7
    requests = qm.query_requests["TestCase"]
    assert len(requests) == 3
    second = requests[1].url.params
    assert second["rqm_qm.queryToken"] == "_TOKEN"  # the token of nextPage, untouched
    assert second["rqm_qm.pageNum"] == "1"
    assert second["oslc.paging"] == "true"


async def test_walks_every_page_when_the_limit_is_above_the_total(w2_router):
    """EARS-5: limit above the total -> all 7 members, not truncated, one request per page."""
    qm = QmServer(w2_router, {"TestCase": _members("TestCase", 7)})
    result = await server.list_qm_resources(PROJECT, "TestCase", limit=100)
    assert numbers_of(result["items"]) == [1, 2, 3, 4, 5, 6, 7]
    assert result["truncated"] is False
    assert result["total"] == 7
    assert len(qm.query_requests["TestCase"]) == 4


async def test_default_limit_is_50(w2_router):
    """No limit -> 50 items (the W2 default), truncated because 120 exist."""
    QmServer(w2_router, {"TestCase": _members("TestCase", 120)}, server_page_cap=25)
    result = await server.list_qm_resources(PROJECT, "TestCase")
    assert numbers_of(result["items"]) == list(range(1, 51))
    assert result["truncated"] is True
    assert result["total"] == 120


@pytest.mark.parametrize("limit", [0, -1])
async def test_non_positive_limit_is_refused(w2_router, limit):
    QmServer(w2_router, {"TestCase": _members("TestCase", 3)})
    with pytest.raises(ToolError, match="limit"):
        await server.list_qm_resources(PROJECT, "TestCase", limit=limit)


async def test_a_server_that_repeats_the_same_page_stops_and_reports_truncation(w2_router):
    """W2 loop guard reused: same members + same nextPage forever -> no infinite walk, no duplicates, truncated."""
    qm = QmServer(w2_router, {"TestCase": _members("TestCase", 2)}, loop_forever=True)
    result = await server.list_qm_resources(PROJECT, "TestCase", limit=100)
    assert numbers_of(result["items"]) == [1, 2]
    assert result["truncated"] is True
    assert len(qm.query_requests["TestCase"]) <= 4


async def test_next_page_on_another_host_is_refused(w2_router):
    """W2 same-origin guard reused: a nextPage pointing outside the configured server is never requested."""
    QmServer(w2_router, {"TestCase": _members("TestCase", 4)}, foreign_next_page="https://evil.example.net/qm/next")
    with pytest.raises(ToolError, match="outside the configured ELM server"):
        await server.list_qm_resources(PROJECT, "TestCase", limit=100)


async def test_total_is_none_when_the_page_has_no_total_count(w2_router):
    QmServer(w2_router, {"TestCase": _members("TestCase", 2)}, total={"TestCase": None})
    result = await server.list_qm_resources(PROJECT, "TestCase")
    assert len(result["items"]) == 2
    assert result["total"] is None


async def test_empty_result_is_an_empty_list_with_total_zero(w2_router):
    QmServer(w2_router)
    result = await server.list_qm_resources(PROJECT, "TestCase")
    assert result["items"] == []
    assert result["truncated"] is False
    assert result["total"] == 0


async def test_query_is_sent_as_oslc_where(w2_router):
    qm = QmServer(w2_router, {"TestCase": _members("TestCase", 1)})
    await server.list_qm_resources(PROJECT, "TestCase", query='dcterms:title="Item 1"')
    assert qm.query_requests["TestCase"][0].url.params["oslc.where"] == 'dcterms:title="Item 1"'


# ======================================================================
# D12: search_testcases
# ======================================================================
async def test_search_testcases_returns_one_item_per_member_from_the_testcase_capability(w2_router):
    """D12: one item per test case (uri + title), TestCase capability, NOT one raw_xml blob of the first capability."""
    qm = QmServer(
        w2_router,
        {"TestCase": _members("TestCase", 3), "TestExecutionRecord": _members("TestExecutionRecord", 2)},
    )
    result = await server.search_testcases(PROJECT)
    assert set(result) == {"items", "truncated"}
    assert result["truncated"] is False
    assert numbers_of(result["items"]) == [1, 2, 3]
    assert [i["title"] for i in result["items"]] == ["TestCase 1", "TestCase 2", "TestCase 3"]
    assert all("raw_xml" not in i for i in result["items"])
    assert qm.queried_types() == {"TestCase"}
    assert qm.json_requests() == []


async def test_search_testcases_on_the_real_page_does_not_return_the_page_as_one_item(w2_router):
    """D12 on the real sanitized page: 3 items (not 1 raw_xml, not 5 Descriptions)."""
    QmServer(w2_router, raw_pages={"TestCase": fixture_text("qm_testcase_page.xml")})
    result = await server.search_testcases(PROJECT)
    assert len(result["items"]) == 3
    assert {i["title"] for i in result["items"]} == {"Item 1", "Item 3", "Item 4"}


async def test_search_testcases_follows_pages_and_sends_the_query(w2_router):
    qm = QmServer(w2_router, {"TestCase": _members("TestCase", 5)})
    result = await server.search_testcases(PROJECT, 'dcterms:title="x"')
    assert numbers_of(result["items"]) == [1, 2, 3, 4, 5]
    assert qm.query_requests["TestCase"][0].url.params["oslc.where"] == 'dcterms:title="x"'
    assert len(qm.query_requests["TestCase"]) == 3


# ======================================================================
# get_qm_resource
# ======================================================================
def _case_with_links() -> tuple[str, str]:
    uri = member_uri("TestCase", "_CASE1")
    xml = member_xml(
        "TestCase",
        uri,
        "Login works",
        "501",
        links={
            "oslc_qm:validatesRequirement": [rm_uri("_REQ1"), rm_uri("_REQ2")],
            "oslc_qm:testsChangeRequest": [wi_uri("100")],
            "oslc_qm:usesTestScript": [member_uri("TestScript", "_SCR1")],
        },
    )
    return uri, xml


async def test_get_qm_resource_lists_requirement_and_work_item_links_with_target_uris(w2_router):
    """EARS-4: validatesRequirement and testsChangeRequest come out grouped, with the destination URIs."""
    qm = QmServer(w2_router)
    uri, xml = _case_with_links()
    calls: list[httpx.Request] = []
    mock_get_resource(w2_router, uri, xml, calls)
    result = await server.get_qm_resource(uri)
    assert result["uri"] == uri
    assert result["type"] == "TestCase"
    assert result["title"] == "Login works"
    assert result["short_id"] == "501"
    assert result["state"].endswith("common.new")
    links = result["links"]
    assert sorted(links["validates_requirement"]) == [rm_uri("_REQ1"), rm_uri("_REQ2")]
    assert links["tests_change_request"] == [wi_uri("100")]
    assert links["uses_test_script"] == [member_uri("TestScript", "_SCR1")]
    assert "application/rdf+xml" in calls[0].headers["accept"]
    assert qm.json_requests() == []


async def test_get_qm_resource_execution_record_links(w2_router):
    """Evidence 5: runsTestCase, executesTestScript, producesTestResult/currentTestResult of a TCER."""
    QmServer(w2_router)
    uri = member_uri("TestExecutionRecord", "_TER1")
    result_uri = member_uri("TestResult", "_RES1")
    xml = member_xml(
        "TestExecutionRecord",
        uri,
        "Run 1",
        "601",
        links={
            "oslc_qm:runsTestCase": [member_uri("TestCase", "_CASE1")],
            "oslc_qm:executesTestScript": [member_uri("TestScript", "_SCR1")],
            "rqm_qm:producesTestResult": [result_uri],
            "rqm_qm:currentTestResult": [result_uri],
        },
    )
    mock_get_resource(w2_router, uri, xml)
    result = await server.get_qm_resource(uri)
    assert result["type"] == "TestExecutionRecord"
    assert result["links"]["runs_test_case"] == [member_uri("TestCase", "_CASE1")]
    assert result["links"]["executes_test_script"] == [member_uri("TestScript", "_SCR1")]
    assert result_uri in result["links"]["test_results"]


async def test_get_qm_resource_without_links_has_no_link_targets(w2_router):
    QmServer(w2_router)
    uri, xml = simple_member("TestCase", 1)
    mock_get_resource(w2_router, uri, xml)
    result = await server.get_qm_resource(uri)
    assert result["title"] == "TestCase 1"
    assert result["links"].get("validates_requirement", []) == []
    assert result["links"].get("tests_change_request", []) == []


async def test_get_qm_resource_reads_the_described_resource_not_a_decoy_in_the_same_document(w2_router):
    """The document may carry other Descriptions: the one whose rdf:about is the requested URI wins."""
    QmServer(w2_router)
    uri, xml = _case_with_links()
    _, decoy = simple_member("TestCase", 9)
    mock_get_resource(w2_router, uri, decoy + xml)
    result = await server.get_qm_resource(uri)
    assert result["title"] == "Login works"
    assert result["uri"] == uri


async def test_get_qm_resource_refuses_a_uri_on_another_host_without_any_request(w2_router):
    qm = QmServer(w2_router)
    with pytest.raises(ToolError, match="outside the configured ELM server"):
        await server.get_qm_resource("https://evil.example.net/qm/oslc_qm/contexts/_PA1/resources/x/_1")
    assert qm.requests == []


async def test_get_qm_resource_404_names_the_gate(w2_router):
    QmServer(w2_router)
    uri = member_uri("TestCase", "_GONE")
    w2_router.get(uri).mock(return_value=httpx.Response(404))
    with pytest.raises(ToolError, match=r"\[recurso-inexistente-ou-invisivel\]"):
        await server.get_qm_resource(uri)


# ======================================================================
# get_test_coverage: from the INLINE links of the listing, no GET per test case
# ======================================================================
def _coverage_cases() -> list[tuple[str, str]]:
    cases = []
    spec = [
        (1, [rm_uri("_REQ1"), rm_uri("_REQ2")], [wi_uri("100")]),
        (2, [rm_uri("_REQ1")], []),
        (3, [], []),
        (4, [rm_uri("_REQ3")], [wi_uri("100"), wi_uri("200")]),
    ]
    for n, reqs, wis in spec:
        cases.append(
            simple_member(
                "TestCase",
                n,
                links={"oslc_qm:validatesRequirement": reqs, "oslc_qm:testsChangeRequest": wis},
            )
        )
    return cases


async def test_get_test_coverage_returns_requirements_and_work_items_per_test_case(w2_router):
    """Traceability: per case, the validated requirements and the tested work items (4 cases over 2 pages)."""
    qm = QmServer(w2_router, {"TestCase": _coverage_cases()})
    result = await server.get_test_coverage(PROJECT)
    assert result["truncated"] is False
    by_n = {n: item for n, item in zip(numbers_of(result["items"]), result["items"], strict=True)}
    assert sorted(by_n) == [1, 2, 3, 4]
    assert sorted(by_n[1]["requirements"]) == [rm_uri("_REQ1"), rm_uri("_REQ2")]
    assert by_n[1]["work_items"] == [wi_uri("100")]
    assert by_n[2]["requirements"] == [rm_uri("_REQ1")]
    assert by_n[2]["work_items"] == []
    assert by_n[3]["requirements"] == []
    assert by_n[3]["work_items"] == []
    assert sorted(by_n[4]["work_items"]) == [wi_uri("100"), wi_uri("200")]
    assert by_n[4]["title"] == "TestCase 4"
    assert by_n[4]["short_id"] == "1004"
    assert qm.queried_types() == {"TestCase"}


async def test_get_test_coverage_does_not_fetch_each_test_case(w2_router):
    """No 1-GET-per-case: the member URIs have no route, so any such GET would raise; only the query is called."""
    qm = QmServer(w2_router, {"TestCase": _coverage_cases()})
    await server.get_test_coverage(PROJECT)
    member_paths = {u.split(BASE, 1)[1] for u, _ in _coverage_cases()}
    assert not [r for r in qm.requests if r.url.path in member_paths]
    assert len(qm.query_requests["TestCase"]) == 2  # 4 cases / server pages of 2


async def test_get_test_coverage_of_a_project_without_test_cases_is_empty(w2_router):
    QmServer(w2_router)
    result = await server.get_test_coverage(PROJECT)
    assert result["items"] == []
    assert result["truncated"] is False


# ======================================================================
# get_test_results
# ======================================================================
VERDICT = "http://jazz.net/ns/qm/rqm/execution/verdict/"


def _result(n: int, verdict: str, passed: int, failed: int, blocked: int, case: str, ter: str) -> tuple[str, str]:
    return simple_member(
        "TestResult",
        n,
        links={
            "rqm_qm:verdict": [f"{VERDICT}{verdict}"],
            "oslc_qm:reportsOnTestCase": [case],
            "oslc_qm:producedByTestExecutionRecord": [ter],
        },
        literals={
            "rqm_qm:pointsPassed": str(passed),
            "rqm_qm:pointsFailed": str(failed),
            "rqm_qm:pointsBlocked": str(blocked),
        },
    )


def _results() -> list[tuple[str, str]]:
    c1, c2 = member_uri("TestCase", "_CASE1"), member_uri("TestCase", "_CASE2")
    t1, t2, t3 = (member_uri("TestExecutionRecord", f"_TER{i}") for i in (1, 2, 3))
    return [
        _result(1, "passed", 5, 0, 0, c1, t1),
        _result(2, "failed", 3, 2, 1, c1, t2),
        _result(3, "passed", 4, 0, 0, c2, t3),
    ]


async def test_get_test_results_returns_verdict_points_and_originating_execution_record(w2_router):
    """Results from the TestResult capability, with verdict, points per status and the TCER they came from."""
    qm = QmServer(w2_router, {"TestResult": _results(), "TestExecutionRecord": _members("TestExecutionRecord", 4)})
    result = await server.get_test_results(PROJECT)
    assert result["truncated"] is False
    by_n = dict(zip(numbers_of(result["items"]), result["items"], strict=True))
    assert sorted(by_n) == [1, 2, 3]
    assert by_n[1]["verdict"].endswith("passed")
    assert by_n[2]["verdict"].endswith("failed")
    assert by_n[2]["points"] == {"passed": 3, "failed": 2, "blocked": 1}
    assert by_n[1]["points"]["passed"] == 5
    assert by_n[2]["execution_record"] == member_uri("TestExecutionRecord", "_TER2")
    assert by_n[3]["test_case"] == member_uri("TestCase", "_CASE2")
    assert qm.queried_types() == {"TestResult"}
    assert qm.json_requests() == []


async def test_get_test_results_filters_by_test_case(w2_router):
    """test_case=<TestCase URI> keeps only the results that report on that case (client-side)."""
    QmServer(w2_router, {"TestResult": _results()})
    result = await server.get_test_results(PROJECT, test_case=member_uri("TestCase", "_CASE1"))
    assert numbers_of(result["items"]) == [1, 2]


async def test_get_test_results_for_a_case_without_results_is_empty(w2_router):
    QmServer(w2_router, {"TestResult": _results()})
    result = await server.get_test_results(PROJECT, test_case=member_uri("TestCase", "_NONE"))
    assert result["items"] == []
