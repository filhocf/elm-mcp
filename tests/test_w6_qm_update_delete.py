"""G3 (RED) tests for SPEC-qm-write, wave W6 (release v0.6.0): ``update_qm_resource`` and ``delete_qm_resource``.

    update_qm_resource(uri, title=None, description=None)
    delete_qm_resource(uri)

Update: the resource is re-read (RDF/XML, ETag from the header), ONLY the title and/or description change,
and the COMPLETE RDF goes back in a PUT with ``If-Match``. A 412 is a ``[conflito]`` ToolError (no blind
retry with a fresh ETag). The real sanitized TestCase of tests/fixtures/qm_testcase_one.xml is the seed: it
has links (validatesRequirement, testsChangeRequest, usesTestScript), rich-text sections, a template, a
category... that a partial PUT would lose.

Delete: DELETE, then a confirming read: 404/410 confirms; a still-readable resource or a denied (401/403)
confirmation read is an error, the latter saying the deletion was NOT confirmed.

Pinned design decisions (the developer validates them, see the G3 report):
- nothing to change (both None) and a blank title are refused before any request;
- a resource without ETag is never written (an unconditional PUT would overwrite concurrent edits);
- these tools only touch QM resources of the configured server: a URI of another origin, or of another
  domain (an RM requirement), is refused before any request; a resource of a deferred type (TestResult,
  TestExecutionRecord) is not updated.

New symbols are reached by attribute INSIDE each test so each test fails by itself.
"""

import re

import pytest
from mcp.server.mcpserver.exceptions import ToolError

from elm_mcp import server
from elm_mcp.auth import close_client

from . import w6_fixtures as w6
from .w3_fixtures import BASE, DCTERMS, rm_uri
from .w6_fixtures import QmWriteServer, creation_url, literal, triples

URI = w6.TESTCASE_FIXTURE_URI
TITLE_TAG = f"{{{DCTERMS}}}title"
NOT_CONFIRMED = re.compile(r"not confirmed|não confirmada|nao confirmada", re.IGNORECASE)


@pytest.fixture(autouse=True)
async def _cfg(monkeypatch):
    monkeypatch.setattr("elm_mcp.config.settings.elm_url", BASE)
    yield
    await close_client()


def _seeded(router, **kwargs) -> QmWriteServer:
    qm = QmWriteServer(router, **kwargs)
    qm.add_fixture_testcase()
    return qm


# ======================================================================
# update: complete PUT with If-Match, only title/description change
# ======================================================================
async def test_update_title_rereads_then_puts_the_whole_resource_with_if_match(w2_router):
    qm = _seeded(w2_router)
    etag = qm.etag(URI)
    result = await server.update_qm_resource(URI, title="Renamed case")
    assert qm.order.index(f"GET {URI}") < qm.order.index("PUT")
    assert len(qm.puts) == 1
    put = qm.puts[0]
    assert put.headers["if-match"] == etag
    assert "rdf+xml" in put.headers["content-type"]
    assert literal(qm.doc(URI), URI, DCTERMS, "title") == "Renamed case"
    assert result["uri"] == URI
    assert qm.json_requests() == []


async def test_update_changes_only_the_title_everything_else_is_preserved(w2_router):
    """Links, template, category, rich-text sections, dates, counters: the PUT body is the COMPLETE resource."""
    qm = _seeded(w2_router)
    before = triples(qm.doc(URI))
    await server.update_qm_resource(URI, title="Renamed case")
    after = triples(qm.doc(URI))
    subject_before = {t for t in before if t[0] == URI and t[1] != TITLE_TAG}
    subject_after = {t for t in after if t[0] == URI and t[1] != TITLE_TAG}
    assert subject_after == subject_before
    assert {t for t in after if t[0] != URI} == {t for t in before if t[0] != URI}
    assert (URI, TITLE_TAG, "Renamed case") in after


async def test_update_description_only_keeps_the_title(w2_router):
    qm = _seeded(w2_router)
    before = triples(qm.doc(URI))
    await server.update_qm_resource(URI, description="Checks the login flow")
    assert literal(qm.doc(URI), URI, DCTERMS, "title") == "Test case 1"
    assert literal(qm.doc(URI), URI, DCTERMS, "description") == "Checks the login flow"
    keep = (f"{{{DCTERMS}}}description",)
    assert triples(qm.doc(URI), skip=keep) == triples_without(before, keep)


def triples_without(items: set[tuple[str, str, str]], props: tuple[str, ...]) -> set[tuple[str, str, str]]:
    return {t for t in items if t[1] not in props}


async def test_update_adds_the_description_to_a_resource_that_has_none(w2_router):
    qm = QmWriteServer(w2_router)
    uri = qm.add("TestPlan", "_P01", "Plan A")
    await server.update_qm_resource(uri, description="Scope of the plan")
    assert literal(qm.doc(uri), uri, DCTERMS, "description") == "Scope of the plan"
    assert literal(qm.doc(uri), uri, DCTERMS, "title") == "Plan A"


async def test_update_title_and_description_in_one_put(w2_router):
    qm = _seeded(w2_router)
    await server.update_qm_resource(URI, title="T2", description="D2")
    assert len(qm.puts) == 1
    assert literal(qm.doc(URI), URI, DCTERMS, "title") == "T2"
    assert literal(qm.doc(URI), URI, DCTERMS, "description") == "D2"


@pytest.mark.parametrize("type_name", w6.SUPPORTED)
async def test_update_works_for_every_supported_type(w2_router, type_name):
    qm = QmWriteServer(w2_router)
    uri = qm.add(type_name, "_S01", "Old title")
    await server.update_qm_resource(uri, title="New title")
    assert literal(qm.doc(uri), uri, DCTERMS, "title") == "New title"
    assert len(qm.puts) == 1


async def test_update_without_anything_to_change_is_refused_before_any_request(w2_router):
    qm = _seeded(w2_router)
    with pytest.raises(ToolError):
        await server.update_qm_resource(URI)
    assert qm.requests == []


@pytest.mark.parametrize("title", ["", "  ", "\x00\x07"])
async def test_update_with_a_blank_title_is_refused_without_a_put(w2_router, title):
    qm = _seeded(w2_router)
    with pytest.raises(ToolError):
        await server.update_qm_resource(URI, title=title)
    assert qm.puts == []


async def test_update_xml_injection_in_the_title_stays_text(w2_router):
    qm = _seeded(w2_router)
    evil = 'A</dcterms:title><oslc_qm:validatesRequirement rdf:resource="https://evil.example.net/x"/><dcterms:title>B'
    await server.update_qm_resource(URI, title=evil)
    assert literal(qm.doc(URI), URI, DCTERMS, "title") == evil
    assert w6.link_targets(qm.doc(URI), URI, "validatesRequirement") == [w6.FIXTURE_REQUIREMENT]


async def test_update_removes_control_characters(w2_router):
    qm = _seeded(w2_router)
    await server.update_qm_resource(URI, title="A\x00B\x07C", description="x\x1by")
    assert literal(qm.doc(URI), URI, DCTERMS, "title") == "ABC"
    assert literal(qm.doc(URI), URI, DCTERMS, "description") == "xy"


async def test_update_with_a_stale_etag_is_a_conflict_and_is_not_retried(w2_router):
    qm = _seeded(w2_router, change_after_get=True)
    before = qm.doc(URI)
    with pytest.raises(ToolError) as exc:
        await server.update_qm_resource(URI, title="Renamed case")
    assert "[conflito]" in str(exc.value)
    assert len(qm.puts) == 1, "a 412 must not be retried with a fresh ETag"
    assert qm.doc(URI) == before


async def test_update_without_an_etag_never_writes(w2_router):
    qm = _seeded(w2_router, hide_etag=True)
    with pytest.raises(ToolError):
        await server.update_qm_resource(URI, title="Renamed case")
    assert qm.puts == []


async def test_update_of_a_missing_resource_names_the_404_gate(w2_router):
    qm = QmWriteServer(w2_router)
    with pytest.raises(ToolError) as exc:
        await server.update_qm_resource(URI, title="Renamed case")
    assert "[recurso-inexistente-ou-invisivel]" in str(exc.value)
    assert qm.puts == []


async def test_update_refused_by_a_process_rule_repasses_the_server_message(w2_router):
    qm = _seeded(w2_router, put_status=403, put_body="<oslc:message>CRRRS6404E: locked by the process</oslc:message>")
    with pytest.raises(ToolError) as exc:
        await server.update_qm_resource(URI, title="Renamed case")
    assert "[regra-de-processo]" in str(exc.value)
    assert "CRRRS6404E: locked by the process" in str(exc.value)
    assert len(qm.puts) == 1


async def test_update_denied_without_a_message_names_the_permission_gate(w2_router):
    _seeded(w2_router, put_status=403)
    with pytest.raises(ToolError) as exc:
        await server.update_qm_resource(URI, title="Renamed case")
    assert "[permissao]" in str(exc.value)


@pytest.mark.parametrize("type_name", w6.DEFERRED)
async def test_update_of_a_deferred_type_is_refused_without_a_put(w2_router, type_name):
    qm = QmWriteServer(w2_router)
    uri = qm.add(type_name, "_D01", "Deferred thing")
    with pytest.raises(ToolError):
        await server.update_qm_resource(uri, title="New title")
    assert qm.puts == []


async def test_update_outside_the_elm_origin_calls_nothing(w2_router):
    qm = _seeded(w2_router)
    with pytest.raises(ToolError):
        await server.update_qm_resource("https://evil.example.net/qm/oslc_qm/contexts/_PA1/resources/x/_1", title="T")
    assert qm.evil.call_count == 0
    assert qm.requests == []


async def test_update_refuses_a_resource_of_another_domain(w2_router):
    qm = _seeded(w2_router)
    with pytest.raises(ToolError):
        await server.update_qm_resource(rm_uri("_REQ1"), title="T")
    assert qm.requests == []


# ======================================================================
# delete: DELETE + confirming re-read (404/410)
# ======================================================================
def _gone(router, **kwargs) -> tuple[QmWriteServer, str]:
    qm = QmWriteServer(router, **kwargs)
    return qm, qm.add("TestCase", "_G01", "To be deleted")


async def test_delete_removes_the_resource_and_confirms_by_re_reading_it(w2_router):
    qm, uri = _gone(w2_router)
    result = await server.delete_qm_resource(uri)
    assert result["uri"] == uri
    assert result["deleted"] is True
    assert len(qm.deletes) == 1
    assert qm.order.index("DELETE") < len(qm.order) - 1
    assert qm.order[-1] == f"GET {uri}", "the last request must be the confirming read"


async def test_delete_confirms_with_410_too(w2_router):
    qm, uri = _gone(w2_router, after_delete_status=410)
    result = await server.delete_qm_resource(uri)
    assert result["deleted"] is True
    assert len(qm.deletes) == 1


@pytest.mark.parametrize("status", [401, 403])
async def test_delete_with_a_denied_confirming_read_says_the_deletion_is_not_confirmed(w2_router, status):
    qm, uri = _gone(w2_router, after_delete_status=status)
    with pytest.raises(ToolError) as exc:
        await server.delete_qm_resource(uri)
    assert NOT_CONFIRMED.search(str(exc.value))
    assert len(qm.deletes) == 1


async def test_delete_that_leaves_the_resource_readable_is_an_error(w2_router):
    qm, uri = _gone(w2_router, after_delete_status=200)
    with pytest.raises(ToolError):
        await server.delete_qm_resource(uri)
    assert len(qm.deletes) == 1


async def test_delete_refused_by_a_process_rule_names_the_gate(w2_router):
    _qm, uri = _gone(w2_router, delete_status=403, delete_body="<oslc:message>CRRRS6404E: cannot delete</oslc:message>")
    with pytest.raises(ToolError) as exc:
        await server.delete_qm_resource(uri)
    assert "[regra-de-processo]" in str(exc.value)
    assert "CRRRS6404E: cannot delete" in str(exc.value)


async def test_delete_of_a_missing_resource_names_the_404_gate(w2_router):
    qm = QmWriteServer(w2_router)
    with pytest.raises(ToolError) as exc:
        await server.delete_qm_resource(creation_url("TestCase") + "/_NOPE")
    assert "[recurso-inexistente-ou-invisivel]" in str(exc.value)
    assert qm.json_requests() == []


async def test_delete_outside_the_elm_origin_calls_nothing(w2_router):
    qm = QmWriteServer(w2_router)
    with pytest.raises(ToolError):
        await server.delete_qm_resource("https://evil.example.net/qm/oslc_qm/contexts/_PA1/resources/x/_1")
    assert qm.evil.call_count == 0
    assert qm.requests == []


async def test_delete_never_touches_a_resource_of_another_domain(w2_router):
    """``delete_qm_resource`` must not become a way to delete an RM requirement."""
    qm = QmWriteServer(w2_router)
    with pytest.raises(ToolError):
        await server.delete_qm_resource(rm_uri("_REQ1"))
    assert qm.requests == []
