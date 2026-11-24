"""RED tests (G3) for SPEC-rm-conteudo (release v0.7.0): idempotent root div, ``content_format``, content safety
and the 2-step creation after a 403.

Target behavior (EARS of the SPEC):
  A  exactly ONE ``<div xmlns="http://www.w3.org/1999/xhtml">`` inside jazz_rm:primaryText, also when the content
     already comes with that div (create_requirement and update_requirement); applying it twice changes nothing.
  B  ``content_format`` = html (default) | xhtml (validate and wrap only) | markdown; anything else is a clear
     error naming the valid values. Tools and ``oslc.update_requirement`` accept it.
  D  script/style/iframe/object/embed (with their content) and every character above U+FFFF are removed in the
     html path; malformed XHTML is refused BEFORE any request.
  E  POST refused with 403 AND content larger than ELM_RM_CREATE_FALLBACK_BYTES (default 4096): the same body is
     never POSTed again; the requirement is created with no text and the full content goes by PUT with If-Match,
     confirmed by a re-read: {"uri", "identifier", "created": True, "content_applied": True}. A smaller content
     with 403 is a normal error. If the PUT fails the result says the requirement WAS created (uri) and
     ``content_applied`` is False (a dict with a ``warning``), and nothing is deleted. A timeout after the POST is
     still never resent. With ``if_exists`` the duplicate check runs ONCE.

Tenant-neutral fixtures (tests/w1_fixtures.py, tests/w4_fixtures.py).
"""

import inspect
import re

import httpx
import pytest
import respx
from mcp.server.mcpserver.exceptions import ToolError

from elm_mcp import oslc, server
from elm_mcp.auth import close_client

from . import w1_fixtures as w1
from . import w4_fixtures as w4
from .w1_fixtures import BASE, CREATED_RDF, CREATED_URI, FACTORY_URL, PROJECT, SHAPE_REQ

DIV = '<div xmlns="http://www.w3.org/1999/xhtml">'
PRIMARY_RE = re.compile(r"<jazz_rm:primaryText[^>]*>(.*?)</jazz_rm:primaryText>", re.DOTALL)


@pytest.fixture(autouse=True)
async def _cfg(monkeypatch):
    monkeypatch.setattr("elm_mcp.config.settings.elm_url", BASE)
    monkeypatch.delenv("ELM_RM_CREATE_FALLBACK_BYTES", raising=False)
    yield
    await close_client()


def primary_text(body: bytes | str) -> str:
    text = body.decode("utf-8") if isinstance(body, bytes) else body
    found = PRIMARY_RE.search(text)
    assert found, f"no jazz_rm:primaryText in the body: {text[:300]}"
    return found.group(1)


def _post_ok() -> respx.Route:
    w1.mock_rm_discovery()
    respx.get(CREATED_URI).mock(return_value=httpx.Response(200, text=CREATED_RDF))
    return respx.post(FACTORY_URL).mock(return_value=httpx.Response(201, headers={"Location": CREATED_URI}, text=""))


async def _create(content: str, **kwargs) -> dict:
    kwargs.setdefault("if_exists", "create")
    kwargs.setdefault("type", SHAPE_REQ)
    return await server.create_requirement(project=PROJECT, title="Req", content=content, **kwargs)


EXISTING_RDF = f"""<?xml version="1.0"?>
<rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#"
    xmlns:dcterms="http://purl.org/dc/terms/" xmlns:jazz_rm="http://jazz.net/ns/rm#">
  <rdf:Description rdf:about="{CREATED_URI}">
    <dcterms:identifier>42</dcterms:identifier>
    <dcterms:title>Old</dcterms:title>
    <jazz_rm:primaryText rdf:parseType="Literal">{DIV}<p>old</p></div></jazz_rm:primaryText>
  </rdf:Description>
</rdf:RDF>"""


def _update_routes() -> respx.Route:
    w1.mock_rm_discovery()
    respx.get(CREATED_URI).mock(return_value=httpx.Response(200, text=EXISTING_RDF, headers={"ETag": '"e1"'}))
    return respx.put(CREATED_URI).mock(return_value=httpx.Response(200, text=""))


# ======================================================================
# A — idempotent root div
# ======================================================================
@respx.mock
async def test_a_create_content_already_wrapped_has_exactly_one_div():
    post = _post_ok()
    await _create(f"{DIV}<p>hello</p></div>")
    inner = primary_text(post.calls[0].request.content)
    assert inner.count(DIV) == 1, inner
    assert inner.count("</div>") == 1
    assert "<p>hello</p>" in inner


@respx.mock
async def test_a_create_content_without_div_is_wrapped_once():
    post = _post_ok()
    await _create("<p>hello</p>")
    inner = primary_text(post.calls[0].request.content)
    assert inner.count(DIV) == 1
    assert inner.startswith(DIV) and inner.endswith("</div>")


@respx.mock
async def test_a_create_wrapped_and_unwrapped_content_give_the_same_body():
    post = _post_ok()
    await _create("<p>hello</p>")
    await _create(f"{DIV}<p>hello</p></div>")
    assert primary_text(post.calls[0].request.content) == primary_text(post.calls[1].request.content)


@respx.mock
async def test_a_create_wrapped_with_surrounding_whitespace_has_one_div():
    post = _post_ok()
    await _create(f"\n  {DIV}<p>hello</p></div>\n")
    assert primary_text(post.calls[0].request.content).count(DIV) == 1


@respx.mock
async def test_a_update_content_already_wrapped_has_exactly_one_div():
    put = _update_routes()
    await server.update_requirement(uri=CREATED_URI, content=f"{DIV}<p>new</p></div>")
    inner = primary_text(put.calls[0].request.content)
    assert inner.count(DIV) == 1, inner
    assert "<p>new</p>" in inner


@respx.mock
async def test_a_update_content_without_div_is_wrapped_once():
    put = _update_routes()
    await server.update_requirement(uri=CREATED_URI, content="<p>new</p>")
    assert primary_text(put.calls[0].request.content).count(DIV) == 1


@respx.mock
async def test_a_update_reading_then_writing_back_the_same_content_does_not_nest():
    """The server returns the text already wrapped; sending it back must not stack divs (round trip)."""
    put = _update_routes()
    await server.update_requirement(uri=CREATED_URI, content=f"{DIV}<p>old</p></div>")
    first = primary_text(put.calls[0].request.content)
    await server.update_requirement(uri=CREATED_URI, content=first)
    assert primary_text(put.calls[1].request.content) == first


def test_a_normalization_applied_twice_is_stable_and_unwraps_the_root_div():
    once = oslc.sanitize_html_to_xhtml(f"{DIV}<p>x</p></div>")
    assert oslc.sanitize_html_to_xhtml(once) == once
    assert once == oslc.sanitize_html_to_xhtml("<p>x</p>")
    assert DIV not in once


# ======================================================================
# B — content_format
# ======================================================================
@respx.mock
async def test_b_create_markdown_is_converted_to_xhtml():
    post = _post_ok()
    await _create("# Title\n\nsome **bold** & more", content_format="markdown")
    inner = primary_text(post.calls[0].request.content)
    assert "<h1>Title</h1>" in inner
    assert "<strong>bold</strong>" in inner
    assert "&amp;" in inner
    assert inner.count(DIV) == 1


@respx.mock
async def test_b_create_markdown_raw_html_does_not_reach_the_body():
    post = _post_ok()
    await _create("hi <b onclick=x>there</b>", content_format="markdown")
    body = post.calls[0].request.content.decode()
    assert "<b onclick" not in body
    assert "&lt;b" in body


@respx.mock
async def test_b_create_html_is_the_default():
    post = _post_ok()
    await _create("<p>a<br>b</p>")
    assert "<br/>" in primary_text(post.calls[0].request.content)
    await _create("<p>a<br>b</p>", content_format="html")
    assert primary_text(post.calls[0].request.content) == primary_text(post.calls[1].request.content)


@respx.mock
async def test_b_create_xhtml_is_only_validated_and_wrapped():
    post = _post_ok()
    await _create("<p>a<br/>b</p>", content_format="xhtml")
    inner = primary_text(post.calls[0].request.content)
    assert inner == f"{DIV}<p>a<br/>b</p></div>"


@respx.mock
async def test_b_create_xhtml_that_is_not_well_formed_is_refused_not_repaired():
    post = _post_ok()
    with pytest.raises(ToolError, match="(?i)well-formed"):
        await _create("<p>a<br>b</p>", content_format="xhtml")
    assert post.call_count == 0


@respx.mock
async def test_b_create_unknown_format_is_a_clear_error_before_any_request():
    with pytest.raises(ToolError) as exc:
        await _create("<p>x</p>", content_format="rtf")
    message = str(exc.value)
    assert "content_format" in message
    assert "rtf" in message
    for valid in ("html", "xhtml", "markdown"):
        assert valid in message
    assert respx.calls.call_count == 0


@respx.mock
async def test_b_update_markdown_is_converted():
    put = _update_routes()
    await server.update_requirement(uri=CREATED_URI, content="## Sub\n\n- a\n- b", content_format="markdown")
    inner = primary_text(put.calls[0].request.content)
    assert "<h2>Sub</h2>" in inner
    assert "<li>a</li>" in inner
    assert inner.count(DIV) == 1


@respx.mock
async def test_b_update_xhtml_malformed_is_refused_before_the_put():
    put = _update_routes()
    with pytest.raises(ToolError, match="(?i)well-formed"):
        await server.update_requirement(uri=CREATED_URI, content="<p>open", content_format="xhtml")
    assert put.call_count == 0


@respx.mock
async def test_b_update_unknown_format_is_a_clear_error_before_any_request():
    with pytest.raises(ToolError) as exc:
        await server.update_requirement(uri=CREATED_URI, content="x", content_format="docx")
    for valid in ("html", "xhtml", "markdown"):
        assert valid in str(exc.value)
    assert respx.calls.call_count == 0


@respx.mock
async def test_b_oslc_update_requirement_takes_content_format_next_to_xhtml():
    put = _update_routes()
    await oslc.update_requirement(CREATED_URI, xhtml="# T", content_format="markdown")
    assert "<h1>T</h1>" in primary_text(put.calls[0].request.content)


def test_b_oslc_update_requirement_keeps_the_xhtml_parameter_and_defaults_to_html():
    params = inspect.signature(oslc.update_requirement).parameters
    assert "xhtml" in params
    assert params["content_format"].default == "html"


@pytest.mark.parametrize("tool_name", ["create_requirement", "update_requirement"])
async def test_b_tools_expose_content_format_as_optional_defaulting_to_html(tool_name):
    tool = {t.name: t for t in await server.mcp.list_tools()}[tool_name]
    schema = tool.input_schema["properties"]["content_format"]
    assert schema.get("default") == "html"
    assert "content_format" not in tool.input_schema.get("required", [])


# ======================================================================
# D — content safety
# ======================================================================
@pytest.mark.parametrize(
    "markup",
    [
        "<script>alert(1)</script>",
        "<SCRIPT type='text/javascript'>alert(1)</SCRIPT>",
        "<style>p{color:red}</style>",
        '<iframe src="https://evil.example.com"></iframe>',
        '<iframe src="https://evil.example.com"/>',
        '<object data="x.swf"><param name="a" value="b"/></object>',
        '<embed src="x.swf">',
    ],
)
def test_d_active_content_is_removed_with_its_content(markup):
    out = oslc.sanitize_html_to_xhtml(f"<p>before</p>{markup}<p>after</p>")
    lowered = out.lower()
    for word in ("<script", "<style", "<iframe", "<object", "<embed", "alert(1)", "color:red", "evil.example.com"):
        assert word not in lowered
    assert "<p>before</p>" in out
    assert "<p>after</p>" in out
    oslc._assert_xhtml_wellformed(out)


@pytest.mark.parametrize("char", ["\U0001f680", "\U0001f1e7", "\U0001f9ec", "\U0001fa70", "\U0002f800", "\U000e0041"])
def test_d_every_character_above_ffff_is_removed(char):
    out = oslc.sanitize_html_to_xhtml(f"<p>a{char}b</p>")
    assert out == "<p>ab</p>"


@respx.mock
async def test_d_create_script_and_emoji_never_reach_the_body():
    post = _post_ok()
    await _create("<p>ok \U0001f680</p><script>steal()</script>")
    body = post.calls[0].request.content.decode()
    assert "<script" not in body
    assert "steal" not in body
    assert "\U0001f680" not in body


@respx.mock
async def test_d_update_script_never_reaches_the_body():
    put = _update_routes()
    await server.update_requirement(uri=CREATED_URI, content="<p>ok</p><style>x{}</style><script>steal()</script>")
    body = put.calls[0].request.content.decode()
    assert "steal" not in body
    assert "<style" not in body


@pytest.mark.parametrize("bad", ["<p>unclosed", "<p>a</div></p>", "a </jazz_rm:primaryText><x/> b"])
@respx.mock
async def test_d_create_malformed_content_is_refused_before_any_request(bad):
    with pytest.raises(ToolError, match="(?i)well-formed"):
        await _create(bad)
    assert respx.calls.call_count == 0, "nothing may be sent (not even discovery) for malformed content"


@respx.mock
async def test_d_update_malformed_content_is_refused_before_any_request():
    get = respx.get(CREATED_URI).mock(return_value=httpx.Response(200, text=EXISTING_RDF, headers={"ETag": '"e1"'}))
    put = respx.put(CREATED_URI).mock(return_value=httpx.Response(200))
    with pytest.raises(ToolError, match="(?i)well-formed"):
        await server.update_requirement(uri=CREATED_URI, content="<p>unclosed")
    assert get.call_count == 0
    assert put.call_count == 0


# ======================================================================
# E — 2-step creation after a 403
# ======================================================================
class TwoStep:
    """Factory POST that refuses (403) any body carrying primaryText and accepts one without it; stateful GET/PUT."""

    def __init__(self, *, put_status: int = 200, refuse_always: bool = False) -> None:
        w1.mock_rm_discovery()
        self.events: list[str] = []
        self.posts: list[str] = []
        self.refuse_always = refuse_always
        self.text_applied = False
        self.post = respx.post(FACTORY_URL).mock(side_effect=self._post)
        self.get = respx.get(CREATED_URI).mock(side_effect=self._get)
        self.put = respx.put(CREATED_URI).mock(side_effect=lambda r: self._put(r, put_status))
        self.delete = respx.delete(CREATED_URI).mock(return_value=httpx.Response(204))

    def _post(self, request: httpx.Request) -> httpx.Response:
        body = request.content.decode()
        self.events.append("POST")
        self.posts.append(body)
        if "primaryText" in body or self.refuse_always:
            return httpx.Response(403, json={"oslc:message": "CRRRS0000E content refused"})
        return httpx.Response(201, headers={"Location": CREATED_URI}, text="")

    def _get(self, request: httpx.Request) -> httpx.Response:
        self.events.append("GET")
        text = EXISTING_RDF if self.text_applied else CREATED_RDF
        return httpx.Response(200, text=text, headers={"ETag": '"etag-after-create"'})

    def _put(self, request: httpx.Request, status: int) -> httpx.Response:
        self.events.append("PUT")
        if status < 300:
            self.text_applied = True
        return httpx.Response(status, text="")


BIG = "<p>" + ("lorem ipsum dolor sit amet " * 200) + "</p>"  # ~5.4 KB > 4096
assert len(BIG.encode()) > 4096


@respx.mock
async def test_e_big_content_refused_with_403_is_created_empty_then_applied_by_put():
    env = TwoStep()
    result = await _create(BIG)
    assert result["uri"] == CREATED_URI
    assert result["identifier"] == "42"
    assert result["created"] is True
    assert result["content_applied"] is True
    assert env.put.call_count == 1


@respx.mock
async def test_e_the_refused_body_is_not_resent_the_second_post_has_no_text():
    env = TwoStep()
    await _create(BIG)
    assert env.post.call_count == 2
    assert "primaryText" in env.posts[0]
    assert "lorem ipsum" in env.posts[0]
    assert "primaryText" not in env.posts[1]
    assert "lorem ipsum" not in env.posts[1]
    assert "<dcterms:title>Req</dcterms:title>" in env.posts[1], "title/type/shape must still be sent"
    assert SHAPE_REQ in env.posts[1]


@respx.mock
async def test_e_the_put_carries_the_full_content_with_if_match_from_a_fresh_read():
    env = TwoStep()
    await _create(BIG)
    request = env.put.calls[0].request
    assert request.headers["If-Match"] == '"etag-after-create"'
    inner = primary_text(request.content)
    assert inner.count(DIV) == 1
    assert "lorem ipsum" in inner


@respx.mock
async def test_e_order_is_post_refused_post_empty_get_put_and_a_confirming_reread():
    env = TwoStep()
    await _create(BIG)
    assert env.events[:2] == ["POST", "POST"]
    assert "PUT" in env.events
    assert "GET" in env.events[env.events.index("PUT") :], "the PUT must be confirmed by a re-read afterwards"
    assert env.events.index("GET") < env.events.index("PUT"), "the ETag must be read before the PUT"


@respx.mock
async def test_e_nothing_is_deleted():
    env = TwoStep()
    await _create(BIG)
    assert env.delete.call_count == 0


@respx.mock
async def test_e_small_content_refused_with_403_is_a_normal_error_without_fallback():
    env = TwoStep()
    env.refuse_always = True
    with pytest.raises(ToolError, match="403"):
        await _create("<p>small</p>")
    assert env.post.call_count == 1
    assert env.put.call_count == 0


@respx.mock
async def test_e_threshold_comes_from_the_env_var(monkeypatch):
    monkeypatch.setenv("ELM_RM_CREATE_FALLBACK_BYTES", "50")
    env = TwoStep()
    result = await _create("<p>" + "x" * 100 + "</p>")
    assert result["content_applied"] is True
    assert env.post.call_count == 2


@respx.mock
async def test_e_content_below_a_raised_threshold_has_no_fallback(monkeypatch):
    monkeypatch.setenv("ELM_RM_CREATE_FALLBACK_BYTES", "100000")
    env = TwoStep()
    with pytest.raises(ToolError, match="403"):
        await _create(BIG)
    assert env.post.call_count == 1


@respx.mock
async def test_e_403_for_big_content_followed_by_a_failed_put_reports_the_requirement_was_created():
    env = TwoStep(put_status=500)
    result = await _create(BIG)
    assert result["created"] is True
    assert result["uri"] == CREATED_URI
    assert result["content_applied"] is False
    warning = result["warning"].lower()
    assert "created" in warning
    assert "not applied" in warning or "was not" in warning
    assert env.delete.call_count == 0
    assert env.post.call_count == 2, "no further POST after the failed PUT"


@respx.mock
async def test_e_failed_second_post_is_an_error_and_there_is_no_third_post():
    env = TwoStep(refuse_always=True)
    with pytest.raises(ToolError, match="403"):
        await _create(BIG)
    assert env.post.call_count == 2
    assert env.put.call_count == 0


@respx.mock
async def test_e_timeout_after_the_post_is_still_never_resent_even_with_big_content():
    """Regression (passes today): the ambiguous failure re-queries the duplicates and never resends or falls back."""
    w4.mock_rm_config([w4.STREAM_1])
    w4.FakeRmQuery([], page_size=50)
    post = respx.post(FACTORY_URL).mock(side_effect=httpx.ReadTimeout("timed out after the request was sent"))
    put = respx.put(CREATED_URI).mock(return_value=httpx.Response(200))
    with pytest.raises(ToolError, match="(?i)not resent"):
        await _create(BIG)
    assert post.call_count == 1
    assert put.call_count == 0


@respx.mock
async def test_e_with_if_exists_the_duplicate_check_runs_once_across_both_steps():
    w4.mock_rm_config([w4.STREAM_1])
    fake = w4.FakeRmQuery([], page_size=50)
    env = TwoStep()
    result = await _create(BIG, if_exists="error", folder=w4.FOLDER_A)
    assert result["created"] is True
    assert result["content_applied"] is True
    assert env.post.call_count == 2
    assert len(fake.requests) == 1, "the duplicate check must not run again for the second step"
