"""G3 (RED) tests for SPEC-oslc-links (wave W3, release v0.6.0).

Tools under test: ``link_artifacts(source_uri, target_uri, link_type)`` and
``unlink_artifacts(source_uri, target_uri, link_type)``.

The SOURCE is a CCM work item (served by ``FakeItem`` from tests/w2_fixtures.py, a stateful fake with an
ETag and a partial PUT that applies only ``?oslc.properties``); the TARGET is an RM or QM URI. The write
format follows ``link_workitems`` (W2): JSON partial PUT of ``oslc_cm:<type>`` as a list of
``{"rdf:resource": uri}``, ``If-Match`` with the ETag read just before, then a re-read that must show the
change. The write format is NOT verified live (writes to the reference ALM are suspended): it stays
"to be confirmed in live acceptance".

Contract pinned here (the developer validates it, see the G3 report):
- result ``{"source_uri", "target_uri", "link_type", "property", "changed"}``; ``property`` is
  ``oslc_cm:<type>``; ``changed`` is False (and no PUT is sent) when the link already exists;
- the closed list is the 13 ``oslc_cm`` types below; anything else (including ``tracksWorkItem``,
  ``trackedWorkItem``, ``tracksChangeSet``, the W2 ``rtc_cm`` types and a wrong case) is refused before
  any request;
- target and source must be on the configured server; source == target is refused; unlinking a target
  that is not linked is a ToolError without a PUT.

New symbols are reached by attribute INSIDE each test so each test fails by itself.
"""

import json

import httpx
import pytest
from mcp.server.mcpserver.exceptions import ToolError

from elm_mcp import server
from elm_mcp.auth import close_client

from .w2_fixtures import BASE, FakeItem, item_uri, mock_login
from .w3_fixtures import member_uri, rm_uri

LINK_TYPES = [
    "implementsRequirement",
    "tracksRequirement",
    "affectsRequirement",
    "testedByTestCase",
    "relatedTestCase",
    "relatedTestScript",
    "relatedTestPlan",
    "relatedTestExecutionRecord",
    "affectsPlanItem",
    "affectedByDefect",
    "affectsTestResult",
    "blocksTestExecutionRecord",
    "relatedChangeRequest",
]
TESTED_BY = "oslc_cm:testedByTestCase"
IMPLEMENTS = "oslc_cm:implementsRequirement"
SRC = item_uri("100")


@pytest.fixture(autouse=True)
async def _cfg(monkeypatch):
    monkeypatch.setattr("elm_mcp.config.settings.elm_url", BASE)
    yield
    await close_client()


def _case(n: str = "_C1") -> str:
    return member_uri("TestCase", n)


def _entry(uri: str) -> dict:
    return {"rdf:resource": uri, "dcterms:title": "linked"}


def _source(router, data: dict | None = None, **kwargs) -> FakeItem:
    mock_login(router)
    return FakeItem(router, "100", data=data, **kwargs)


def _targets(body: dict, prop: str) -> list[str]:
    return [e["rdf:resource"] for e in body[prop]]


async def _registered() -> dict:
    return {t.name: t for t in await server.mcp.list_tools()}


# ======================================================================
# Contract
# ======================================================================
@pytest.mark.parametrize("name", ["link_artifacts", "unlink_artifacts"])
async def test_link_tools_are_registered_as_writes_with_three_required_parameters(name):
    """FAILS today: not registered. Both write to the ALM: readOnlyHint must NOT be True."""
    tools = await _registered()
    assert name in tools, f"tool not registered: {name}"
    schema = tools[name].input_schema or {}
    assert list(schema.get("properties", {})) == ["source_uri", "target_uri", "link_type"]
    assert set(schema.get("required", [])) == {"source_uri", "target_uri", "link_type"}
    hint = tools[name].annotations.read_only_hint if tools[name].annotations else None
    assert not hint


# ======================================================================
# link_artifacts
# ======================================================================
@pytest.mark.parametrize("link_type", LINK_TYPES)
async def test_link_artifacts_puts_only_the_oslc_cm_property_with_if_match(w2_router, link_type):
    """EARS-1: partial PUT of oslc_cm:<type> on the source, conditioned on its ETag, naming the target."""
    source = _source(w2_router)
    target = rm_uri("_REQ1")
    result = await server.link_artifacts(SRC, target, link_type)
    prop = f"oslc_cm:{link_type}"
    assert len(source.puts) == 1
    assert source.put_properties() == prop
    assert source.puts[0].headers["if-match"] == '"etag1"'
    body = source.put_body()
    assert set(body) - {"prefixes"} == {prop}
    assert _targets(body, prop) == [target]
    assert result["link_type"] == link_type
    assert result["property"] == prop
    assert result["source_uri"] == SRC
    assert result["target_uri"] == target
    assert result["changed"] is True


async def test_link_artifacts_accepts_a_qm_target(w2_router):
    """The destination may be a QM test case, not only RM."""
    source = _source(w2_router)
    await server.link_artifacts(SRC, _case(), "testedByTestCase")
    assert _targets(source.put_body(), TESTED_BY) == [_case()]


async def test_link_artifacts_confirms_by_rereading_the_source(w2_router):
    """EARS-1: after the PUT the source is read again (GET before the PUT and GET after it)."""
    source = _source(w2_router)
    await server.link_artifacts(SRC, rm_uri("_REQ1"), "implementsRequirement")
    assert len(source.gets) >= 2
    assert [e["rdf:resource"] for e in source.data[IMPLEMENTS]] == [rm_uri("_REQ1")]


async def test_link_artifacts_preserves_the_other_targets_of_the_same_type(w2_router):
    """Linking a 3rd test case keeps the 2 already there (the PUT replaces the property, so it must resend them)."""
    existing = [_entry(_case("_C1")), _entry(_case("_C2"))]
    source = _source(w2_router, {TESTED_BY: existing})
    await server.link_artifacts(SRC, _case("_C3"), "testedByTestCase")
    assert set(_targets(source.put_body(), TESTED_BY)) == {_case("_C1"), _case("_C2"), _case("_C3")}
    assert len(_targets(source.put_body(), TESTED_BY)) == 3


async def test_link_artifacts_reads_a_single_valued_property_given_as_an_object(w2_router):
    """A server may return one target as a bare object (not a list): it is preserved all the same."""
    source = _source(w2_router, {TESTED_BY: _entry(_case("_C1"))})
    await server.link_artifacts(SRC, _case("_C2"), "testedByTestCase")
    assert set(_targets(source.put_body(), TESTED_BY)) == {_case("_C1"), _case("_C2")}


async def test_link_artifacts_does_not_touch_other_properties_or_link_types(w2_router):
    """The PUT body names one property: title and a link of another type stay out of it and on the item."""
    source = _source(w2_router, {IMPLEMENTS: [_entry(rm_uri("_REQ9"))]})
    await server.link_artifacts(SRC, _case(), "testedByTestCase")
    body = source.put_body()
    assert IMPLEMENTS not in body
    assert "dcterms:title" not in body
    assert source.put_properties() == TESTED_BY
    assert source.data["dcterms:title"] == "Item 100"
    assert [e["rdf:resource"] for e in source.data[IMPLEMENTS]] == [rm_uri("_REQ9")]


async def test_link_artifacts_is_idempotent_when_the_link_already_exists(w2_router):
    """Already linked -> changed False and NO PUT (same behavior as link_workitems)."""
    source = _source(w2_router, {TESTED_BY: [_entry(_case("_C1"))]})
    result = await server.link_artifacts(SRC, _case("_C1"), "testedByTestCase")
    assert result["changed"] is False
    assert source.puts == []


async def test_link_artifacts_raises_when_the_reread_does_not_show_the_link(w2_router):
    """The server answers 200 but ignores the PUT -> ToolError, and the PUT is not repeated."""
    source = _source(w2_router, apply_put=False)
    with pytest.raises(ToolError):
        await server.link_artifacts(SRC, rm_uri("_REQ1"), "implementsRequirement")
    assert len(source.puts) == 1


async def test_link_artifacts_without_an_etag_does_not_write(w2_router):
    source = _source(w2_router, etag=None)
    with pytest.raises(ToolError, match="ETag"):
        await server.link_artifacts(SRC, rm_uri("_REQ1"), "implementsRequirement")
    assert source.puts == []


async def test_link_artifacts_412_names_the_conflict_gate(w2_router):
    """Stale ETag (412) -> [conflito] gate; the write is not retried."""
    source = _source(w2_router, put_response=httpx.Response(412))
    with pytest.raises(ToolError, match=r"\[conflito\]"):
        await server.link_artifacts(SRC, rm_uri("_REQ1"), "implementsRequirement")
    assert len(source.puts) == 1


async def test_link_artifacts_403_with_server_message_is_a_process_rule(w2_router):
    """A 403 carrying oslc:message is passed verbatim and is not resent."""
    body = json.dumps({"oslc:message": "Work item is closed"})
    source = _source(
        w2_router, put_response=httpx.Response(403, text=body, headers={"Content-Type": "application/json"})
    )
    with pytest.raises(ToolError, match=r"\[regra-de-processo\].*Work item is closed"):
        await server.link_artifacts(SRC, rm_uri("_REQ1"), "implementsRequirement")
    assert len(source.puts) == 1


# ---- refusals that need no network --------------------------------------------------------------
@pytest.mark.parametrize("tool", ["link_artifacts", "unlink_artifacts"])
async def test_unknown_link_type_lists_the_valid_types_before_any_request(w2_router, tool):
    """EARS-2: invalid type -> ToolError listing the 13 valid types; the router has no route, so no request happened."""
    with pytest.raises(ToolError) as exc:
        await getattr(server, tool)(SRC, rm_uri(), "implementsBanana")
    for link_type in LINK_TYPES:
        assert link_type in str(exc.value)


@pytest.mark.parametrize(
    "bad",
    [
        "tracksWorkItem",
        "trackedWorkItem",
        "tracksChangeSet",
        "related",
        "rtc_cm:com.ibm.team.workitem.linktype.relatedworkitem.related",
        "oslc_cm:testedByTestCase",
        "TestedByTestCase",
        "",
    ],
)
async def test_the_list_is_closed_to_the_13_oslc_cm_types(w2_router, bad):
    """Types of the shape that are out of the W3 scope, W2 link types and wrong spellings are refused."""
    with pytest.raises(ToolError):
        await server.link_artifacts(SRC, rm_uri(), bad)


async def test_target_on_another_host_is_refused_without_writing(w2_router):
    """Same-origin rule: a destination outside the configured server is refused, nothing is written."""
    source = _source(w2_router)
    with pytest.raises(ToolError, match="outside the configured ELM server"):
        await server.link_artifacts(SRC, "https://evil.example.net/rm/resources/_REQ1", "implementsRequirement")
    assert source.puts == []


async def test_source_on_another_host_is_refused_without_any_request(w2_router):
    with pytest.raises(ToolError, match="outside the configured ELM server"):
        await server.link_artifacts(
            "https://evil.example.net/ccm/resource/itemName/com.ibm.team.workitem.WorkItem/100",
            rm_uri(),
            "implementsRequirement",
        )


async def test_source_equal_to_target_is_refused(w2_router):
    source = _source(w2_router)
    with pytest.raises(ToolError, match="itself"):
        await server.link_artifacts(SRC, SRC, "relatedChangeRequest")
    assert source.puts == []


async def test_empty_target_is_refused(w2_router):
    source = _source(w2_router)
    with pytest.raises(ToolError):
        await server.link_artifacts(SRC, "  ", "implementsRequirement")
    assert source.puts == []


# ======================================================================
# unlink_artifacts
# ======================================================================
async def test_unlink_artifacts_removes_only_the_target_and_resends_the_rest(w2_router):
    """EARS-3: PUT with If-Match carrying the remaining targets only; other properties untouched."""
    existing = [_entry(_case("_C1")), _entry(_case("_C2")), _entry(_case("_C3"))]
    source = _source(w2_router, {TESTED_BY: existing, IMPLEMENTS: [_entry(rm_uri("_REQ9"))]})
    result = await server.unlink_artifacts(SRC, _case("_C2"), "testedByTestCase")
    assert len(source.puts) == 1
    assert source.put_properties() == TESTED_BY
    assert source.puts[0].headers["if-match"] == '"etag1"'
    body = source.put_body()
    assert set(body) - {"prefixes"} == {TESTED_BY}
    assert sorted(_targets(body, TESTED_BY)) == [_case("_C1"), _case("_C3")]
    assert [e["rdf:resource"] for e in source.data[IMPLEMENTS]] == [rm_uri("_REQ9")]
    assert source.data["dcterms:title"] == "Item 100"
    assert result["changed"] is True
    assert result["property"] == TESTED_BY
    assert result["source_uri"] == SRC
    assert result["target_uri"] == _case("_C2")


async def test_unlink_artifacts_removing_the_last_target_sends_an_empty_list(w2_router):
    source = _source(w2_router, {TESTED_BY: [_entry(_case("_C1"))]})
    await server.unlink_artifacts(SRC, _case("_C1"), "testedByTestCase")
    assert source.put_body()[TESTED_BY] == []
    assert source.data[TESTED_BY] == []


async def test_unlink_artifacts_of_a_missing_target_is_an_error_without_put(w2_router):
    """EARS: a target that is not linked -> ToolError, no PUT (nothing to remove)."""
    source = _source(w2_router, {TESTED_BY: [_entry(_case("_C1"))]})
    with pytest.raises(ToolError, match="nothing to remove"):
        await server.unlink_artifacts(SRC, _case("_C9"), "testedByTestCase")
    assert source.puts == []


async def test_unlink_artifacts_with_the_target_linked_under_another_type_is_an_error_without_put(w2_router):
    """The target is linked as implementsRequirement, not testedByTestCase: unlinking the wrong type is refused."""
    source = _source(w2_router, {IMPLEMENTS: [_entry(rm_uri("_REQ1"))]})
    with pytest.raises(ToolError):
        await server.unlink_artifacts(SRC, rm_uri("_REQ1"), "testedByTestCase")
    assert source.puts == []


async def test_unlink_artifacts_raises_when_the_reread_still_shows_the_link(w2_router):
    source = _source(w2_router, {TESTED_BY: [_entry(_case("_C1"))]}, apply_put=False)
    with pytest.raises(ToolError):
        await server.unlink_artifacts(SRC, _case("_C1"), "testedByTestCase")
    assert len(source.puts) == 1


async def test_unlink_artifacts_on_another_host_is_refused(w2_router):
    source = _source(w2_router, {TESTED_BY: [_entry(_case("_C1"))]})
    with pytest.raises(ToolError, match="outside the configured ELM server"):
        await server.unlink_artifacts(SRC, "https://evil.example.net/qm/x", "testedByTestCase")
    assert source.puts == []


async def test_link_then_unlink_round_trip_leaves_the_item_as_it_was(w2_router):
    """Link a new target and unlink it again: the original targets are back and nothing else changed."""
    source = _source(w2_router, {TESTED_BY: [_entry(_case("_C1"))]})
    await server.link_artifacts(SRC, _case("_C2"), "testedByTestCase")
    await server.unlink_artifacts(SRC, _case("_C2"), "testedByTestCase")
    assert [e["rdf:resource"] for e in source.data[TESTED_BY]] == [_case("_C1")]
    assert source.data["dcterms:title"] == "Item 100"
