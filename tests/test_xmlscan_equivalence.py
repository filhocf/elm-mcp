"""Equivalence of the linear scanners (``xmlscan``) with the regexes they replaced (issue #88).

``OLD_*`` are the patterns exactly as they stood in ``oslc.py`` / ``attachments.py`` on main 798394d. Each test feeds the
OLD regex and the NEW code the same inputs and demands the same answer: the repo fixtures, hand-made legitimate and
malformed XML, and a seeded fuzz of random tag soup (small texts, so the old regexes finish at once).
"""

import html
import random
import re
from pathlib import Path

import pytest

from elm_mcp import attachments, oslc, xmlscan

FIXTURES = Path(__file__).parent / "fixtures"
DOTALL = re.DOTALL

# ---- the patterns of main 798394d -------------------------------------------------------------------------------
OLD_SP = re.compile(
    r"<\w+:ServiceProvider>\s*"
    r"<(?:\w+:)?title>([^<]+)</(?:\w+:)?title>\s*.*?"
    r"<\w+:services\s+[^>]*rdf:resource=\"([^\"]+)\"",
    DOTALL,
)
OLD_COMPONENT = re.compile(r"<oslc_config:component\s[^>]*rdf:resource=\"([^\"]+)\"")
OLD_CONFIGURATIONS = re.compile(r"<(?:\w+:)?configurations\s[^>]*rdf:resource=\"([^\"]+)\"")
OLD_MEMBER = re.compile(r"<(?:\w+:)?member\s[^>]*rdf:resource=\"([^\"]+)\"")
OLD_FACTORY = re.compile(r"<\w*:?CreationFactory\b.*?</\w*:?CreationFactory>", DOTALL)


def old_attr(name: str) -> re.Pattern[str]:
    return re.compile(r"<\w*:?" + name + r"\s+[^>]*rdf:resource=\"([^\"]+)\"")


OLD_IDENTIFIER = re.compile(r"<\w*:?identifier[^>]*>([^<]+)</\w*:?identifier>")
OLD_PRIMARY = re.compile(r"<jazz_rm:primaryText[^>]*>")
OLD_PROPERTY = re.compile(r"<\w*:?property\b.*?</\w*:?property>", DOTALL)
OLD_TITLE = re.compile(r"<\w*:?title[^>]*>([^<]+)</\w*:?title>")
OLD_TITLE_EMPTY = re.compile(r"<\w*:?title[^>]*>([^<]*)</\w*:?title>")
OLD_TITLE_BLOCK = re.compile(r"(<\w*:?title[^>]*>)(.*?)(</\w*:?title>)", DOTALL)
OLD_DESCRIPTION_BLOCK = re.compile(r"<\w*:?Description\b[^>]*>.*?</\w*:?Description>", DOTALL)
OLD_DESCRIPTION_ANY = re.compile(r"(<\w*:?Description\b[^>]*>)(.*?)(</\w*:?Description>)", DOTALL)
OLD_TAG = re.compile(r"<[^>]+>")
OLD_STAR = re.compile(r"filename\*\s*=\s*([^';]*)'[^';]*'([^;]*)", re.IGNORECASE)


def old_description_about(uri: str) -> re.Pattern[str]:
    return re.compile(
        r'(<\w*:?Description\b[^>]*rdf:about="' + re.escape(uri) + r'"[^>]*>)(.*?)(</\w*:?Description>)', DOTALL
    )


# ---- inputs -----------------------------------------------------------------------------------------------------
def _fixtures() -> list[str]:
    return [p.read_text(encoding="utf-8") for p in sorted(FIXTURES.glob("*.xml"))]


CF = "<oslc:CreationFactory>"
CF_END = "</oslc:CreationFactory>"
RM_SERVICES = (
    '<rdf:RDF><oslc:ServiceProvider rdf:about="a"><dcterms:title>Alpha</dcterms:title>'
    '<oslc:details rdf:resource="https://h/rm/process/project-areas/_pa"/>'
    f'{CF}<oslc:resourceType rdf:resource="http://open-services.net/ns/rm#Requirement"/>'
    '<oslc:creation rdf:resource="https://h/rm/requirementFactory?projectURL=x"/>'
    '<oslc:resourceShape rdf:resource="https://h/rm/shapes/1"/>'
    '<oslc:resourceShape rdf:resource="https://h/rm/shapes/2"/>'
    f"<dcterms:title>Location for creation of Task change requests</dcterms:title>{CF_END}"
    f"{CF}<oslc:resourceType rdf:resource='http://single/quotes'/>{CF_END}"
    f'{CF}<oslc:resourceType\n  rdf:resource="http://open-services.net/ns/rm#RequirementCollection"/>{CF_END}'
    "<oslc:property><oslc:Property><dcterms:title>Prop</dcterms:title></oslc:Property></oslc:property>"
    "<dcterms:title>Shape title</dcterms:title><dcterms:identifier>42</dcterms:identifier>"
    '<oslc_config:component rdf:resource="http://c"/>'
    '<oslc:configurations\n rdf:resource="http://cfg"/><rdfs:member rdf:resource="http://m1"/>'
    '<member rdf:resource="http://m2"/><jazz_rm:primaryText rdf:parseType="Literal"><div>x</div></jazz_rm:primaryText>'
    '<jazz_rm:primaryText/><rdf:Description rdf:about="https://h/rm/resources/TX_1"><dcterms:title>old</dcterms:title>'
    '</rdf:Description><rdf:Description rdf:about="u"/><oslc:allowedValue rdf:resource="http://v/1"/>'
    '<oslc:allowedValue rdf:resource="a>b"/><x:allowedValue a="1" rdf:resource="first" rdf:resource="last"/>'
    "</oslc:ServiceProvider></rdf:RDF>"
)
CATALOG = (
    "<oslc:ServiceProvider><dcterms:title>Alpha</dcterms:title><oslc:details/>"
    '<oslc:services rdf:resource="https://h/rm/alpha/services.xml"/></oslc:ServiceProvider>'
    "<a:ServiceProvider>\n <dcterms:title>Beta</dcterms:title>"
    '<a:services\n rdf:resource="https://h/rm/beta/services.xml"/></a:ServiceProvider>'
    "<a:ServiceProvider><title>NoServices</title></a:ServiceProvider>"
    '<a:ServiceProvider><title>Gamma</title><a:services rdf:resource="g"/></a:ServiceProvider>'
)
HAND = [
    "",
    "<",
    ">",
    "<>",
    "<a>",
    "x<a:title>t</a:title>",
    "<a:title><a:title>t</a:title>",
    "<a:title>t</b:title>",
    "<a:title>t</a:title",
    "<a:title </a:title>",
    "<a:title>x<</a:title>",
    "<a:title></a:title>",
    "<a:titleX>t</a:title>",
    "<xtitle>t</title>",
    "<a:b:title>t</a:b:title>",
    "<a:identifier rdf:x='1'>12</a:identifier>",
    "<identifier>7</identifier><identifier>8</identifier>",
    '<a:resourceType rdf:resource="">x</a:resourceType>',
    '<a:resourceType rdf:resource=""/><a:resourceType rdf:resource="ok"/>',
    '<a:resourceType rdf:resource="x',
    '<a:resourceType rdf:resource="x" <a:resourceType rdf:resource="y"/>',
    '<a:resourceType\trdf:resource="x"/>',
    '<a:resourceType rdf:resource="a"b="rdf:resource="c"/>',
    '<a:resourceType rdf:resource="x"',
    '<resourceType rdf:resource="x"/>',
    "<oslc:CreationFactory>never closed",
    "<oslc:CreationFactory></oslc:CreationFactory></x:CreationFactory>",
    "<a:CreationFactory><a:CreationFactory>inner</a:CreationFactory>outer</a:CreationFactory>",
    "<CreationFactoryX>y</CreationFactory>",
    '<rdf:Description rdf:about="U"><a:title>t</a:title></rdf:Description>',
    '<rdf:Description rdf:about="U" x="1"/>',
    '<rdf:Description x="1" rdf:about="U"/></rdf:Description>',
    '<rdf:Description rdf:about="U>"></rdf:Description>',
    "<jazz_rm:primaryText>a</jazz_rm:primaryText>",
    "<jazz_rm:primaryText",
    "<jazz_rm:primaryTextX/>",
    "a <b>bold</b> <br/> <> < 2 > 1 <a href='x'>l</a>",
    "1 < 2 and 3 > 2",
    "<a <b> c>",
]
SEEDED = [RM_SERVICES, CATALOG, *HAND, *_fixtures()]

TOKENS = [
    "<",
    "</",
    ">",
    "/>",
    " ",
    "\n",
    '"',
    "'",
    ":",
    "a",
    "ab",
    "x:",
    "<a:",
    "</a:",
    "rdf:resource=",
    'rdf:resource="u"',
    'rdf:resource=""',
    "rdf:about=",
    'rdf:about="U"',
    "title",
    "identifier",
    "property",
    "primaryText",
    "jazz_rm:",
    "CreationFactory",
    "resourceType",
    "creation",
    "resourceShape",
    "details",
    "allowedValue",
    "Description",
    "ServiceProvider",
    "services",
    "oslc_config:",
    "component",
    "configurations",
    "member",
    "<a:title>",
    "</a:title>",
    "<a:ServiceProvider>",
    "<a:Description>",
    "</a:Description>",
    "<a:CreationFactory>",
    "</a:CreationFactory>",
    "<a:property>",
    "</a:property>",
    'rdf:resource="a>b"',
    "<!--",
    "<![CDATA[",
    "<:x",
    "<ab:cd:creation",
    "<a:ServiceProviderX>",
]


def _soup(rng: random.Random) -> str:
    return "".join(rng.choice(TOKENS) for _ in range(rng.randint(1, 30)))


def _fuzz(seed: int, count: int = 4000) -> list[str]:
    rng = random.Random(seed)
    return [_soup(rng) for _ in range(count)]


_NAMES = [
    "resourceType",
    "creation",
    "resourceShape",
    "details",
    "allowedValue",
    "title",
    "identifier",
    "Description",
    "CreationFactory",
    "property",
    "ServiceProvider",
    "services",
    "component",
    "configurations",
    "member",
]
_PREFIXES = ["", "a:", "oslc:", "oslc_config:", "jazz_rm:", "x"]
_PARTS = [
    " ",
    "\n",
    ">",
    "/>",
    '"',
    "t",
    "<",
    "x",
    'rdf:resource="u"',
    'rdf:resource=""',
    'rdf:about="U"',
    "rdf:resource=",
]


_ATTRS = ['rdf:resource="u"', 'rdf:about="U"', 'rdf:resource="v" rdf:resource="w"', 'rdf:resource=""', "", "a=1"]
_PARTS += ["<a:ServiceProvider>", "<a:ServiceProvider><title>T</title>", "<a:services", "<a:Description", "U", "T"]


def _element_soup(rng: random.Random) -> str:
    """Soup built from whole opener/closer tags, so that the fuzz reaches the matching branches of every pattern."""
    out = []
    for _ in range(rng.randint(1, 14)):
        name = rng.choice(_NAMES)
        kind = rng.random()
        if kind < 0.45:
            out.append(f"<{rng.choice(_PREFIXES)}{name}")
            out.append(rng.choice([" ", " ", "\n", ""]) + rng.choice(_ATTRS))
        elif kind < 0.65:
            out.append(f"</{rng.choice(_PREFIXES)}{name}>")
        else:
            out.append(rng.choice(_PARTS))
        out.extend(rng.choice(_PARTS) for _ in range(rng.randint(0, 3)))
    return "".join(out)


def _fuzz_elements(seed: int, count: int = 6000) -> list[str]:
    rng = random.Random(seed)
    return [_element_soup(rng) for _ in range(count)]


CASES = SEEDED + _fuzz(88) + _fuzz_elements(881)


def _id(text: str) -> str:
    return repr(text[:40])


# ---- tests ------------------------------------------------------------------------------------------------------
def test_service_providers_equal():
    for text in CASES:
        assert oslc._rm_providers(text) == [{"title": t, "url": u} for t, u in OLD_SP.findall(text)], repr(text)


@pytest.mark.parametrize(
    ("new", "old"),
    [
        (oslc._RM_COMPONENT, OLD_COMPONENT),
        (oslc._RM_CONFIGURATIONS, OLD_CONFIGURATIONS),
        (oslc._RM_MEMBER, OLD_MEMBER),
    ],
)
def test_rm_configuration_search_equal(new, old):
    for text in CASES:
        m = old.search(text)
        assert xmlscan.first_attr(text, new) == (m.group(1) if m else None), repr(text)


@pytest.mark.parametrize("name", ["resourceType", "creation", "resourceShape", "details", "allowedValue"])
def test_attribute_search_and_findall_equal(name):
    old = old_attr(name)
    for text in CASES:
        m = old.search(text)
        assert xmlscan.first_attr(text, name) == (m.group(1) if m else None), repr(text)
        assert xmlscan.all_attrs(text, name) == old.findall(text), repr(text)


@pytest.mark.parametrize("tag_end", [False, True])
def test_blocks_equal(tag_end):
    name, old = ("Description", OLD_DESCRIPTION_BLOCK) if tag_end else ("CreationFactory", OLD_FACTORY)
    for text in CASES:
        assert xmlscan.blocks(text, name, tag_end=tag_end) == old.findall(text), repr(text)


def test_remove_blocks_equal():
    for text in CASES:
        assert xmlscan.remove_blocks(text, "property") == OLD_PROPERTY.sub("", text), repr(text)


@pytest.mark.parametrize(
    ("name", "allow_empty", "old"),
    [("identifier", False, OLD_IDENTIFIER), ("title", False, OLD_TITLE), ("title", True, OLD_TITLE_EMPTY)],
)
def test_element_text_equal(name, allow_empty, old):
    for text in CASES:
        m = old.search(text)
        assert xmlscan.element_text(text, name, allow_empty=allow_empty) == (m.group(1) if m else None), repr(text)


def test_shape_title_equal():
    for text in CASES:
        stripped = OLD_PROPERTY.sub("", text)
        m = OLD_TITLE.search(stripped)
        assert oslc._shape_title(text) == (m.group(1).strip() if m else None), repr(text)


def test_open_tag_span_equal():
    for text in CASES:
        m = OLD_PRIMARY.search(text)
        assert xmlscan.open_tag_span(text, "<jazz_rm:primaryText") == ((m.start(), m.end()) if m else None), repr(text)


def _same_element(el, m):
    if m is None:
        return el is None
    return el is not None and (el.start, el.end, el.open_tag, el.body, el.close_tag) == (
        m.start(),
        m.end(),
        m.group(1),
        m.group(2),
        m.group(3),
    )


@pytest.mark.parametrize("uri", ["U", "https://h/rm/resources/TX_1"])
def test_description_element_equal(uri):
    about = old_description_about(uri)
    for text in CASES:
        assert _same_element(xmlscan.find_element(text, "Description", about=uri), about.search(text)), repr(text)
        assert _same_element(xmlscan.find_element(text, "Description"), OLD_DESCRIPTION_ANY.search(text)), repr(text)


def test_title_element_equal():
    for text in CASES:
        got = xmlscan.find_element(text, "title", boundary=False)
        assert _same_element(got, OLD_TITLE_BLOCK.search(text)), repr(text)


def test_strip_tags_equal():
    for text in CASES:
        assert xmlscan.strip_tags(text) == OLD_TAG.sub("", text), repr(text)


def test_plain_equal_on_comment_text():
    for text in CASES:
        old = re.sub(r"<br\s*/?>|</(?:div|p)>", " ", text, flags=re.IGNORECASE)
        old = " ".join(html.unescape(OLD_TAG.sub("", old)).split())
        assert oslc._plain(text) == old, repr(text)


FILENAME_HEADERS = [
    "attachment; filename*=UTF-8''a%20b.txt",
    "attachment; filename*= utf-8 ''caf%C3%A9.pdf; filename=\"x.pdf\"",
    "attachment; FILENAME*=iso-8859-1'en'na%EFve.txt",
    "attachment; filename*\t=\tUTF-8''tab.txt",
    "attachment; filename*=UTF-8'",
    "attachment; filename*=bad",
    'attachment; filename*=;filename="plain.pdf"',
    'attachment; filename="plain.pdf"',
    "attachment; filename=bare.bin; size=1",
    "filename*=UTF-8''a;b",
]


def test_filename_star_equal_on_legit_headers():
    for header in [*FILENAME_HEADERS, *(c for c in CASES if "filename" in c)]:
        old, new = OLD_STAR.search(header), attachments._FILENAME_STAR.search(header)
        assert (new.groups() if new else None) == (old.groups() if old else None), repr(header)


def test_filename_star_known_divergence_beyond_the_bounds():
    # DIVERGENCE (documented in the audit): a charset or language tag longer than 64 characters, or more than 64
    # blanks around '=', is no longer read as RFC 5987 ``filename*``; the plain ``filename=`` alternative is used.
    header = "attachment; filename*=" + "c" * 65 + "''x.txt"
    assert OLD_STAR.search(header) is not None
    assert attachments._FILENAME_STAR.search(header) is None


def test_the_fuzz_reaches_every_branch():
    # guard against a fuzz that silently stops producing matches (the tokens must still build real elements)
    assert any(OLD_SP.search(t) for t in CASES)
    assert any(old_attr("resourceType").search(t) for t in CASES)
    assert any(OLD_FACTORY.search(t) for t in CASES)
    assert any(OLD_IDENTIFIER.search(t) for t in CASES)
    assert any(OLD_TITLE_BLOCK.search(t) for t in CASES)
    assert any(old_description_about("U").search(t) for t in CASES)
    assert any(OLD_TAG.search(t) for t in CASES)
