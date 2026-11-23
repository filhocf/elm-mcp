"""RED da aceitação viva da W2: o que o servidor real aceita em oslc.where e onde está o workflow.

Evidência (servidor ELM 7.0.1, sanitizada) que os mocks anteriores não pegaram:
- oslc.where aceita ``rtc_cm:type="<id>"`` e ``rtc_cm:state="<id completo do estado>"``;
  ``dcterms:type="<título>"`` não filtra, ``oslc_cm:status`` dá HTTP 400 e o owner só vale como URI.
- A propriedade ``state`` da shape do tipo aponta para
  ``.../shapes/workitems/<tipo>/property/internalState/allowedValues`` (NÃO contém /oslc/workflows);
  esse documento lista as URIs ``.../oslc/workflows/{pa}/states/{wf}/{estado}``.

Tudo via respx em roteador privado (``w2_router``) e fixtures neutras (elm.example.com, _PA1, jdoe).
"""

import re

import httpx
import pytest
from mcp.server.mcpserver.exceptions import ToolError

from elm_mcp import server

from . import f1_fixtures as f
from .w2_fixtures import BASE, PROJECT, FakePages, mock_ccm, wi

WORKFLOW = "com.example.workflow"
STATES_DOC = f"{BASE}/ccm/oslc/workflows/{f.PA}/states/{WORKFLOW}"
ACTIONS_DOC = f"{BASE}/ccm/oslc/workflows/{f.PA}/actions/{WORKFLOW}"
STATE_IDS = [f"{WORKFLOW}.state.s1", f"{WORKFLOW}.state.s3", f"{WORKFLOW}.state.s4"]
TASK_STATES_ALLOWED = f"{BASE}/ccm/oslc/shapes/workitems/task/property/internalState/allowedValues"
STORY_STATES_ALLOWED = f"{BASE}/ccm/oslc/shapes/workitems/{f.STORY_ID}/property/internalState/allowedValues"


def _allowed_values_xml(state_ids: list[str]) -> str:
    refs = "".join(f'<oslc:allowedValue rdf:resource="{STATES_DOC}/{state_id}"/>' for state_id in state_ids)
    return (
        '<rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#" '
        'xmlns:oslc="http://open-services.net/ns/core#">'
        f'<rdf:Description rdf:about="{TASK_STATES_ALLOWED}">'
        '<rdf:type rdf:resource="http://open-services.net/ns/core#AllowedValues"/>'
        f"{refs}</rdf:Description></rdf:RDF>"
    )


def _states_json() -> dict:
    return {
        "oslc:results": [
            {"dcterms:identifier": STATE_IDS[0], "dcterms:title": "New", "rtc_cm:group": "open"},
            {"dcterms:identifier": STATE_IDS[1], "dcterms:title": "In Progress", "rtc_cm:group": "inprogress"},
            {"dcterms:identifier": STATE_IDS[2], "dcterms:title": "Done", "rtc_cm:group": "closed"},
        ]
    }


def _mock_workflow(router, *, type_id: str = "task", states_doc_ok: bool = True, ids: list[str] | None = None) -> None:
    """Shape of the type (state -> internalState/allowedValues) + allowedValues + workflow docs."""
    allowed_url = f"{BASE}/ccm/oslc/shapes/workitems/{type_id}/property/internalState/allowedValues"
    state_prop = f.prop("state", "State", "Exactly-one", propdef=f"{f.RTC_CM}state", allowed=allowed_url)
    router.get(f.shape_uri(type_id)).mock(
        return_value=httpx.Response(200, text=f.real_shape_xml(type_id, [state_prop]))
    )
    router.get(allowed_url).mock(return_value=httpx.Response(200, text=_allowed_values_xml(ids or STATE_IDS)))
    if states_doc_ok:
        router.get(STATES_DOC).mock(return_value=httpx.Response(200, json=_states_json()))
    else:
        router.get(STATES_DOC).mock(return_value=httpx.Response(404))
    router.get(ACTIONS_DOC).mock(
        return_value=httpx.Response(200, json={"oslc:results": [{"rdf:about": f"{ACTIONS_DOC}/{WORKFLOW}.action.a1"}]})
    )


async def _where_of(router, query: str | None = None, **filters) -> str:
    pages = FakePages(router, [[wi(1)]])
    await server.list_workitems(PROJECT, query, **filters)
    where = pages.where_of_first_request()
    assert where is not None
    return where


# ======================================================================
# B) list_workflow: o workflow vem do documento allowedValues da propriedade state
# ======================================================================
async def test_list_workflow_reads_the_states_from_the_allowed_values_document(w2_router):
    mock_ccm(w2_router)
    _mock_workflow(w2_router)
    result = await server.list_workflow(PROJECT, "task")
    assert result["workflow"] == WORKFLOW
    assert result["states"] == [
        {"id": STATE_IDS[0], "title": "New", "group": "open"},
        {"id": STATE_IDS[1], "title": "In Progress", "group": "inprogress"},
        {"id": STATE_IDS[2], "title": "Done", "group": "closed"},
    ]
    assert result["actions"] == [f"{WORKFLOW}.action.a1"]


async def test_list_workflow_without_the_states_titles_document_returns_the_state_ids(w2_router):
    mock_ccm(w2_router)
    _mock_workflow(w2_router, states_doc_ok=False)
    result = await server.list_workflow(PROJECT, "task")
    assert result["workflow"] == WORKFLOW
    assert [s["id"] for s in result["states"]] == STATE_IDS
    assert all(s["title"] is None and s["group"] is None for s in result["states"])


async def test_list_workflow_allowed_values_without_workflow_uris_is_an_error(w2_router):
    mock_ccm(w2_router)
    _mock_workflow(w2_router)
    w2_router.get(TASK_STATES_ALLOWED).mock(return_value=httpx.Response(200, text=_allowed_values_xml([])))
    with pytest.raises(ToolError, match="workflow"):
        await server.list_workflow(PROJECT, "task")


# ======================================================================
# A) filtros de list_workitems: só o que o servidor aceita
# ======================================================================
@pytest.mark.parametrize("value", ["Task", "task", "TASK"])
async def test_type_filter_is_resolved_by_discovery_and_sent_as_rtc_cm_type_with_the_id(w2_router, value):
    mock_ccm(w2_router)
    where = await _where_of(w2_router, type=value)
    assert where == 'rtc_cm:type="task"'
    assert "dcterms:type" not in where


@pytest.mark.parametrize("value", ["Story", "story", f.STORY_ID])
async def test_type_filter_resolves_title_alias_and_full_id_to_the_full_id(w2_router, value):
    mock_ccm(w2_router)
    where = await _where_of(w2_router, type=value)
    assert where == f'rtc_cm:type="{f.STORY_ID}"'


async def test_type_filter_unknown_type_lists_the_available_types_and_sends_no_query(w2_router):
    mock_ccm(w2_router)
    pages = FakePages(w2_router, [[wi(1)]])
    with pytest.raises(ToolError, match="Story"):
        await server.list_workitems(PROJECT, type="epic")
    assert pages.requests == []


async def test_state_filter_with_a_full_state_id_is_sent_as_rtc_cm_state(w2_router):
    mock_ccm(w2_router)
    where = await _where_of(w2_router, state=STATE_IDS[1])
    assert where == f'rtc_cm:state="{STATE_IDS[1]}"'
    assert "oslc_cm:status" not in where


async def test_state_filter_with_a_state_uri_uses_its_last_segment(w2_router):
    mock_ccm(w2_router)
    where = await _where_of(w2_router, state=f"{STATES_DOC}/{STATE_IDS[1]}")
    assert where == f'rtc_cm:state="{STATE_IDS[1]}"'


async def test_state_filter_with_a_title_is_resolved_by_the_workflow_of_the_given_type(w2_router):
    mock_ccm(w2_router)
    _mock_workflow(w2_router)
    where = await _where_of(w2_router, type="task", state="in progress")
    assert where == f'rtc_cm:type="task" and rtc_cm:state="{STATE_IDS[1]}"'


async def test_state_filter_with_a_title_and_no_type_asks_for_the_state_id(w2_router):
    mock_ccm(w2_router)
    pages = FakePages(w2_router, [[wi(1)]])
    with pytest.raises(ToolError, match="list_workflow"):
        await server.list_workitems(PROJECT, state="Cancelado")
    assert pages.requests == []


async def test_state_filter_with_a_title_that_does_not_resolve_asks_for_the_state_id(w2_router):
    mock_ccm(w2_router)
    _mock_workflow(w2_router)
    pages = FakePages(w2_router, [[wi(1)]])
    with pytest.raises(ToolError, match="list_workflow"):
        await server.list_workitems(PROJECT, type="task", state="Nonexistent")
    assert pages.requests == []


async def test_state_filter_never_uses_oslc_cm_status(w2_router):
    mock_ccm(w2_router)
    _mock_workflow(w2_router)
    for kwargs in ({"state": STATE_IDS[0]}, {"type": "task", "state": "Done"}):
        where = await _where_of(w2_router, **kwargs)
        assert "oslc_cm:status" not in where


@pytest.mark.parametrize("owner", ["jdoe", f"{BASE}/jts/users/jdoe"])
async def test_owner_filter_is_always_the_user_uri_never_a_quoted_name(w2_router, owner):
    mock_ccm(w2_router)
    where = await _where_of(w2_router, owner=owner)
    assert where == f"dcterms:contributor=<{BASE}/jts/users/jdoe>"
    assert '"' not in where


async def test_filters_are_combined_with_and_in_the_server_accepted_forms(w2_router):
    mock_ccm(w2_router)
    iteration = f"{BASE}/ccm/resource/itemOid/com.ibm.team.apt.Iteration/_ITER1"
    where = await _where_of(
        w2_router, 'dcterms:title="Login"', type="story", state=STATE_IDS[0], owner="jdoe", planned_for=iteration
    )
    assert re.split(r"\s+and\s+", where) == [
        'dcterms:title="Login"',
        f'rtc_cm:type="{f.STORY_ID}"',
        f'rtc_cm:state="{STATE_IDS[0]}"',
        f"dcterms:contributor=<{BASE}/jts/users/jdoe>",
        f"rtc_cm:plannedFor=<{iteration}>",
    ]
