"""Linear-time scanners for the XML an ELM server returns (issue #88).

The OSLC code reads RDF/XML with regexes, not ElementTree (ET fails in the MCP stdio context). The patterns it used
(``[^>]*``, ``.*?``) are re-run from EVERY opener that has no ``>`` or no closing tag, so a hostile or truncated body
(``<a:title <a:title ...``) costs O(n^2). These scanners keep the exact result of those patterns and visit each part
of the text a bounded number of times:

* the opener (``<prefix:name``) is still found by a regex, which is linear because the runs of word characters of two
  openers never overlap;
* the next ``>`` is located once and reused by every opener before it (``_Gt``);
* when an opener fails, every opener before the same ``>`` (or, for a missing closing tag, every later opener) fails
  too, so the scan jumps past them instead of retrying each one.

Where the old pattern was unbounded and the answer depends on the whole text (the lazy ``.*?`` up to a closing tag),
the scanner is unbounded too: the behavior is the same, only the time changes.
"""

import re
from collections.abc import Iterator
from functools import lru_cache
from typing import NamedTuple

_ATTR_QUOTE = '"'
_NEVER = 1 << 62


class Element(NamedTuple):
    """An element found by ``find_element``: ``text[start:end] == open_tag + body + close_tag``."""

    start: int
    end: int
    open_tag: str
    body: str
    close_tag: str


class _Gt:
    """``after(pos)``: index of the first ``>`` at or after ``pos`` (-1 when none), searched at most once per stretch."""

    __slots__ = ("hi", "lo", "text")

    def __init__(self, text: str) -> None:
        self.text = text
        self.lo = _NEVER  # the last search started here
        self.hi = -1  # and found the first '>' here (-1: none from lo on)

    def after(self, pos: int) -> int:
        if self.lo <= pos:
            if self.hi == -1:
                return -1
            if pos <= self.hi:
                return self.hi
        self.lo = pos
        self.hi = self.text.find(">", pos)
        return self.hi


@lru_cache(maxsize=64)
def tag(name: str, suffix: str = r"(?=\s)") -> re.Pattern[str]:
    """Opener ``<[prefix]name`` (the old ``<\\w*:?name``), followed by ``suffix`` (whitespace by default)."""
    return re.compile(r"<\w*:?" + re.escape(name) + suffix)


@lru_cache(maxsize=64)
def _closer(name: str) -> re.Pattern[str]:
    return re.compile(r"</\w*:?" + re.escape(name) + ">")


def _last_attr(text: str, lit: str, start: int, limit: int) -> tuple[str, int] | None:
    """The LAST ``lit`` + non-empty value + quote inside ``text[start:limit]``, as the greedy ``[^>]*`` found it."""
    q = text.rfind(lit, start, limit)
    while q != -1:
        v0 = q + len(lit)
        close = text.find(_ATTR_QUOTE, v0)
        if close > v0:
            return text[v0:close], close + 1
        q = text.rfind(lit, start, v0 - 1)
    return None


def attr_matches(
    text: str, opener: re.Pattern[str], attr: str = "rdf:resource", pos: int = 0
) -> Iterator[tuple[str, int, int]]:
    """Yield ``(value, start, end)`` of ``<opener ... attr="value"`` (the old ``opener[^>]*attr="([^"]+)"``) in order.

    The value is the last ``attr="..."`` before the first ``>`` that follows the opener (the whole rest of the text
    when there is no ``>``); like the regex, it may run past that ``>``. Matches do not overlap (``findall``).
    """
    lit = f"{attr}={_ATTR_QUOTE}"
    n = len(text)
    gt = _Gt(text)
    i = pos
    while True:
        m = opener.search(text, i)
        if m is None:
            return
        limit = gt.after(m.end())
        if limit == -1:
            limit = n
        got = _last_attr(text, lit, m.end(), limit)
        if got is None:
            if limit >= n:
                return  # no '>' ahead: every later opener has a smaller region and fails too
            i = limit + 1  # openers before this '>' share the region (a subset of it) and fail too
            continue
        value, end = got
        yield value, m.start(), end
        i = end


def first_attr(text: str, opener: re.Pattern[str] | str, attr: str = "rdf:resource") -> str | None:
    """The first ``attr`` value of ``opener`` (a compiled opener or a local name) or None."""
    pattern = tag(opener) if isinstance(opener, str) else opener
    return next((v for v, _s, _e in attr_matches(text, pattern, attr)), None)


def all_attrs(text: str, opener: re.Pattern[str] | str, attr: str = "rdf:resource") -> list[str]:
    """Every ``attr`` value of ``opener`` (``re.findall`` semantics)."""
    pattern = tag(opener) if isinstance(opener, str) else opener
    return [v for v, _s, _e in attr_matches(text, pattern, attr)]


def block_spans(text: str, name: str, *, tag_end: bool = False) -> Iterator[tuple[int, int]]:
    """Yield ``(start, end)`` of ``<[p:]name\\b .*? </[p:]name>`` (DOTALL, non-greedy, ``findall`` semantics).

    ``tag_end`` makes the opener end at its first ``>`` (``\\b[^>]*>``) before the lazy part starts.
    """
    opener = tag(name, r"\b")
    closer = _closer(name)
    gt = _Gt(text)
    i = 0
    while True:
        m = opener.search(text, i)
        if m is None:
            return
        from_ = m.end()
        if tag_end:
            g = gt.after(from_)
            if g == -1:
                return
            from_ = g + 1
        c = closer.search(text, from_)
        if c is None:
            return  # no closing tag from here on: none for a later opener either
        yield m.start(), c.end()
        i = c.end()


def blocks(text: str, name: str, *, tag_end: bool = False) -> list[str]:
    """The texts of ``block_spans``."""
    return [text[s:e] for s, e in block_spans(text, name, tag_end=tag_end)]


def remove_blocks(text: str, name: str) -> str:
    """``text`` without its ``<[p:]name\\b .*? </[p:]name>`` blocks (``re.sub(..., "", text, flags=DOTALL)``)."""
    out: list[str] = []
    last = 0
    for s, e in block_spans(text, name):
        out.append(text[last:s])
        last = e
    out.append(text[last:])
    return "".join(out)


def find_element(text: str, name: str, *, about: str | None = None, boundary: bool = True) -> Element | None:
    """First ``(<[p:]name[^>]*>)(.*?)(</[p:]name>)`` (DOTALL); ``boundary`` adds ``\\b`` after the name.

    ``about`` requires ``rdf:about="<about>"`` among the attributes of the opener (the old
    ``<name\\b[^>]*rdf:about="URI"[^>]*>``; an ``about`` that itself holds ``>`` is not supported).
    """
    opener = tag(name, r"\b" if boundary else "")
    closer = _closer(name)
    lit = None if about is None else f'rdf:about="{about}"'
    gt = _Gt(text)
    i = 0
    while True:
        m = opener.search(text, i)
        if m is None:
            return None
        g = gt.after(m.end())
        if g == -1:
            return None
        if lit is None or text.find(lit, m.end(), g) != -1:
            c = closer.search(text, g + 1)
            if c is None:
                return None  # no closing tag after this opener: none after a later one either
            return Element(m.start(), c.end(), text[m.start() : g + 1], text[g + 1 : c.start()], c.group(0))
        i = g + 1


def element_text(text: str, name: str, *, allow_empty: bool = False) -> str | None:
    """Text of the first ``<[p:]name[^>]*>([^<]+)</[p:]name>`` (``[^<]*`` with ``allow_empty``) or None."""
    opener = tag(name, "")
    closer = _closer(name)
    gt = _Gt(text)
    i = 0
    while True:
        m = opener.search(text, i)
        if m is None:
            return None
        g = gt.after(m.end())
        if g == -1:
            return None
        lt = text.find("<", g + 1)
        if lt == -1:
            return None
        if (lt > g + 1 or allow_empty) and closer.match(text, lt):
            return text[g + 1 : lt]
        i = g + 1  # an opener before the same '>' reads the same text and fails the same way


def open_tag_span(text: str, literal: str) -> tuple[int, int] | None:
    """``(start, end)`` of the first ``literal[^>]*>`` (``literal`` is plain text such as ``<ns:name``) or None."""
    p = text.find(literal)
    if p == -1:
        return None
    g = text.find(">", p + len(literal))
    return None if g == -1 else (p, g + 1)


def strip_tags(text: str) -> str:
    """``re.sub(r"<[^>]+>", "", text)``: from each ``<`` to the first ``>`` after it (``<>`` is not a tag)."""
    out: list[str] = []
    gt = _Gt(text)
    last = cur = 0
    while True:
        i = text.find("<", cur)
        if i == -1:
            break
        g = gt.after(i + 1)
        if g == -1:
            break
        if g == i + 1:
            cur = g
            continue
        out.append(text[last:i])
        last = cur = g + 1
    out.append(text[last:])
    return "".join(out)
