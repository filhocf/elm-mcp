"""Minimal Markdown to XHTML converter for the RM primaryText (SPEC-rm-conteudo).

Own converter, no dependency. Subset: headings, paragraphs, bold/italic/inline code, fenced code, ul/ol (one
nesting level), blockquote, ``---``, links, images and pipe tables. Every piece of text is escaped (raw HTML never
passes) and only http/https/mailto URLs become links or images, so the output is always well-formed XHTML.
"""

import re


def _drop_control(text: str) -> str:
    """Remove the characters XML 1.0 cannot carry: C0 controls (except tab, LF, CR), U+FFFE/U+FFFF, lone surrogates."""
    return "".join(
        ch
        for ch in text
        if not ((ord(ch) < 0x20 and ch not in "\t\n\r") or ord(ch) in (0xFFFE, 0xFFFF) or 0xD800 <= ord(ch) <= 0xDFFF)
    )


_SAFE_URL = re.compile(r"^(https?://|mailto:)\S+$", re.IGNORECASE)
_HEADING = re.compile(r"^(#{1,6})\s+(.*)$")
_FENCE = re.compile(r"^\s*```")
_HR = re.compile(r"^\s*([-*_])(\s*\1){2,}\s*$")
_LIST_ITEM = re.compile(r"^(\s*)([-*+]|\d+[.)])\s+(.*)$")
_TABLE_SEP_CELL = re.compile(r":?-+:?")
# URL of a link/image: no whitespace, at most one level of balanced parentheses; the window below bounds the scan.
_URL = re.compile(r"((?:[^()\s]|\([^()\s]*\))*)\)")
_MAX_LABEL = 1000
_MAX_URL = 2000
_MAX_INLINE_DEPTH = 8


def _esc(text: str, attr: bool = False) -> str:
    out = text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    return out.replace('"', "&quot;") if attr else out


def _match_link(text: str, i: int, last_close: int, image: bool) -> tuple[str, str, int] | None:
    """``[label](url)`` at ``text[i]`` (``[``): (label, url, end) or None. Bounded: label <= 1000, url <= 2000."""
    if i > last_close:
        return None
    close = text.find("]", i + 1, i + 2 + _MAX_LABEL)
    if close == -1 or text[close + 1 : close + 2] != "(" or (close == i + 1 and not image):
        return None
    m = _URL.match(text, close + 2, close + 3 + _MAX_URL)
    if m is None or (not m.group(1) and not image):
        return None
    return text[i + 1 : close], m.group(1), m.end()


def _inline(text: str, depth: int = 0) -> str:
    """Inline spans of ``text``; whatever is not a recognised, closed span is emitted escaped and literal."""
    if depth > _MAX_INLINE_DEPTH:
        return _esc(text)
    out: list[str] = []
    i = 0
    last_close = text.rfind("]")
    while i < len(text):
        char = text[i]
        if char == "`":
            end = text.find("`", i + 1)
            if end > i + 1:
                out.append(f"<code>{_esc(text[i + 1 : end])}</code>")
                i = end + 1
                continue
        elif char == "!" and text[i + 1 : i + 2] == "[" and (found := _match_link(text, i + 1, last_close, True)):
            alt, url, end = found
            if _SAFE_URL.match(url):
                out.append(f'<img src="{_esc(url, True)}" alt="{_esc(alt, True)}"/>')
            else:
                out.append(_esc(alt))
            i = end
            continue
        elif char == "[" and (found := _match_link(text, i, last_close, False)):
            label, url, end = _inline(found[0], depth + 1), found[1], found[2]
            out.append(f'<a href="{_esc(url, True)}">{label}</a>' if _SAFE_URL.match(url) else label)
            i = end
            continue
        elif text.startswith("**", i):
            end = text.find("**", i + 2)
            if end > i + 2:
                out.append(f"<strong>{_inline(text[i + 2 : end], depth + 1)}</strong>")
                i = end + 2
                continue
        elif char == "*":
            end = text.find("*", i + 1)
            if end > i + 1 and not text[i + 1].isspace():
                out.append(f"<em>{_inline(text[i + 1 : end], depth + 1)}</em>")
                i = end + 1
                continue
        out.append(_esc(char))
        i += 1
    return "".join(out)


def _cells(row: str) -> list[str]:
    row = row.strip()
    row = row.removeprefix("|")
    row = row.removesuffix("|")
    return [cell.strip() for cell in row.split("|")]


def _table(lines: list[str]) -> str:
    head = "".join(f"<th>{_inline(c)}</th>" for c in _cells(lines[0]))
    body = "".join("<tr>" + "".join(f"<td>{_inline(c)}</td>" for c in _cells(line)) + "</tr>" for line in lines[2:])
    parts = [f"<table><thead><tr>{head}</tr></thead>"]
    if body:
        parts.append(f"<tbody>{body}</tbody>")
    parts.append("</table>")
    return "".join(parts)


def _list_tag(marker: str) -> str:
    return "ol" if marker[0].isdigit() else "ul"


def _list(lines: list[str]) -> list[str]:
    """Lists for consecutive item lines: indented items nest (one level) inside the previous top-level item."""
    groups: list[list] = []  # [tag, [[text, nested_tag, [nested texts]]]]
    for line in lines:
        indent, marker, text = _LIST_ITEM.match(line).groups()  # type: ignore[union-attr]
        tag = _list_tag(marker)
        nested = len(indent.replace("\t", "    ")) >= 2 and groups and groups[-1][1]
        if nested:
            item = groups[-1][1][-1]
            item[1] = item[1] or tag
            item[2].append(text)
        elif groups and groups[-1][0] == tag:
            groups[-1][1].append([text, None, []])
        else:
            groups.append([tag, [[text, None, []]]])
    html = []
    for tag, items in groups:
        rendered = []
        for text, nested_tag, children in items:
            inner = _inline(text)
            if children:
                inner += f"<{nested_tag}>" + "".join(f"<li>{_inline(c)}</li>" for c in children) + f"</{nested_tag}>"
            rendered.append(f"<li>{inner}</li>")
        html.append(f"<{tag}>{''.join(rendered)}</{tag}>")
    return html


def _is_table_sep(line: str) -> bool:
    return all(_TABLE_SEP_CELL.fullmatch(cell) for cell in _cells(line))


def _is_table_start(lines: list[str], i: int) -> bool:
    return "|" in lines[i] and i + 1 < len(lines) and "|" in lines[i + 1] and _is_table_sep(lines[i + 1])


def _heading_text(rest: str) -> str:
    """Heading text without the optional closing ``#`` sequence (linear; only ``" ##"`` counts as closing)."""
    without = rest.rstrip("#")
    if without != rest and (not without or without[-1].isspace()):
        return without.rstrip()
    return rest


def markdown_to_xhtml(text: str) -> str:
    """Convert the supported Markdown subset to well-formed XHTML; all text is escaped, raw HTML never passes."""
    lines = _drop_control(text or "").replace("\r\n", "\n").replace("\r", "\n").split("\n")
    blocks: list[str] = []
    paragraph: list[str] = []

    def flush() -> None:
        if paragraph:
            blocks.append(f"<p>{_inline(chr(10).join(paragraph))}</p>")
            paragraph.clear()

    i = 0
    while i < len(lines):
        line = lines[i]
        stripped = line.strip()
        if not stripped:
            flush()
            i += 1
        elif _FENCE.match(line):
            flush()
            end = i + 1
            while end < len(lines) and not _FENCE.match(lines[end]):
                end += 1
            blocks.append(f"<pre><code>{_esc(chr(10).join(lines[i + 1 : end]))}</code></pre>")
            i = end + 1
        elif m := _HEADING.match(stripped):
            flush()
            level = len(m.group(1))
            blocks.append(f"<h{level}>{_inline(_heading_text(m.group(2)))}</h{level}>")
            i += 1
        elif _HR.match(line):
            flush()
            blocks.append("<hr/>")
            i += 1
        elif stripped.startswith(">"):
            flush()
            quoted = []
            while i < len(lines) and lines[i].strip().startswith(">"):
                quoted.append(lines[i].strip()[1:].strip())
                i += 1
            blocks.append(f"<blockquote><p>{_inline(chr(10).join(quoted))}</p></blockquote>")
        elif _is_table_start(lines, i):
            flush()
            end = i + 2
            while end < len(lines) and lines[end].strip() and "|" in lines[end]:
                end += 1
            blocks.append(_table(lines[i:end]))
            i = end
        elif _LIST_ITEM.match(line):
            flush()
            end = i
            while end < len(lines) and _LIST_ITEM.match(lines[end]) and not _HR.match(lines[end]):
                end += 1
            blocks.extend(_list(lines[i:end]))
            i = end
        else:
            paragraph.append(stripped)
            i += 1
    flush()
    return "\n".join(blocks)
