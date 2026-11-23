"""G3 (RED) tests for SPEC-qm-write, wave W6 (release v0.6.0): ``link_test_case`` and ``unlink_test_case``.

    link_test_case(test_case, target, link_type)
    unlink_test_case(test_case, target, link_type)      link_type in {"validatesRequirement", "testsChangeRequest"}

The TestCase is re-read (RDF/XML + ETag), the link is added (or removed) PRESERVING every other property and
link, the COMPLETE RDF goes back in a PUT with ``If-Match``, and the re-read must show the final set of
that link type == the expected set. The seed is the real sanitized TestCase of tests/fixtures/qm_testcase_one.xml
(one requirement, one work item and one test script already linked, rich-text sections, template, category...).

Pinned design decisions (the developer validates them, see the G3 report):
- the new link element is in the QM namespace (``oslc_qm:<link_type>``), like the ones the server writes;
- ``validatesRequirement`` takes an RM URI and ``testsChangeRequest`` a CCM work item URI; any other
  target (another link kind, a QM URI) is refused; target and test case must be on the configured server;
- the source must be a TestCase (not a plan, suite, script or result) and a QM resource;
- linking a target that is already linked leaves the set unchanged (no duplicate); unlinking a target that
  is not linked is an error without a PUT (same as ``unlink_artifacts``);
- a resource without ETag is never written, and a 412 is a ``[conflito]`` error that is not retried.

New symbols are reached by attribute INSIDE each test so each test fails by itself.
"""

import pytest
from mcp.server.mcpserver.exceptions import ToolError

from elm_mcp import server
from elm_mcp.auth import close_client

from . import w6_fixtures as w6
from .w3_fixtures import BASE, QM_NS, member_uri, rm_uri, wi_uri
from .w6_fixtures import QmWriteServer, link_targets, triples

URI = w6.TESTCASE_FIXTURE_URI
REQ = rm_uri("_REQ1")
WI = wi_uri("100")
VALIDATES = f"{{{QM_NS}}}validatesRequirement"
TESTS = f"{{{QM_NS}}}testsChangeRequest"


@pytest.fixture(autouse=True)
async def _cfg(monkeypatch):
    monkeypatch.setattr("elm_mcp.config.settings.elm_url", BASE)
    yield
    await close_client()


def _seeded(router, **kwargs) -> QmWriteServer:
    qm = QmWriteServer(router, **kwargs)
    qm.add_fixture_testcase()
    return qm


def _links(qm: QmWriteServer, link: str) -> list[str]:
    return link_targets(qm.doc(URI), URI, link)


# ======================================================================
# link: acrescenta, preserva o resto, PUT completo com If-Match, confirma na releitura
# ======================================================================
async def test_link_a_requirement_adds_it_next_to_the_existing_one(w2_router):
    qm = _seeded(w2_router)
    await server.link_test_case(URI, REQ, "validatesRequirement")
    assert sorted(_links(qm, "validatesRequirement")) == sorted([w6.FIXTURE_REQUIREMENT, REQ])


async def test_link_a_work_item_adds_it_next_to_the_existing_one(w2_router):
    qm = _seeded(w2_router)
    await server.link_test_case(URI, WI, "testsChangeRequest")
    assert sorted(_links(qm, "testsChangeRequest")) == sorted([w6.FIXTURE_WORKITEM, WI])


async def test_link_preserves_every_other_property_and_link(w2_router):
    """The only difference in the stored resource is the new triple: usesTestScript, template, sections... survive."""
    qm = _seeded(w2_router)
    before = triples(qm.doc(URI))
    await server.link_test_case(URI, REQ, "validatesRequirement")
    after = triples(qm.doc(URI))
    assert after - before == {(URI, VALIDATES, REQ)}
    assert before - after == set()
    assert _links(qm, "usesTestScript") == [w6.FIXTURE_SCRIPT]
    assert _links(qm, "testsChangeRequest") == [w6.FIXTURE_WORKITEM]


async def test_link_rereads_then_puts_with_if_match_then_confirms_by_re_reading(w2_router):
    qm = _seeded(w2_router)
    etag = qm.etag(URI)
    await server.link_test_case(URI, REQ, "validatesRequirement")
    assert len(qm.puts) == 1
    assert qm.puts[0].headers["if-match"] == etag
    assert "rdf+xml" in qm.puts[0].headers["content-type"]
    assert qm.order.index(f"GET {URI}") < qm.order.index("PUT")
    assert qm.order[-1] == f"GET {URI}", "the last request must be the confirming re-read"
    assert qm.json_requests() == []


async def test_link_on_a_test_case_without_links(w2_router):
    qm = QmWriteServer(w2_router)
    uri = qm.add("TestCase", "_C01", "Bare case")
    await server.link_test_case(uri, REQ, "validatesRequirement")
    assert link_targets(qm.doc(uri), uri, "validatesRequirement") == [REQ]


async def test_linking_an_already_linked_target_does_not_duplicate_it(w2_router):
    qm = _seeded(w2_router)
    await server.link_test_case(URI, w6.FIXTURE_REQUIREMENT, "validatesRequirement")
    assert _links(qm, "validatesRequirement") == [w6.FIXTURE_REQUIREMENT]


async def test_link_that_the_server_swallows_is_not_reported_as_done(w2_router):
    """200 on the PUT but the re-read does not show the link: conjunto final != esperado."""
    qm = _seeded(w2_router, put_transform=lambda old, new: old)
    with pytest.raises(ToolError):
        await server.link_test_case(URI, REQ, "validatesRequirement")
    assert len(qm.puts) == 1


async def test_link_whose_final_set_has_an_unexpected_target_is_an_error(w2_router):
    injected = '<oslc_qm:validatesRequirement rdf:resource="https://elm.example.com/rm/resources/_SURPRISE"/>'

    def add_surprise(old: str, new: str) -> str:
        return new.replace("<oslc_qm:validatesRequirement", injected + "<oslc_qm:validatesRequirement", 1)

    qm = _seeded(w2_router, put_transform=add_surprise)
    with pytest.raises(ToolError):
        await server.link_test_case(URI, REQ, "validatesRequirement")
    assert len(qm.puts) == 1


# ======================================================================
# unlink: só aquele alvo
# ======================================================================
async def test_unlink_removes_only_that_target(w2_router):
    qm = _seeded(w2_router)
    await server.link_test_case(URI, REQ, "validatesRequirement")
    await server.unlink_test_case(URI, w6.FIXTURE_REQUIREMENT, "validatesRequirement")
    assert _links(qm, "validatesRequirement") == [REQ]
    assert _links(qm, "testsChangeRequest") == [w6.FIXTURE_WORKITEM]
    assert _links(qm, "usesTestScript") == [w6.FIXTURE_SCRIPT]


async def test_unlink_preserves_every_other_property_and_link(w2_router):
    qm = _seeded(w2_router)
    before = triples(qm.doc(URI))
    await server.unlink_test_case(URI, w6.FIXTURE_WORKITEM, "testsChangeRequest")
    after = triples(qm.doc(URI))
    assert before - after == {(URI, TESTS, w6.FIXTURE_WORKITEM)}
    assert after - before == set()


async def test_unlink_rereads_then_puts_with_if_match_then_confirms_by_re_reading(w2_router):
    qm = _seeded(w2_router)
    etag = qm.etag(URI)
    await server.unlink_test_case(URI, w6.FIXTURE_REQUIREMENT, "validatesRequirement")
    assert len(qm.puts) == 1
    assert qm.puts[0].headers["if-match"] == etag
    assert qm.order[-1] == f"GET {URI}"
    assert _links(qm, "validatesRequirement") == []


async def test_unlinking_a_target_that_is_not_linked_is_an_error_without_a_put(w2_router):
    qm = _seeded(w2_router)
    with pytest.raises(ToolError):
        await server.unlink_test_case(URI, REQ, "validatesRequirement")
    assert qm.puts == []


async def test_unlink_that_the_server_ignores_is_not_reported_as_done(w2_router):
    qm = _seeded(w2_router, put_transform=lambda old, new: old)
    with pytest.raises(ToolError):
        await server.unlink_test_case(URI, w6.FIXTURE_REQUIREMENT, "validatesRequirement")
    assert len(qm.puts) == 1


# ======================================================================
# Validation before any request
# ======================================================================
@pytest.mark.parametrize("tool", ["link_test_case", "unlink_test_case"])
@pytest.mark.parametrize("link_type", ["usesTestScript", "validatesrequirement", "tracksRequirement", "", "foo"])
async def test_invalid_link_type_is_refused_before_any_request(w2_router, tool, link_type):
    qm = _seeded(w2_router)
    with pytest.raises(ToolError):
        await getattr(server, tool)(URI, REQ, link_type)
    assert qm.requests == []


@pytest.mark.parametrize("tool", ["link_test_case", "unlink_test_case"])
@pytest.mark.parametrize("target", ["", "   "])
async def test_empty_target_is_refused_without_a_put(w2_router, tool, target):
    qm = _seeded(w2_router)
    with pytest.raises(ToolError):
        await getattr(server, tool)(URI, target, "validatesRequirement")
    assert qm.puts == []


@pytest.mark.parametrize("tool", ["link_test_case", "unlink_test_case"])
async def test_target_outside_the_elm_origin_is_never_called_nor_written(w2_router, tool):
    qm = _seeded(w2_router)
    with pytest.raises(ToolError):
        await getattr(server, tool)(URI, "https://evil.example.net/rm/resources/_REQ1", "validatesRequirement")
    assert qm.evil.call_count == 0
    assert qm.puts == []


@pytest.mark.parametrize("tool", ["link_test_case", "unlink_test_case"])
async def test_test_case_outside_the_elm_origin_is_never_called(w2_router, tool):
    qm = _seeded(w2_router)
    with pytest.raises(ToolError):
        await getattr(server, tool)(
            "https://evil.example.net/qm/oslc_qm/contexts/_PA1/resources/x/_1", REQ, "validatesRequirement"
        )
    assert qm.evil.call_count == 0
    assert qm.requests == []


@pytest.mark.parametrize("tool", ["link_test_case", "unlink_test_case"])
async def test_test_case_that_is_not_a_qm_resource_is_refused_without_a_request(w2_router, tool):
    qm = _seeded(w2_router)
    with pytest.raises(ToolError):
        await getattr(server, tool)(rm_uri("_REQ9"), REQ, "validatesRequirement")
    assert qm.requests == []


@pytest.mark.parametrize(
    ("target", "link_type"),
    [
        (WI, "validatesRequirement"),
        (REQ, "testsChangeRequest"),
        (member_uri("TestCase", "_ID001"), "validatesRequirement"),
        (member_uri("TestCase", "_ID001"), "testsChangeRequest"),
    ],
)
async def test_target_of_the_wrong_kind_for_the_link_type_is_refused_without_a_put(w2_router, target, link_type):
    qm = _seeded(w2_router)
    with pytest.raises(ToolError):
        await server.link_test_case(URI, target, link_type)
    assert qm.puts == []


@pytest.mark.parametrize("type_name", ["TestPlan", "TestSuite", "TestScript", "TestResult", "TestExecutionRecord"])
async def test_source_that_is_not_a_test_case_is_refused_without_a_put(w2_router, type_name):
    qm = QmWriteServer(w2_router)
    uri = qm.add(type_name, "_O01", "Not a test case")
    with pytest.raises(ToolError):
        await server.link_test_case(uri, REQ, "validatesRequirement")
    assert qm.puts == []


async def test_unlink_source_that_is_not_a_test_case_is_refused_without_a_put(w2_router):
    qm = QmWriteServer(w2_router)
    uri = qm.add("TestPlan", "_O01", "Not a test case", links={"oslc_qm:validatesRequirement": [REQ]})
    with pytest.raises(ToolError):
        await server.unlink_test_case(uri, REQ, "validatesRequirement")
    assert qm.puts == []


# ======================================================================
# Conflict, missing ETag and gate diagnosis
# ======================================================================
@pytest.mark.parametrize("tool", ["link_test_case", "unlink_test_case"])
async def test_stale_etag_is_a_conflict_and_is_not_retried(w2_router, tool):
    qm = _seeded(w2_router, change_after_get=True)
    before = qm.doc(URI)
    target = REQ if tool == "link_test_case" else w6.FIXTURE_REQUIREMENT
    with pytest.raises(ToolError) as exc:
        await getattr(server, tool)(URI, target, "validatesRequirement")
    assert "[conflito]" in str(exc.value)
    assert len(qm.puts) == 1, "a 412 must not be retried with a fresh ETag"
    assert qm.doc(URI) == before


async def test_link_without_an_etag_never_writes(w2_router):
    qm = _seeded(w2_router, hide_etag=True)
    with pytest.raises(ToolError):
        await server.link_test_case(URI, REQ, "validatesRequirement")
    assert qm.puts == []


async def test_link_of_a_missing_test_case_names_the_404_gate(w2_router):
    qm = QmWriteServer(w2_router)
    with pytest.raises(ToolError) as exc:
        await server.link_test_case(URI, REQ, "validatesRequirement")
    assert "[recurso-inexistente-ou-invisivel]" in str(exc.value)
    assert qm.puts == []


async def test_link_refused_by_a_process_rule_repasses_the_server_message(w2_router):
    qm = _seeded(w2_router, put_status=403, put_body="<oslc:message>CRRRS6404E: links are frozen</oslc:message>")
    with pytest.raises(ToolError) as exc:
        await server.link_test_case(URI, REQ, "validatesRequirement")
    assert "[regra-de-processo]" in str(exc.value)
    assert "CRRRS6404E: links are frozen" in str(exc.value)
    assert len(qm.puts) == 1
