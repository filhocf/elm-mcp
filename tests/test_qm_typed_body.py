"""RED tests for issue #78: ``create_qm_resource`` must POST ONE TYPED node, not a bare ``rdf:Description``.

Live evidence (issue #78): the ETM answers 400 to an untyped ``<rdf:Description>`` and accepts a typed node.
TestCase/TestPlan/TestScript -> ``{oslc_qm}Type``; TestSuite -> ``{rqm}TestSuite``. The node is anonymous
(``rdf:about=""``) and its children are ``dcterms:title`` (+ ``dcterms:description`` when given).

``StrictTypedServer`` is the stateful W6 fake with ONE extra rule, copied from the live behavior: a POST whose
resource node is not the typed node of the factory's type is refused with 400. Synthetic fixtures only.
"""

import unicodedata
from xml.etree import ElementTree as ET

import httpx
import pytest
from mcp.server.mcpserver.exceptions import ToolError

from elm_mcp import qm_write, server
from elm_mcp.auth import close_client

from . import w6_fixtures as w6
from .w3_fixtures import BASE, DCTERMS, PROJECT, QM_NS, RDF, RQM
from .w6_fixtures import QmWriteServer, creation_url

TITLE = "Login Flow"

# Qualified element name the ETM requires for each supported type.
TYPED = {
    "TestCase": f"{{{QM_NS}}}TestCase",
    "TestPlan": f"{{{QM_NS}}}TestPlan",
    "TestScript": f"{{{QM_NS}}}TestScript",
    "TestSuite": f"{{{RQM}}}TestSuite",
}
ABOUT = f"{{{RDF}}}about"


class StrictTypedServer(QmWriteServer):
    """W6 fake that, like the live ETM, answers 400 to a body whose node is not the typed node of the type."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.rejected: list[httpx.Request] = []

    def _post(self, request: httpx.Request, type_name: str) -> httpx.Response:
        try:
            root = ET.fromstring(request.content.decode("utf-8"))
        except (ET.ParseError, UnicodeDecodeError):
            root = None
        nodes = [] if root is None else (list(root) if root.tag == f"{{{RDF}}}RDF" else [root])
        if len(nodes) != 1 or nodes[0].tag != TYPED[type_name]:
            self.requests.append(request)
            self.posts.append(request)
            self.rejected.append(request)
            return httpx.Response(400, text="resource node must be typed")
        return super()._post(request, type_name)


@pytest.fixture(autouse=True)
async def _cfg(monkeypatch):
    monkeypatch.setattr("elm_mcp.config.settings.elm_url", BASE)
    yield
    await close_client()


async def _create(type_name: str = "TestCase", title: str = TITLE, **kwargs) -> dict:
    return await server.create_qm_resource(PROJECT, type_name, title, **kwargs)


def _nodes(request: httpx.Request) -> list[ET.Element]:
    root = ET.fromstring(request.content.decode("utf-8"))
    return list(root) if root.tag == f"{{{RDF}}}RDF" else [root]


def _texts(node: ET.Element, local: str) -> list[str]:
    return [c.text or "" for c in node if c.tag == f"{{{DCTERMS}}}{local}"]


# ======================================================================
# 1. The body is ONE typed anonymous node
# ======================================================================
@pytest.mark.parametrize("type_name", w6.SUPPORTED)
async def test_post_body_is_one_typed_node_about_empty(w2_router, type_name):
    """FAILS today: the node is ``rdf:Description`` without ``rdf:about``."""
    qm = QmWriteServer(w2_router)
    await _create(type_name)
    nodes = _nodes(qm.posts[0])
    assert len(nodes) == 1
    assert nodes[0].tag == TYPED[type_name]
    assert nodes[0].get(ABOUT) == ""


@pytest.mark.parametrize("type_name", w6.SUPPORTED)
async def test_typed_node_children_are_only_title_and_description(w2_router, type_name):
    """FAILS today (wrong element name); the children themselves are already right."""
    qm = QmWriteServer(w2_router)
    await _create(type_name, description="Checks the login")
    node = _nodes(qm.posts[0])[0]
    assert node.tag == TYPED[type_name]
    assert sorted(c.tag for c in node) == [f"{{{DCTERMS}}}description", f"{{{DCTERMS}}}title"]
    assert _texts(node, "title") == [TITLE]
    assert _texts(node, "description") == ["Checks the login"]


@pytest.mark.parametrize("type_name", w6.SUPPORTED)
async def test_typed_node_has_no_description_when_none_given(w2_router, type_name):
    """FAILS today (wrong element name)."""
    qm = QmWriteServer(w2_router)
    await _create(type_name)
    node = _nodes(qm.posts[0])[0]
    assert node.tag == TYPED[type_name]
    assert [c.tag for c in node] == [f"{{{DCTERMS}}}title"]


@pytest.mark.parametrize("type_name", w6.SUPPORTED)
async def test_typed_body_escapes_markup_and_drops_control_characters(w2_router, type_name):
    """FAILS today (wrong element name); escaping and control-character removal are asserted on the typed node."""
    qm = QmWriteServer(w2_router)
    await _create(type_name, title="a < b & c > d\x00\x07", description="5 < 6 & <b>x</b>\x1b")
    node = _nodes(qm.posts[0])[0]
    assert node.tag == TYPED[type_name]
    assert _texts(node, "title") == ["a < b & c > d"]
    assert _texts(node, "description") == ["5 < 6 & <b>x</b>"]
    for value in (*_texts(node, "title"), *_texts(node, "description")):
        assert not [c for c in value if unicodedata.category(c) == "Cc"]


async def test_xml_injection_in_the_title_stays_text_inside_the_typed_node(w2_router):
    """FAILS today (wrong element name)."""
    qm = QmWriteServer(w2_router)
    evil = 'A</dcterms:title><oslc_qm:validatesRequirement rdf:resource="https://evil.example.net/x"/><dcterms:title>B'
    await _create("TestPlan", title=evil)
    node = _nodes(qm.posts[0])[0]
    assert node.tag == TYPED["TestPlan"]
    assert [c.tag for c in node] == [f"{{{DCTERMS}}}title"]
    assert _texts(node, "title") == [evil]


# ======================================================================
# 2. Server that, like the live ETM, refuses the untyped node
# ======================================================================
@pytest.mark.parametrize("type_name", w6.SUPPORTED)
async def test_create_succeeds_against_a_server_that_refuses_untyped_bodies(w2_router, type_name):
    """FAILS today: the fake answers 400 to ``rdf:Description`` and the tool raises a ToolError."""
    qm = StrictTypedServer(w2_router)
    result = await _create(type_name)
    assert qm.rejected == []
    assert len(qm.posts) == 1
    assert set(result) == {"uri", "short_id", "created"}
    assert result["created"] is True
    assert result["uri"] == f"{creation_url(type_name)}/_N001"
    assert result["short_id"] == "5001"


async def test_the_strict_fake_refuses_a_testsuite_in_the_oslc_qm_namespace(w2_router):
    """Guards the per-type map: ``oslc_qm:TestSuite`` is wrong (TestSuite lives in ``rqm``), the fake must say 400."""
    StrictTypedServer(w2_router)
    body = (
        f'<rdf:RDF xmlns:rdf="{RDF}" xmlns:dcterms="{DCTERMS}" xmlns:oslc_qm="{QM_NS}">'
        '<oslc_qm:TestSuite rdf:about=""><dcterms:title>x</dcterms:title></oslc_qm:TestSuite></rdf:RDF>'
    )
    async with httpx.AsyncClient() as client:
        wrong = await client.post(
            creation_url("TestSuite"), content=body, headers={"Content-Type": "application/rdf+xml"}
        )
        right = await client.post(
            creation_url("TestSuite"),
            content=body.replace("oslc_qm:TestSuite", "rqm:TestSuite").replace(QM_NS, RQM).replace("oslc_qm", "rqm"),
            headers={"Content-Type": "application/rdf+xml"},
        )
    assert wrong.status_code == 400
    assert right.status_code == 201


async def test_testsuite_body_uses_the_rqm_namespace_not_oslc_qm(w2_router):
    """FAILS today. Explicit: the TestSuite element must be ``{rqm}TestSuite`` and never ``{oslc_qm}TestSuite``."""
    qm = StrictTypedServer(w2_router)
    await _create("TestSuite")
    assert qm.rejected == []
    assert _nodes(qm.posts[0])[0].tag == f"{{{RQM}}}TestSuite"
    assert _nodes(qm.posts[0])[0].tag != f"{{{QM_NS}}}TestSuite"


# ======================================================================
# 3. Limits and refusals unchanged (no request at all before them)
# ======================================================================
@pytest.mark.parametrize("title", ["", "   ", "\x00\x07", "x" * 256])
async def test_bad_title_is_refused_before_any_request(w2_router, title):
    qm = StrictTypedServer(w2_router)
    with pytest.raises(ToolError):
        await _create(title=title)
    assert qm.requests == []


async def test_description_over_32768_is_refused_before_any_request(w2_router):
    qm = StrictTypedServer(w2_router)
    with pytest.raises(ToolError):
        await _create(description="d" * 32769)
    assert qm.requests == []


@pytest.mark.parametrize("type_name", [*w6.DEFERRED, "TestScriptStep", "NoSuchType"])
async def test_deferred_type_is_refused_before_any_request(w2_router, type_name):
    qm = StrictTypedServer(w2_router)
    with pytest.raises(ToolError) as exc:
        await _create(type_name)
    assert any(word in str(exc.value).lower() for word in ("adiad", "deferred"))
    assert qm.requests == []


async def test_title_of_255_and_description_of_32768_are_accepted_in_a_typed_node(w2_router):
    """FAILS today (wrong element name): boundary values still go through, now typed."""
    qm = StrictTypedServer(w2_router)
    result = await _create(title="t" * 255, description="d" * 32768)
    assert result["created"] is True
    node = _nodes(qm.posts[0])[0]
    assert node.tag == TYPED["TestCase"]
    assert len(_texts(node, "title")[0]) == 255
    assert len(_texts(node, "description")[0]) == 32768


async def test_if_exists_error_refuses_a_duplicate_without_posting(w2_router):
    qm = StrictTypedServer(w2_router)
    existing = _seed(qm)
    with pytest.raises(ToolError) as exc:
        await _create()
    assert existing in str(exc.value) or "777" in str(exc.value)
    assert qm.posts == []


async def test_if_exists_return_hands_back_the_existing_one_without_posting(w2_router):
    qm = StrictTypedServer(w2_router)
    existing = _seed(qm)
    result = await _create(if_exists="return")
    assert result == {"uri": existing, "short_id": "777", "created": False}
    assert qm.posts == []


async def test_if_exists_create_posts_a_typed_node_without_checking(w2_router):
    """FAILS today: skips the check and POSTs, but the POST body is untyped and the strict fake refuses it."""
    qm = StrictTypedServer(w2_router)
    _seed(qm)
    result = await _create(if_exists="create")
    assert result["created"] is True
    assert len(qm.posts) == 1 and qm.rejected == []
    assert qm.queried_types() == set()


@pytest.mark.parametrize("mode", ["stored", "lost"])
async def test_timeout_after_the_post_never_resends(w2_router, mode):
    qm = StrictTypedServer(w2_router, post_timeout=mode)
    try:
        await _create()
    except ToolError:
        pass
    assert len(qm.posts) == 1


# ======================================================================
# 4. The generated body passes the code's own single-resource scan
# ======================================================================
@pytest.mark.parametrize("type_name", w6.SUPPORTED)
async def test_posted_body_passes_scan_as_one_typed_top_level_node(w2_router, type_name):
    """FAILS today: the scanned node is ``rdf:Description``, not the typed element."""
    qm = QmWriteServer(w2_router)
    await _create(type_name, description="desc")
    doc = qm_write._scan(qm.posts[0].content)
    assert qm_write._top_nodes(doc) == 1
    top = doc.root.children[0] if doc.root.name == f"{{{RDF}}}RDF" else doc.root
    assert top.name == TYPED[type_name]
    assert [c.name for c in top.children] == [f"{{{DCTERMS}}}title", f"{{{DCTERMS}}}description"]


# ======================================================================
# 5. Regression: update / link / unlink / delete on what the server returns
# ======================================================================
async def test_update_delete_work_on_a_resource_created_through_the_typed_post(w2_router):
    """FAILS today only because the create step is refused by the strict fake; update/delete themselves are unchanged."""
    qm = StrictTypedServer(w2_router)
    created = await _create(description="old")
    uri = created["uri"]
    updated = await server.update_qm_resource(uri, title="New title")
    assert updated is not None
    assert "New title" in qm.doc(uri)
    await server.delete_qm_resource(uri)
    assert len(qm.deletes) == 1


async def test_link_and_unlink_work_on_a_resource_created_through_the_typed_post(w2_router):
    """FAILS today only because the create step is refused by the strict fake; link/unlink are unchanged."""
    qm = StrictTypedServer(w2_router)
    uri = (await _create())["uri"]
    await server.link_test_case(uri, w6.FIXTURE_REQUIREMENT, "validatesRequirement")
    assert w6.FIXTURE_REQUIREMENT in qm.doc(uri)
    await server.unlink_test_case(uri, w6.FIXTURE_REQUIREMENT, "validatesRequirement")
    assert w6.FIXTURE_REQUIREMENT not in qm.doc(uri)


def _seed(qm: QmWriteServer) -> str:
    """One stored TestCase titled TITLE with short id 777 (what the duplicate check must find)."""
    return qm.add("TestCase", "_E001", TITLE, short_id="777")
