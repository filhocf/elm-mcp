"""Loop-2 review fixes for SPEC-rm-conteudo: linear-time sanitizing/conversion (ReDoS), content size cap,
rejection of comments/CDATA/PI, ETag check after update, and small hardening items."""

import time

import httpx
import pytest
import respx

from elm_mcp import oslc, server
from elm_mcp.auth import close_client
from elm_mcp.markdown_xhtml import markdown_to_xhtml

from . import w1_fixtures as w1
from .test_rm_conteudo import CREATED_URI, DIV, EXISTING_RDF, _update_routes, primary_text
from .w1_fixtures import BASE

SIZE = 200_000
LIMIT = 1.0


def _timed(fn, arg):
    start = time.perf_counter()
    fn(arg)
    return time.perf_counter() - start


def _md(text: str) -> str:
    return oslc.prepare_content(text, "markdown")


@pytest.fixture(autouse=True)
async def _cfg(monkeypatch):
    monkeypatch.setattr("elm_mcp.config.settings.elm_url", BASE)
    monkeypatch.delenv("ELM_RM_CREATE_FALLBACK_BYTES", raising=False)
    monkeypatch.delenv("ELM_RM_MAX_CONTENT_BYTES", raising=False)
    yield
    await close_client()


# ---------------------------------------------------------------- H1/H2/H3: timing
MARKDOWN_INPUTS = {
    "brackets": "[" * SIZE,
    "bang-brackets": "![" * (SIZE // 2),
    "link-open": "[x](" * (SIZE // 4),
    "link-open-closing-bracket": "[" * (SIZE // 2) + "]" * (SIZE // 2),
    "heading-spaces": "# a" + " " * 5000 + "x",
    "heading-spaces-big": "# a" + " " * SIZE + "x",
    "stars": "*" * SIZE,
    "backticks": "`" * SIZE,
    "pipes": "|" * SIZE,
    "gt": ">" * SIZE,
    "table-spaces": "|a|\n|-| " + " " * SIZE + "x",
    "hr-spaces": "- - " + " " * SIZE + "x",
}


@pytest.mark.parametrize("name", sorted(MARKDOWN_INPUTS))
def test_h_markdown_is_linear(name):
    assert _timed(_md, MARKDOWN_INPUTS[name]) < LIMIT


HTML_INPUTS = {
    "script": "<script " * (SIZE // 8),
    "style": "<style " * (SIZE // 7),
    "img": "<img " * (SIZE // 5),
    "input": "<input " * (SIZE // 7),
    "html": "<html " * (SIZE // 6),
    "body": "<body " * (SIZE // 6),
    "meta": "<meta " * (SIZE // 6),
    "head": "<head >" * (SIZE // 7),
    "embed": "<embed " * (SIZE // 7),
    "div": DIV + "<div " * (SIZE // 5) + "</div>",
    "script-open-close-names": "<script>" * (SIZE // 8),
}


@pytest.mark.parametrize("name", sorted(HTML_INPUTS))
def test_h_html_sanitizer_is_linear(name):
    assert _timed(oslc.sanitize_html_to_xhtml, HTML_INPUTS[name]) < LIMIT


@pytest.mark.parametrize("name", ["div"])
def test_h_xhtml_unwrap_is_linear(name):
    assert _timed(oslc._unwrap_root_div, HTML_INPUTS[name]) < LIMIT


def test_h_heading_still_strips_closing_hashes():
    assert markdown_to_xhtml("# a ##") == "<h1>a</h1>"
    assert markdown_to_xhtml("## b  ") == "<h2>b</h2>"
    assert markdown_to_xhtml("### c #d") == "<h3>c #d</h3>"
    assert markdown_to_xhtml("# #") == "<h1></h1>"


def test_h_sanitizer_semantics_kept():
    assert oslc.sanitize_html_to_xhtml('<p>a</p><script src="x">alert(1)</script><p>b</p>') == "<p>a</p><p>b</p>"
    assert oslc.sanitize_html_to_xhtml("<p>a</p><SCRIPT>x</Script  ><p>b</p>") == "<p>a</p><p>b</p>"
    assert oslc.sanitize_html_to_xhtml("<p>a</p><iframe src='x'/><p>b</p>") == "<p>a</p><p>b</p>"
    assert oslc.sanitize_html_to_xhtml("<p>a</p><embed src='x'></embed><p>b</p>") == "<p>a</p><p>b</p>"
    assert oslc.sanitize_html_to_xhtml("<p>a</p><script>x") == "<p>a</p>"
    assert oslc.sanitize_html_to_xhtml("<p>a</p><script ") == "<p>a</p>"
    assert oslc.sanitize_html_to_xhtml('<img src="a">') == '<img src="a"/>'
    assert oslc.sanitize_html_to_xhtml('<img src="a"/>') == '<img src="a"/>'
    assert oslc.sanitize_html_to_xhtml("<input disabled>") == "<input disabled/>"
    assert oslc.sanitize_html_to_xhtml("<html><head><title>t</title></head><body><p>x</p></body></html>") == "<p>x</p>"


def test_h_link_and_image_limits_and_balanced_parens():
    assert markdown_to_xhtml("[a](http://x/a(b))") == '<p><a href="http://x/a(b)">a</a></p>'
    assert markdown_to_xhtml("![i](http://x/y.png)") == '<p><img src="http://x/y.png" alt="i"/></p>'
    long_label = "x" * 1001
    assert "<a " not in markdown_to_xhtml(f"[{long_label}](http://x/)")
    long_url = "http://x/" + "a" * 2001
    assert "<a " not in markdown_to_xhtml(f"[a]({long_url})")


# ---------------------------------------------------------------- H3: size cap
def test_h_content_over_cap_is_refused_naming_the_variable():
    with pytest.raises(ValueError, match="ELM_RM_MAX_CONTENT_BYTES"):
        oslc.prepare_content("a" * (1024 * 1024 + 1), "markdown")
    for fmt in ("html", "xhtml"):
        with pytest.raises(ValueError, match="ELM_RM_MAX_CONTENT_BYTES"):
            oslc.prepare_content("a" * (1024 * 1024 + 1), fmt)


def test_h_content_cap_is_configurable_and_counts_bytes(monkeypatch):
    monkeypatch.setenv("ELM_RM_MAX_CONTENT_BYTES", "10")
    assert oslc.prepare_content("<p>a</p>", "xhtml") == "<p>a</p>"
    with pytest.raises(ValueError, match="ELM_RM_MAX_CONTENT_BYTES"):
        oslc.prepare_content("é" * 6, "markdown")  # 12 bytes
    monkeypatch.setenv("ELM_RM_MAX_CONTENT_BYTES", "garbage")
    assert oslc.prepare_content("a" * 5000, "markdown")  # invalid -> default 1 MB


# ---------------------------------------------------------------- M1
@pytest.mark.parametrize("fmt", ["html", "xhtml"])
@pytest.mark.parametrize(
    "bad", ["<p>a</p><!-- c -->", "<p><![CDATA[x]]></p>", "<?xml version='1.0'?><p>a</p>", "<p><?pi x?></p>"]
)
def test_m1_comment_cdata_and_pi_are_refused(fmt, bad):
    with pytest.raises(ValueError, match=r"<!--|CDATA|<\?"):
        oslc.prepare_content(bad, fmt)


def test_m1_markdown_never_emits_them():
    out = oslc.prepare_content("<!-- x --> <![CDATA[y]]> <?pi?>", "markdown")
    assert "<!--" not in out and "<![CDATA[" not in out and "<?" not in out


@respx.mock
async def test_m1_update_with_server_comment_holding_the_closing_tag():
    rdf = EXISTING_RDF.replace("<p>old</p>", "<p>old</p><!-- </jazz_rm:primaryText> -->")
    w1.mock_rm_discovery()
    respx.get(CREATED_URI).mock(return_value=httpx.Response(200, text=rdf, headers={"ETag": '"e1"'}))
    put = respx.put(CREATED_URI).mock(return_value=httpx.Response(200, text=""))
    await server.update_requirement(uri=CREATED_URI, content="<p>new</p>")
    body = put.calls[0].request.content.decode()
    assert primary_text(body) == f"{DIV}<p>new</p></div>"
    assert "-->" not in body and "old" not in body


# ---------------------------------------------------------------- M2
@respx.mock
async def test_m2_new_etag_after_put_gives_no_warning():
    w1.mock_rm_discovery()
    gets = iter(
        [
            httpx.Response(200, text=EXISTING_RDF, headers={"ETag": '"e1"'}),
            httpx.Response(200, text=EXISTING_RDF, headers={"ETag": '"e2"'}),
        ]
    )
    respx.get(CREATED_URI).mock(side_effect=lambda request: next(gets))
    respx.put(CREATED_URI).mock(return_value=httpx.Response(200, text=""))
    result = await oslc.update_requirement(CREATED_URI, xhtml="<p>n</p>", content_format="xhtml")
    assert "warning" not in result


@respx.mock
async def test_m2_same_etag_after_put_warns_that_the_change_is_not_confirmed():
    _update_routes()
    result = await oslc.update_requirement(CREATED_URI, xhtml="<p>n</p>", content_format="xhtml")
    assert "ETag" in result["warning"]


# ---------------------------------------------------------------- LOW
@pytest.mark.parametrize("value", ["-5", "0", "abc", ""])
def test_low_fallback_bytes_invalid_or_non_positive_uses_default(monkeypatch, value):
    monkeypatch.setenv("ELM_RM_CREATE_FALLBACK_BYTES", value)
    assert oslc._create_fallback_bytes() == 4096


def test_low_fallback_bytes_valid_value(monkeypatch):
    monkeypatch.setenv("ELM_RM_CREATE_FALLBACK_BYTES", "100")
    assert oslc._create_fallback_bytes() == 100


def test_low_unwrap_root_div_accepts_single_quoted_xmlns():
    assert oslc._unwrap_root_div("<div xmlns='http://www.w3.org/1999/xhtml'><p>x</p></div>") == "<p>x</p>"


def test_low_lone_surrogate_is_removed():
    assert "\ud800" not in markdown_to_xhtml("a\ud800b")
    out = _md("a\udc00b")
    out.encode("utf-8")
    for fmt in ("html", "xhtml"):
        oslc.prepare_content("<p>a\ud800b</p>", fmt).encode("utf-8")


def test_active_content_split_by_nested_tags_does_not_reassemble():
    from elm_mcp.oslc import sanitize_html_to_xhtml

    out = sanitize_html_to_xhtml("<scr<script></script>ipt>alert(1)</scr<script></script>ipt>")
    assert "<script" not in out.lower()
    assert "alert(1)" not in out or "<" not in out.split("alert(1)")[0]


def test_active_content_nested_many_levels_is_cleaned_or_refused():
    from elm_mcp.oslc import sanitize_html_to_xhtml

    s = "<scr" * 8 + "<script></script>" + "ipt>" * 8
    try:
        out = sanitize_html_to_xhtml(s)
    except ValueError:
        return
    assert "<script" not in out.lower()


@pytest.mark.parametrize(
    "payload",
    [
        "<scr<head></head>ipt>alert(1)</scr<head></head>ipt>",
        "<scr<html>ipt>alert(1)</scr</html>ipt>",
        "<scr<body>ipt>alert(1)</scr</body>ipt>",
        "<scr<meta charset='x'>ipt>alert(1)</scr<meta charset='x'>ipt>",
        "<scr\U0001f600ipt>alert(1)</scr\U0001f600ipt>",
        "<scr\ud800ipt>alert(1)</scr\ud800ipt>",
        "<ifr<head></head>ame src='x'></ifr<head></head>ame>",
    ],
)
def test_active_content_reassembled_by_other_removals_is_gone(payload):
    from elm_mcp.oslc import prepare_content

    try:
        out = prepare_content(payload, "html")
    except ValueError:
        return
    low = out.lower()
    assert "<script" not in low
    assert "<iframe" not in low


def test_sanitize_is_a_fixed_point():
    from elm_mcp.oslc import sanitize_html_to_xhtml

    for payload in ["<scr<head></head>ipt>x</scr<head></head>ipt>", "<p>a &amp; b</p>", "<br><img src=x>"]:
        once = sanitize_html_to_xhtml(payload)
        assert sanitize_html_to_xhtml(once) == once


def test_deeply_nested_root_divs_unwrap_in_linear_time():
    import time

    from elm_mcp.oslc import _unwrap_root_div

    ns = "http://www.w3.org/1999/xhtml"
    n = 20000
    text = f'<div xmlns="{ns}">' * n + "x" + "</div>" * n
    t0 = time.perf_counter()
    out = _unwrap_root_div(text)
    assert time.perf_counter() - t0 < 1.0
    assert out == "x"


def test_root_div_not_enclosing_everything_is_kept():
    from elm_mcp.oslc import _unwrap_root_div

    ns = "http://www.w3.org/1999/xhtml"
    a = f'<div xmlns="{ns}">a</div>b'
    b = f'<div xmlns="{ns}">a</div><div>b</div>'
    c = f'<div xmlns="{ns}"><div>keep</div></div>'
    assert _unwrap_root_div(a) == a
    assert _unwrap_root_div(b) == b
    assert _unwrap_root_div(c) == "<div>keep</div>"


def test_replace_primary_text_handles_an_empty_self_closing_literal():
    from elm_mcp.oslc import _replace_primary_text

    body = '<dcterms:title>t</dcterms:title><jazz_rm:primaryText rdf:parseType="Literal"/><x:y/>'
    out = _replace_primary_text(body, '<div xmlns="http://www.w3.org/1999/xhtml">new</div>')
    assert out.count("<jazz_rm:primaryText") == 1
    assert out.endswith("</jazz_rm:primaryText><x:y/>")
    assert "new" in out
