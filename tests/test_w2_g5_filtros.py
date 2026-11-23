"""RED G5 filtros: id de estado/tipo/owner validado depois do unquote, heurística 'ponto = id' e workflow só de estados.

Tudo via respx (roteador privado ``w2_router``) e fixtures neutras (elm.example.com, _PA1, jdoe).
"""

import httpx
import pytest
from mcp.server.mcpserver.exceptions import ToolError

from elm_mcp import server

from . import f1_fixtures as f
from .test_w2_aceitacao_viva import (
    ACTIONS_DOC,
    STATE_IDS,
    STATES_DOC,
    WORKFLOW,
    _mock_workflow,
    _where_of,
)
from .w2_fixtures import BASE, PROJECT, FakePages, mock_ccm, wi


# ---------------------------------------------------------------- 1) injeção por %-encoding
@pytest.mark.parametrize(
    "tail",
    [
        "x.y%22%20or%20rtc_cm:type%3D%22task",
        "x.y%22",
        "x.y%20z",
        "x.y%3Dz",
        "x.y%0Az",
        'x.y"',
        "x.y z",
    ],
)
async def test_state_uri_with_encoded_injection_is_rejected_and_sends_no_query(w2_router, tail):
    mock_ccm(w2_router)
    pages = FakePages(w2_router, [[wi(1)]])
    with pytest.raises(ToolError, match="state"):
        await server.list_workitems(PROJECT, state=f"{STATES_DOC}/{tail}")
    assert pages.requests == []


@pytest.mark.parametrize("state_id", ["com.example.wf.state.s-1", "a.b:c-d_e", "wf.v1.state.s.2"])
async def test_state_id_with_dot_hyphen_and_colon_is_accepted(w2_router, state_id):
    mock_ccm(w2_router)
    assert await _where_of(w2_router, state=state_id) == f'rtc_cm:state="{state_id}"'
    assert await _where_of(w2_router, state=f"{STATES_DOC}/{state_id}") == f'rtc_cm:state="{state_id}"'


@pytest.mark.parametrize("state", ["x.y or z", 'x.y"', "x.y=z", "é.ñ", "x.y%22"])
async def test_state_id_with_forbidden_characters_is_rejected(w2_router, state):
    mock_ccm(w2_router)
    pages = FakePages(w2_router, [[wi(1)]])
    with pytest.raises(ToolError):
        await server.list_workitems(PROJECT, state=state)
    assert pages.requests == []


async def test_state_id_from_the_server_with_encoded_quote_is_never_sent(w2_router):
    mock_ccm(w2_router)
    _mock_workflow(w2_router, ids=[f"{WORKFLOW}.state.s1%22%20or%20x", STATE_IDS[1]], states_doc_ok=False)
    pages = FakePages(w2_router, [[wi(1)]])
    with pytest.raises(ToolError):
        await server.list_workitems(PROJECT, type="task", state=f"{WORKFLOW}.state.s1%22%20or%20x")
    assert pages.requests == []


async def test_state_title_resolved_to_a_hostile_server_id_is_rejected(w2_router):
    mock_ccm(w2_router)
    _mock_workflow(w2_router)
    hostile = {"oslc:results": [{"dcterms:identifier": 'x.y" or rtc_cm:type="task', "dcterms:title": "Evil"}]}
    w2_router.get(STATES_DOC).mock(return_value=httpx.Response(200, json=hostile))
    pages = FakePages(w2_router, [[wi(1)]])
    with pytest.raises(ToolError):
        await server.list_workitems(PROJECT, type="task", state="Evil")
    assert pages.requests == []


async def test_owner_with_encoded_or_special_characters_stays_inside_the_uri(w2_router):
    mock_ccm(w2_router)
    where = await _where_of(w2_router, owner="jdoe or x")
    assert where == f"dcterms:contributor=<{BASE}/jts/users/jdoe%20or%20x>"
    assert '"' not in where


# ---------------------------------------------------------------- 2) heurística 'ponto = id'
async def test_with_type_a_title_containing_a_dot_is_resolved_by_title_not_taken_as_id(w2_router):
    mock_ccm(w2_router)
    doc = {"oslc:results": [{"dcterms:identifier": STATE_IDS[0], "dcterms:title": "Fase 1.2 aprovada"}]}
    _mock_workflow(w2_router)
    w2_router.get(STATES_DOC).mock(return_value=httpx.Response(200, json=doc))
    where = await _where_of(w2_router, type="task", state="Fase 1.2 aprovada")
    assert where == f'rtc_cm:type="task" and rtc_cm:state="{STATE_IDS[0]}"'


async def test_with_type_a_value_with_dot_that_is_not_in_the_workflow_is_an_error(w2_router):
    mock_ccm(w2_router)
    _mock_workflow(w2_router)
    pages = FakePages(w2_router, [[wi(1)]])
    with pytest.raises(ToolError, match="list_workflow"):
        await server.list_workitems(PROJECT, type="task", state="com.other.state.zz")
    assert pages.requests == []


async def test_with_type_exact_id_wins_over_a_title_equal_to_another_id(w2_router):
    mock_ccm(w2_router)
    doc = {
        "oslc:results": [
            {"dcterms:identifier": STATE_IDS[0], "dcterms:title": "New"},
            {"dcterms:identifier": STATE_IDS[1], "dcterms:title": STATE_IDS[0]},
        ]
    }
    _mock_workflow(w2_router)
    w2_router.get(STATES_DOC).mock(return_value=httpx.Response(200, json=doc))
    where = await _where_of(w2_router, type="task", state=STATE_IDS[0])
    assert where == f'rtc_cm:type="task" and rtc_cm:state="{STATE_IDS[0]}"'


async def test_with_type_an_ambiguous_title_is_an_error(w2_router):
    mock_ccm(w2_router)
    doc = {
        "oslc:results": [
            {"dcterms:identifier": STATE_IDS[0], "dcterms:title": "Same"},
            {"dcterms:identifier": STATE_IDS[1], "dcterms:title": "same"},
        ]
    }
    _mock_workflow(w2_router)
    w2_router.get(STATES_DOC).mock(return_value=httpx.Response(200, json=doc))
    pages = FakePages(w2_router, [[wi(1)]])
    with pytest.raises(ToolError, match="ambiguous"):
        await server.list_workitems(PROJECT, type="task", state="Same")
    assert pages.requests == []


@pytest.mark.parametrize("title", ["Fase 1.2 aprovada", "Closed", "v1.2 final", "a b.c"])
async def test_without_type_a_title_is_never_taken_as_an_id(w2_router, title):
    mock_ccm(w2_router)
    pages = FakePages(w2_router, [[wi(1)]])
    with pytest.raises(ToolError, match="list_workflow"):
        await server.list_workitems(PROJECT, state=title)
    assert pages.requests == []


# ---------------------------------------------------------------- 3) _workflow_states sem actions
async def test_state_title_filter_does_not_depend_on_the_actions_document(w2_router):
    mock_ccm(w2_router)
    _mock_workflow(w2_router)
    w2_router.get(ACTIONS_DOC).mock(return_value=httpx.Response(404))
    where = await _where_of(w2_router, type="task", state="in progress")
    assert where == f'rtc_cm:type="task" and rtc_cm:state="{STATE_IDS[1]}"'
    assert not any(str(c.request.url) == ACTIONS_DOC for c in w2_router.calls)


async def test_list_workflow_still_needs_the_actions_document(w2_router):
    mock_ccm(w2_router)
    _mock_workflow(w2_router)
    w2_router.get(ACTIONS_DOC).mock(return_value=httpx.Response(404))
    with pytest.raises(ToolError):
        await server.list_workflow(PROJECT, "task")


# ---------------------------------------------------------------- type resolvido
async def test_type_factory_id_with_forbidden_characters_is_rejected(w2_router):
    mock_ccm(w2_router)
    from elm_mcp import oslc

    bad = {"id": 'task" or x="', "title": "Bad", "shape_uri": None}
    orig = oslc._resolve_ccm_type
    oslc._resolve_ccm_type = lambda factories, t: bad
    try:
        with pytest.raises(ToolError):
            await server.list_workitems(PROJECT, type="bad")
    finally:
        oslc._resolve_ccm_type = orig
    assert f.STORY_ID  # fixture import kept explicit
