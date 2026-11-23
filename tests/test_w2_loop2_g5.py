"""G5 loop 2 (RED) tests for wave W2: adversarial findings of the reviewer, ported without network.

Everything runs on a private respx router (or on monkeypatched oslc functions): an unrouted request
raises, so nothing can reach a real server. Tenant-neutral data only (https://elm.example.com).
"""

import html
import json

import httpx
import pytest
from mcp.server.mcpserver.exceptions import ToolError

from elm_mcp import oslc, server
from elm_mcp.auth import close_client

from . import conftest as suite_conftest
from . import f1_fixtures as f
from .w2_fixtures import (
    BASE,
    DCTERMS,
    PROJECT,
    QBASE,
    FakeComments,
    FakeItem,
    FakePages,
    mock_ccm,
    mock_creation,
    mock_login,
    wi,
)

EVIL = "https://evil.example.org"
XSD = "http://www.w3.org/2001/XMLSchema#"


@pytest.fixture(autouse=True)
async def _cfg(monkeypatch):
    monkeypatch.setattr("elm_mcp.config.settings.elm_url", BASE)
    yield
    await close_client()


class _RunAway(BaseException):
    """Raised by a fake server that is being asked for pages forever (not catchable by ``except Exception``)."""


# ======================================================================
# 1. query_resources: cyclic / endless nextPage must terminate
# ======================================================================
def _page_body(members: list[dict], next_url: str | None) -> dict:
    body: dict = {"oslc:results": members}
    if next_url:
        body["oslc:responseInfo"] = {"oslc:nextPage": {"rdf:resource": next_url}}
    return body


def _counting_endpoint(router, responder, max_calls: int = 150) -> list[httpx.Request]:
    calls: list[httpx.Request] = []

    def side_effect(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        if len(calls) > max_calls:
            raise _RunAway(f"more than {max_calls} requests: the query never terminates")
        return httpx.Response(200, json=responder(request, len(calls)))

    router.get(QBASE).mock(side_effect=side_effect)
    return calls


async def test_a_cyclic_next_page_stops_and_is_reported_as_truncated(w2_router):
    mock_ccm(w2_router)
    calls = _counting_endpoint(w2_router, lambda req, n: _page_body([wi(1), wi(2)], f"{QBASE}?page=2"))
    result = await oslc.query_resources("ccm", PROJECT, None, limit=1000)
    assert [m["dcterms:identifier"] for m in result] == ["1", "2"]
    assert result.truncated is True
    assert len(calls) <= 3


async def test_a_page_without_any_new_item_stops_the_walk(w2_router):
    """Every next URL is new, but the pages repeat the same items: stop at the first page with nothing new."""
    mock_ccm(w2_router)
    calls = _counting_endpoint(w2_router, lambda req, n: _page_body([wi(1), wi(2)], f"{QBASE}?page={n + 1}"))
    result = await oslc.query_resources("ccm", PROJECT, None, limit=1000)
    assert len(result) == 2
    assert result.truncated is True
    assert len(calls) <= 2


async def test_the_number_of_pages_has_a_ceiling(w2_router):
    mock_ccm(w2_router)
    calls = _counting_endpoint(w2_router, lambda req, n: _page_body([wi(n)], f"{QBASE}?page={n + 1}"), max_calls=500)
    result = await oslc.query_resources("ccm", PROJECT, None, limit=1000)
    assert result.truncated is True
    assert len(calls) <= 100


@pytest.mark.parametrize("limit", [0, -1])
async def test_a_non_positive_limit_is_an_error_not_the_default(w2_router, limit):
    mock_ccm(w2_router)
    FakePages(w2_router, [[wi(1)]])
    with pytest.raises(ValueError, match="limit"):
        await oslc.query_resources("ccm", PROJECT, None, limit=limit)


# ======================================================================
# 2. search_requirements / search_testcases / get_plan_items: explicit limit + truncated
# ======================================================================
def _fake_query(monkeypatch, members: list[dict], truncated: bool) -> list[dict]:
    seen: list[dict] = []

    async def fake(domain, project, where=None, select=None, limit=None):
        seen.append({"domain": domain, "limit": limit})
        results = oslc.QueryResults(members)
        results.truncated = truncated
        return results

    monkeypatch.setattr(oslc, "query_resources", fake)
    return seen


@pytest.mark.parametrize(
    ("tool", "domain"), [("search_requirements", "rm"), ("search_testcases", "qm")], ids=["rm", "qm"]
)
async def test_search_tools_use_the_maximum_limit_and_report_truncation(monkeypatch, tool, domain):
    seen = _fake_query(monkeypatch, [{"rdf:about": f"{BASE}/x/1"}], truncated=True)
    result = await getattr(server, tool)("PROJ")
    assert result == {"items": [{"rdf:about": f"{BASE}/x/1"}], "truncated": True}
    assert seen == [{"domain": domain, "limit": oslc.MAX_QUERY_LIMIT}]


async def test_get_plan_items_uses_the_maximum_limit_and_reports_truncation(monkeypatch):
    seen = _fake_query(monkeypatch, [], truncated=True)

    async def iterations(project):
        return [{"title": "Sprint 1", "uri": f"{BASE}/it/1", "start_date": "2026-01-01", "end_date": None}]

    monkeypatch.setattr(server, "list_iterations", iterations)
    result = await server.get_plan_items("PROJ", plan="Sprint 1")
    assert seen == [{"domain": "ccm", "limit": oslc.MAX_QUERY_LIMIT}]
    assert result["truncated"] is True
    assert result["items"] == []


# ======================================================================
# 3. add_comment: escaped text, confirmation by the new id
# ======================================================================
async def test_add_comment_sends_the_text_html_escaped_with_line_breaks(w2_router):
    mock_login(w2_router)
    thread = FakeComments(w2_router, "100")
    text = 'a & b <i>"x"</i>\nsecond line'
    result = await server.add_comment("100", text)
    sent = json.loads(thread.posts[0].content)["dc:description"]
    assert "<i>" not in sent
    assert html.escape('a & b <i>"x"</i>') in sent
    assert "<br/>" in sent
    assert "\n" not in sent
    assert len(thread.posts) == 1
    assert result["id"] == "2"


class _NormalizingComments(FakeComments):
    """A server that stores the comment wrapped in markup, so its text never equals what was sent."""

    def _post(self, request: httpx.Request) -> httpx.Response:
        self.posts.append(request)
        text = json.loads(request.content)["dc:description"]
        cid = str(len(self.comments) + 1)
        self.comments.append((cid, f"<div>{text}</div>"))
        return httpx.Response(201, json={"rdf:about": self.comment_uri(cid)})


async def test_add_comment_is_confirmed_by_the_new_id_even_when_the_server_rewrites_the_text(w2_router):
    mock_login(w2_router)
    thread = _NormalizingComments(w2_router, "100")
    result = await server.add_comment("100", "plain text")
    assert result["id"] == "2"
    assert len(thread.posts) == 1


async def test_add_comment_with_comments_without_id_still_finds_the_new_one(monkeypatch):
    calls = {"n": 0}

    async def fake_list(id):
        calls["n"] += 1
        old = {"id": None, "author": None, "created": None, "text": "old"}
        if calls["n"] == 1:
            return [old]
        return [old, {"id": "9", "author": None, "created": None, "text": "<div>new</div>"}]

    async def fake_fetch(*args, **kwargs):
        return httpx.Response(201, json={"rdf:about": f"{BASE}/ccm/oslc/workitems/7/rtc_cm:comments/9"})

    async def fake_client():
        return httpx.AsyncClient()

    monkeypatch.setattr(oslc, "list_comments", fake_list)
    monkeypatch.setattr(oslc, "fetch", fake_fetch)
    monkeypatch.setattr(oslc, "get_client", fake_client)
    result = await oslc.add_comment("7", "new")
    assert result["id"] == "9"


# ======================================================================
# 4. SSRF: every URL followed from a server response stays on settings.elm_url
# ======================================================================
async def test_next_page_on_another_host_is_refused_and_never_requested(w2_router):
    mock_ccm(w2_router)
    w2_router.get(QBASE).mock(return_value=httpx.Response(200, json=_page_body([wi(1)], f"{EVIL}/steal?page=2")))
    evil = w2_router.get(url__startswith=EVIL).mock(return_value=httpx.Response(200, json=_page_body([wi(2)], None)))
    with pytest.raises(ValueError, match="ELM server"):
        await oslc.query_resources("ccm", PROJECT, None, limit=100)
    assert evil.call_count == 0


async def test_workflow_states_url_on_another_host_is_refused(w2_router):
    mock_ccm(w2_router)
    evil_states = f"{EVIL}/ccm/oslc/workflows/{f.PA}/states/{f.WF}"
    state_prop = f.prop("state", "State", "Exactly-one", propdef=f"{f.RTC_CM}state", allowed=evil_states)
    w2_router.get(f.shape_uri(f.STORY_ID)).mock(
        return_value=httpx.Response(200, text=f.real_shape_xml(f.STORY_ID, [state_prop]))
    )
    evil = w2_router.get(url__startswith=EVIL).mock(return_value=httpx.Response(200, json=f.states_json()))
    w2_router.get(f.ACTIONS_URL).mock(return_value=httpx.Response(200, json=f.actions_json()))
    with pytest.raises(ToolError, match="ELM server"):
        await server.list_workflow(PROJECT, "story")
    assert evil.call_count == 0


async def test_comment_reference_on_another_host_is_refused(w2_router):
    mock_login(w2_router)
    thread = FakeComments(w2_router, "100")
    evil = w2_router.get(url__startswith=EVIL).mock(return_value=httpx.Response(200, json={}))
    w2_router.get(thread.url).mock(
        return_value=httpx.Response(200, json={"oslc:comment": [{"rdf:resource": f"{EVIL}/c/1"}]})
    )
    with pytest.raises(ToolError, match="ELM server"):
        await server.list_comments("100")
    assert evil.call_count == 0


async def test_iteration_reference_on_another_host_is_refused(w2_router):
    mock_ccm(w2_router)
    w2_router.get(f"{BASE}/ccm/oslc/iterations").mock(
        return_value=httpx.Response(200, json={"oslc:results": [{"rdf:resource": f"{EVIL}/it/1"}]})
    )
    evil = w2_router.get(url__startswith=EVIL).mock(return_value=httpx.Response(200, json={}))
    with pytest.raises(ToolError, match="ELM server"):
        await server.list_iterations(PROJECT)
    assert evil.call_count == 0


# ======================================================================
# 5. _resolve_ccm_type: explicit precedence and ambiguity
# ======================================================================
def _factory(type_id: str, title: str) -> dict:
    return {"id": type_id, "title": title, "shape_uri": None, "factory_uri": f"{BASE}/f/{type_id}"}


def test_two_types_with_the_same_title_are_ambiguous_and_listed():
    factories = [_factory("a.risk", "Risk"), _factory("b.hazard", "Risk")]
    with pytest.raises(ValueError, match=r"a\.risk.*b\.hazard|b\.hazard.*a\.risk"):
        oslc._resolve_ccm_type(factories, "risk")


def test_two_types_with_the_same_alias_are_ambiguous_and_listed():
    factories = [_factory("com.one.risk", "Risk One"), _factory("com.two.risk", "Risk Two")]
    with pytest.raises(ValueError, match=r"com\.one\.risk.*com\.two\.risk"):
        oslc._resolve_ccm_type(factories, "risk")


def test_a_custom_title_does_not_steal_an_alias():
    factories = [
        _factory("custom.foo", "Story"),
        _factory("com.ibm.team.apt.workItemType.story", "Historia"),
    ]
    assert oslc._resolve_ccm_type(factories, "story")["id"] == "com.ibm.team.apt.workItemType.story"


def test_the_exact_id_beats_the_alias_and_the_alias_beats_the_title():
    factories = [_factory("x.task", "Other"), _factory("task", "Plain"), _factory("y.job", "task")]
    assert oslc._resolve_ccm_type(factories, "task")["id"] == "task"
    assert oslc._resolve_ccm_type(factories[:1] + factories[2:], "task")["id"] == "x.task"
    assert oslc._resolve_ccm_type(factories[2:], "task")["id"] == "y.job"


# ======================================================================
# 6. set_field: field name validation, read-only, unknown field, typed values
# ======================================================================
def _typed_item(router, props: list[dict]) -> FakeItem:
    mock_login(router)
    router.get(f.shape_uri("task")).mock(return_value=httpx.Response(200, text=f.real_shape_xml("task", props)))
    return FakeItem(router, "100")


@pytest.mark.parametrize(
    "field",
    ["dcterms:title,dcterms:description", "dcterms:title dcterms:x", "dcterms:{x}", "a:b:c", ":title", "dcterms:"],
)
async def test_set_field_refuses_a_malformed_field_name_before_writing(w2_router, field):
    item = _typed_item(w2_router, [f.prop("title", "Summary", propdef=f"{DCTERMS}title")])
    with pytest.raises(ToolError, match="field"):
        await server.set_field("100", field, "x")
    assert item.puts == []


async def test_set_field_refuses_a_read_only_property(w2_router):
    item = _typed_item(w2_router, [f.prop("modified", "Modified", read_only=True, propdef=f"{DCTERMS}modified")])
    with pytest.raises(ToolError, match="read-only"):
        await server.set_field("100", "dcterms:modified", "x")
    assert item.puts == []


async def test_set_field_refuses_a_field_that_is_not_in_the_shape(w2_router):
    item = _typed_item(w2_router, [f.prop("title", "Summary", propdef=f"{DCTERMS}title")])
    with pytest.raises(ToolError, match="list_fields"):
        await server.set_field("100", "rtc_cm:unknownThing", "x")
    assert item.puts == []


@pytest.mark.parametrize(
    ("range_name", "raw", "expected"),
    [
        ("integer", "5", 5),
        ("boolean", "true", True),
        ("boolean", "False", False),
        ("decimal", "1.5", 1.5),
        ("string", "7", "7"),
    ],
)
async def test_set_field_converts_the_value_to_the_range_of_the_property(w2_router, range_name, raw, expected):
    prop = f.prop("custom", "Custom", propdef=f"{f.RTC_CM}custom", range_uri=f"{XSD}{range_name}")
    item = _typed_item(w2_router, [prop])
    await server.set_field("100", "rtc_cm:custom", raw)
    sent = item.put_body()["rtc_cm:custom"]
    assert sent == expected
    assert type(sent) is type(expected)


@pytest.mark.parametrize(("range_name", "raw"), [("integer", "abc"), ("boolean", "maybe"), ("decimal", "x")])
async def test_set_field_refuses_a_value_that_does_not_fit_the_range(w2_router, range_name, raw):
    prop = f.prop("custom", "Custom", propdef=f"{f.RTC_CM}custom", range_uri=f"{XSD}{range_name}")
    item = _typed_item(w2_router, [prop])
    with pytest.raises(ToolError, match="custom"):
        await server.set_field("100", "rtc_cm:custom", raw)
    assert item.puts == []


# ======================================================================
# 7. create_workitem: one discovery, not two
# ======================================================================
async def test_create_workitem_discovers_the_project_and_factories_once(w2_router):
    mock_ccm(w2_router)
    mock_creation(w2_router)
    await server.create_workitem(project=PROJECT, title="T", type="task")
    urls = [str(request.url) for request, _ in w2_router.calls]
    assert sum(u.endswith("/services") for u in urls) == 1, urls
    assert sum(u.endswith("/workitems/catalog") for u in urls) == 1, urls


# ======================================================================
# 8. LOW: filters, self link, documentation of the raw query
# ======================================================================
@pytest.mark.parametrize("value", ["a\nb", "a\tb", "a\x00b", "a\x1bb", "a\u200bb"])
def test_filters_refuse_non_printable_characters(value):
    for name in ("type", "state", "owner"):
        with pytest.raises(ValueError, match="forbidden"):
            oslc.workitem_filters(**{name: value})


async def test_a_work_item_cannot_be_linked_to_itself(w2_router):
    mock_login(w2_router)
    source = FakeItem(w2_router, "100")
    for target in ("100", " 100 "):
        with pytest.raises(ToolError, match="itself"):
            await server.link_workitems("100", target, "related")
    assert source.puts == []


def test_list_workitems_documents_that_the_raw_query_is_combined_only_with_and():
    doc = server.list_workitems.__doc__ or ""
    assert "only" in doc
    assert "`or`" in doc


# ======================================================================
# 9. LOW: the network-block error cannot be swallowed; the temp HOME is cleaned
# ======================================================================
async def test_the_network_block_error_is_not_catchable_by_except_exception():
    async with httpx.AsyncClient() as client:
        try:
            await client.get(f"{BASE}/ccm/rootservices")
        except Exception:  # noqa: BLE001 — this is exactly what the guard must NOT allow
            pytest.fail("the network guard raised an Exception subclass: production code could swallow it")
        except BaseException as exc:  # noqa: BLE001
            assert "real network blocked" in str(exc)
        else:
            pytest.fail("the unmocked request did not raise")


def test_the_temporary_home_is_removed_when_the_session_finishes(monkeypatch, tmp_path):
    fake_home = tmp_path / "elm-mcp-tests-home"
    fake_home.mkdir()
    (fake_home / "marker").write_text("x")
    monkeypatch.setattr(suite_conftest, "_EMPTY_HOME", str(fake_home))
    suite_conftest.pytest_sessionfinish(None, 0)
    assert not fake_home.exists()
