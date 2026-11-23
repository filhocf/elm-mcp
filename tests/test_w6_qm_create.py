"""G3 (RED) tests for SPEC-qm-write, wave W6 (release v0.6.0): ``create_qm_resource``.

    create_qm_resource(project, type, title, description=None, if_exists="error") -> {"uri", "short_id", "created"}

Scope by evidence: TestCase, TestPlan, TestSuite and TestScript are created by TITLE only; TestExecutionRecord,
TestResult and every other type are DEFERRED and must be refused with a ToolError that lists the supported
types and says the rest is deferred, WITHOUT any write request.

The ETM is the stateful fake of tests/w6_fixtures.py (``QmWriteServer``): RDF/XML only, the creation
factories of the real sanitized fixture (resourceType in the ``oslc_qm`` and ``rqm`` namespaces,
TestExecutionRecord first, creation URL of TestPlan/TestScript/TestSuite different from their queryBase),
a POST that accepts only ``dcterms:title`` (+ ``dcterms:description``) and answers 201 + Location with an
EMPTY body, and the W3 query endpoints listing what is stored (they ignore ``oslc.where``).

Pinned design decisions (the developer validates them, see the G3 report):
- ``created`` is True for a new resource; ``short_id`` is a string read from the server by re-reading the
  Location (the POST answer carries no id);
- the POST body holds ONE resource node whose properties are only ``dcterms:title`` and, when given,
  ``dcterms:description`` (an ``rdf:type`` of the requested type is tolerated);
- the duplicate check is a QM query of the SAME type, compared by normalized title (``oslc.normalize_title``).

New symbols are reached by attribute INSIDE each test so each test fails by itself.
"""

import unicodedata

import httpx
import pytest
from mcp.server.mcpserver.exceptions import ToolError

from elm_mcp import server
from elm_mcp.auth import close_client

from . import w6_fixtures as w6
from .w3_fixtures import BASE, DCTERMS, PROJECT, QM_NS, RDF
from .w6_fixtures import QmWriteServer, body_properties, creation_url

TITLE = "Login Flow"


@pytest.fixture(autouse=True)
async def _cfg(monkeypatch):
    monkeypatch.setattr("elm_mcp.config.settings.elm_url", BASE)
    yield
    await close_client()


async def _create(type_name: str = "TestCase", title: str = TITLE, **kwargs) -> dict:
    return await server.create_qm_resource(PROJECT, type_name, title, **kwargs)


def _title_in_body(request: httpx.Request) -> list[str]:
    return [text for tag, _res, text in body_properties(request) if tag == f"{{{DCTERMS}}}title"]


# ======================================================================
# Contract: tool registered, a write tool, agreed parameters
# ======================================================================
async def test_create_qm_resource_is_registered_as_a_write_tool_with_the_agreed_parameters():
    """FAILS today: the tool does not exist. Parameters in order; project, type and title are required."""
    tools = {t.name: t for t in await server.mcp.list_tools()}
    assert "create_qm_resource" in tools
    schema = tools["create_qm_resource"].input_schema or {}
    assert list(schema.get("properties", {})) == ["project", "type", "title", "description", "if_exists"]
    assert set(schema.get("required", [])) == {"project", "type", "title"}
    annotations = tools["create_qm_resource"].annotations
    assert annotations is not None and annotations.read_only_hint is not True


# ======================================================================
# EARS-1: factory by resourceType (local name, two namespaces), POST only the title, result by re-read
# ======================================================================
@pytest.mark.parametrize("type_name", w6.SUPPORTED)
async def test_post_goes_to_the_factory_of_the_requested_type(w2_router, type_name):
    """TestCase/TestPlan/TestScript are ``oslc_qm`` types, TestSuite is ``rqm``; TestExecutionRecord is FIRST in the file."""
    qm = QmWriteServer(w2_router)
    await _create(type_name)
    assert [str(r.url) for r in qm.posts] == [creation_url(type_name)]


@pytest.mark.parametrize("type_name", w6.SUPPORTED)
async def test_factory_is_found_by_resource_type_not_by_its_title_or_position(w2_router, type_name):
    """Every factory is TITLED after the NEXT type: only the ``oslc:resourceType`` tells them apart."""
    qm = QmWriteServer(w2_router, swap_titles=True)
    await _create(type_name)
    assert [str(r.url) for r in qm.posts] == [creation_url(type_name)]


async def test_the_post_is_rdf_xml_and_never_asks_for_json(w2_router):
    qm = QmWriteServer(w2_router)
    await _create()
    post = qm.posts[0]
    assert "rdf+xml" in post.headers["content-type"]
    assert "json" not in post.headers.get("accept", "")
    assert qm.json_requests() == []


async def test_the_post_body_has_only_the_title(w2_router):
    qm = QmWriteServer(w2_router)
    await _create()
    props = [(tag, res) for tag, res, _text in body_properties(qm.posts[0]) if tag != f"{{{RDF}}}type"]
    assert props == [(f"{{{DCTERMS}}}title", None)]
    assert _title_in_body(qm.posts[0]) == [TITLE]


async def test_the_post_body_adds_the_description_only_when_given(w2_router):
    qm = QmWriteServer(w2_router)
    await _create(description="Checks the login")
    tags = sorted(tag for tag, _res, _text in body_properties(qm.posts[0]) if tag != f"{{{RDF}}}type")
    assert tags == [f"{{{DCTERMS}}}description", f"{{{DCTERMS}}}title"]
    texts = {tag: text for tag, _res, text in body_properties(qm.posts[0])}
    assert texts[f"{{{DCTERMS}}}description"] == "Checks the login"


async def test_the_post_declares_no_other_type_than_the_requested_one(w2_router):
    qm = QmWriteServer(w2_router)
    await _create("TestPlan")
    types = [res for tag, res, _t in body_properties(qm.posts[0]) if tag == f"{{{RDF}}}type"]
    assert all(t.endswith("#TestPlan") for t in types)


async def test_result_comes_from_re_reading_the_created_resource(w2_router):
    """The POST answers 201 + Location with an EMPTY body: ``short_id`` can only come from a GET of the Location."""
    qm = QmWriteServer(w2_router)
    result = await _create()
    location = f"{creation_url('TestCase')}/_N001"
    assert result["uri"] == location
    assert result["short_id"] == "5001"
    assert result["created"] is True
    assert qm.order.index(f"GET {location}") > qm.order.index("POST")


# ======================================================================
# Factory discovery failures: nothing is guessed, nothing is written
# ======================================================================
async def test_type_without_a_creation_factory_is_refused_without_a_post(w2_router):
    qm = QmWriteServer(w2_router, factory_types=["TestExecutionRecord", "TestPlan", "TestSuite"])
    with pytest.raises(ToolError):
        await _create("TestCase")
    assert qm.posts == []


async def test_two_factories_for_the_same_type_are_ambiguous_and_nothing_is_posted(w2_router):
    qm = QmWriteServer(w2_router, duplicate_factory_for="TestCase")
    with pytest.raises(ToolError):
        await _create("TestCase")
    assert qm.posts == []


async def test_a_factory_outside_the_elm_origin_is_never_called(w2_router):
    """Same origin: a services.xml that points the factory to another host must not receive the POST (nor the session)."""
    qm = QmWriteServer(w2_router, foreign_factory_for="TestCase")
    with pytest.raises(ToolError):
        await _create("TestCase")
    assert qm.evil.call_count == 0
    assert qm.posts == []


async def test_unknown_project_is_refused_without_a_post(w2_router):
    qm = QmWriteServer(w2_router)
    with pytest.raises(ToolError):
        await server.create_qm_resource("No Such Project", "TestCase", TITLE)
    assert qm.posts == []


# ======================================================================
# Deferred / unsupported types
# ======================================================================
@pytest.mark.parametrize("type_name", [*w6.DEFERRED, "TestScriptStep", "Keyword", "NoSuchType"])
async def test_deferred_or_unsupported_type_is_refused_listing_the_supported_ones(w2_router, type_name):
    qm = QmWriteServer(w2_router)
    with pytest.raises(ToolError) as exc:
        await _create(type_name)
    message = str(exc.value)
    for supported in w6.SUPPORTED:
        assert supported in message
    assert any(word in message.lower() for word in ("adiad", "deferred", "postponed"))
    assert qm.writes() == []


# ======================================================================
# Title / description sanitation
# ======================================================================
async def test_xml_injection_in_the_title_stays_text(w2_router):
    """``</dcterms:title><...>`` must not close the element: the body keeps ONE title and no extra property."""
    qm = QmWriteServer(w2_router)
    evil = 'A</dcterms:title><oslc_qm:validatesRequirement rdf:resource="https://evil.example.net/x"/><dcterms:title>B'
    result = await _create(title=evil)
    props = body_properties(qm.posts[0])
    assert [t for t, _r, _x in props if t != f"{{{RDF}}}type"] == [f"{{{DCTERMS}}}title"]
    assert not any(t == f"{{{QM_NS}}}validatesRequirement" for t, _r, _x in props)
    assert _title_in_body(qm.posts[0]) == [evil]
    assert result["created"] is True


async def test_markup_characters_in_title_and_description_are_escaped(w2_router):
    qm = QmWriteServer(w2_router)
    await _create(title="a < b & c > d", description="5 < 6 & 7 <b>x</b>")
    texts = {tag: text for tag, _r, text in body_properties(qm.posts[0])}
    assert texts[f"{{{DCTERMS}}}title"] == "a < b & c > d"
    assert texts[f"{{{DCTERMS}}}description"] == "5 < 6 & 7 <b>x</b>"


async def test_control_characters_are_removed_from_title_and_description(w2_router):
    qm = QmWriteServer(w2_router)
    await _create(title="A\x00B\x07C\x1bD\tE\nF", description="x\x00y\x08z")
    texts = {tag: text for tag, _r, text in body_properties(qm.posts[0])}
    for value in (texts[f"{{{DCTERMS}}}title"], texts[f"{{{DCTERMS}}}description"]):
        assert not [c for c in value if unicodedata.category(c) == "Cc"]
    assert all(letter in texts[f"{{{DCTERMS}}}title"] for letter in "ABCDEF")
    assert "x" in texts[f"{{{DCTERMS}}}description"] and "z" in texts[f"{{{DCTERMS}}}description"]


@pytest.mark.parametrize("title", ["", "   ", "\x00\x07", "\t\n"])
async def test_title_empty_after_sanitation_is_refused_without_a_post(w2_router, title):
    qm = QmWriteServer(w2_router)
    with pytest.raises(ToolError):
        await _create(title=title)
    assert qm.posts == []


# ======================================================================
# Idempotency: if_exists = error | return | create (SPEC-idempotencia on the QM query of W3)
# ======================================================================
def _with_existing(qm: QmWriteServer, *, short_id: str = "777") -> str:
    qm.add("TestCase", "_X01", "Other one", short_id="1")
    qm.add("TestCase", "_X02", "Another one", short_id="2")
    qm.add("TestCase", "_X03", "Third one", short_id="3")
    qm.add("TestCase", "_X04", "Fourth one", short_id="4")
    return qm.add("TestCase", "_X05", TITLE, short_id=short_id)  # beyond the first query pages (server cap: 2)


async def test_new_title_creates_and_says_so(w2_router):
    qm = QmWriteServer(w2_router)
    _with_existing(qm)
    result = await _create(title="Brand new")
    assert result["created"] is True
    assert len(qm.posts) == 1


async def test_duplicate_check_queries_the_requested_type_only(w2_router):
    qm = QmWriteServer(w2_router)
    await _create("TestPlan", "Plan A")
    assert qm.queried_types() == {"TestPlan"}


async def test_duplicate_is_refused_by_default_naming_the_existing_resource(w2_router):
    qm = QmWriteServer(w2_router)
    existing = _with_existing(qm)
    with pytest.raises(ToolError) as exc:
        await _create()
    assert existing in str(exc.value)
    assert qm.posts == []


@pytest.mark.parametrize("variant", ["  login   FLOW  ", "LOGIN FLOW", "login flow"])
async def test_duplicate_title_is_compared_normalized(w2_router, variant):
    qm = QmWriteServer(w2_router)
    _with_existing(qm)
    with pytest.raises(ToolError):
        await _create(title=variant)
    assert qm.posts == []


@pytest.mark.parametrize("variant", ["Login Flow 2", "LoginFlow", "Login"])
async def test_a_different_title_is_not_a_duplicate(w2_router, variant):
    qm = QmWriteServer(w2_router)
    _with_existing(qm)
    result = await _create(title=variant)
    assert result["created"] is True


async def test_same_title_of_another_type_is_not_a_duplicate(w2_router):
    qm = QmWriteServer(w2_router)
    qm.add("TestPlan", "_P01", TITLE)
    result = await _create("TestCase")
    assert result["created"] is True
    assert len(qm.posts) == 1


async def test_if_exists_return_hands_back_the_existing_one_without_writing(w2_router):
    qm = QmWriteServer(w2_router)
    existing = _with_existing(qm, short_id="777")
    result = await _create(if_exists="return")
    assert result["uri"] == existing
    assert result["short_id"] == "777"
    assert result["created"] is False
    assert qm.posts == []


async def test_if_exists_create_skips_the_check_and_creates_anyway(w2_router):
    qm = QmWriteServer(w2_router)
    _with_existing(qm)
    result = await _create(if_exists="create")
    assert result["created"] is True
    assert len(qm.posts) == 1
    assert qm.queried_types() == set()


@pytest.mark.parametrize("mode", ["", "ERROR", "replace", "none"])
async def test_invalid_if_exists_is_refused_before_any_request(w2_router, mode):
    qm = QmWriteServer(w2_router)
    with pytest.raises(ToolError):
        await _create(if_exists=mode)
    assert qm.requests == []


async def test_failed_duplicate_check_refuses_the_creation(w2_router):
    qm = QmWriteServer(w2_router, query_status=500)
    with pytest.raises(ToolError):
        await _create()
    assert qm.posts == []


async def test_failed_duplicate_check_does_not_stop_if_exists_create(w2_router):
    qm = QmWriteServer(w2_router, query_status=500)
    result = await _create(if_exists="create")
    assert result["created"] is True
    assert len(qm.posts) == 1


async def test_timeout_after_the_post_requeries_and_never_resends(w2_router):
    """The server created it but never answered: the title is queried again, found as NEW, POST sent once."""
    qm = QmWriteServer(w2_router, post_timeout="stored")
    result = await _create()
    assert len(qm.posts) == 1, "the POST must never be resent after an ambiguous failure"
    assert len(qm.query_requests["TestCase"]) >= 2, "the duplicate must be queried again after the timeout"
    assert result["uri"] == f"{creation_url('TestCase')}/_N001"
    assert result["created"] is True


async def test_timeout_after_the_post_with_nothing_found_is_an_error_and_is_not_resent(w2_router):
    qm = QmWriteServer(w2_router, post_timeout="lost")
    with pytest.raises(ToolError):
        await _create()
    assert len(qm.posts) == 1
    assert len(qm.query_requests["TestCase"]) >= 2


async def test_timeout_after_the_post_with_a_failing_requery_is_an_error_and_is_not_resent(w2_router):
    qm = QmWriteServer(w2_router, post_timeout="stored", query_status_after_post=500)
    with pytest.raises(ToolError):
        await _create()
    assert len(qm.posts) == 1


# ======================================================================
# Gate diagnosis on the POST
# ======================================================================
async def test_post_refused_by_a_process_rule_names_the_gate_and_repasses_the_message(w2_router):
    qm = QmWriteServer(
        w2_router,
        post_status=403,
        post_body="<oslc:message>CRRRS6404E: creation blocked by the process</oslc:message>",
    )
    with pytest.raises(ToolError) as exc:
        await _create()
    assert "[regra-de-processo]" in str(exc.value)
    assert "CRRRS6404E: creation blocked by the process" in str(exc.value)
    assert len(qm.posts) == 1
