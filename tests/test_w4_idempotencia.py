"""G3 (RED) tests for SPEC-idempotencia (wave W4, release v0.6.0).

``create_requirement`` and ``create_workitem`` look for an existing artifact (same normalized title, same
type, same folder / same parent) BEFORE creating, and take ``if_exists``: ``error`` (default) refuses and
names the existing one, ``return`` hands it back with ``created: false`` without writing, ``create`` creates
anyway. A failed check refuses the creation (except ``create``). The POST is never resent after an
ambiguous failure (timeout): the duplicate is queried again and only then the outcome is decided.

The query fakes (tests/w4_fixtures.py) return only the properties named in ``oslc.select`` and ignore
``oslc.where``: the implementation must compare the returned title/type/folder/parent itself, and must
select what it compares. Tenant-neutral data only.
"""

import json

import httpx
import pytest
import respx
from mcp.server.mcpserver.exceptions import ToolError

from elm_mcp import server
from elm_mcp.auth import close_client

from . import f1_fixtures as f
from . import w1_fixtures as w1
from . import w2_fixtures as w2
from . import w4_fixtures as w4
from .w4_fixtures import BASE, PROJECT

TITLE = "Login Flow"
NORMALIZATION = [
    ("Login Flow", "  login   FLOW  "),
    ("Login Flow", "LOGIN FLOW"),
    ("Tab\tSeparated  Words", "tab separated words"),
    ("STRASSE", "straße"),
    ("straße", "STRASSE"),
]
NOT_THE_SAME = ["Login Flow 2", "LoginFlow", "Login", "Flow Login"]


@pytest.fixture(autouse=True)
async def _cfg(monkeypatch):
    monkeypatch.setattr("elm_mcp.config.settings.elm_url", BASE)
    yield
    await close_client()


# ======================================================================
# RM: create_requirement
# ======================================================================
def _rm_env(rows: list[dict], *, fail: httpx.Response | None = None):
    w4.mock_rm_config([w4.STREAM_1])
    fake = w4.FakeRmQuery(rows, page_size=50, fail=fail)
    return fake, w4.mock_rm_write()


async def _create_req(title: str = TITLE, **kwargs) -> dict:
    kwargs.setdefault("folder", w4.FOLDER_A)
    kwargs.setdefault("type", "Requirement")
    return await server.create_requirement(project=PROJECT, title=title, content="<p>x</p>", **kwargs)


@respx.mock
async def test_rm_without_duplicate_creates_and_says_so():
    _fake, post = _rm_env([w4.rm_req(1, "Another title", folder=w4.FOLDER_A)])
    result = await _create_req()
    assert result["created"] is True
    assert result["uri"] == w1.CREATED_URI
    assert post.call_count == 1


@respx.mock
async def test_rm_duplicate_check_is_a_query_with_context_and_the_fields_it_compares():
    fake, _post = _rm_env([])
    await _create_req()
    assert fake.requests, "the duplicate query must be sent before the POST"
    request = fake.requests[0]
    assert request.headers.get("Configuration-Context") == w4.STREAM_1
    select = set(request.url.params["oslc.select"].split(","))
    assert {"dcterms:title", "oslc:instanceShape", "nav:parent"} <= select


@respx.mock
async def test_rm_duplicate_is_refused_by_default_naming_the_existing_requirement():
    existing = w4.rm_req(7, TITLE, folder=w4.FOLDER_A)
    _, post = _rm_env([existing])
    with pytest.raises(ToolError) as exc:
        await _create_req()
    assert existing["uri"] in str(exc.value)
    assert existing["identifier"] in str(exc.value)
    assert post.call_count == 0


@respx.mock
async def test_rm_if_exists_error_is_the_same_as_the_default():
    existing = w4.rm_req(7, TITLE, folder=w4.FOLDER_A)
    _, post = _rm_env([existing])
    with pytest.raises(ToolError, match=existing["uri"]):
        await _create_req(if_exists="error")
    assert post.call_count == 0


@respx.mock
async def test_rm_if_exists_return_gives_the_existing_one_without_writing():
    existing = w4.rm_req(7, TITLE, folder=w4.FOLDER_A)
    _, post = _rm_env([existing])
    result = await _create_req(if_exists="return")
    assert result["created"] is False
    assert result["uri"] == existing["uri"]
    assert str(result["identifier"]) == existing["identifier"]
    assert post.call_count == 0


@respx.mock
async def test_rm_if_exists_create_creates_even_with_a_duplicate():
    _, post = _rm_env([w4.rm_req(7, TITLE, folder=w4.FOLDER_A)])
    result = await _create_req(if_exists="create")
    assert result["created"] is True
    assert post.call_count == 1


@respx.mock
async def test_rm_unknown_if_exists_is_refused_before_anything_is_sent():
    _fake, post = _rm_env([])
    with pytest.raises(ToolError, match="if_exists"):
        await _create_req(if_exists="overwrite")
    assert post.call_count == 0


@respx.mock
@pytest.mark.parametrize(("existing_title", "requested"), NORMALIZATION)
async def test_rm_title_comparison_ignores_edge_and_repeated_spaces_and_case(existing_title, requested):
    _, post = _rm_env([w4.rm_req(7, existing_title, folder=w4.FOLDER_A)])
    with pytest.raises(ToolError):
        await _create_req(requested)
    assert post.call_count == 0


@respx.mock
@pytest.mark.parametrize("requested", NOT_THE_SAME)
async def test_rm_a_different_title_is_not_a_duplicate(requested):
    _, post = _rm_env([w4.rm_req(7, TITLE, folder=w4.FOLDER_A)])
    result = await _create_req(requested)
    assert result["created"] is True
    assert post.call_count == 1


@respx.mock
async def test_rm_same_title_in_another_folder_is_not_a_duplicate():
    _, post = _rm_env([w4.rm_req(7, TITLE, folder=w4.FOLDER_B)])
    result = await _create_req(folder=w4.FOLDER_A)
    assert result["created"] is True
    assert post.call_count == 1


@respx.mock
async def test_rm_same_title_of_another_type_is_not_a_duplicate():
    _, post = _rm_env([w4.rm_req(7, TITLE, shape=w1.SHAPE_FEATURE, folder=w4.FOLDER_A)])
    result = await _create_req(type="Requirement")
    assert result["created"] is True
    assert post.call_count == 1


@respx.mock
async def test_rm_failed_duplicate_check_refuses_the_creation():
    _, post = _rm_env([], fail=httpx.Response(500, text="boom"))
    with pytest.raises(ToolError, match="(?i)duplicate"):
        await _create_req()
    assert post.call_count == 0


@respx.mock
async def test_rm_permission_error_in_the_check_refuses_the_creation():
    _, post = _rm_env([], fail=httpx.Response(403, json={"oslc:message": "no access"}))
    with pytest.raises(ToolError, match="(?i)duplicate"):
        await _create_req()
    assert post.call_count == 0


@respx.mock
async def test_rm_failed_duplicate_check_does_not_block_if_exists_create():
    _, post = _rm_env([], fail=httpx.Response(500, text="boom"))
    result = await _create_req(if_exists="create")
    assert result["created"] is True
    assert post.call_count == 1


@respx.mock
async def test_rm_timeout_after_the_post_requeries_instead_of_resending_and_returns_what_exists():
    fake, post = _rm_env([])

    def lost_answer(request: httpx.Request) -> httpx.Response:
        fake.rows.append(w4.rm_req(9, TITLE, folder=w4.FOLDER_A) | {"uri": w1.CREATED_URI})  # the server DID create it
        raise httpx.ReadTimeout("timed out after the request was sent")

    post.side_effect = lost_answer
    result = await _create_req()
    assert post.call_count == 1, "the POST must never be resent after an ambiguous failure"
    assert len(fake.requests) >= 2, "the duplicate must be queried again after the timeout"
    assert result["uri"] == w1.CREATED_URI
    assert isinstance(result["created"], bool)


@respx.mock
async def test_rm_timeout_after_the_post_with_nothing_found_is_an_error_and_is_not_resent():
    fake, post = _rm_env([])
    post.side_effect = httpx.ReadTimeout("timed out after the request was sent")
    with pytest.raises(ToolError):
        await _create_req()
    assert post.call_count == 1
    assert len(fake.requests) >= 2


# ======================================================================
# CCM: create_workitem
# ======================================================================
def _ccm_env(router, rows: list[dict] | None = None, *, fail: httpx.Response | None = None):
    posts = w4.mock_create_env(router, props=f.default_task_props())
    query = w4.FakeCcmQuery(router, rows if rows is not None else [], fail=fail)
    return posts, query


async def _create_wi(title: str = TITLE, **kwargs) -> dict:
    return await server.create_workitem(project=PROJECT, title=title, type="task", **kwargs)


async def test_wi_without_duplicate_creates_and_says_so(w2_router):
    posts, _ = _ccm_env(w2_router, [w4.ccm_row(5, "Another title")])
    result = await _create_wi()
    assert result["created"] is True
    assert result["dcterms:identifier"] == "42"
    assert posts["task"].call_count == 1


async def test_wi_duplicate_check_selects_what_it_compares(w2_router):
    _, query = _ccm_env(w2_router)
    await _create_wi(parent_id="3")
    assert query.requests, "the duplicate query must be sent before the POST"
    select = set(query.requests[0].url.params["oslc.select"].split(","))
    assert "dcterms:title" in select
    assert {"rtc_cm:type", "dcterms:type"} & select
    assert w4.PARENT_LINK in select


async def test_wi_duplicate_is_refused_by_default_naming_the_existing_one(w2_router):
    posts, _ = _ccm_env(w2_router, [w4.ccm_row(731, TITLE)])
    with pytest.raises(ToolError) as exc:
        await _create_wi()
    assert w2.item_uri(731) in str(exc.value)
    assert "731" in str(exc.value)
    assert posts["task"].call_count == 0


async def test_wi_if_exists_return_gives_the_existing_one_without_writing(w2_router):
    posts, _ = _ccm_env(w2_router, [w4.ccm_row(731, TITLE)])
    result = await _create_wi(if_exists="return")
    assert result["created"] is False
    assert w2.item_uri(731) in json.dumps(result)
    assert posts["task"].call_count == 0


async def test_wi_if_exists_create_creates_even_with_a_duplicate(w2_router):
    posts, _ = _ccm_env(w2_router, [w4.ccm_row(731, TITLE)])
    result = await _create_wi(if_exists="create")
    assert result["created"] is True
    assert posts["task"].call_count == 1


async def test_wi_unknown_if_exists_is_refused_before_anything_is_sent(w2_router):
    posts, _query = _ccm_env(w2_router)
    with pytest.raises(ToolError, match="if_exists"):
        await _create_wi(if_exists="overwrite")
    assert posts["task"].call_count == 0


@pytest.mark.parametrize(("existing_title", "requested"), NORMALIZATION)
async def test_wi_title_comparison_ignores_edge_and_repeated_spaces_and_case(w2_router, existing_title, requested):
    posts, _ = _ccm_env(w2_router, [w4.ccm_row(731, existing_title)])
    with pytest.raises(ToolError):
        await _create_wi(requested)
    assert posts["task"].call_count == 0


@pytest.mark.parametrize("requested", NOT_THE_SAME)
async def test_wi_a_different_title_is_not_a_duplicate(w2_router, requested):
    posts, _ = _ccm_env(w2_router, [w4.ccm_row(731, TITLE)])
    result = await _create_wi(requested)
    assert result["created"] is True
    assert posts["task"].call_count == 1


async def test_wi_same_title_of_another_type_is_not_a_duplicate(w2_router):
    posts, _ = _ccm_env(w2_router, [w4.ccm_row(731, TITLE, type_id=f.STORY_ID)])
    result = await _create_wi()
    assert result["created"] is True
    assert posts["task"].call_count == 1


async def test_wi_same_title_and_type_under_the_same_parent_is_a_duplicate(w2_router):
    posts, _ = _ccm_env(w2_router, [w4.ccm_row(731, TITLE, parent=3)])
    with pytest.raises(ToolError, match="731"):
        await _create_wi(parent_id="3")
    assert posts["task"].call_count == 0


async def test_wi_same_title_and_type_under_another_parent_is_not_a_duplicate(w2_router):
    posts, _ = _ccm_env(w2_router, [w4.ccm_row(731, TITLE, parent=4)])
    result = await _create_wi(parent_id="3")
    assert result["created"] is True
    assert posts["task"].call_count == 1


async def test_wi_existing_with_a_parent_is_not_a_duplicate_of_a_parentless_request(w2_router):
    posts, _ = _ccm_env(w2_router, [w4.ccm_row(731, TITLE, parent=3)])
    result = await _create_wi()
    assert result["created"] is True
    assert posts["task"].call_count == 1


async def test_wi_existing_without_parent_is_not_a_duplicate_of_a_request_with_a_parent(w2_router):
    posts, _ = _ccm_env(w2_router, [w4.ccm_row(731, TITLE)])
    result = await _create_wi(parent_id="3")
    assert result["created"] is True
    assert posts["task"].call_count == 1


async def test_wi_add_child_workitem_is_also_checked(w2_router):
    posts, _ = _ccm_env(w2_router, [w4.ccm_row(731, TITLE, parent=3)])
    with pytest.raises(ToolError, match="731"):
        await server.add_child_workitem(parent_id="3", title=TITLE, project=PROJECT)
    assert posts["task"].call_count == 0


@pytest.mark.parametrize(
    "response", [httpx.Response(500, text="boom"), httpx.Response(403, json={"oslc:message": "x"})]
)
async def test_wi_failed_duplicate_check_refuses_the_creation(w2_router, response):
    posts, _ = _ccm_env(w2_router, fail=response)
    with pytest.raises(ToolError, match="(?i)duplicate"):
        await _create_wi()
    assert posts["task"].call_count == 0


async def test_wi_failed_duplicate_check_does_not_block_if_exists_create(w2_router):
    posts, _ = _ccm_env(w2_router, fail=httpx.Response(500, text="boom"))
    result = await _create_wi(if_exists="create")
    assert result["created"] is True
    assert posts["task"].call_count == 1


async def test_wi_timeout_after_the_post_requeries_instead_of_resending_and_returns_what_exists(w2_router):
    posts, query = _ccm_env(w2_router)

    def lost_answer(request: httpx.Request) -> httpx.Response:
        query.add(w4.ccm_row(42, TITLE))  # the server DID create it
        raise httpx.ReadTimeout("timed out after the request was sent")

    posts["task"].side_effect = lost_answer
    result = await _create_wi()
    assert posts["task"].call_count == 1, "the POST must never be resent after an ambiguous failure"
    assert len(query.requests) >= 2, "the duplicate must be queried again after the timeout"
    assert w2.item_uri(42) in json.dumps(result)
    assert isinstance(result["created"], bool)


async def test_wi_timeout_after_the_post_with_nothing_found_is_an_error_and_is_not_resent(w2_router):
    posts, query = _ccm_env(w2_router)
    posts["task"].side_effect = httpx.ReadTimeout("timed out after the request was sent")
    with pytest.raises(ToolError):
        await _create_wi()
    assert posts["task"].call_count == 1
    assert len(query.requests) >= 2


# ======================================================================
# Contract: the new parameter
# ======================================================================
@pytest.mark.parametrize("tool_name", ["create_requirement", "create_workitem"])
async def test_creation_tools_register_if_exists_defaulting_to_error(tool_name):
    tool = next(t for t in await server.mcp.list_tools() if t.name == tool_name)
    schema = tool.input_schema["properties"]["if_exists"]
    assert schema.get("default") == "error"
    assert "if_exists" not in tool.input_schema.get("required", [])
