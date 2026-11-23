"""G5 loop 2 (RED) for W6 (QM write): URI anchoring, pre-PUT invariants, post-create Location, size limits, LOW items.

Fixtures are neutral (elm.example.com). No network: respx only.
"""

import pytest
from mcp.server.mcpserver.exceptions import ToolError

from elm_mcp import oslc, qm_write, server
from elm_mcp.auth import close_client

from . import w6_fixtures as w6
from .w3_fixtures import BASE, PROJECT, QM_NS, rm_uri, wi_uri
from .w6_fixtures import QmWriteServer, creation_url, link_targets, literal

URI = w6.TESTCASE_FIXTURE_URI
REQ = rm_uri("_REQ1")
CTX = f"{BASE}/qm/oslc_qm/contexts/p/resources"
VALIDATES = f"{{{QM_NS}}}validatesRequirement"

BAD_URIS = [
    f"{CTX}/x/..",
    f"{CTX}/../..",
    f"{BASE}/qm/oslc_qm/contexts/../resources/a/b",
    f"{BASE}/ccm/foo/qm/oslc_qm/contexts/p/resources/a/b",
    f"{BASE}/rm/x/qm/oslc_qm/contexts/p/resources/a/b",
    f"{CTX}/a/%2e%2e",
    f"{CTX}/a/%2E%2e",
    f"{CTX}/a/b%2Fc",
    f"{CTX}/a\\b",
    f"{CTX}/a/.",
]


@pytest.fixture(autouse=True)
async def _cfg(monkeypatch):
    monkeypatch.setattr("elm_mcp.config.settings.elm_url", BASE)
    yield
    await close_client()


def _seeded(router, **kwargs) -> QmWriteServer:
    qm = QmWriteServer(router, **kwargs)
    qm.add_fixture_testcase()
    return qm


# ---- 1. anchored URI check ------------------------------------------------------------------
@pytest.mark.parametrize("uri", BAD_URIS)
def test_check_qm_uri_refuses_traversal_and_unanchored_paths(uri):
    with pytest.raises(ValueError):
        qm_write._check_qm_uri(uri)


def test_check_qm_uri_accepts_a_normal_resource_with_dots_in_the_id():
    uri = f"{CTX}/com.ibm.rqm.planning.VersionedTestCase/_ID.1"
    assert qm_write._check_qm_uri(uri) == uri
    assert qm_write._check_qm_uri(URI) == URI


@pytest.mark.parametrize("uri", BAD_URIS)
async def test_tools_refuse_bad_uris_without_any_request(w2_router, uri):
    qm = _seeded(w2_router)
    before = len(qm.requests)
    with pytest.raises(ToolError):
        await server.update_qm_resource(uri, title="X")
    with pytest.raises(ToolError):
        await server.delete_qm_resource(uri)
    with pytest.raises(ToolError):
        await server.link_test_case(uri, REQ, "validatesRequirement")
    with pytest.raises(ToolError):
        await server.unlink_test_case(uri, REQ, "validatesRequirement")
    assert len(qm.requests) == before


# ---- 2. invariants before the PUT -----------------------------------------------------------
async def test_update_aborts_before_the_put_when_the_edit_breaks_the_title(w2_router, monkeypatch):
    qm = _seeded(w2_router)
    monkeypatch.setattr(qm_write, "_set_text_edit", lambda doc, node, ns, local, text: (0, 0, b"<!-- x -->"))
    with pytest.raises(ToolError):
        await server.update_qm_resource(URI, title="Renamed")
    assert qm.puts == []


async def test_link_aborts_before_the_put_when_the_edit_adds_nothing(w2_router, monkeypatch):
    qm = _seeded(w2_router)
    monkeypatch.setattr(qm_write, "_insert_child", lambda doc, parent, element: (0, 0, b""))
    with pytest.raises(ToolError):
        await server.link_test_case(URI, REQ, "validatesRequirement")
    assert qm.puts == []


async def test_unlink_aborts_before_the_put_when_the_edit_removes_nothing(w2_router, monkeypatch):
    qm = QmWriteServer(w2_router)
    uri = qm.add("TestCase", "_O01", "Case", links={"oslc_qm:validatesRequirement": [REQ]})
    monkeypatch.setattr(qm_write, "_remove_edit", lambda doc, el: (0, 0, b""))
    with pytest.raises(ToolError):
        await server.unlink_test_case(uri, REQ, "validatesRequirement")
    assert qm.puts == []


async def test_update_confirms_by_re_reading_and_fails_when_the_server_swallows_it(w2_router):
    qm = _seeded(w2_router, put_transform=lambda old, new: old)
    with pytest.raises(ToolError):
        await server.update_qm_resource(URI, title="Renamed case")
    assert len(qm.puts) == 1


async def test_update_re_reads_after_the_put_when_all_is_well(w2_router):
    qm = _seeded(w2_router)
    await server.update_qm_resource(URI, title="Renamed case", description="New text")
    assert literal(qm.doc(URI), URI, w6.DCTERMS, "title") == "Renamed case"
    assert qm.order.count(f"GET {URI}") == 2


# ---- 3. Location outside the origin after a 201 -----------------------------------------------
async def test_location_outside_the_origin_reports_the_url_and_does_not_recreate(w2_router):
    qm = QmWriteServer(w2_router, post_status=201, post_headers={"Location": "https://evil.example.net/x/1"})
    with pytest.raises(ToolError) as exc:
        await server.create_qm_resource(PROJECT, "TestCase", "Case with odd location")
    assert "https://evil.example.net/x/1" in str(exc.value)
    assert "may exist" in str(exc.value)
    assert "do not recreate" in str(exc.value)
    assert len(qm.posts) == 1


async def test_201_without_location_says_it_may_exist_and_is_not_resent(w2_router):
    qm = QmWriteServer(w2_router, post_status=201)
    with pytest.raises(ToolError) as exc:
        await server.create_qm_resource(PROJECT, "TestCase", "Case without location")
    assert "may exist" in str(exc.value)
    assert len(qm.posts) == 1


# ---- 4. size limits ---------------------------------------------------------------------------
async def test_title_over_255_is_refused_in_create_and_update_without_requests(w2_router):
    qm = _seeded(w2_router)
    before = len(qm.requests)
    with pytest.raises(ToolError, match="255"):
        await server.create_qm_resource(PROJECT, "TestCase", "t" * 256)
    with pytest.raises(ToolError, match="255"):
        await server.update_qm_resource(URI, title="t" * 256)
    assert len(qm.requests) == before and qm.posts == [] and qm.puts == []


async def test_description_over_32768_is_refused_in_create_and_update_without_requests(w2_router):
    qm = _seeded(w2_router)
    before = len(qm.requests)
    with pytest.raises(ToolError, match="32768"):
        await server.create_qm_resource(PROJECT, "TestCase", "ok", description="d" * 32769)
    with pytest.raises(ToolError, match="32768"):
        await server.update_qm_resource(URI, description="d" * 32769)
    assert len(qm.requests) == before and qm.posts == [] and qm.puts == []


def test_limits_accept_the_exact_maximum():
    assert len(qm_write.clean_title("t" * 255)) == 255
    assert len(qm_write.clean_description("d" * 32768)) == 32768


async def test_update_with_title_of_exactly_255_works(w2_router):
    qm = _seeded(w2_router)
    await server.update_qm_resource(URI, title="t" * 255)
    assert literal(qm.doc(URI), URI, w6.DCTERMS, "title") == "t" * 255


# ---- 5. LOW items -------------------------------------------------------------------------------
async def test_title_as_an_attribute_is_refused_without_a_put(w2_router):
    qm = QmWriteServer(w2_router)
    uri = qm.add("TestCase", "_A01", "Case")
    qm.store[uri]["doc"] = qm.doc(uri).replace(f'rdf:about="{uri}">', f'rdf:about="{uri}" dcterms:title="Attr">', 1)
    with pytest.raises(ToolError):
        await server.update_qm_resource(uri, title="N")
    assert qm.puts == []


async def test_unlink_is_refused_when_a_reified_statement_points_at_the_target(w2_router):
    qm = QmWriteServer(w2_router)
    uri = qm.add("TestCase", "_S01", "Case", links={"oslc_qm:validatesRequirement": [REQ]})
    stmt = f'<rdf:Statement><rdf:subject rdf:resource="{uri}"/><rdf:object rdf:resource="{REQ}"/></rdf:Statement></rdf:RDF>'
    qm.store[uri]["doc"] = qm.doc(uri).replace("</rdf:RDF>", stmt)
    with pytest.raises(ToolError, match="Statement"):
        await server.unlink_test_case(uri, REQ, "validatesRequirement")
    assert qm.puts == []


@pytest.mark.parametrize(
    ("target", "link_type"),
    [
        (f"{BASE}/rm/%2e%2e/qm/x", "validatesRequirement"),
        (f"{BASE}/rm/../../jts/x", "validatesRequirement"),
        (f"{BASE}/rm/../ccm/x", "validatesRequirement"),
        (f"{BASE}/rm/a\\b", "validatesRequirement"),
        (f"{BASE}/ccm/../jts/com.ibm.team.workitem.WorkItem/1", "testsChangeRequest"),
        (f"{BASE}/ccm/%2e%2e/x/com.ibm.team.workitem.WorkItem/1", "testsChangeRequest"),
    ],
)
async def test_targets_with_dots_or_percent_are_refused_without_a_put(w2_router, target, link_type):
    qm = _seeded(w2_router)
    with pytest.raises(ToolError):
        await server.link_test_case(URI, target, link_type)
    assert qm.puts == []


def test_normal_targets_still_pass_the_target_check():
    qm_write._check_target(rm_uri("_REQ1"), "validatesRequirement")
    qm_write._check_target(wi_uri("100"), "testsChangeRequest")


def test_doc_text_decodes_numeric_entities():
    data = b'<r:RDF xmlns:r="http://www.w3.org/1999/02/22-rdf-syntax-ns#"><r:Description><x xmlns="urn:x">caf&#233; &#x4e2d; &amp;amp;</x></r:Description></r:RDF>'
    doc = qm_write._scan(data)
    assert doc.text(doc.root.children[0].children[0]) == "café 中 &amp;"


@pytest.mark.parametrize("tool", ["update", "link", "unlink"])
async def test_weak_etag_is_refused_without_a_put(w2_router, tool):
    qm = _seeded(w2_router, weak_etag=True)
    with pytest.raises(ToolError, match="(?i)weak"):
        if tool == "update":
            await server.update_qm_resource(URI, title="N")
        elif tool == "link":
            await server.link_test_case(URI, REQ, "validatesRequirement")
        else:
            await server.unlink_test_case(URI, REQ, "validatesRequirement")
    assert qm.puts == []


async def test_truncated_duplicate_query_fails_closed(w2_router, monkeypatch):
    qm = QmWriteServer(w2_router)

    async def truncated(*_a, **_k):
        return [], 5000, True

    monkeypatch.setattr("elm_mcp.qm.query_members", truncated)
    with pytest.raises(ToolError, match="truncated"):
        await server.create_qm_resource(PROJECT, "TestCase", "Some case")
    assert qm.posts == []


def test_unused_imports_guard():
    assert oslc and creation_url and link_targets and VALIDATES
