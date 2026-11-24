"""RED tests for issue #77: ``list_fields`` returns ``title=None`` for the values of enumerated fields.

The real server lists only references in the ``allowedValues`` document
(``<oslc:allowedValue rdf:resource=".../enumerations/{pa}/{enum}/{enum}.literal.l38"/>``); the labels live in the
enumeration document ``GET {origin}/ccm/oslc/enumerations/{pa}/{enum}`` (one ``rdf:Description`` per literal,
label in ``rdfs:label``). Target behavior pinned here:

  * a value without a title whose URI is same-origin and has ``/oslc/enumerations/`` in its path makes the code
    read the enumeration (the URL is BUILT by the code: the value URI minus its last segment) once, through the
    discovery cache, and fill the titles (``rdfs:label``, ``dcterms:title`` also accepted);
  * any failure of that read leaves the titles ``None`` and never fails ``list_fields``;
  * ``set_field`` matches the label; duplicated labels are ambiguous;
  * workflow states and the profile draft are unchanged (no enumeration read).

Tests that only guard unchanged behavior (group 8, the id regression of group 7) pass today on purpose; every
other test fails today because the enumeration is never read.

Mocks: a private respx router per test (an unrouted request raises). Tenant-neutral data only.
"""

import json
import sys
import time
from pathlib import Path

import httpx
import pytest
import respx
from mcp.server.mcpserver.exceptions import ToolError

from elm_mcp import cli, server
from elm_mcp.auth import close_client

from . import f1_fixtures as f
from . import w2_fixtures as w2
from .test_w4_cache import STATES_DOC, WORKFLOW, Srv
from .w2_fixtures import FakeItem

BASE = f.BASE
OTHER_BASE = "https://elm2.example.com"
PA = f.PA
PROJECT = w2.PROJECT
ENUM = "com.example.enumeration.categoria"
FIXTURE = Path(__file__).parent / "fixtures" / "ccm_enumeration.xml"
RDF_NS = (
    'xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#" '
    'xmlns:rdfs="http://www.w3.org/2000/01/rdf-schema#" '
    'xmlns:dcterms="http://purl.org/dc/terms/" '
    'xmlns:oslc="http://open-services.net/ns/core#"'
)


def enum_url(enum: str = ENUM, pa: str = PA, base: str = BASE) -> str:
    return f"{base}/ccm/oslc/enumerations/{pa}/{enum}"


def lit(n: int | str, enum: str = ENUM, pa: str = PA, base: str = BASE) -> str:
    """URI of the literal ``lN`` of an enumeration (real shape: .../{enum}/{enum}.literal.lN)."""
    return f"{enum_url(enum, pa, base)}/{enum}.literal.l{n}"


def enum_doc(
    literals: list[tuple[int | str, str]], *, enum: str = ENUM, pa: str = PA, base: str = BASE, tag="rdfs:label"
):
    """Enumeration document in the ALM shape: a Description per literal with its label in ``tag``."""
    descs = "".join(
        f'<rdf:Description rdf:about="{lit(n, enum, pa, base)}"><{tag}>{title}</{tag}></rdf:Description>'
        for n, title in literals
    )
    container = f'<rdf:Description rdf:about="{enum_url(enum, pa, base)}"><rdfs:label>Enumeration</rdfs:label></rdf:Description>'
    return f"<rdf:RDF {RDF_NS}>{container}{descs}</rdf:RDF>"


def refs_doc(av: str, uris: list[str]) -> str:
    """allowedValues document as the real server serves it: references only, no titles."""
    vals = "".join(f'<oslc:allowedValue rdf:resource="{u}"/>' for u in uris)
    return f'<rdf:RDF {RDF_NS}><rdf:Description rdf:about="{av}">{vals}</rdf:Description></rdf:RDF>'


LABELS = [(38, "Demanda / Projeto"), (33, "Outros Trabalhos"), (10, "Codificação")]


class Env:
    """A CCM server (task type) whose shape properties reference refs-only allowedValues documents."""

    def __init__(self, router: respx.MockRouter, base: str = BASE) -> None:
        self.router = router
        self.base = base
        self.props: list[dict] = []
        self.enum_calls: list[httpx.Request] = []
        self.enum_resp: dict[str, httpx.Response | None] = {}
        swap = self._swap
        router.post(f"{base}/jts/j_security_check").mock(return_value=httpx.Response(200))
        router.get(f"{base}/ccm/rootservices").mock(return_value=httpx.Response(200, text=swap(f.ROOTSERVICES_XML)))
        router.get(f"{base}/ccm/oslc/workitems/catalog").mock(
            return_value=httpx.Response(200, text=swap(w2._catalog_xml()))
        )
        services = swap(w2.services_xml(f.TYPED_FACTORIES[:1]))
        ctx = f"{base}/ccm/oslc/contexts/{PA}"
        router.get(f"{ctx}/services").mock(return_value=httpx.Response(200, text=services))
        router.get(ctx).mock(return_value=httpx.Response(200, text=services))
        router.get(swap(f.shape_uri("task"))).mock(side_effect=self._shape)

    def _swap(self, text: str) -> str:
        return text.replace(BASE, self.base)

    def _shape(self, request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text=self._swap(f.real_shape_xml("task", self.props)))

    def av_url(self, name: str) -> str:
        return f"{self.base}/ccm/oslc/shapes/workitems/task/property/{name}/allowedValues"

    def add_field(self, name: str, uris: list[str], *, doc: str | None = None, required: bool = False) -> str:
        """A shape property whose allowedValues document lists ``uris`` (or is ``doc``). Returns the AV URL."""
        av = self.av_url(name)
        self.props.append(
            f.prop(
                name,
                name.title(),
                "Exactly-one" if required else "Zero-or-one",
                propdef=f"{f.RTC_CM}{name}",
                allowed=av,
            )
        )
        text = doc if doc is not None else refs_doc(av, uris)
        self.router.get(av).mock(return_value=httpx.Response(200, text=text))
        return av

    def serve_enum(self, url: str, text: str | None = None, status: int = 200) -> None:
        """Serve (or re-serve, changing the answer) the enumeration document at ``url``; calls are recorded."""
        first = url not in self.enum_resp
        self.enum_resp[url] = httpx.Response(status, text=text if text is not None else "")
        if first:
            self.router.get(url).mock(side_effect=lambda request, u=url: self._enum(request, u))

    def _enum(self, request: httpx.Request, url: str) -> httpx.Response:
        self.enum_calls.append(request)
        resp = self.enum_resp[url]
        return httpx.Response(resp.status_code, text=resp.text)

    def enum_gets(self, url: str | None = None) -> int:
        return sum(1 for c in self.enum_calls if url is None or str(c.url).split("?")[0] == url)


@pytest.fixture(autouse=True)
async def _isolated(monkeypatch):
    monkeypatch.setattr("elm_mcp.config.settings.elm_url", BASE)
    yield
    await close_client()


@pytest.fixture
def env(w2_router) -> Env:
    return Env(w2_router)


async def _fields(refresh: bool = False) -> dict[str, dict]:
    result = await server.list_fields("ccm", PROJECT, "task", refresh=refresh)
    return {fld["name"]: fld for fld in result["required"] + result["optional"]}


def _titles(field: dict) -> dict[str, str | None]:
    return {v["value_uri"]: v["title"] for v in field["allowed_values"]}


def _categoria_env(env: Env, labels=LABELS) -> str:
    """One enumerated field 'categoria' with refs-only values and a served enumeration. Returns the enum URL."""
    env.add_field("categoria", [lit(n) for n, _ in labels])
    env.serve_enum(enum_url(), enum_doc(labels))
    return enum_url()


# ======================================================================
# 1 + 2. The real case: refs-only allowedValues + enumeration document (sanitized fixture)
# ======================================================================
async def test_list_fields_fills_titles_from_the_enumeration_document_fixture(env):
    """Fixture ccm_enumeration.xml: l38/l33/l10 get their rdfs:label (with '&amp;' decoded); nothing else changes."""
    uris = [lit(38), lit(33), lit(10)]
    env.add_field("categoria", uris)
    env.serve_enum(enum_url(), FIXTURE.read_text(encoding="utf-8"))
    categoria = (await _fields())["categoria"]
    assert _titles(categoria) == {
        lit(38): "Demanda / Projeto",
        lit(33): "Outros Trabalhos",
        lit(10): "Pesquisa & Desenvolvimento",
    }
    assert [v["value_uri"] for v in categoria["allowed_values"]] == uris, "order and URIs are kept"


async def test_the_fixture_is_the_enumeration_document_of_the_test_server():
    """Guard: the fixture URLs match what the helpers build (a wrong fixture would make group 1 meaningless)."""
    text = FIXTURE.read_text(encoding="utf-8")
    for n in (38, 33, 10):
        assert f'rdf:about="{lit(n)}"' in text
    assert "&amp;" in text and "<!DOCTYPE" not in text


async def test_the_enumeration_is_requested_as_rdf_xml_with_the_oslc_version_header(env):
    url = _categoria_env(env)
    await _fields()
    assert env.enum_gets(url) == 1
    request = env.enum_calls[0]
    assert "application/rdf+xml" in request.headers["accept"]
    assert request.headers["oslc-core-version"] == "2.0"


@pytest.mark.parametrize("tag", ["dcterms:title", "rdfs:label"])
async def test_both_label_elements_are_accepted(env, tag):
    env.add_field("categoria", [lit(38)])
    env.serve_enum(enum_url(), enum_doc([(38, "Demanda / Projeto")], tag=tag))
    assert _titles((await _fields())["categoria"]) == {lit(38): "Demanda / Projeto"}


async def test_a_literal_missing_from_the_enumeration_keeps_a_none_title_and_the_others_are_filled(env):
    env.add_field("categoria", [lit(38), lit(99)])
    env.serve_enum(enum_url(), enum_doc([(38, "Demanda / Projeto")]))
    assert _titles((await _fields())["categoria"]) == {lit(38): "Demanda / Projeto", lit(99): None}


async def test_titles_of_two_enumerations_are_read_one_request_each(env):
    other = "com.example.enumeration.outra"
    env.add_field("categoria", [lit(38)])
    env.add_field("outra", [lit(1, other)])
    env.serve_enum(enum_url(), enum_doc([(38, "Demanda / Projeto")]))
    env.serve_enum(enum_url(other), enum_doc([(1, "Um")], enum=other))
    fields = await _fields()
    assert _titles(fields["categoria"]) == {lit(38): "Demanda / Projeto"}
    assert _titles(fields["outra"]) == {lit(1, other): "Um"}
    assert env.enum_gets() == 2


# ======================================================================
# 3. Values that already carry a title do not trigger the enumeration read
# ======================================================================
async def test_values_with_a_title_in_the_allowed_values_document_do_not_read_the_enumeration(env):
    av = env.av_url("categoria")
    descs = "".join(
        f'<rdf:Description rdf:about="{lit(n)}"><dcterms:title>{t}</dcterms:title></rdf:Description>' for n, t in LABELS
    )
    refs = "".join(f'<oslc:allowedValue rdf:resource="{lit(n)}"/>' for n, _ in LABELS)
    doc = f'<rdf:RDF {RDF_NS}><rdf:Description rdf:about="{av}">{refs}</rdf:Description>{descs}</rdf:RDF>'
    env.add_field("categoria", [], doc=doc)
    env.serve_enum(enum_url(), enum_doc([(38, "Other label"), (33, "Other"), (10, "Other")]))
    categoria = (await _fields())["categoria"]
    assert _titles(categoria) == {lit(n): t for n, t in LABELS}, "the title of the allowedValues document wins"
    assert env.enum_gets() == 0, "nothing to fill: no extra request"


async def test_only_the_missing_titles_are_filled_from_the_enumeration(env):
    """Mixed document: l38 has its title in the allowedValues document, l33 does not."""
    av = env.av_url("categoria")
    doc = (
        f'<rdf:RDF {RDF_NS}><rdf:Description rdf:about="{av}">'
        f'<oslc:allowedValue rdf:resource="{lit(38)}"/><oslc:allowedValue rdf:resource="{lit(33)}"/>'
        f'</rdf:Description><rdf:Description rdf:about="{lit(38)}"><dcterms:title>From AV</dcterms:title>'
        "</rdf:Description></rdf:RDF>"
    )
    env.add_field("categoria", [], doc=doc)
    env.serve_enum(enum_url(), enum_doc([(38, "From enumeration"), (33, "Outros Trabalhos")]))
    assert _titles((await _fields())["categoria"]) == {lit(38): "From AV", lit(33): "Outros Trabalhos"}


# ======================================================================
# 4. A failed enumeration read never fails list_fields
# ======================================================================
_ENTITY = '<!DOCTYPE rdf:RDF [<!ENTITY lol "PWNED">]>'
_XXE = '<!DOCTYPE rdf:RDF [<!ENTITY xxe SYSTEM "file:///etc/passwd">]>'
_LAUGHS = (
    '<!DOCTYPE rdf:RDF [<!ENTITY a "aaaaaaaaaa"><!ENTITY b "&a;&a;&a;&a;&a;&a;&a;&a;&a;&a;">'
    '<!ENTITY c "&b;&b;&b;&b;&b;&b;&b;&b;&b;&b;">]>'
)


def _with_dtd(dtd: str, entity: str) -> str:
    return (
        f'<?xml version="1.0"?>{dtd}<rdf:RDF {RDF_NS}>'
        f'<rdf:Description rdf:about="{lit(38)}"><rdfs:label>&{entity};</rdfs:label></rdf:Description></rdf:RDF>'
    )


_BROKEN = [
    pytest.param(404, "not found", id="404"),
    pytest.param(403, "forbidden", id="403"),
    pytest.param(500, "boom", id="500"),
    pytest.param(200, "<rdf:RDF><unclosed>", id="invalid-xml"),
    pytest.param(200, "", id="empty-body"),
    pytest.param(200, _with_dtd(_ENTITY, "lol"), id="dtd-entity"),
    pytest.param(200, _with_dtd(_XXE, "xxe"), id="dtd-external-entity"),
    pytest.param(200, _with_dtd(_LAUGHS, "c"), id="dtd-entity-expansion"),
]


@pytest.mark.parametrize(("status", "body"), _BROKEN)
async def test_a_failed_or_refused_enumeration_leaves_the_titles_none_and_keeps_every_value(env, status, body):
    uris = [lit(38), lit(33), lit(10)]
    env.add_field("categoria", uris)
    env.serve_enum(enum_url(), body, status)
    categoria = (await _fields())["categoria"]  # must not raise
    assert env.enum_gets(enum_url()) >= 1, "the enumeration was attempted (otherwise this proves nothing)"
    assert [v["value_uri"] for v in categoria["allowed_values"]] == uris, "no empty list in place of the values"
    assert all(v["title"] is None for v in categoria["allowed_values"]), "a refused document yields no title"


@pytest.mark.parametrize(("status", "body"), _BROKEN)
async def test_a_failed_enumeration_of_one_field_does_not_spoil_the_other_field(env, status, body):
    other = "com.example.enumeration.outra"
    env.add_field("categoria", [lit(38)])
    env.add_field("outra", [lit(1, other)])
    env.serve_enum(enum_url(), body, status)
    env.serve_enum(enum_url(other), enum_doc([(1, "Um")], enum=other))
    fields = await _fields()
    assert env.enum_gets(enum_url()) >= 1
    assert _titles(fields["categoria"]) == {lit(38): None}
    assert _titles(fields["outra"]) == {lit(1, other): "Um"}


@pytest.mark.parametrize("dtd,entity,leak", [(_ENTITY, "lol", "PWNED"), (_XXE, "xxe", "root:")])
async def test_entity_expansion_never_reaches_a_title(env, dtd, entity, leak):
    env.add_field("categoria", [lit(38)])
    env.serve_enum(enum_url(), _with_dtd(dtd, entity))
    categoria = (await _fields())["categoria"]
    assert env.enum_gets(enum_url()) >= 1
    assert leak not in json.dumps(categoria)
    assert categoria["allowed_values"][0]["title"] is None


# ======================================================================
# 5. Cache: one read per enumeration per TTL period
# ======================================================================
async def test_two_list_fields_calls_read_the_enumeration_once(env):
    url = _categoria_env(env)
    first = await _fields()
    second = await _fields()
    assert _titles(first["categoria"])[lit(38)] == "Demanda / Projeto"
    assert second == first
    assert env.enum_gets(url) == 1


async def test_two_fields_sharing_an_enumeration_read_it_once(env):
    env.add_field("categoria", [lit(38)])
    env.add_field("espelho", [lit(38), lit(33)])
    env.serve_enum(enum_url(), enum_doc(LABELS))
    fields = await _fields()
    assert _titles(fields["espelho"]) == {lit(38): "Demanda / Projeto", lit(33): "Outros Trabalhos"}
    assert env.enum_gets(enum_url()) == 1


async def test_refresh_true_reads_the_enumeration_again_once_and_repopulates(env):
    url = _categoria_env(env)
    await _fields()
    env.serve_enum(url, enum_doc([(38, "Demanda / Projeto (novo)"), (33, "Outros Trabalhos"), (10, "Codificação")]))
    assert _titles((await _fields())["categoria"])[lit(38)] == "Demanda / Projeto", "inside the TTL: cached"
    refreshed = await _fields(refresh=True)
    assert _titles(refreshed["categoria"])[lit(38)] == "Demanda / Projeto (novo)"
    assert env.enum_gets(url) == 2
    assert _titles((await _fields())["categoria"])[lit(38)] == "Demanda / Projeto (novo)"
    assert env.enum_gets(url) == 2, "the refresh repopulated the cache"


async def test_refresh_with_two_fields_on_one_enumeration_reads_it_once(env):
    env.add_field("categoria", [lit(38)])
    env.add_field("espelho", [lit(38)])
    env.serve_enum(enum_url(), enum_doc(LABELS))
    await _fields()
    await _fields(refresh=True)
    assert env.enum_gets(enum_url()) == 2


async def test_ttl_zero_does_not_cache_the_enumeration(env, monkeypatch):
    monkeypatch.setenv("ELM_CACHE_TTL", "0")
    url = _categoria_env(env)
    first = await _fields()
    assert _titles(first["categoria"])[lit(38)] == "Demanda / Projeto"
    await _fields()
    assert env.enum_gets(url) == 2


async def test_the_enumeration_is_read_again_after_the_ttl(env, monkeypatch):
    real = time.monotonic
    offset = [0.0]
    monkeypatch.setattr(time, "monotonic", lambda: real() + offset[0])
    url = _categoria_env(env)
    await _fields()
    offset[0] = 299
    await _fields()
    assert env.enum_gets(url) == 1, "still inside the 300 s default"
    offset[0] = 301
    await _fields()
    assert env.enum_gets(url) == 2


async def test_a_failed_enumeration_read_is_not_cached(env):
    url = _categoria_env(env)
    env.serve_enum(url, "boom", 500)
    assert _titles((await _fields())["categoria"])[lit(38)] is None
    assert env.enum_gets(url) == 1
    env.serve_enum(url, enum_doc(LABELS))
    assert _titles((await _fields())["categoria"])[lit(38)] == "Demanda / Projeto", "the failure was not remembered"
    assert env.enum_gets(url) == 2


async def test_the_cache_key_includes_the_project_area(env):
    """Same enumeration name under two project areas: two reads, two different label sets."""
    pa2 = "_PA2"
    env.add_field("categoria", [lit(38)])
    env.add_field("espelho", [lit(38, pa=pa2)])
    env.serve_enum(enum_url(), enum_doc([(38, "Area one")]))
    env.serve_enum(enum_url(pa=pa2), enum_doc([(38, "Area two")], pa=pa2))
    fields = await _fields()
    assert _titles(fields["categoria"]) == {lit(38): "Area one"}
    assert _titles(fields["espelho"]) == {lit(38, pa=pa2): "Area two"}
    assert env.enum_gets() == 2


async def test_the_cache_key_includes_the_server(w2_router, monkeypatch):
    a = Env(w2_router, BASE)
    a.add_field("categoria", [lit(38)])
    a.serve_enum(enum_url(), enum_doc([(38, "Server A")]))
    b = Env(w2_router, OTHER_BASE)
    b.add_field("categoria", [lit(38, base=OTHER_BASE)])
    b.serve_enum(enum_url(base=OTHER_BASE), enum_doc([(38, "Server B")], base=OTHER_BASE))
    assert _titles((await _fields())["categoria"]) == {lit(38): "Server A"}
    await close_client()
    monkeypatch.setattr("elm_mcp.config.settings.elm_url", OTHER_BASE)
    assert _titles((await _fields())["categoria"]) == {lit(38, base=OTHER_BASE): "Server B"}
    await close_client()
    monkeypatch.setattr("elm_mcp.config.settings.elm_url", BASE)
    assert _titles((await _fields())["categoria"]) == {lit(38): "Server A"}
    assert (a.enum_gets(), b.enum_gets()) == (1, 1), "each server is read once and cached on its own"


# ======================================================================
# 6. Security: only same-origin enumeration URLs the code builds itself; DTD refused; size
# ======================================================================
_FOREIGN = [
    pytest.param("https://evil.example.org", id="other-host"),
    pytest.param("https://elm.example.com:8443", id="other-port"),
    pytest.param("http://elm.example.com", id="other-scheme"),
    pytest.param("https://elm.example.com.evil.example.org", id="suffix-host"),
]


@pytest.mark.parametrize("origin", _FOREIGN)
async def test_a_value_on_another_origin_is_never_fetched(env, origin):
    foreign = f"{origin}/ccm/oslc/enumerations/{PA}/{ENUM}/{ENUM}.literal.l38"
    env.add_field("categoria", [foreign])
    env.serve_enum(f"{origin}/ccm/oslc/enumerations/{PA}/{ENUM}", enum_doc([(38, "Evil")]))
    result = await _fields()  # must not raise either
    assert env.enum_calls == []
    assert result["categoria"]["allowed_values"] == [{"value_uri": foreign, "title": None}]
    assert all(c.request.url.host == "elm.example.com" and c.request.url.port is None for c in env.router.calls)


async def test_a_foreign_value_next_to_a_same_origin_value_only_the_latter_is_resolved(env):
    foreign = f"https://evil.example.org/ccm/oslc/enumerations/{PA}/{ENUM}/{ENUM}.literal.l33"
    env.add_field("categoria", [lit(38), foreign])
    env.serve_enum(enum_url(), enum_doc(LABELS))
    env.serve_enum(f"https://evil.example.org/ccm/oslc/enumerations/{PA}/{ENUM}", enum_doc(LABELS))
    categoria = (await _fields())["categoria"]
    assert _titles(categoria) == {lit(38): "Demanda / Projeto", foreign: None}
    assert [str(c.url) for c in env.enum_calls] == [enum_url()]


_NOT_ENUMERATIONS = [
    pytest.param(f"{BASE}/ccm/oslc/workflows/{PA}/states/{WORKFLOW}/{WORKFLOW}.state.s1", id="workflow-state"),
    pytest.param(f.CAT_2, id="category"),
    pytest.param(f"{BASE}/ccm/admin/enumerations/{PA}/{ENUM}/{ENUM}.literal.l38", id="admin-enumerations"),
    pytest.param(f"{BASE}/ccm/oslc/enumerationsX/{PA}/{ENUM}/{ENUM}.literal.l38", id="lookalike-segment"),
    pytest.param(f"{BASE}/ccm/oslc/shapes/workitems/task/property/x/allowedValues", id="plain-resource"),
]


@pytest.mark.parametrize("value", _NOT_ENUMERATIONS)
async def test_values_outside_oslc_enumerations_trigger_no_read(env, value):
    env.add_field("estado", [value])
    parent = value.rsplit("/", 1)[0]
    env.serve_enum(parent, enum_doc(LABELS))  # a trap: served, must never be asked
    env.serve_enum(value, enum_doc(LABELS))
    result = await _fields()
    assert result["estado"]["allowed_values"] == [{"value_uri": value, "title": None}]
    assert env.enum_calls == []


async def test_the_enumeration_url_is_built_from_the_value_never_taken_from_the_documents(env):
    """The enumeration document tries to redirect the reader (oslc:allowedValue/rdfs:seeAlso to another host)."""
    evil = "https://evil.example.org/steal"
    env.add_field("categoria", [lit(38)])
    doc = (
        f"<rdf:RDF {RDF_NS}>"
        f'<rdf:Description rdf:about="{lit(38)}"><rdfs:label>Demanda / Projeto</rdfs:label>'
        f'<rdfs:seeAlso rdf:resource="{evil}"/></rdf:Description></rdf:RDF>'
    )
    env.serve_enum(enum_url(), doc)
    env.serve_enum(evil, "stolen")
    assert _titles((await _fields())["categoria"]) == {lit(38): "Demanda / Projeto"}
    assert [str(c.url) for c in env.enum_calls] == [enum_url()]


async def test_an_enumeration_with_5000_literals_is_read_in_under_a_second(env):
    labels = [(n, f"Rótulo {n}") for n in range(5000)]
    env.add_field("categoria", [lit(n) for n, _ in labels])
    env.serve_enum(enum_url(), enum_doc(labels))
    start = time.perf_counter()
    categoria = (await _fields())["categoria"]
    elapsed = time.perf_counter() - start
    assert len(categoria["allowed_values"]) == 5000
    assert categoria["allowed_values"][0]["title"] == "Rótulo 0"
    assert categoria["allowed_values"][-1]["title"] == "Rótulo 4999"
    assert elapsed < 1.0, f"{elapsed:.2f}s"
    assert env.enum_gets() == 1


# ======================================================================
# 7. set_field accepts the label of an enumerated field whose allowedValues carry no titles
# ======================================================================
FIELD = "rtc_cm:categoria"


def _item(env: Env, labels=LABELS) -> FakeItem:
    _categoria_env(env, labels)
    return FakeItem(env.router, "100")


async def test_set_field_accepts_the_label_read_from_the_enumeration(env):
    item = _item(env)
    await server.set_field("100", FIELD, "Demanda / Projeto")
    assert item.put_body()[FIELD] == {"rdf:resource": lit(38)}


async def test_set_field_label_match_is_case_and_space_insensitive(env):
    item = _item(env)
    await server.set_field("100", FIELD, "  demanda / PROJETO ")
    assert item.put_body()[FIELD] == {"rdf:resource": lit(38)}


async def test_set_field_label_with_an_escaped_ampersand(env):
    env.add_field("categoria", [lit(10)])
    env.serve_enum(enum_url(), FIXTURE.read_text(encoding="utf-8"))
    item = FakeItem(env.router, "100")
    await server.set_field("100", FIELD, "Pesquisa & Desenvolvimento")
    assert item.put_body()[FIELD] == {"rdf:resource": lit(10)}


async def test_set_field_unknown_label_is_refused_listing_the_valid_labels(env):
    item = _item(env)
    with pytest.raises(ToolError) as exc:
        await server.set_field("100", FIELD, "Inexistente")
    message = str(exc.value)
    for _, label in LABELS:
        assert label in message, f"the valid labels are listed (not the URI tails): {label!r}"
    assert item.puts == []


async def test_set_field_still_accepts_the_literal_id_and_the_full_uri(env):
    item = _item(env)
    await server.set_field("100", FIELD, f"{ENUM}.literal.l38")
    await server.set_field("100", FIELD, lit(33))
    assert [item.put_body(i)[FIELD] for i in range(2)] == [{"rdf:resource": lit(38)}, {"rdf:resource": lit(33)}]


async def test_set_field_by_id_with_the_enumeration_unavailable_still_works_and_a_label_is_refused(env):
    item = _item(env)
    env.serve_enum(enum_url(), "boom", 500)
    await server.set_field("100", FIELD, f"{ENUM}.literal.l38")
    assert item.put_body()[FIELD] == {"rdf:resource": lit(38)}
    assert env.enum_gets(enum_url()) >= 1, "the label lookup was attempted"
    with pytest.raises(ToolError, match="not allowed"):
        await server.set_field("100", FIELD, "Demanda / Projeto")
    assert len(item.puts) == 1


async def test_set_field_reuses_the_cached_enumeration_from_list_fields(env):
    item = _item(env)
    await _fields()
    await server.set_field("100", FIELD, "Outros Trabalhos")
    assert item.put_body()[FIELD] == {"rdf:resource": lit(33)}
    assert env.enum_gets(enum_url()) == 1


# ======================================================================
# 9. Duplicated labels: the label match is ambiguous
# ======================================================================
DUP = [(38, "Duplicado"), (39, "duplicado "), (33, "Outros Trabalhos")]


async def test_set_field_duplicated_label_is_ambiguous_and_lists_the_ids(env):
    item = _item(env, DUP)
    with pytest.raises(ToolError) as exc:
        await server.set_field("100", FIELD, "Duplicado")
    message = str(exc.value)
    assert "ambig" in message.lower()
    assert f"{ENUM}.literal.l38" in message
    assert f"{ENUM}.literal.l39" in message
    assert f"{ENUM}.literal.l33" not in message, "only the candidates are listed"
    assert item.puts == []


async def test_set_field_a_duplicated_label_can_still_be_written_by_id(env):
    item = _item(env, DUP)
    await server.set_field("100", FIELD, f"{ENUM}.literal.l39")
    assert item.put_body()[FIELD] == {"rdf:resource": lit(39)}


async def test_set_field_unique_label_next_to_duplicates_still_resolves(env):
    item = _item(env, DUP)
    await server.set_field("100", FIELD, "Outros Trabalhos")
    assert item.put_body()[FIELD] == {"rdf:resource": lit(33)}


async def test_list_fields_keeps_both_values_when_labels_repeat(env):
    _categoria_env(env, DUP)
    categoria = (await _fields())["categoria"]
    assert [v["title"] for v in categoria["allowed_values"]] == ["Duplicado", "duplicado", "Outros Trabalhos"]


# ======================================================================
# 8. Regression: workflow states and the profile draft do not change
# ======================================================================
async def test_list_workflow_states_trigger_no_enumeration_read(w2_router):
    srv = Srv(w2_router)
    result = await server.list_workflow(PROJECT, "task")
    assert [s["id"] for s in result["states"]] == [f"{WORKFLOW}.state.s1", f"{WORKFLOW}.state.s3"]
    requested = [str(c.request.url).split("?")[0] for c in w2_router.calls]
    assert not [u for u in requested if "/enumerations/" in u]
    assert requested.count(srv.allowed_url) == 1
    assert requested.count(STATES_DOC) == 1


async def test_list_fields_on_workflow_state_values_triggers_no_enumeration_read(w2_router):
    srv = Srv(w2_router)
    result = await server.list_fields("ccm", PROJECT, "task")
    state = next(fld for fld in result["required"] if fld["name"] == "state")
    assert [v["title"] for v in state["allowed_values"]] == [None, None]
    assert not [c for c in w2_router.calls if "/enumerations/" in str(c.request.url)]
    assert srv.allowed_url in {str(c.request.url) for c in w2_router.calls}


def test_profile_draft_is_unchanged_it_reads_no_enumeration(monkeypatch, capsys):
    monkeypatch.setattr("elm_mcp.auth._client", None)
    monkeypatch.setattr(sys, "argv", ["elm-mcp", "profile", "draft", PROJECT])
    with respx.MockRouter(assert_all_called=False) as router:
        w2.mock_ccm(router, f.TYPED_FACTORIES[:1])
        av = f"{BASE}/ccm/oslc/shapes/workitems/task/property/categoria/allowedValues"
        prop = f.prop("categoria", "Categoria", "Exactly-one", propdef=f"{f.RTC_CM}categoria", allowed=av)
        router.get(f.shape_uri("task")).mock(return_value=httpx.Response(200, text=f.real_shape_xml("task", [prop])))
        router.get(av).mock(return_value=httpx.Response(200, text=refs_doc(av, [lit(38), lit(33)])))
        enum_route = router.get(enum_url()).mock(return_value=httpx.Response(200, text=enum_doc(LABELS)))
        cli.main()
    draft = json.loads(capsys.readouterr().out)
    assert enum_route.call_count == 0, "the draft keeps listing the allowed values as they are"
    assert draft["_review"]["task"]["rtc_cm:categoria"]["allowed_values"] == [
        {"value_uri": lit(38), "title": None},
        {"value_uri": lit(33), "title": None},
    ]
