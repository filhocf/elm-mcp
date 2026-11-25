"""RED tests for issue #88: every regex fed by a server response (or user content) must run in linear time.

Audit: docs/audit/regex-audit-2026-11-25.md. Each ``TIMING`` case feeds the REAL function that owns the pattern with a
pathological input (unclosed openers repeated, no ``>``, no closing tag) and requires it to finish in under ``LIMIT``
seconds. CI runs with coverage tracing (about 10x slower than a plain run), so the limit is 5 s and the input is sized
so that the CURRENT code needs well over 5 s on a plain run (size in bytes, per case). The ``FUNCTIONAL`` cases prove the
right result on a small legitimate input; they pass today and must keep passing after the fix.

Patterns that already have a timing test elsewhere (not repeated here): ``_MESSAGE_XML`` and ``_NEEDS_TO_BE_SET``
(tests/test_hint_and_add_child.py), the Markdown/HTML sanitizer and ``_unwrap_root_div`` (tests/test_rm_conteudo_loop2.py).
"""

import asyncio
import itertools
import time

import pytest

from elm_mcp import attachments, oslc

LIMIT = 5.0
_PROJECT = itertools.count()

CF = "<oslc:CreationFactory>"
CF_END = "</oslc:CreationFactory>"
RT_RM = '<oslc:resourceType rdf:resource="http://open-services.net/ns/rm#Requirement"/>'
RT_CM = '<oslc:resourceType rdf:resource="http://open-services.net/ns/cm#ChangeRequest"/>'
CREATION = '<oslc:creation rdf:resource="https://h/ccm/oslc/contexts/c/workitems/task"/>'


def rep(unit: str, size: int) -> str:
    return unit * max(1, size // len(unit))


class FakeResponse:
    status_code = 200
    is_success = True
    headers = {"ETag": "etag-1"}  # noqa: RUF012

    def __init__(self, text: str) -> None:
        self.text = text


def _patch_xml(monkeypatch, **fakes):
    """Replace oslc module-level coroutines by ``async def`` returning the given value (or calling it)."""
    for name, value in fakes.items():

        async def fake(*_a, _value=value, **_k):
            return _value(*_a) if callable(_value) else _value

        monkeypatch.setattr(oslc, name, fake)


# ---------------------------------------------------------------------------------------------
# Runners: each patches the I/O edge, then calls the real function. They return the zero-arg thunk to time.
# ---------------------------------------------------------------------------------------------
def r_service_providers(unit):
    def make(mp, size):
        _patch_xml(mp, get_client=None, get_catalog_url="http://catalog")
        _patch_xml(mp, fetch=FakeResponse(rep(unit, size)))
        return lambda: asyncio.run(oslc.list_service_providers("rm"))

    return make


def r_rm_configuration(stage):
    def make(mp, size):
        component = '<oslc_config:component rdf:resource="http://c"/>'
        configurations = '<oslc:configurations rdf:resource="http://cfg"/>'
        bad = {
            0: rep("<oslc_config:component ", size),
            1: rep("<oslc:configurations ", size),
            2: rep("<rdfs:member ", size),
        }[stage]
        _patch_xml(mp, _fetch_service_xml=bad if stage == 0 else component)

        async def get_xml(url):
            if url == "http://c":
                return bad if stage == 1 else configurations
            return bad

        mp.setattr(oslc, "_get_xml_text", get_xml)
        url = f"http://h/rm/oslc_rm/pa-{next(_PROJECT)}/services.xml"
        return lambda: asyncio.run(oslc._find_rm_configuration(url))

    return make


def r_requirement_factory(build):
    def make(mp, size):
        _patch_xml(mp, _get_xml_text=build(size))
        return lambda: asyncio.run(oslc._find_requirement_factory("http://p"))

    return make


def r_extract_identifier(unit):
    return lambda _mp, size: lambda: oslc._extract_identifier(rep(unit, size))


def r_primary_text(unit):
    return lambda _mp, size: lambda: oslc._replace_primary_text(rep(unit, size), "x")


URI = "https://h/rm/resources/TX_1"


def r_update_requirement(build):
    def make(mp, size):
        _patch_xml(mp, get_client=None, fetch=FakeResponse(build(size)), _request=FakeResponse(""))
        return lambda: asyncio.run(oslc.update_requirement(URI, title="new"))

    return make


def r_shape_title(unit):
    return lambda _mp, size: lambda: oslc._shape_title(rep(unit, size))


def r_ccm_factories(build):
    def make(mp, size):
        _patch_xml(mp, _fetch_service_xml=build(size))
        return lambda: asyncio.run(oslc._list_ccm_creation_factories("http://p"))

    return make


def r_resource_shapes(build):
    def make(mp, size):
        _patch_xml(mp, _fetch_service_xml=build(size), _resolve_project_url="http://p", _cached_xml_text="<x/>")
        return lambda: asyncio.run(oslc.list_resource_shapes("rm", "p"))

    return make


def r_allowed_values(build):
    def make(mp, size):
        _patch_xml(mp, _cached_xml_text=build(size))
        return lambda: asyncio.run(oslc._allowed_values("http://a"))

    return make


def r_folders(mp, size):
    _patch_xml(mp, _get_xml_text=rep("<a:details ", size), _resolve_project_url="http://p")
    return lambda: asyncio.run(oslc.list_folders("p"))


def r_plain(unit):
    return lambda _mp, size: lambda: oslc._plain(rep(unit, size))


def r_disposition(unit):
    return lambda _mp, size: lambda: attachments.filename_from_disposition(rep(unit, size))


def _blk(inner):
    return CF + inner + CF_END


# id -> (size in bytes, runner, exception the call raises on this input today, expected value or None)
# size: smallest round figure for which the current code needs > 6 s on a plain run (the local time at 200 KB is in
# the audit table); 200_000 where 200 KB already exceeds 5 s.
TIMING = {
    "oslc.py:264 list_service_providers": (
        250_000,
        r_service_providers("<a:ServiceProvider><title>x</title>"),
        None,
        [],
    ),
    "oslc.py:445 rm component": (660_000, r_rm_configuration(0), None, None),
    "oslc.py:451 rm configurations": (570_000, r_rm_configuration(1), None, None),
    "oslc.py:454 rm member": (350_000, r_rm_configuration(2), None, None),
    "oslc.py:1433 CreationFactory unclosed": (
        220_000,
        r_requirement_factory(lambda n: rep(CF, n)),
        ValueError,
        None,
    ),
    "oslc.py:1434 resourceType": (
        400_000,
        r_requirement_factory(lambda n: _blk(rep("<a:resourceType ", n))),
        ValueError,
        None,
    ),
    "oslc.py:1437 creation": (
        370_000,
        r_requirement_factory(lambda n: _blk(RT_RM + rep("<a:creation ", n))),
        ValueError,
        None,
    ),
    "oslc.py:1494 identifier": (520_000, r_extract_identifier("<a:identifier "), None, None),
    "oslc.py:1807 primaryText": (660_000, r_primary_text("<jazz_rm:primaryText "), ValueError, None),
    "oslc.py:1846 Description (about)": (
        320_000,
        r_update_requirement(lambda n: rep("<rdf:Description ", n)),
        ValueError,
        None,
    ),
    "oslc.py:1852 Description (fallback)": (
        520_000,
        r_update_requirement(lambda n: rep('<rdf:Description rdf:about="https://h/other" ', n)),
        ValueError,
        None,
    ),
    "oslc.py:1860 title": (
        560_000,
        r_update_requirement(
            lambda n: f'<rdf:Description rdf:about="{URI}">' + rep("<dcterms:title ", n) + "</rdf:Description>"
        ),
        None,
        None,
    ),
    "oslc.py:1939 property": (200_000, r_shape_title("<oslc:property>"), None, None),
    "oslc.py:1940 title": (540_000, r_shape_title("<dcterms:title "), None, None),
    "oslc.py:2006 ccm CreationFactory unclosed": (200_000, r_ccm_factories(lambda n: rep(CF, n)), None, []),
    "oslc.py:2007 ccm resourceType": (
        400_000,
        r_ccm_factories(lambda n: _blk(rep("<a:resourceType ", n))),
        None,
        [],
    ),
    "oslc.py:2008 ccm creation": (
        370_000,
        r_ccm_factories(lambda n: _blk(RT_CM + rep("<a:creation ", n))),
        None,
        [],
    ),
    "oslc.py:2014 ccm resourceShape": (
        410_000,
        r_ccm_factories(lambda n: _blk(RT_CM + CREATION + rep("<a:resourceShape ", n))),
        None,
        None,
    ),
    "oslc.py:2015 ccm title": (
        420_000,
        r_ccm_factories(lambda n: _blk(RT_CM + CREATION + rep("<a:title ", n))),
        None,
        None,
    ),
    "oslc.py:2049 shapes CreationFactory unclosed": (200_000, r_resource_shapes(lambda n: rep(CF, n)), None, []),
    "oslc.py:2050 shapes resourceType": (
        400_000,
        r_resource_shapes(lambda n: _blk(rep("<a:resourceType ", n))),
        None,
        [],
    ),
    "oslc.py:2053 shapes resourceShape": (
        430_000,
        r_resource_shapes(lambda n: _blk(RT_RM + rep("<a:resourceShape ", n))),
        None,
        None,
    ),
    "oslc.py:2220 allowed Description no '>'": (
        590_000,
        r_allowed_values(lambda n: rep("<rdf:Description ", n)),
        None,
        [],
    ),
    "oslc.py:2220 allowed Description unclosed": (
        200_000,
        r_allowed_values(lambda n: rep("<rdf:Description>", n)),
        None,
        [],
    ),
    "oslc.py:2222 allowed title": (
        540_000,
        r_allowed_values(
            lambda n: '<rdf:Description rdf:about="u">' + rep("<dcterms:title ", n) + "</rdf:Description>"
        ),
        None,
        None,
    ),
    "oslc.py:2227 allowedValue": (630_000, r_allowed_values(lambda n: rep("<oslc:allowedValue ", n)), None, []),
    "oslc.py:2341 details": (470_000, r_folders, ValueError, None),
    "oslc.py:2603 plain '<'": (200_000, r_plain("<"), None, "<" * 200_000),
    "oslc.py:2603 plain '<a '": (250_000, r_plain("<a "), None, None),
    "attachments.py:30 filename*": (200_000, r_disposition("filename*="), None, None),
}


@pytest.mark.parametrize("case", sorted(TIMING))
def test_pathological_input_is_handled_in_linear_time(monkeypatch, case):
    size, make, raises, expected = TIMING[case]
    run = make(monkeypatch, size)
    start = time.perf_counter()
    if raises is not None:
        with pytest.raises(raises):
            run()
        elapsed = time.perf_counter() - start
    else:
        result = run()
        elapsed = time.perf_counter() - start
        if expected is not None:
            assert result == expected
    assert elapsed < LIMIT, f"{case}: {elapsed:.1f}s for {size} bytes (limit {LIMIT}s)"


# ---------------------------------------------------------------------------------------------
# Functional guards: the legitimate behavior each pattern must keep (passes today, must pass after the fix).
# ---------------------------------------------------------------------------------------------
RM_CATALOG = (
    '<rdf:RDF><oslc:ServiceProviderCatalog rdf:about="c">'
    '<oslc:ServiceProvider rdf:about="a"><oslc:x/></oslc:ServiceProvider>'
    "</oslc:ServiceProviderCatalog></rdf:RDF>"
)


def f_service_providers(mp):
    xml = (
        "<oslc:ServiceProvider><dcterms:title>Alpha</dcterms:title><oslc:details/>"
        '<oslc:services rdf:resource="https://h/rm/alpha/services.xml"/></oslc:ServiceProvider>'
        "<oslc:ServiceProvider>\n <dcterms:title>Beta</dcterms:title>"
        '<oslc:services rdf:resource="https://h/rm/beta/services.xml"/></oslc:ServiceProvider>'
    )
    _patch_xml(mp, get_client=None, get_catalog_url="http://catalog", fetch=FakeResponse(xml))
    got = asyncio.run(oslc.list_service_providers("rm"))
    assert got == [
        {"title": "Alpha", "url": "https://h/rm/alpha/services.xml"},
        {"title": "Beta", "url": "https://h/rm/beta/services.xml"},
    ]


def f_rm_configuration(mp):
    _patch_xml(mp, _fetch_service_xml='<oslc_config:component rdf:resource="http://c"/>')

    async def get_xml(url):
        if url == "http://c":
            return '<x><oslc_config:configurations\n rdf:resource="http://cfg"/></x>'
        return '<x><rdfs:member rdf:resource="http://stream"/></x>'

    mp.setattr(oslc, "_get_xml_text", get_xml)
    url = f"http://h/rm/oslc_rm/pa-{next(_PROJECT)}/services.xml"
    assert asyncio.run(oslc._find_rm_configuration(url)) == "http://stream"


def f_requirement_factory(mp):
    other = _blk(
        '<oslc:resourceType rdf:resource="http://open-services.net/ns/rm#RequirementCollection"/>'
        '<oslc:creation rdf:resource="http://wrong"/>'
    )
    wanted = _blk(RT_RM + '<oslc:creation rdf:resource="http://right"/>')
    _patch_xml(mp, _get_xml_text=other + wanted)
    assert asyncio.run(oslc._find_requirement_factory("http://p")) == "http://right"


def f_extract_identifier(_mp):
    assert (
        oslc._extract_identifier('<rdf:RDF><dcterms:identifier rdf:x="1">1234</dcterms:identifier></rdf:RDF>') == "1234"
    )
    assert oslc._extract_identifier("<a/>") is None


def f_primary_text(_mp):
    body = '<x><jazz_rm:primaryText rdf:parseType="Literal"><div>old</div></jazz_rm:primaryText></x>'
    out = oslc._replace_primary_text(body, "<div>new</div>")
    assert out == '<x><jazz_rm:primaryText rdf:parseType="Literal"><div>new</div></jazz_rm:primaryText></x>'
    empty = oslc._replace_primary_text("<x><jazz_rm:primaryText/></x>", "<b/>")
    assert "<b/></jazz_rm:primaryText>" in empty


def _description(about, inner):
    return f'<rdf:RDF><rdf:Description rdf:about="{about}">{inner}</rdf:Description></rdf:RDF>'


def f_update_requirement(mp):
    puts = []

    async def request(_client, _method, _url, **kwargs):
        puts.append(kwargs["content"].decode())
        return FakeResponse("")

    xml = _description("https://h/rm/resources/OTHER", "<dcterms:title>x</dcterms:title>") + _description(
        URI, "<dcterms:title>old</dcterms:title><dcterms:identifier>7</dcterms:identifier>"
    )
    _patch_xml(mp, get_client=None, fetch=FakeResponse(xml))
    mp.setattr(oslc, "_request", request)
    result = asyncio.run(oslc.update_requirement(URI, title="new & <b>"))
    assert result["uri"] == URI
    sent = puts[0]
    assert "<dcterms:title>new &amp; &lt;b&gt;</dcterms:title>" in sent
    assert "<dcterms:title>x</dcterms:title>" in sent  # the other Description is untouched
    assert sent.count("<dcterms:title>old</dcterms:title>") == 0


def f_update_requirement_fallback(mp):
    puts = []

    async def request(_client, _method, _url, **kwargs):
        puts.append(kwargs["content"].decode())
        return FakeResponse("")

    xml = '<rdf:RDF><rdf:Description rdf:about="https://h/abbrev"><dcterms:title>old</dcterms:title></rdf:Description></rdf:RDF>'
    _patch_xml(mp, get_client=None, fetch=FakeResponse(xml))
    mp.setattr(oslc, "_request", request)
    asyncio.run(oslc.update_requirement(URI, title="new"))
    assert "<dcterms:title>new</dcterms:title>" in puts[0]


def f_shape_title(_mp):
    xml = (
        "<oslc:ResourceShape><oslc:property><oslc:Property><dcterms:title>Prop</dcterms:title></oslc:Property>"
        "</oslc:property><dcterms:title>Task</dcterms:title></oslc:ResourceShape>"
    )
    assert oslc._shape_title(xml) == "Task"
    assert oslc._shape_title("<a/>") is None


CCM_FACTORY = _blk(
    RT_CM
    + '<oslc:creation rdf:resource="https://h/ccm/oslc/contexts/c/workitems/task"/>'
    + '<oslc:resourceShape rdf:resource="https://h/ccm/oslc/context/c/shapes/workitems/task"/>'
    + "<dcterms:title>Location for creation of Task change requests</dcterms:title>"
)


def f_ccm_factories(mp):
    generic = _blk(RT_CM + '<oslc:creation rdf:resource="https://h/ccm/oslc/contexts/c/workitems"/>')
    _patch_xml(mp, _fetch_service_xml=generic + CCM_FACTORY)
    got = asyncio.run(oslc._list_ccm_creation_factories("http://p"))
    assert got == [
        {
            "title": "Task",
            "id": "task",
            "factory_title": "Location for creation of Task change requests",
            "factory_uri": "https://h/ccm/oslc/contexts/c/workitems/task",
            "shape_uri": "https://h/ccm/oslc/context/c/shapes/workitems/task",
            "resource_type": "http://open-services.net/ns/cm#ChangeRequest",
        }
    ]


def f_resource_shapes(mp):
    other = _blk('<oslc:resourceType rdf:resource="http://open-services.net/ns/rm#RequirementCollection"/>')
    wanted = _blk(
        RT_RM + '<oslc:resourceShape rdf:resource="http://s1"/><oslc:resourceShape rdf:resource="http://s2"/>'
    )
    _patch_xml(
        mp,
        _fetch_service_xml=other + wanted,
        _resolve_project_url="http://p",
        _cached_xml_text="<oslc:ResourceShape><dcterms:title>Feature</dcterms:title></oslc:ResourceShape>",
    )
    got = asyncio.run(oslc.list_resource_shapes("rm", "p"))
    assert [(g["title"], g["shape_uri"]) for g in got] == [("Feature", "http://s1"), ("Feature", "http://s2")]


def f_allowed_values(mp):
    xml = (
        '<rdf:RDF><rdf:Description rdf:about="http://v/1"><dcterms:title> One </dcterms:title></rdf:Description>'
        '<rdf:Description rdf:about="http://v/2"/>'
        '<rdf:Description rdf:about="http://container"><oslc:allowedValue rdf:resource="http://v/1"/>'
        '<oslc:allowedValue\n rdf:resource="http://v/2"/></rdf:Description></rdf:RDF>'
    )
    _patch_xml(mp, _cached_xml_text=xml)
    got = asyncio.run(oslc._allowed_values("http://a"))
    assert got == [{"value_uri": "http://v/1", "title": "One"}, {"value_uri": "http://v/2", "title": None}]


def f_folders(mp):
    xml = '<x><oslc:details\n rdf:resource="https://h/rm/process/project-areas/_pa"/></x>'
    _patch_xml(mp, _get_xml_text=xml, _resolve_project_url="http://p", get_client=None)

    async def fetch(_client, _method, _url, _op, **kwargs):
        assert kwargs["params"]["oslc.where"] == "public_rm:parent=https://h/rm/process/project-areas/_pa"

        class Resp:
            @staticmethod
            def json():
                return {}

        return Resp()

    mp.setattr(oslc, "fetch", fetch)
    assert asyncio.run(oslc.list_folders("p")) == []


def f_plain(_mp):
    assert oslc._plain("<div>a &amp; b</div><p>c</p><br/>d <b>e</b>  f") == "a & b c d e f"
    assert oslc._plain("1 < 2") == "1 < 2"  # no closing > : not a tag
    assert oslc._plain(None) == ""


def f_disposition(_mp):
    assert attachments.filename_from_disposition("attachment; filename*=UTF-8''a%20b.txt") == "a b.txt"
    assert attachments.filename_from_disposition('attachment; filename="plain.pdf"') == "plain.pdf"
    assert attachments.filename_from_disposition("attachment; filename=bare.bin; size=1") == "bare.bin"
    assert attachments.filename_from_disposition(None) is None


FUNCTIONAL = {
    "oslc.py:264 list_service_providers": f_service_providers,
    "oslc.py:445-454 rm configuration": f_rm_configuration,
    "oslc.py:1433-1437 requirement factory": f_requirement_factory,
    "oslc.py:1494 identifier": f_extract_identifier,
    "oslc.py:1807 primaryText": f_primary_text,
    "oslc.py:1846-1860 update_requirement": f_update_requirement,
    "oslc.py:1852 update_requirement fallback": f_update_requirement_fallback,
    "oslc.py:1939-1940 shape title": f_shape_title,
    "oslc.py:2006-2015 ccm factories": f_ccm_factories,
    "oslc.py:2049-2053 resource shapes": f_resource_shapes,
    "oslc.py:2220-2227 allowed values": f_allowed_values,
    "oslc.py:2341 folders": f_folders,
    "oslc.py:2603 plain": f_plain,
    "attachments.py:30 disposition": f_disposition,
}


@pytest.mark.parametrize("case", sorted(FUNCTIONAL))
def test_legitimate_input_keeps_its_behavior(monkeypatch, case):
    FUNCTIONAL[case](monkeypatch)
