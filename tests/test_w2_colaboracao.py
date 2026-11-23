"""G3 (RED) tests for SPEC-ccm-colaboracao (wave W2, release v0.6.0).

Tools under test: add_comment, list_comments, link_workitems, unlink_workitems, set_field,
list_workflow and the filters of list_workitems. Behavior is observed through respx (bodies,
headers, query params, call counts) on a PRIVATE router: an unrouted request raises, so no test
can reach a real server. Tenant-neutral data only (see tests/w2_fixtures.py).

New symbols are reached by attribute (``server.add_comment``) INSIDE each test, so every test
fails on its own against the current code (AttributeError / TypeError) instead of one collection
error hiding the rest.
"""

import json
import re

import httpx
import pytest
from mcp.server.mcpserver.exceptions import ToolError

from elm_mcp import server
from elm_mcp.auth import close_client
from elm_mcp.server import mcp

from . import f1_fixtures as f
from .test_w2_aceitacao_viva import _mock_workflow
from .w2_fixtures import (
    BASE,
    POINTS_ENUM,
    PREFIXES,
    PROJECT,
    SEVERITY_ENUM,
    FakeComments,
    FakeItem,
    FakePages,
    item_uri,
    literal_uri,
    mock_ccm,
    mock_enum_shape,
    mock_login,
    wi,
)

LINK_PREFIX = "rtc_cm:com.ibm.team.workitem.linktype."
# Link type (tool argument) -> work item property. Names verified read-only on the real server (7.0.1).
LINK_PROPS = {
    "parent": LINK_PREFIX + "parentworkitem.parent",
    "blocks": LINK_PREFIX + "blocksworkitem.blocks",
    "depends_on": LINK_PREFIX + "blocksworkitem.dependsOn",
    "related": LINK_PREFIX + "relatedworkitem.related",
    "duplicate_of": LINK_PREFIX + "duplicateworkitem.duplicateOf",
    "resolves": LINK_PREFIX + "resolvesworkitem.resolves",
}
RELATED = LINK_PROPS["related"]


@pytest.fixture(autouse=True)
async def _cfg(monkeypatch):
    monkeypatch.setattr("elm_mcp.config.settings.elm_url", BASE)
    yield
    await close_client()


# ======================================================================
# Contract: the new tools are registered with the agreed parameters
# ======================================================================
async def _registered() -> dict:
    return {t.name: t for t in await mcp.list_tools()}


@pytest.mark.parametrize(
    ("name", "params", "read_only"),
    [
        ("add_comment", ["id", "text"], False),
        ("list_comments", ["id"], True),
        ("link_workitems", ["source", "target", "link_type"], False),
        ("unlink_workitems", ["source", "target", "link_type"], False),
        ("set_field", ["id", "field", "value"], False),
        ("list_workflow", ["project", "type"], True),
    ],
)
async def test_new_tool_is_registered_with_the_agreed_required_parameters(name, params, read_only):
    """FAILS today: the tool is not registered. Every parameter is required; writes are not read-only."""
    tools = await _registered()
    assert name in tools, f"tool not registered: {name}"
    schema = tools[name].input_schema or {}
    # W4: ``refresh`` is an optional discovery-cache switch (never required)
    assert [p for p in schema.get("properties", {}) if p != "refresh"] == params
    assert set(schema.get("required", [])) == set(params)
    hint = tools[name].annotations.read_only_hint if tools[name].annotations else None
    assert bool(hint) is read_only


async def test_list_workitems_gains_the_optional_filters_and_limit():
    """FAILS today: list_workitems only has project and query. project stays required, the rest optional."""
    tool = (await _registered())["list_workitems"]
    schema = tool.input_schema or {}
    names = list(schema["properties"])
    assert names[:2] == ["project", "query"]
    assert set(names[2:]) == {"type", "state", "owner", "planned_for", "limit", "include_hidden"}
    assert set(schema.get("required", [])) == {"project"}
    assert tool.annotations.read_only_hint is True


# ======================================================================
# add_comment: OSLC CM 1.0 POST, confirmed through list_comments
# ======================================================================
async def test_add_comment_posts_cm10_without_core_version_and_confirms(w2_router):
    """EARS-1: POST {dc:description} as CM 1.0 (no OSLC-Core-Version: 2.0 answers 405); result carries the new id."""
    mock_login(w2_router)
    thread = FakeComments(w2_router, "100")
    result = await server.add_comment("100", "Second comment")
    assert len(thread.posts) == 1
    post = thread.posts[0]
    assert post.url.path == "/ccm/oslc/workitems/100/rtc_cm:comments"
    assert post.headers["content-type"].split(";")[0].strip() == "application/x-oslc-cm-change-request+json"
    assert "oslc-core-version" not in post.headers
    assert json.loads(post.content) == {"dc:description": "Second comment"}
    assert result["id"] == "2"
    assert result["text"] == "Second comment"


async def test_add_comment_raises_without_repeating_the_post_when_the_comment_does_not_appear(w2_router):
    """EARS-1: server answers 201 but the thread never shows the comment -> ToolError, exactly ONE POST."""
    mock_login(w2_router)
    thread = FakeComments(w2_router, "100", persist=False)
    with pytest.raises(ToolError):
        await server.add_comment("100", "Lost comment")
    assert len(thread.posts) == 1


async def test_add_comment_is_not_fooled_by_an_older_comment_with_the_same_text(w2_router):
    """Adversarial: an OLD comment with the same text must not count as confirmation of the new POST."""
    mock_login(w2_router)
    thread = FakeComments(w2_router, "100", existing=(("1", "Same text"),), persist=False)
    with pytest.raises(ToolError):
        await server.add_comment("100", "Same text")
    assert len(thread.posts) == 1


async def test_add_comment_with_text_equal_to_an_existing_one_returns_the_new_id(w2_router):
    """Counterpart: same text, but the server stored it -> the NEW comment (id 2) is the one reported."""
    mock_login(w2_router)
    FakeComments(w2_router, "100", existing=(("1", "Same text"),))
    result = await server.add_comment("100", "Same text")
    assert result["id"] == "2"


async def test_add_comment_405_names_the_gate_and_does_not_retry(w2_router):
    """The 2.0 endpoint answers 405: surfaced through the gate diagnosis, one POST only."""
    mock_login(w2_router)
    thread = FakeComments(w2_router, "100", post_response=httpx.Response(405))
    with pytest.raises(ToolError, match=r"\[http-405\]"):
        await server.add_comment("100", "x")
    assert len(thread.posts) == 1


async def test_add_comment_403_with_server_message_is_a_process_rule_and_is_not_resent(w2_router):
    """403 with oslc:message is a business rule: gate [regra-de-processo], message verbatim, one POST."""
    mock_login(w2_router)
    refusal = httpx.Response(403, json={"oslc:message": "Comments are closed for this item"})
    thread = FakeComments(w2_router, "100", post_response=refusal)
    with pytest.raises(ToolError, match=r"\[regra-de-processo\].*Comments are closed for this item"):
        await server.add_comment("100", "x")
    assert len(thread.posts) == 1


# ======================================================================
# list_comments: oslc:discussedBy -> oslc:comment
# ======================================================================
@pytest.mark.parametrize("mode", ["inline", "refs"])
async def test_list_comments_returns_id_author_date_and_text(w2_router, mode):
    """EARS: oslc:comment members inline or as references -> [{id, author, created, text}] in thread order."""
    mock_login(w2_router)
    FakeComments(w2_router, "100", existing=(("1", "First comment"), ("2", "Second comment")), mode=mode)
    result = await server.list_comments("100")
    assert [c["id"] for c in result] == ["1", "2"]
    assert [c["text"] for c in result] == ["First comment", "Second comment"]
    assert all("jdoe" in c["author"] for c in result)
    assert all(c["created"].startswith("2026-01-01") for c in result)


async def test_list_comments_empty_thread_is_an_empty_list(w2_router):
    mock_login(w2_router)
    FakeComments(w2_router, "100", existing=())
    assert await server.list_comments("100") == []


async def test_list_comments_item_without_discussion_is_an_empty_list(w2_router):
    """A work item that never had a comment may carry no oslc:discussedBy: not an error."""
    mock_login(w2_router)
    FakeItem(w2_router, "100")
    assert await server.list_comments("100") == []


async def test_list_comments_unknown_item_names_the_gate(w2_router):
    mock_login(w2_router)
    FakeItem(w2_router, "100", get_response=httpx.Response(404))
    with pytest.raises(ToolError, match=r"\[recurso-inexistente-ou-invisivel\]"):
        await server.list_comments("100")


# ======================================================================
# link_workitems / unlink_workitems
# ======================================================================
def _link(uri_id: str, title: str = "Other") -> dict:
    return {"rdf:resource": item_uri(uri_id), "dcterms:title": f"{uri_id}: {title}"}


def _targets(body: dict, prop: str) -> set[str]:
    return {entry["rdf:resource"] for entry in body[prop]}


def _two_items(router, source_data: dict | None = None, **source_kwargs) -> tuple[FakeItem, FakeItem]:
    mock_login(router)
    return FakeItem(router, "100", data=source_data, **source_kwargs), FakeItem(router, "200")


@pytest.mark.parametrize("link_type", sorted(LINK_PROPS))
async def test_link_workitems_puts_only_the_link_property_with_if_match(w2_router, link_type):
    """EARS-2: partial PUT of rtc_cm:...linktype.<tipo> on the SOURCE, conditioned on its ETag."""
    source, _ = _two_items(w2_router)
    result = await server.link_workitems("100", "200", link_type)
    prop = LINK_PROPS[link_type]
    assert len(source.puts) == 1
    assert source.put_properties() == prop
    assert source.puts[0].headers["if-match"] == '"etag1"'
    body = source.put_body()
    assert set(body) - {"prefixes"} == {prop}
    assert _targets(body, prop) == {item_uri("200")}
    assert result["link_type"] == link_type
    assert result["source"] == "100"
    assert result["target"] == "200"


async def test_link_workitems_preserves_the_existing_links_of_that_type(w2_router):
    """EARS-2: linking 200 must resend the links already there (300, 400), not replace them."""
    source, _ = _two_items(w2_router, {RELATED: [_link("300"), _link("400")]})
    await server.link_workitems("100", "200", "related")
    assert _targets(source.put_body(), RELATED) == {item_uri("200"), item_uri("300"), item_uri("400")}
    assert {e["rdf:resource"] for e in source.data[RELATED]} == {item_uri(i) for i in ("200", "300", "400")}


async def test_link_workitems_does_not_touch_other_link_types(w2_router):
    """The PUT names one property: a 'blocks' link already on the source stays out of the body."""
    blocks = LINK_PROPS["blocks"]
    source, _ = _two_items(w2_router, {blocks: [_link("300")]})
    await server.link_workitems("100", "200", "related")
    assert blocks not in source.put_body()
    assert source.put_properties() == RELATED
    assert _targets({blocks: source.data[blocks]}, blocks) == {item_uri("300")}


async def test_link_workitems_raises_when_the_reread_does_not_show_the_link(w2_router):
    """EARS-2: the server accepts the PUT (200) but ignores it -> ToolError, no second PUT."""
    source, _ = _two_items(w2_router, apply_put=False)
    with pytest.raises(ToolError):
        await server.link_workitems("100", "200", "related")
    assert len(source.puts) == 1


async def test_link_workitems_unknown_type_lists_the_valid_types_before_any_read(w2_router):
    """Validated before touching the server: the work item routes do not even exist in this test."""
    mock_login(w2_router)
    with pytest.raises(ToolError) as exc:
        await server.link_workitems("100", "200", "friends")
    for name in LINK_PROPS:
        assert name in str(exc.value)


async def test_link_workitems_precondition_failed_names_the_conflict_gate(w2_router):
    source, _ = _two_items(w2_router, put_response=httpx.Response(412))
    with pytest.raises(ToolError, match=r"\[conflito\]"):
        await server.link_workitems("100", "200", "related")
    assert len(source.puts) == 1


async def test_link_workitems_without_etag_refuses_to_write(w2_router):
    source, _ = _two_items(w2_router, etag=None)
    with pytest.raises(ToolError, match="ETag"):
        await server.link_workitems("100", "200", "related")
    assert source.puts == []


async def test_link_workitems_unknown_source_names_the_gate(w2_router):
    source, _ = _two_items(w2_router, get_response=httpx.Response(404))
    with pytest.raises(ToolError, match=r"\[recurso-inexistente-ou-invisivel\]"):
        await server.link_workitems("100", "200", "related")
    assert source.puts == []


async def test_unlink_workitems_removes_only_the_target_and_keeps_the_others(w2_router):
    """EARS-2: unlink 200 from [200, 300] -> PUT resends [300]; the re-read confirms 200 is gone."""
    source, _ = _two_items(w2_router, {RELATED: [_link("200"), _link("300")]})
    result = await server.unlink_workitems("100", "200", "related")
    assert source.put_properties() == RELATED
    assert source.puts[0].headers["if-match"] == '"etag1"'
    assert _targets(source.put_body(), RELATED) == {item_uri("300")}
    assert {e["rdf:resource"] for e in source.data[RELATED]} == {item_uri("300")}
    assert result["link_type"] == "related"


async def test_unlink_workitems_last_link_sends_an_empty_list(w2_router):
    """Removing the only link must still PUT the property, with an empty list (not omit it)."""
    source, _ = _two_items(w2_router, {RELATED: [_link("200")]})
    await server.unlink_workitems("100", "200", "related")
    assert source.put_body()[RELATED] == []


async def test_unlink_workitems_leaves_other_link_types_alone(w2_router):
    blocks = LINK_PROPS["blocks"]
    source, _ = _two_items(w2_router, {RELATED: [_link("200")], blocks: [_link("200")]})
    await server.unlink_workitems("100", "200", "related")
    assert set(source.put_body()) - {"prefixes"} == {RELATED}
    assert _targets({blocks: source.data[blocks]}, blocks) == {item_uri("200")}


async def test_unlink_workitems_target_not_linked_is_an_error_without_a_write(w2_router):
    source, _ = _two_items(w2_router, {RELATED: [_link("300")]})
    with pytest.raises(ToolError, match="200"):
        await server.unlink_workitems("100", "200", "related")
    assert source.puts == []


async def test_unlink_workitems_raises_when_the_reread_still_shows_the_link(w2_router):
    source, _ = _two_items(w2_router, {RELATED: [_link("200")]}, apply_put=False)
    with pytest.raises(ToolError):
        await server.unlink_workitems("100", "200", "related")
    assert len(source.puts) == 1


async def test_unlink_workitems_unknown_type_lists_the_valid_types(w2_router):
    mock_login(w2_router)
    with pytest.raises(ToolError) as exc:
        await server.unlink_workitems("100", "200", "friends")
    for name in LINK_PROPS:
        assert name in str(exc.value)


# ======================================================================
# set_field: enum literal by title or id, validated against allowedValues
# ======================================================================
POINTS = "rtc_cm:storyPoints"
SEVERITY = "rtc_cm:severity"


def _field_item(router, **kwargs) -> FakeItem:
    mock_login(router)
    mock_enum_shape(router)
    return FakeItem(router, "100", **kwargs)


async def test_set_field_resolves_an_enum_title_to_the_literal_uri(w2_router):
    """EARS-3: '5 pts' -> the literal URI of the project enumeration, sent as a partial PUT with If-Match."""
    item = _field_item(w2_router)
    result = await server.set_field("100", POINTS, "5 pts")
    assert isinstance(result, dict)
    assert len(item.puts) == 1
    assert item.put_properties() == POINTS
    assert item.puts[0].headers["if-match"] == '"etag1"'
    assert item.put_body()[POINTS] == {"rdf:resource": literal_uri(POINTS_ENUM, f"{POINTS_ENUM}.literal.l3")}


async def test_set_field_accepts_the_literal_id(w2_router):
    item = _field_item(w2_router)
    await server.set_field("100", POINTS, f"{POINTS_ENUM}.literal.l4")
    assert item.put_body()[POINTS] == {"rdf:resource": literal_uri(POINTS_ENUM, f"{POINTS_ENUM}.literal.l4")}


async def test_set_field_accepts_the_shape_property_name_and_writes_the_prefixed_key(w2_router):
    """field may be the shape's property name: the PUT key is still the item's prefixed name."""
    item = _field_item(w2_router)
    await server.set_field("100", "storyPoints", "3 pts")
    assert item.put_properties() == POINTS
    assert item.put_body()[POINTS] == {"rdf:resource": literal_uri(POINTS_ENUM, f"{POINTS_ENUM}.literal.l2")}


@pytest.mark.parametrize(
    "value",
    ["13 pts", f"{POINTS_ENUM}.literal.l99", f"{SEVERITY_ENUM}.literal.l2"],
    ids=["unknown-title", "unknown-id", "literal-of-another-enum"],
)
async def test_set_field_refuses_a_value_outside_allowed_values_listing_the_valid_ones(w2_router, value):
    """EARS-3: nothing is written; the error lists the valid titles."""
    item = _field_item(w2_router)
    with pytest.raises(ToolError) as exc:
        await server.set_field("100", POINTS, value)
    for title in ("3 pts", "5 pts", "8 pts"):
        assert title in str(exc.value)
    assert item.puts == []


async def test_set_field_plain_property_is_sent_as_is(w2_router):
    """A property without allowedValues carries the value verbatim."""
    item = _field_item(w2_router)
    await server.set_field("100", "dcterms:title", "New title")
    assert item.put_properties() == "dcterms:title"
    assert item.put_body()["dcterms:title"] == "New title"


async def test_set_field_unassigned_on_a_required_attribute_warns_that_the_server_treats_it_as_empty(w2_router):
    """EARS-4: severity is required; its 'Unassigned' literal is stored, and the caller is warned."""
    item = _field_item(w2_router)
    result = await server.set_field("100", SEVERITY, "Unassigned")
    assert item.put_body()[SEVERITY] == {"rdf:resource": literal_uri(SEVERITY_ENUM, f"{SEVERITY_ENUM}.literal.l1")}
    warnings = result["warnings"]
    assert isinstance(warnings, list)
    text = " ".join(warnings)
    assert re.search("unassigned", text, re.IGNORECASE)
    assert re.search("empty", text, re.IGNORECASE)


async def test_set_field_unassigned_on_an_optional_attribute_does_not_warn(w2_router):
    item = _field_item(w2_router)
    result = await server.set_field("100", POINTS, "Unassigned")
    assert len(item.puts) == 1
    assert not result.get("warnings")


async def test_set_field_regular_value_on_a_required_attribute_does_not_warn(w2_router):
    item = _field_item(w2_router)
    result = await server.set_field("100", SEVERITY, "Critical")
    assert len(item.puts) == 1
    assert not result.get("warnings")


async def test_set_field_without_etag_refuses_to_write(w2_router):
    item = _field_item(w2_router, etag=None)
    with pytest.raises(ToolError, match="ETag"):
        await server.set_field("100", POINTS, "5 pts")
    assert item.puts == []


async def test_set_field_precondition_failed_names_the_conflict_gate(w2_router):
    item = _field_item(w2_router, put_response=httpx.Response(412))
    with pytest.raises(ToolError, match=r"\[conflito\]"):
        await server.set_field("100", POINTS, "5 pts")
    assert len(item.puts) == 1


async def test_set_field_repasses_the_server_message_of_a_refused_write(w2_router):
    refusal = httpx.Response(400, json={"oslc:message": "Attribute Story Points is read-only in this state"})
    _field_item(w2_router, put_response=refusal)
    with pytest.raises(ToolError, match="Attribute Story Points is read-only in this state"):
        await server.set_field("100", POINTS, "5 pts")


async def test_set_field_unknown_item_names_the_gate(w2_router):
    item = _field_item(w2_router, get_response=httpx.Response(404))
    with pytest.raises(ToolError, match=r"\[recurso-inexistente-ou-invisivel\]"):
        await server.set_field("100", POINTS, "5 pts")
    assert item.puts == []


# ======================================================================
# list_workflow: states (title, group) and action ids of a type's workflow
# ======================================================================
def _mock_story_workflow(router) -> None:
    """Project discovery + the Story shape (its state property points at the workflow states) + workflow docs."""
    mock_ccm(router)
    allowed_url = f"{BASE}/ccm/oslc/shapes/workitems/{f.STORY_ID}/property/internalState/allowedValues"
    state_prop = f.prop("state", "State", "Exactly-one", propdef=f"{f.RTC_CM}state", allowed=allowed_url)
    title_prop = f.prop("title", "Summary", "Exactly-one", propdef="http://purl.org/dc/terms/title")
    router.get(f.shape_uri(f.STORY_ID)).mock(
        return_value=httpx.Response(200, text=f.real_shape_xml(f.STORY_ID, [state_prop, title_prop]))
    )
    refs = "".join(
        f'<oslc:allowedValue rdf:resource="{f.STATES_URL}/{sid}"/>'
        for sid in (f"{f.WF}.state.s1", f.STATE_IDLE, f.STATE_RUNNING)
    )
    router.get(allowed_url).mock(
        return_value=httpx.Response(
            200,
            text=(
                '<rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#" '
                'xmlns:oslc="http://open-services.net/ns/core#">'
                f'<rdf:Description rdf:about="{allowed_url}">{refs}</rdf:Description></rdf:RDF>'
            ),
        )
    )
    router.get(f.STATES_URL).mock(return_value=httpx.Response(200, json=f.states_json()))
    router.get(f.ACTIONS_URL).mock(return_value=httpx.Response(200, json=f.actions_json()))


@pytest.mark.parametrize("type_arg", ["story", "Story", f.STORY_ID])
async def test_list_workflow_returns_states_with_title_and_group_and_the_action_ids(w2_router, type_arg):
    """The type may be given by alias, title or id; states keep the server order, actions are full ids."""
    _mock_story_workflow(w2_router)
    result = await server.list_workflow(PROJECT, type_arg)
    assert result["workflow"] == f.WF
    assert result["states"] == [
        {"id": f"{f.WF}.state.s1", "title": "Suspended", "group": "inprogress"},
        {"id": f.STATE_IDLE, "title": "Idea", "group": "open"},
        {"id": f.STATE_RUNNING, "title": "In Progress", "group": "inprogress"},
    ]
    assert result["actions"] == f.ACTION_IDS


async def test_list_workflow_unknown_type_lists_the_available_types(w2_router):
    _mock_story_workflow(w2_router)
    with pytest.raises(ToolError) as exc:
        await server.list_workflow(PROJECT, "epic")
    assert "Story" in str(exc.value)
    assert "Technical Debt" in str(exc.value)


async def test_list_workflow_type_without_a_state_property_is_an_error(w2_router):
    """A shape that does not expose its workflow cannot be guessed: ToolError, not an empty answer."""
    mock_ccm(w2_router)
    w2_router.get(f.shape_uri("task")).mock(
        return_value=httpx.Response(
            200,
            text=f.real_shape_xml(
                "task", [f.prop("title", "Summary", "Exactly-one", propdef=f"{PREFIXES['dcterms']}title")]
            ),
        )
    )
    with pytest.raises(ToolError, match="workflow"):
        await server.list_workflow(PROJECT, "task")


async def test_list_workflow_missing_states_document_falls_back_to_the_state_ids(w2_router):
    _mock_story_workflow(w2_router)
    w2_router.get(f.STATES_URL).mock(return_value=httpx.Response(404))
    result = await server.list_workflow(PROJECT, "story")
    assert [s["id"] for s in result["states"]] == [f"{f.WF}.state.s1", f.STATE_IDLE, f.STATE_RUNNING]


async def test_list_workflow_missing_actions_document_names_the_gate(w2_router):
    _mock_story_workflow(w2_router)
    w2_router.get(f.ACTIONS_URL).mock(return_value=httpx.Response(404))
    with pytest.raises(ToolError, match=r"\[recurso-inexistente-ou-invisivel\]"):
        await server.list_workflow(PROJECT, "story")


# ======================================================================
# list_workitems: filters are applied by the server (oslc.where)
# ======================================================================
ITERATION = f"{BASE}/ccm/resource/itemOid/com.ibm.team.apt.Iteration/_ITER1"
FILTER_PATTERNS = {
    "type": r'rtc_cm:type\s*=\s*"task"',
    "state": r'rtc_cm:state\s*=\s*"com\.example\.workflow\.state\.s3"',
    "owner": r"dcterms:contributor\s*=\s*<[^>]*/jdoe>",
    "planned_for": rf"rtc_cm:plannedFor\s*=\s*<{re.escape(ITERATION)}>",
}
FILTER_VALUES = {"type": "Task", "state": "com.example.workflow.state.s3", "owner": "jdoe", "planned_for": ITERATION}


def _query_setup(router) -> FakePages:
    mock_ccm(router)
    return FakePages(router, [[wi(1)]])


@pytest.mark.parametrize("name", sorted(FILTER_VALUES))
async def test_list_workitems_filter_is_sent_to_the_server_in_oslc_where(w2_router, name):
    """EARS-5: each filter becomes an oslc.where clause on the first request."""
    pages = _query_setup(w2_router)
    await server.list_workitems(PROJECT, **{name: FILTER_VALUES[name]})
    where = pages.where_of_first_request()
    assert where is not None
    assert re.search(FILTER_PATTERNS[name], where), where


async def test_list_workitems_combines_filters_and_the_raw_query_with_and(w2_router):
    pages = _query_setup(w2_router)
    _mock_workflow(w2_router)  # com `type`, o id do estado tem de estar no workflow do tipo
    await server.list_workitems(PROJECT, 'dcterms:title="Login"', **FILTER_VALUES)
    where = pages.where_of_first_request()
    assert 'dcterms:title="Login"' in where
    for pattern in FILTER_PATTERNS.values():
        assert re.search(pattern, where), where
    assert len(re.split(r"\s+and\s+", where)) == 5


@pytest.mark.parametrize("name", sorted(FILTER_VALUES))
async def test_list_workitems_filter_value_with_a_double_quote_is_refused_before_any_query(w2_router, name):
    """A quote would close the oslc.where string literal and inject a clause: refuse, send nothing."""
    pages = _query_setup(w2_router)
    with pytest.raises(ToolError):
        await server.list_workitems(PROJECT, **{name: 'x" or dcterms:title="y'})
    assert pages.requests == []
