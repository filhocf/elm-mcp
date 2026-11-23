"""G3 (RED) tests for SPEC-rm-consulta (wave W4, release v0.6.0).

``search_requirements`` must (1) discover the configuration (stream) of the project: service provider ->
``oslc_config:component`` -> component -> ``oslc_config:configurations`` -> ``rdfs:member``; (2) send it as
``Configuration-Context`` together with ``oslc.prefix`` and ``oslc.select``; (3) turn the RDF/JSON answer
into ONE item per requirement (container and ResponseInfo excluded) with the total taken from
"Query Results: N"; (4) keep pagination, limit, same origin and the gate diagnosis.

The fake server (tests/w4_fixtures.py FakeRmQuery) answers 400 without the context header and 400 for an
undeclared prefix, like the real one (G0 evidence 1-2). Tenant-neutral data only.
"""

import json
import re

import httpx
import pytest
import respx
from mcp.server.mcpserver.exceptions import ToolError

from elm_mcp import server
from elm_mcp.auth import close_client

from . import w1_fixtures as w1
from . import w4_fixtures as w4
from .w4_fixtures import BASE, PROJECT

SELECT_NEEDED = {"dcterms:title", "dcterms:identifier", "dcterms:modified", "oslc:instanceShape"}


@pytest.fixture(autouse=True)
async def _cfg(monkeypatch):
    monkeypatch.setattr("elm_mcp.config.settings.elm_url", BASE)
    yield
    await close_client()


def _rows(n: int) -> list[dict]:
    return [w4.rm_req(i, f"Requirement {i}", folder=w4.FOLDER_A if i % 2 else None) for i in range(1, n + 1)]


# ======================================================================
# Stream discovery -> Configuration-Context
# ======================================================================
@respx.mock
async def test_context_header_is_the_stream_found_through_component_and_configurations():
    routes = w4.mock_rm_config([w4.STREAM_1])
    fake = w4.FakeRmQuery(_rows(2))
    result = await server.search_requirements(PROJECT, 'dcterms:title="x"')
    assert routes["component"].call_count == 1
    assert routes["configurations"].call_count == 1
    assert fake.headers_seen and set(fake.headers_seen) == {w4.STREAM_1}
    assert len(result["items"]) == 2


@respx.mock
async def test_context_header_is_sent_on_every_page():
    w4.mock_rm_config([w4.STREAM_1])
    fake = w4.FakeRmQuery(_rows(5), page_size=2)
    result = await server.search_requirements(PROJECT, None, limit=5)
    assert len(result["items"]) == 5
    assert len(fake.requests) == 3
    assert fake.headers_seen == [w4.STREAM_1] * 3


@respx.mock
async def test_several_streams_use_the_first_listed_and_report_it():
    w4.mock_rm_config([w4.STREAM_1, w4.STREAM_2])
    fake = w4.FakeRmQuery(_rows(1))
    result = await server.search_requirements(PROJECT, None)
    assert set(fake.headers_seen) == {w4.STREAM_1}
    assert result["configuration"] == w4.STREAM_1


@respx.mock
async def test_single_stream_is_reported_in_the_result():
    w4.mock_rm_config([w4.STREAM_2])
    w4.FakeRmQuery(_rows(1))
    result = await server.search_requirements(PROJECT, None)
    assert result["configuration"] == w4.STREAM_2


@respx.mock
async def test_project_without_configuration_queries_without_the_header():
    w4.mock_rm_config(None, services=w4.services_xml_without_configuration())
    fake = w4.FakeRmQuery(_rows(2), require_context=False)
    result = await server.search_requirements(PROJECT, None)
    assert fake.requests, "the query must still be sent"
    assert all(h is None for h in fake.headers_seen)
    assert len(result["items"]) == 2
    assert result.get("configuration") is None


@respx.mock
async def test_component_without_streams_queries_without_the_header():
    w4.mock_rm_config([])
    fake = w4.FakeRmQuery(_rows(1), require_context=False)
    result = await server.search_requirements(PROJECT, None)
    assert all(h is None for h in fake.headers_seen)
    assert [i["title"] for i in result["items"]] == ["Requirement 1"]


@respx.mock
async def test_server_400_for_a_missing_context_reaches_the_caller_with_the_gate_diagnosis():
    """No configuration is discoverable and the server insists on one: the 400 is reported, never an empty list."""
    w4.mock_rm_config(None, services=w4.services_xml_without_configuration())
    w4.FakeRmQuery(_rows(1), require_context=True)
    with pytest.raises(ToolError, match=r"\[http-400\]"):
        await server.search_requirements(PROJECT, None)


# ======================================================================
# oslc.prefix and oslc.select
# ======================================================================
@respx.mock
async def test_prefixes_used_by_the_query_are_declared_in_oslc_prefix():
    w4.mock_rm_config([w4.STREAM_1])
    fake = w4.FakeRmQuery(_rows(1))
    await server.search_requirements(PROJECT, 'dcterms:title="Login" and nav:parent=<' + w4.FOLDER_A + ">")
    first = fake.requests[0].url.params
    declared = dict(re.findall(r"(\w+)=<([^>]*)>", first["oslc.prefix"]))
    for prefix, uri in w4.PREFIX_URIS.items():
        assert declared.get(prefix) == uri, f"prefix {prefix} not declared (got {first['oslc.prefix']!r})"
    assert first["oslc.where"].startswith('dcterms:title="Login"')


@respx.mock
async def test_select_asks_for_title_identifier_modified_and_instance_shape():
    w4.mock_rm_config([w4.STREAM_1])
    fake = w4.FakeRmQuery(_rows(1))
    await server.search_requirements(PROJECT, None)
    select = set(fake.requests[0].url.params["oslc.select"].split(","))
    assert SELECT_NEEDED <= select, select


@respx.mock
async def test_query_without_where_sends_no_empty_where():
    w4.mock_rm_config([w4.STREAM_1])
    fake = w4.FakeRmQuery(_rows(1))
    await server.search_requirements(PROJECT, None)
    assert "oslc.where" not in fake.requests[0].url.params


# ======================================================================
# One item per requirement (container and ResponseInfo excluded)
# ======================================================================
@respx.mock
async def test_one_item_per_requirement_with_the_documented_fields():
    w4.mock_rm_config([w4.STREAM_1])
    rows = _rows(2)
    w4.FakeRmQuery(rows)
    result = await server.search_requirements(PROJECT, None)
    assert [i["uri"] for i in result["items"]] == [r["uri"] for r in rows]
    first = result["items"][0]
    assert first["title"] == "Requirement 1"
    assert str(first["identifier"]) == "1001"
    assert first["type"] == w1.SHAPE_REQ
    assert first["modified"] == "2026-01-02T03:04:05.000Z"
    assert first["folder"] == w4.FOLDER_A
    assert result["items"][1].get("folder") is None


@respx.mock
async def test_the_raw_rdf_json_dictionary_is_not_returned():
    w4.mock_rm_config([w4.STREAM_1])
    w4.FakeRmQuery(_rows(2))
    result = await server.search_requirements(PROJECT, None)
    for item in result["items"]:
        assert not any(str(k).startswith("http") for k in item), list(item)
    assert not any("viewsnull" in json.dumps(i) for i in result["items"])


@respx.mock
async def test_real_page_fixture_yields_two_requirements_and_the_total_from_the_response_info_title():
    """tests/fixtures/rm_query_page.json: 2 requirements + container ``viewsnull`` + ResponseInfo 'Query Results: 398'."""
    w4.mock_rm_config([w4.STREAM_1])
    page = json.loads(w4.RM_PAGE_FIXTURE.read_text(encoding="utf-8"))
    respx.get(w4.VIEWS_URL).mock(return_value=httpx.Response(200, json=page))
    result = await server.search_requirements(PROJECT, None, limit=2)
    items = result["items"]
    assert [i["uri"] for i in items] == [f"{BASE}/rm/resources/TX_ID001", f"{BASE}/rm/resources/TX_ID003"]
    assert items[0]["title"] == "EL - Detalhar os dados do imóvel rural selecionado"
    assert str(items[0]["identifier"]) == "353765"
    assert items[0]["type"] == f"{BASE}/rm/types/OT_ID002"
    assert items[0]["modified"] == "2025-03-24T14:40:46.995Z"
    assert result["total"] == 398
    assert result["truncated"] is True


@respx.mock
async def test_total_prefers_oslc_total_count_over_the_title():
    w4.mock_rm_config([w4.STREAM_1])
    w4.FakeRmQuery(_rows(3), page_size=10, total_count=7)
    result = await server.search_requirements(PROJECT, None)
    assert result["total"] == 7


@respx.mock
async def test_total_comes_from_the_title_when_there_is_no_total_count():
    w4.mock_rm_config([w4.STREAM_1])
    w4.FakeRmQuery(_rows(3), page_size=10)
    result = await server.search_requirements(PROJECT, None)
    assert result["total"] == 3
    assert result["truncated"] is False


# ======================================================================
# Pagination, limit, same origin, cycle, gate diagnosis
# ======================================================================
@respx.mock
async def test_pagination_follows_the_rdf_json_next_page():
    w4.mock_rm_config([w4.STREAM_1])
    fake = w4.FakeRmQuery(_rows(5), page_size=2)
    result = await server.search_requirements(PROJECT, None, limit=10)
    assert [i["title"] for i in result["items"]] == [f"Requirement {i}" for i in range(1, 6)]
    assert result["truncated"] is False
    assert len(fake.requests) == 3


@respx.mock
async def test_limit_stops_the_walk_and_flags_truncation():
    w4.mock_rm_config([w4.STREAM_1])
    fake = w4.FakeRmQuery(_rows(6), page_size=2)
    result = await server.search_requirements(PROJECT, None, limit=3)
    assert len(result["items"]) == 3
    assert result["truncated"] is True
    assert len(fake.requests) == 2, "no page beyond the one that reaches the limit"


@respx.mock
async def test_limit_one_asks_a_single_page():
    w4.mock_rm_config([w4.STREAM_1])
    fake = w4.FakeRmQuery(_rows(6), page_size=2)
    result = await server.search_requirements(PROJECT, None, limit=1)
    assert len(result["items"]) == 1
    assert len(fake.requests) == 1


@respx.mock
@pytest.mark.parametrize("limit", [0, -3])
async def test_non_positive_limit_is_refused_before_any_query(limit):
    w4.mock_rm_config([w4.STREAM_1])
    fake = w4.FakeRmQuery(_rows(1))
    with pytest.raises(ToolError, match="limit"):
        await server.search_requirements(PROJECT, None, limit=limit)
    assert fake.requests == []


@respx.mock
async def test_next_page_on_another_origin_is_refused_and_never_requested():
    w4.mock_rm_config([w4.STREAM_1])
    evil = "https://evil.example.net/rm/views?page=2"
    w4.FakeRmQuery(_rows(4), page_size=2, next_override={1: evil})
    with pytest.raises(ToolError, match="outside the configured ELM server"):
        await server.search_requirements(PROJECT, None, limit=10)


@respx.mock
async def test_repeated_next_page_stops_the_walk_without_duplicates():
    w4.mock_rm_config([w4.STREAM_1])
    loop_url = f"{w4.VIEWS_URL}?page=2"
    fake = w4.FakeRmQuery(_rows(6), page_size=2, next_override={2: loop_url})
    result = await server.search_requirements(PROJECT, None, limit=100)
    uris = [i["uri"] for i in result["items"]]
    assert len(uris) == len(set(uris)) == 4
    assert result["truncated"] is True
    assert len(fake.requests) <= 4


@respx.mock
async def test_a_failing_next_page_names_the_gate_and_how_many_items_were_collected():
    w4.mock_rm_config([w4.STREAM_1])
    fake = w4.FakeRmQuery(_rows(4), page_size=2)
    original = fake._serve

    def serve(request: httpx.Request) -> httpx.Response:
        if request.url.params.get("page") == "2":
            fake.requests.append(request)
            return httpx.Response(500, text="boom")
        return original(request)

    fake.route.mock(side_effect=serve)
    with pytest.raises(ToolError, match=r"\[http-500\].*2 items had been collected"):
        await server.search_requirements(PROJECT, None, limit=10)


@respx.mock
async def test_first_page_error_names_the_gate():
    w4.mock_rm_config([w4.STREAM_1])
    w4.FakeRmQuery(_rows(1), fail=httpx.Response(403, json={"oslc:message": "no access"}))
    with pytest.raises(ToolError, match=r"\[regra-de-processo\].*no access"):
        await server.search_requirements(PROJECT, None)


# ======================================================================
# Contract: the tool takes ``limit``
# ======================================================================
async def test_search_requirements_is_registered_with_a_limit_parameter():
    tool = next(t for t in await server.mcp.list_tools() if t.name == "search_requirements")
    assert "limit" in tool.input_schema["properties"]
