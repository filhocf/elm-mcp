"""Loop 2 of G5 (wave W4): corrections of the idempotency, required-field and profile code.

Items: 1 resend after 400/409 re-runs the duplicate check; 2 the duplicate query asks for 1000 items and fails
closed when truncated; 3 the server compares the title EXACTLY (the normalization only applies to the answer);
4 ``folder=None`` of create_requirement matches any folder; 5 required fields are compared after normalizing the
keys of custom_fields; 6 profile/cache hardening. Tenant-neutral data only.
"""

import json
import re
import xml.etree.ElementTree as ET

import httpx
import pytest
import respx
from mcp.server.mcpserver.exceptions import ToolError

from elm_mcp import cache, oslc, profile, server
from elm_mcp.auth import close_client

from . import f1_fixtures as f
from . import w2_fixtures as w2
from . import w4_fixtures as w4
from .w4_fixtures import BASE, PROJECT

TITLE = "Login Flow"
SEV = w4.SEVERITY_URI
CAT_A = f"{f.CAT_BASE}/_CAT_A"


@pytest.fixture(autouse=True)
async def _cfg(monkeypatch):
    monkeypatch.setattr("elm_mcp.config.settings.elm_url", BASE)
    monkeypatch.delenv("ELM_MCP_PROFILE_DIR", raising=False)
    monkeypatch.delenv("ELM_CACHE_TTL", raising=False)
    yield
    await close_client()


def _res(uri: str) -> dict:
    return {"rdf:resource": uri}


# ======================================================================
# 1. HIGH: a 400/409 after which the server created the item must not be resent
# ======================================================================
def _create_then_answer(query: w4.FakeCcmQuery, status: int):
    calls = {"n": 0}

    def side_effect(request):
        calls["n"] += 1
        if calls["n"] == 1:  # the server created it and then answered with the error
            query.add(w4.ccm_row(77, TITLE))
            return httpx.Response(status, json={"oslc:message": "conflict"})
        return httpx.Response(201, json=w2.CREATED)

    return side_effect


@pytest.mark.parametrize("status", [400, 409])
@pytest.mark.parametrize("if_exists", ["error", "return"])
async def test_a_refusal_after_the_server_created_the_item_is_not_resent(w2_router, status, if_exists):
    posts = w4.mock_create_env(w2_router, props=f.default_task_props())
    query = w4.FakeCcmQuery(w2_router, [])
    posts["task"].side_effect = _create_then_answer(query, status)
    result = await server.create_workitem(project=PROJECT, title=TITLE, type="task", if_exists=if_exists)
    assert posts["task"].call_count == 1, "the POST was not resent"
    assert result["created"] is True
    assert result["dcterms:identifier"] == "77"
    assert "NOT resent" in result["warning"]


async def test_a_refusal_with_nothing_new_is_still_resent_once_after_checking_again(w2_router):
    posts = w4.mock_create_env(w2_router, props=f.default_task_props())
    query = w4.FakeCcmQuery(w2_router, [])
    posts["task"].side_effect = [
        httpx.Response(409, json={"oslc:message": "stale"}),
        httpx.Response(201, json=w2.CREATED),
    ]
    result = await server.create_workitem(project=PROJECT, title=TITLE, type="task")
    assert result["created"] is True
    assert posts["task"].call_count == 2
    assert len(query.requests) == 2, "the duplicate check ran again before the resend"


async def test_a_refusal_after_which_the_requery_fails_is_not_resent(w2_router):
    posts = w4.mock_create_env(w2_router, props=f.default_task_props())
    query = w4.FakeCcmQuery(w2_router, [])

    def refuse_then_break(request):
        query.fail = httpx.Response(500, text="boom")
        return httpx.Response(409, json={"oslc:message": "stale"})

    posts["task"].side_effect = refuse_then_break
    with pytest.raises(ToolError, match="(?i)duplicate"):
        await server.create_workitem(project=PROJECT, title=TITLE, type="task")
    assert posts["task"].call_count == 1


async def test_if_exists_create_keeps_resending_after_a_refusal(w2_router):
    posts = w4.mock_create_env(w2_router, props=f.default_task_props())
    w4.FakeCcmQuery(w2_router, [])
    posts["task"].side_effect = [
        httpx.Response(409, json={"oslc:message": "stale"}),
        httpx.Response(201, json=w2.CREATED),
    ]
    result = await server.create_workitem(project=PROJECT, title=TITLE, type="task", if_exists="create")
    assert result["created"] is True
    assert posts["task"].call_count == 2


# ======================================================================
# 2. MED: the duplicate query asks for 1000 items and fails closed when truncated
# ======================================================================
def _others(count: int) -> list[dict]:
    return [w4.ccm_row(i, f"Other {i}") for i in range(1, count + 1)]


async def test_wi_a_duplicate_beyond_the_first_page_is_found(w2_router):
    posts = w4.mock_create_env(w2_router, props=f.default_task_props())
    w4.FakeCcmQuery(w2_router, _others(59) + [w4.ccm_row(99, TITLE)])
    with pytest.raises(ToolError, match="already exists"):
        await server.create_workitem(project=PROJECT, title=TITLE, type="task")
    assert posts["task"].call_count == 0


async def test_wi_a_truncated_duplicate_check_refuses_the_creation(w2_router):
    posts = w4.mock_create_env(w2_router, props=f.default_task_props())
    w4.FakeCcmQuery(w2_router, _others(oslc.MAX_QUERY_LIMIT + 5))
    with pytest.raises(ToolError, match="duplicate check truncated"):
        await server.create_workitem(project=PROJECT, title=TITLE, type="task")
    assert posts["task"].call_count == 0


async def test_wi_a_truncated_duplicate_check_does_not_block_if_exists_create(w2_router):
    posts = w4.mock_create_env(w2_router, props=f.default_task_props())
    w4.FakeCcmQuery(w2_router, _others(oslc.MAX_QUERY_LIMIT + 5))
    result = await server.create_workitem(project=PROJECT, title=TITLE, type="task", if_exists="create")
    assert result["created"] is True
    assert posts["task"].call_count == 1


@respx.mock
async def test_rm_a_duplicate_beyond_the_first_page_is_found():
    w4.mock_rm_config([w4.STREAM_1])
    rows = [w4.rm_req(i, f"Other {i}", folder=w4.FOLDER_A) for i in range(1, 60)]
    w4.FakeRmQuery(rows + [w4.rm_req(99, TITLE, folder=w4.FOLDER_A)], page_size=50)
    post = w4.mock_rm_write()
    with pytest.raises(ToolError, match="already exists"):
        await server.create_requirement(
            project=PROJECT, title=TITLE, content="<p>x</p>", folder=w4.FOLDER_A, type="Requirement"
        )
    assert post.call_count == 0


@respx.mock
async def test_rm_a_truncated_duplicate_check_refuses_the_creation():
    w4.mock_rm_config([w4.STREAM_1])
    rows = [w4.rm_req(i, f"Other {i}", folder=w4.FOLDER_A) for i in range(1, oslc.MAX_QUERY_LIMIT + 6)]
    w4.FakeRmQuery(rows, page_size=500)
    post = w4.mock_rm_write()
    with pytest.raises(ToolError, match="duplicate check truncated"):
        await server.create_requirement(
            project=PROJECT, title=TITLE, content="<p>x</p>", folder=w4.FOLDER_A, type="Requirement"
        )
    assert post.call_count == 0


# ======================================================================
# 3. MED: the server compares the title exactly; the normalization only applies to what comes back
# ======================================================================
class ExactWhereCcm(w4.FakeCcmQuery):
    """A CCM query that applies ``oslc.where`` as the server does: exact title (case and inner spaces count)."""

    def _serve(self, request: httpx.Request) -> httpx.Response:
        where = request.url.params.get("oslc.where", "")
        match = re.fullmatch(r'dcterms:title="((?:[^"\\]|\\.)*)"', where)
        assert match, f"unexpected where: {where!r}"
        wanted = re.sub(r"\\(.)", r"\1", match.group(1))
        everything = self.rows
        self.rows = [row for row in everything if row["title"] == wanted]
        try:
            return super()._serve(request)
        finally:
            self.rows = everything


async def test_where_exact_server_edge_spaces_of_the_request_are_dropped_and_the_duplicate_is_found(w2_router):
    posts = w4.mock_create_env(w2_router, props=f.default_task_props())
    ExactWhereCcm(w2_router, [w4.ccm_row(5, TITLE)])
    with pytest.raises(ToolError, match="already exists"):
        await server.create_workitem(project=PROJECT, title=f"  {TITLE}  ", type="task")
    assert posts["task"].call_count == 0


@pytest.mark.parametrize("existing", ["login flow", "Login   Flow", f" {TITLE}"])
async def test_where_exact_server_a_title_differing_in_case_or_inner_spaces_is_not_found_by_the_query(
    w2_router, existing
):
    """Documented limit: the query is exact, so such a title never comes back and creation goes ahead."""
    posts = w4.mock_create_env(w2_router, props=f.default_task_props())
    ExactWhereCcm(w2_router, [w4.ccm_row(5, existing)])
    result = await server.create_workitem(project=PROJECT, title=TITLE, type="task")
    assert result["created"] is True
    assert posts["task"].call_count == 1


def test_the_docs_do_not_promise_a_case_or_space_insensitive_query():
    from pathlib import Path

    root = Path(__file__).resolve().parent.parent
    for doc in (root / "docs" / "TOOLS.md", root / "CHANGELOG.md"):
        if not doc.exists():
            continue
        text = doc.read_text(encoding="utf-8")
        assert "spaces and case ignored" not in text, doc
    for fn in (server.create_workitem, server.create_requirement):
        assert "spaces and case ignored" not in (fn.__doc__ or ""), fn
    assert "exact" in (server.create_workitem.__doc__ or "").lower()


# ======================================================================
# 4. MED: folder=None of create_requirement compares any folder (accepted false positive)
# ======================================================================
async def _create_req_without_folder(**kwargs) -> dict:
    return await server.create_requirement(
        project=PROJECT, title=TITLE, content="<p>x</p>", type="Requirement", **kwargs
    )


@respx.mock
async def test_rm_without_folder_a_requirement_of_another_folder_counts_as_duplicate():
    w4.mock_rm_config([w4.STREAM_1])
    w4.FakeRmQuery([w4.rm_req(1, TITLE, folder=w4.FOLDER_A)], page_size=50)
    post = w4.mock_rm_write()
    with pytest.raises(ToolError, match="already exists"):
        await _create_req_without_folder()
    assert post.call_count == 0


@respx.mock
@pytest.mark.parametrize("if_exists", ["return", "create"])
async def test_rm_without_folder_if_exists_is_the_way_out(if_exists):
    w4.mock_rm_config([w4.STREAM_1])
    w4.FakeRmQuery([w4.rm_req(1, TITLE, folder=w4.FOLDER_A)], page_size=50)
    post = w4.mock_rm_write()
    result = await _create_req_without_folder(if_exists=if_exists)
    assert result["created"] is (if_exists == "create")
    assert post.call_count == (1 if if_exists == "create" else 0)


@respx.mock
async def test_rm_with_a_folder_only_that_folder_counts():
    w4.mock_rm_config([w4.STREAM_1])
    w4.FakeRmQuery([w4.rm_req(1, TITLE, folder=w4.FOLDER_A)], page_size=50)
    post = w4.mock_rm_write()
    result = await _create_req_without_folder(folder="https://example.invalid/rm/folders/other")
    assert result["created"] is True
    assert post.call_count == 1


def test_the_folder_false_positive_is_documented():
    doc = oslc.create_requirement.__doc__ or ""
    assert "folder" in doc and "any folder" in doc


# ======================================================================
# 5. MED: required fields compared after normalizing the keys of custom_fields
# ======================================================================
def _env(router, props=None):
    posts = w4.mock_create_env(router, props=props)
    w4.FakeCcmQuery(router, [])
    return posts


async def test_custom_fields_with_a_full_property_uri_are_not_a_false_positive(w2_router):
    posts = _env(w2_router)
    result = await server.create_workitem(
        project=PROJECT,
        title="T",
        type="task",
        filed_against=CAT_A,
        custom_fields={f"{f.RTC_CM}severity": _res(SEV)},
    )
    assert result["created"] is True
    body = w4.posted(posts["task"])
    ET.fromstring(body)  # well-formed: the URI key became the canonical prefixed one
    assert "rtc_cm:severity" in body


async def test_custom_fields_in_clark_or_angle_notation_are_normalized_too(w2_router):
    posts = _env(w2_router)
    await server.create_workitem(
        project=PROJECT,
        title="T",
        type="task",
        filed_against=CAT_A,
        custom_fields={f"<{f.RTC_CM}severity>": _res(SEV)},
    )
    ET.fromstring(w4.posted(posts["task"]))


async def test_an_absent_required_field_is_still_refused_after_normalization(w2_router):
    posts = _env(w2_router)
    with pytest.raises(ToolError, match="severity"):
        await server.create_workitem(
            project=PROJECT, title="T", type="task", filed_against=CAT_A, custom_fields={f"{f.RTC_CM}other": "x"}
        )
    assert w4.post_total(posts) == 0


async def test_a_required_field_with_a_default_value_or_read_only_is_not_asked(w2_router):
    props = [
        f.prop("severity", "Severity", "Exactly-one", propdef=f"{f.RTC_CM}severity", default=SEV),
        f.prop("rank", "Rank", "Exactly-one", read_only=True, propdef=f"{f.RTC_CM}rank"),
    ]
    posts = _env(w2_router, props)
    result = await server.create_workitem(project=PROJECT, title="T", type="task")
    assert result["created"] is True
    assert w4.post_total(posts) == 1


async def test_the_servers_own_refusal_message_is_kept(w2_router):
    posts = _env(w2_router)
    posts["task"].side_effect = lambda request: httpx.Response(403, json={"oslc:message": "Severity not allowed here"})
    with pytest.raises(ToolError, match="Severity not allowed here"):
        await server.create_workitem(
            project=PROJECT,
            title="T",
            type="task",
            filed_against=CAT_A,
            custom_fields={f"{f.RTC_CM}severity": _res(SEV)},
        )


# ======================================================================
# 6. LOW: profile, cache and draft hardening
# ======================================================================
def test_a_profile_file_over_one_megabyte_is_refused(tmp_path, monkeypatch):
    monkeypatch.setenv("ELM_MCP_PROFILE_DIR", str(tmp_path))
    path = tmp_path / "project-areas" / f"{w4.PA}.json"
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({"padding": "x" * (1024 * 1024 + 1)}), encoding="utf-8")
    with pytest.raises(ValueError, match="(?i)too large"):
        profile.load(w4.PA)


def _malformed_profile(tmp_path, monkeypatch):
    monkeypatch.setenv("ELM_MCP_PROFILE_DIR", str(tmp_path))
    path = tmp_path / "project-areas" / f"{w4.PA}.json"
    path.parent.mkdir(parents=True)
    path.write_text("{not json", encoding="utf-8")


async def test_list_workitems_with_a_malformed_profile_degrades_with_a_warning(w2_router, tmp_path, monkeypatch):
    _malformed_profile(tmp_path, monkeypatch)
    w4.mock_create_env(w2_router, props=f.default_task_props())
    w4.FakeCcmQuery(w2_router, [w4.ccm_row(1, "A")])
    result = await server.list_workitems(project=PROJECT)
    assert [item["title"] for item in result["items"]] == ["A"]
    assert "profile" in result["warning"].lower()


@respx.mock
async def test_transition_workitem_with_a_malformed_profile_says_which_file(tmp_path, monkeypatch):
    _malformed_profile(tmp_path, monkeypatch)
    f.mock_workflow()
    with pytest.raises(ToolError, match=r"(?i)profile file .*json"):
        await server.transition_workitem(id=f.WI_ID, action="iniciar")


@respx.mock
async def test_an_alias_does_not_shadow_a_valid_action_id(tmp_path, monkeypatch):
    monkeypatch.setenv("ELM_MCP_PROFILE_DIR", str(tmp_path))
    w4.write_profile(tmp_path, area={"action_aliases": {"startWorking": f.ACTION_PREFIX + "closeStory"}})
    routes = f.mock_workflow()
    await server.transition_workitem(id=f.WI_ID, action="startWorking")
    assert routes["put"].calls[0].request.url.params["_action"] == f.ACTION_PREFIX + "startWorking"


@pytest.mark.parametrize("raw", ["nan", "inf", "-inf", "-5", "-0.1"])
def test_an_unusable_cache_ttl_falls_back_to_the_default(monkeypatch, raw):
    monkeypatch.setenv("ELM_CACHE_TTL", raw)
    assert cache.ttl() == cache.DEFAULT_TTL


def test_a_zero_cache_ttl_still_turns_the_cache_off(monkeypatch):
    monkeypatch.setenv("ELM_CACHE_TTL", "0")
    assert cache.ttl() == 0


async def _draft(router, default_xml_fix) -> dict:
    props = [f.prop("severity", "Severity", "Exactly-one", propdef=f"{f.RTC_CM}severity", default="DEFAULT-TOKEN")]
    w2.mock_ccm(router, f.TYPED_FACTORIES[:1])
    xml = f.real_shape_xml("task", props)
    router.get(f.shape_uri("task")).mock(return_value=httpx.Response(200, text=default_xml_fix(xml)))
    return await oslc.build_profile_draft(PROJECT)


async def test_the_draft_keeps_a_uri_default_as_a_resource(w2_router):
    uri = f"{BASE}/ccm/oslc/enumerations/{f.PA}/severity/sev2"
    draft = await _draft(w2_router, lambda xml: xml.replace("DEFAULT-TOKEN", uri))
    assert draft["defaults"]["task"]["rtc_cm:severity"] == {"rdf:resource": uri}


async def test_the_draft_keeps_a_literal_default_as_a_literal(w2_router):
    literal_default = (
        '<oslc:defaultValue rdf:datatype="http://www.w3.org/2001/XMLSchema#string">Plain text</oslc:defaultValue>'
    )
    draft = await _draft(
        w2_router,
        lambda xml: xml.replace('<oslc:defaultValue rdf:resource="DEFAULT-TOKEN"/>', literal_default),
    )
    assert draft["defaults"]["task"]["rtc_cm:severity"] == "Plain text"
