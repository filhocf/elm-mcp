"""RED tests (G3) for SPEC-rm-conteudo, part C: the minimal Markdown to XHTML converter.

Target: ``elm_mcp.markdown_xhtml.markdown_to_xhtml(text) -> str`` (own converter, no new dependency).
Subset: headings, paragraphs, bold/italic/inline code, fenced code, ul/ol (1 nesting level), blockquote,
``---``, links, images, pipe tables. All text is escaped; raw HTML is escaped (never passes); only
http/https/mailto URLs become links/images; the output is always well-formed XHTML.

The module exists as a skeleton raising NotImplementedError, so every test fails at the call.
"""

import pytest

from elm_mcp import oslc
from elm_mcp.markdown_xhtml import markdown_to_xhtml as md


@pytest.mark.parametrize("n", range(1, 7))
def test_heading_levels(n):
    assert f"<h{n}>Title</h{n}>" in md("#" * n + " Title")


def test_seven_hashes_is_not_a_heading():
    out = md("####### x")
    assert "<h7" not in out
    assert "<p>" in out


def test_paragraphs_are_split_on_blank_lines():
    out = md("one\n\ntwo")
    assert "<p>one</p>" in out
    assert "<p>two</p>" in out


def test_bold():
    assert "<strong>b</strong>" in md("a **b** c")


def test_italic():
    assert "<em>i</em>" in md("a *i* c")


def test_inline_code():
    assert "<code>c</code>" in md("a `c` d")


def test_inline_code_is_not_formatted_and_is_escaped():
    out = md("`**x** <b>`")
    assert "<code>**x** &lt;b&gt;</code>" in out
    assert "<strong>" not in out


def test_fenced_code_block_keeps_content_escaped():
    out = md("```\nif a < b:\n    x = '&'\n```")
    assert "<pre><code>" in out
    assert "if a &lt; b:" in out
    assert "    x = '&amp;'" in out
    assert "</code></pre>" in out


def test_fenced_code_block_does_not_interpret_markdown():
    out = md("```\n# not a heading\n**not bold**\n```")
    assert "<h1>" not in out
    assert "<strong>" not in out


def test_unordered_list_with_dash_and_star():
    for marker in ("-", "*"):
        out = md(f"{marker} a\n{marker} b")
        assert "<ul>" in out
        assert "<li>a</li>" in out
        assert "<li>b</li>" in out


def test_ordered_list():
    out = md("1. a\n2. b")
    assert "<ol>" in out
    assert "<li>a</li>" in out
    assert "<li>b</li>" in out


def test_nested_list_one_level_lives_inside_the_parent_item():
    out = md("- a\n  - a1\n  - a2\n- b")
    assert out.count("<ul>") == 2
    assert out.count("<li>") == 4
    parent = out.index("<li>a")
    nested = out.index("<ul>", parent + 1)
    assert nested < out.index("</li>", parent), "the nested list must be inside the <li> of the parent"
    assert "<li>a1</li>" in out


def test_blockquote():
    out = md("> quoted text")
    assert "<blockquote>" in out
    assert "quoted text" in out
    assert "</blockquote>" in out


def test_horizontal_rule_is_a_closed_void_tag():
    out = md("a\n\n---\n\nb")
    assert "<hr/>" in out
    assert "<hr>" not in out


def test_link():
    assert '<a href="https://example.com/p">t</a>' in md("[t](https://example.com/p)")


def test_mailto_link():
    assert '<a href="mailto:a@example.com">mail</a>' in md("[mail](mailto:a@example.com)")


def test_image_is_a_closed_void_tag():
    out = md("![alt text](https://example.com/i.png)")
    assert '<img src="https://example.com/i.png" alt="alt text"/>' in out


def test_table_has_thead_and_tbody():
    out = md("| h1 | h2 |\n|----|----|\n| a | b |\n| c | d |")
    assert "<table>" in out
    assert "<thead>" in out
    assert "<th>h1</th>" in out
    assert "<th>h2</th>" in out
    assert "<tbody>" in out
    assert "<td>a</td>" in out
    assert "<td>d</td>" in out
    assert out.count("<tr>") == 3


# --- escaping and URL safety ---
def test_script_tag_is_escaped_not_passed():
    out = md("<script>alert(1)</script>")
    assert "<script" not in out
    assert "&lt;script&gt;" in out


def test_raw_html_is_escaped_everywhere():
    out = md('text <iframe src="x"></iframe> and <b>bold</b>')
    assert "<iframe" not in out
    assert "<b>" not in out
    assert "&lt;b&gt;" in out


def test_ampersand_is_escaped():
    assert "a &amp; b" in md("a & b")


def test_existing_entity_is_escaped_literally_not_trusted():
    assert "&amp;amp;" in md("&amp;")


def test_quote_in_url_is_escaped_in_the_attribute():
    out = md('[x](https://example.com/?q="b")')
    assert 'href="https://example.com/?q="b""' not in out
    assert "&quot;" in out


@pytest.mark.parametrize(
    "url",
    ["javascript:alert(1)", "JaVaScRiPt:alert(1)", "data:text/html;base64,AAAA", "vbscript:x", "file:///etc/passwd"],
)
def test_unsafe_link_scheme_becomes_text_without_link(url):
    out = md(f"[click]({url})")
    assert "<a" not in out
    assert "href" not in out
    assert "click" in out


def test_unsafe_image_scheme_is_not_an_img():
    out = md("![x](javascript:alert(1))")
    assert "<img" not in out
    assert "src=" not in out


# --- well-formedness and degenerate input ---
@pytest.mark.parametrize("text", ["", "   ", "\n\n"])
def test_empty_text_gives_empty_output(text):
    assert md(text) == ""


def test_full_document_is_well_formed_xhtml():
    doc = (
        "# Title\n\nIntro with **bold**, *it*, `code`, a & b, <b>raw</b> and [l](https://e.com/?a=1&b=2).\n\n"
        "- a\n  - a1\n- b\n\n1. x\n2. y\n\n> q\n\n---\n\n![i](https://e.com/i.png)\n\n"
        "| h | k |\n|---|---|\n| 1 | 2 |\n\n```\n<div>&\n```\n"
    )
    out = md(doc)
    assert out.strip()
    oslc._assert_xhtml_wellformed(out)


def test_unbalanced_emphasis_still_well_formed():
    out = md("**open and *mixed `tick")
    assert out.strip()
    oslc._assert_xhtml_wellformed(out)
