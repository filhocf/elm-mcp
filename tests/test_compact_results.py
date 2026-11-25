"""RED tests for issue #81 (release 0.6.0, deliberate incompatible change): compact results.

create_workitem / update_workitem / add_child_workitem return only {id, uri, title, type, state, created|updated}
unless ``verbose=True`` (then the whole item, as before). list_comments / add_comment return
{id, author, created, text, html}: ``html`` is the body as the server stores it (escaped), ``text`` the readable one.
"""

import json
import time

import httpx
import pytest

from elm_mcp import server
from elm_mcp.auth import close_client

from . import f1_fixtures as f
from . import w2_fixtures as w2
from . import w4_fixtures as w4
from .test_w1_contract import build_contract
from .w4_fixtures import BASE, PROJECT

TITLE = "Login Flow"
COMPACT_KEYS = {"id", "uri", "title", "type", "state"}


@pytest.fixture(autouse=True)
async def _cfg(monkeypatch):
    monkeypatch.setattr("elm_mcp.config.settings.elm_url", BASE)
    yield
    await close_client()


def fat_item(wid: int = 42, title: str = "New", **overrides) -> dict:
    """A realistic server item: 100+ keys, the id hidden in dcterms:identifier, readable type and state."""
    item = {f"rtc_cm:filler{i}": {"rdf:resource": f"{BASE}/ccm/filler/{i}", "note": "x" * 20} for i in range(120)}
    item.update(
        {
            "rdf:about": w2.item_uri(wid),
            "dcterms:identifier": wid,  # a number on the wire: the compact id is a string
            "dcterms:title": title,
            "dcterms:type": "Tarefa",
            "oslc_cm:status": "Novo",
            "prefixes": dict(w2.PREFIXES),
        }
    )
    item.update(overrides)
    return item


def _env(router, item: dict, rows=None):
    posts = w4.mock_create_env(router, props=f.default_task_props())
    w4.FakeCcmQuery(router, rows or [])
    posts["task"].mock(return_value=httpx.Response(201, json=item))
    return posts


async def _create(**kwargs) -> dict:
    return await server.create_workitem(project=PROJECT, title=TITLE, type="task", **kwargs)


# ======================================================================
# create_workitem
# ======================================================================
async def test_create_returns_only_the_compact_fields(w2_router):
    _env(w2_router, fat_item())
    result = await _create()
    assert result == {
        "id": "42",
        "uri": w2.item_uri(42),
        "title": "New",
        "type": "Tarefa",
        "state": "Novo",
        "created": True,
    }


async def test_create_compact_is_small_for_a_fat_item(w2_router):
    item = fat_item()
    assert len(item) >= 100
    _env(w2_router, item)
    result = await _create()
    assert len(result) <= 8
    assert len(json.dumps(result)) < 1024


async def test_create_without_status_or_type_gives_none_not_keyerror(w2_router):
    item = fat_item()
    del item["oslc_cm:status"], item["dcterms:type"]
    _env(w2_router, item)
    result = await _create()
    assert result["type"] is None
    assert result["state"] is None
    assert result["id"] == "42"


async def test_create_compact_keeps_the_warning(w2_router):
    posts = w4.mock_create_env(w2_router, props=f.default_task_props())
    query = w4.FakeCcmQuery(w2_router, [])
    calls = {"n": 0}

    def answer(request):
        calls["n"] += 1
        if calls["n"] == 1:  # the server created it and then answered 409
            query.add(w4.ccm_row(77, TITLE))
            return httpx.Response(409, json={"oslc:message": "conflict"})
        return httpx.Response(201, json=fat_item())

    posts["task"].side_effect = answer
    result = await _create()
    assert posts["task"].call_count == 1
    assert result["id"] == "77"
    assert result["created"] is True
    assert "NOT resent" in result["warning"]
    assert set(result) <= COMPACT_KEYS | {"created", "warning"}


async def test_create_verbose_returns_the_whole_item_plus_created(w2_router):
    item = fat_item()
    _env(w2_router, item)
    result = await _create(verbose=True)
    assert result == {**item, "created": True}
    assert result["dcterms:identifier"] == 42
    assert result["rdf:about"] == w2.item_uri(42)


async def test_create_if_exists_return_is_compact_with_created_false(w2_router):
    posts = _env(w2_router, fat_item(), rows=[w4.ccm_row(731, TITLE)])
    result = await _create(if_exists="return")
    assert posts["task"].call_count == 0
    assert result["created"] is False
    assert result["id"] == "731"
    assert result["uri"] == w2.item_uri(731)
    assert set(result) == COMPACT_KEYS | {"created"}
    assert "dcterms:identifier" not in result


# ======================================================================
# update_workitem
# ======================================================================
async def test_update_returns_only_the_compact_fields(w2_router):
    w2.mock_login(w2_router)
    w2.FakeItem(w2_router, 42, data=fat_item(42, "Old"))
    result = await server.update_workitem(id="42", title="New Title")
    assert result == {
        "id": "42",
        "uri": w2.item_uri(42),
        "title": "New Title",
        "type": "Tarefa",
        "state": "Novo",
        "updated": True,
    }
    assert len(json.dumps(result)) < 1024


async def test_update_without_status_or_type_gives_none(w2_router):
    data = fat_item(42)
    del data["oslc_cm:status"], data["dcterms:type"]
    w2.mock_login(w2_router)
    w2.FakeItem(w2_router, 42, data=data)
    result = await server.update_workitem(id="42", title="T2")
    assert result["type"] is None
    assert result["state"] is None


async def test_update_verbose_returns_the_whole_item(w2_router):
    w2.mock_login(w2_router)
    fake = w2.FakeItem(w2_router, 42, data=fat_item(42, "Old"))
    result = await server.update_workitem(id="42", title="New Title", verbose=True)
    assert result == fake.data
    assert result["dcterms:title"] == "New Title"
    assert result["dcterms:identifier"] == 42
    assert result["rdf:about"] == w2.item_uri(42)
    assert len(result) >= 100


# ======================================================================
# add_child_workitem
# ======================================================================
async def test_add_child_returns_the_compact_item(w2_router):
    posts = _env(w2_router, fat_item())
    result = await server.add_child_workitem(parent_id="10", title=TITLE, project=PROJECT)
    assert posts["task"].call_count == 1
    assert result == {
        "id": "42",
        "uri": w2.item_uri(42),
        "title": "New",
        "type": "Tarefa",
        "state": "Novo",
        "created": True,
    }


async def test_add_child_verbose_is_passed_to_create(w2_router):
    item = fat_item()
    _env(w2_router, item)
    result = await server.add_child_workitem(parent_id="10", title=TITLE, project=PROJECT, verbose=True)
    assert result == {**item, "created": True}


# ======================================================================
# comments
# ======================================================================
def _thread(router, *comments: tuple[str, str]) -> w2.FakeComments:
    w2.mock_login(router)
    return w2.FakeComments(router, "100", existing=comments)


async def test_list_comments_has_the_five_keys_and_decodes_the_stored_html(w2_router):
    stored = "&lt;b&gt;x&lt;/b&gt; &amp; ç"
    _thread(w2_router, ("1", stored))
    [c] = await server.list_comments("100")
    assert set(c) == {"id", "author", "created", "text", "html"}
    assert c["text"] == "<b>x</b> & ç"
    assert c["html"] == stored
    assert c["id"] == "1"


async def test_list_comments_br_becomes_newline(w2_router):
    _thread(w2_router, ("1", "a<br/>b<br>c<BR />d"))
    [c] = await server.list_comments("100")
    assert c["text"] == "a\nb\nc\nd"
    assert c["html"] == "a<br/>b<br>c<BR />d"


async def test_list_comments_rich_html_from_the_ui_is_stripped(w2_router):
    stored = '<div><b>Olá</b> <a href="http://x/?a=1&amp;b=2">link</a> &nbsp;&lt;ok&gt;<br/><i>fim</i></div>'
    _thread(w2_router, ("1", stored))
    [c] = await server.list_comments("100")
    assert "<" not in c["text"].replace("<ok>", "")
    assert c["text"].startswith("Olá link")
    assert "<ok>" in c["text"]
    assert c["text"].endswith("\nfim")
    assert c["html"] == stored


async def test_list_comments_text_is_stripped(w2_router):
    _thread(w2_router, ("1", "  <p> oi </p><br/> "))
    [c] = await server.list_comments("100")
    assert c["text"] == "oi"


async def test_list_comments_empty_and_none_text(w2_router):
    fake = _thread(w2_router, ("1", ""), ("2", "x"))
    fake.comments[1] = ("2", None)
    result = await server.list_comments("100")
    assert result[0]["text"] == ""
    assert result[0]["html"] == ""
    assert result[1]["text"] in ("", None)
    assert result[1]["html"] in ("", None)


async def test_list_comments_one_megabyte_of_lt_is_fast(w2_router):
    stored = "<" * 1_000_000
    _thread(w2_router, ("1", stored))
    start = time.perf_counter()
    [c] = await server.list_comments("100")
    assert time.perf_counter() - start < 1.0
    assert c["html"] == stored
    assert isinstance(c["text"], str)


async def test_add_comment_round_trips_what_the_user_sent(w2_router):
    fake = _thread(w2_router)
    sent = "<b>x</b> & ção\nlinha 2"
    result = await server.add_comment("100", sent)
    assert set(result) == {"id", "author", "created", "text", "html"}
    assert result["text"] == sent
    assert result["html"] == "&lt;b&gt;x&lt;/b&gt; &amp; ção<br/>linha 2"
    assert json.loads(fake.posts[0].content) == {"dc:description": result["html"]}


async def test_add_comment_crlf_comes_back_as_newline(w2_router):
    _thread(w2_router)
    result = await server.add_comment("100", "a\r\nb")
    assert result["text"] == "a\nb"
    assert result["html"] == "a<br/>b"


# ======================================================================
# contract: ``verbose`` only on the three work item write tools
# ======================================================================
VERBOSE = {"name": "verbose", "type": "boolean", "required": False, "default": False}
WITH_VERBOSE = {"create_workitem", "update_workitem", "add_child_workitem"}


async def test_verbose_is_a_trailing_optional_boolean_on_the_three_tools_only():
    actual = {t["name"]: t for t in (await build_contract())["tools"]}
    for name in WITH_VERBOSE:
        assert actual[name]["parameters"][-1] == VERBOSE, name
    for name, tool in actual.items():
        if name not in WITH_VERBOSE:
            assert all(p["name"] != "verbose" for p in tool["parameters"]), name


# ======================================================================
# uri is always the canonical item URL (the POST body's rdf:about is the creation factory)
# ======================================================================
FACTORY_URL = f"{BASE}/ccm/oslc/contexts/_PA/workitems/task"


async def test_create_uri_is_the_item_url_when_the_post_body_says_factory(w2_router):
    item = fat_item(677586, **{"rdf:about": FACTORY_URL})
    _env(w2_router, item)
    result = await _create()
    assert result["uri"] == w2.item_uri(677586)
    assert result["id"] == "677586"


async def test_create_verbose_keeps_the_factory_about_as_it_came(w2_router):
    item = fat_item(677586, **{"rdf:about": FACTORY_URL})
    _env(w2_router, item)
    result = await _create(verbose=True)
    assert result == {**item, "created": True}


def test_compact_uri_without_id_is_none():
    from elm_mcp import oslc

    assert oslc.compact_workitem({"rdf:about": FACTORY_URL}, "created")["uri"] is None


def test_compact_uri_keeps_an_item_url():
    from elm_mcp import oslc

    item = {"rdf:about": w2.item_uri(5), "dcterms:identifier": 5}
    assert oslc.compact_workitem(item, "created")["uri"] == w2.item_uri(5)


# ======================================================================
# duplicate paths: type/state filled by ONE tolerant read of the item
# ======================================================================
def _dup_env(router, *, read=None):
    posts = _env(router, fat_item(), rows=[w4.ccm_row(731, TITLE)])
    route = router.get(w2.item_uri(731)).mock(
        return_value=read if read is not None else httpx.Response(200, json=fat_item(731, TITLE))
    )
    return posts, route


async def test_if_exists_return_fills_state_with_one_read(w2_router):
    posts, route = _dup_env(w2_router)
    result = await _create(if_exists="return")
    assert posts["task"].call_count == 0
    assert route.call_count == 1
    assert result["created"] is False
    assert result["state"] == "Novo"
    assert result["type"] is not None
    assert result["uri"] == w2.item_uri(731)


async def test_if_exists_return_read_failure_gives_none_and_returns(w2_router):
    _, route = _dup_env(w2_router, read=httpx.Response(500, text="boom"))
    result = await _create(if_exists="return")
    assert result["created"] is False
    assert result["id"] == "731"
    assert result["state"] is None
    assert route.call_count >= 1


async def test_if_exists_return_read_timeout_gives_none_and_returns(w2_router):
    _, route = _dup_env(w2_router)
    route.mock(side_effect=httpx.ReadTimeout("slow"))
    result = await _create(if_exists="return")
    assert result["created"] is False
    assert result["state"] is None


async def test_if_exists_return_verbose_does_not_read(w2_router):
    _, route = _dup_env(w2_router)
    result = await _create(if_exists="return", verbose=True)
    assert route.call_count == 0
    assert result["created"] is False
    assert "dcterms:type" in result


async def test_warning_path_fills_state_with_one_read(w2_router):
    posts = w4.mock_create_env(w2_router, props=f.default_task_props())
    query = w4.FakeCcmQuery(w2_router, [])
    route = w2_router.get(w2.item_uri(77)).mock(return_value=httpx.Response(200, json=fat_item(77, TITLE)))

    def answer(request):
        query.add(w4.ccm_row(77, TITLE))
        return httpx.Response(409, json={"oslc:message": "conflict"})

    posts["task"].side_effect = answer
    result = await _create()
    assert route.call_count == 1
    assert result["state"] == "Novo"
    assert "NOT resent" in result["warning"]


async def test_fresh_create_does_not_read_the_item(w2_router):
    _env(w2_router, fat_item())
    route = w2_router.get(w2.item_uri(42)).mock(return_value=httpx.Response(200, json=fat_item()))
    await _create()
    assert route.call_count == 0


# ======================================================================
# readable_comment: block tags become line breaks
# ======================================================================
@pytest.mark.parametrize(
    ("html", "text"),
    [
        ("<div>a</div><div>b</div>", "a\nb"),
        ("<p>a</p><p>b</p>", "a\nb"),
        ("<ul><li>x</li><li>y</li></ul>", "x\n\ny"),
        ("<table><tr><td>a</td></tr><tr><td>b</td></tr></table>", "a\nb"),
        ("<DIV>a</DIV><H2>t</H2>b", "a\nt\nb"),
        ("a\r\nb\rc", "a\nb\nc"),
        ("<p>a</p><p></p><p></p><p></p><p>b</p>", "a\n\nb"),
        ("a<br/>b", "a\nb"),
    ],
)
def test_readable_comment_block_tags_break_lines(html, text):
    from elm_mcp import oslc

    assert oslc.readable_comment(html) == text


@pytest.mark.parametrize("unit", ["</div>", "</p", "<li", "</h1 ", "<br ", "</"])
def test_readable_comment_is_linear_on_one_megabyte(unit):
    from elm_mcp import oslc

    html = unit * (1_000_000 // len(unit))
    start = time.perf_counter()
    oslc.readable_comment(html)
    assert time.perf_counter() - start < 1.0
