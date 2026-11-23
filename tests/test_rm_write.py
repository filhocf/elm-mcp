"""RED tests for RM (DOORS Next) write path: create/update requirement via OSLC.

These tests pin the TARGET behavior of the v0.5.0 RM-write feature BEFORE it is
implemented (G3 → G4). Per G0-rm-write.md §E, the RM write is an ADDITIVE path in
oslc.py (separate from the CCM create_resource/_payload_to_rdfxml gargalo) exercised
by two new @tool entries in server.py.

Target API under test (G0 §E, SPEC-rm-create-requirement.md):
  - server.create_requirement(project, title, content, folder=None, type=None) -> dict
  - server.update_requirement(uri, title=None, content=None) -> dict
  - oslc._find_requirement_factory(project_url) -> str
  - oslc.create_requirement(project, title, xhtml, folder=None, shape=None) -> dict
  - oslc.update_requirement(uri, title=None, xhtml=None) -> dict
  - oslc.sanitize_html_to_xhtml(html) -> str

RED expectation: none of the symbols above exist yet, so the module-top imports of
server.create_requirement / server.update_requirement FAIL at COLLECTION with
ImportError, and the oslc.* calls FAIL with AttributeError. That is valid RED.

Conventions mirror tests/test_write.py and tests/test_oslc_discovery.py:
respx HTTP mock, asyncio_mode=auto, BASE="https://elm.example.com",
autouse cleanup fixture with close_client, j_security_check auth stub.
"""

import httpx
import pytest
import respx
from mcp.server.mcpserver.exceptions import ToolError

from elm_mcp import oslc
from elm_mcp.auth import close_client

# NOTE: these two imports are expected to FAIL at collection today (RED) — the
# tools do not exist yet. When G4 implements them, collection succeeds and the
# behavior assertions below drive the implementation.
from elm_mcp.server import create_requirement, update_requirement

BASE = "https://elm.example.com"

# --- RM discovery fixtures: rootservices -> catalog -> service provider ---

# RM rootservices points at the RM catalog via the rm 1.0 namespace.
RM_ROOTSERVICES_XML = f"""<?xml version="1.0" encoding="UTF-8"?>
<rdf:Description xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#"
    xmlns:oslc_rm="http://open-services.net/xmlns/rm/1.0/">
    <oslc_rm:rmServiceProviders rdf:resource="{BASE}/rm/oslc_rm/catalog"/>
</rdf:Description>
"""

# RM catalog: list_service_providers("rm") parses via regex (ET fails in stdio).
# It matches <*:ServiceProvider> ... <*:title>T</*:title> ... <*:services rdf:resource="U">.
RM_CATALOG_XML = f"""<?xml version="1.0" encoding="UTF-8"?>
<oslc:ServiceProviderCatalog
    xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#"
    xmlns:dcterms="http://purl.org/dc/terms/"
    xmlns:oslc="http://open-services.net/ns/core#">
    <oslc:serviceProvider>
        <oslc:ServiceProvider>
            <dcterms:title>MY RM PROJECT</dcterms:title>
            <oslc:services rdf:resource="{BASE}/rm/oslc_rm/_rmproj/services.xml"/>
        </oslc:ServiceProvider>
    </oslc:serviceProvider>
</oslc:ServiceProviderCatalog>
"""

# RM service provider services.xml: a CreationFactory whose resourceType is
# rm#Requirement, exposing the requirementFactory via oslc:creation.
RM_FACTORY_URL = f"{BASE}/rm/oslc_rm/_rmproj/requirementFactory"
RM_SERVICES_XML = f"""<?xml version="1.0" encoding="UTF-8"?>
<rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#"
    xmlns:dcterms="http://purl.org/dc/terms/"
    xmlns:oslc="http://open-services.net/ns/core#">
    <oslc:Service>
        <oslc:creationFactory>
            <oslc:CreationFactory>
                <dcterms:title>Requirement</dcterms:title>
                <oslc:resourceType rdf:resource="http://open-services.net/ns/rm#Requirement"/>
                <oslc:creation rdf:resource="{RM_FACTORY_URL}"/>
            </oslc:CreationFactory>
        </oslc:creationFactory>
    </oslc:Service>
</rdf:RDF>
"""

# Created requirement URI (Location header after POST).
CREATED_RM_URI = f"{BASE}/rm/resources/_req42"

# W1/D7: `type` is now required; a shape URI is accepted as is (no discovery needed).
RM_SHAPE_URI = f"{BASE}/rm/types/_shapeReq"

# W1/D8: the identifier is read back from the created artifact.
CREATED_RM_RDF = f"""<?xml version="1.0"?>
<rdf:Description xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#"
    xmlns:dcterms="http://purl.org/dc/terms/" rdf:about="{CREATED_RM_URI}">
    <dcterms:identifier>42</dcterms:identifier>
</rdf:Description>"""


@pytest.fixture(autouse=True)
async def cleanup(monkeypatch):
    monkeypatch.setattr("elm_mcp.config.settings.elm_url", BASE)
    yield
    await close_client()


def _mock_auth():
    respx.post(f"{BASE}/jts/j_security_check").mock(return_value=httpx.Response(200))


def _mock_rm_discovery():
    """rootservices -> catalog -> service provider services.xml (with factory)."""
    _mock_auth()
    respx.get(f"{BASE}/rm/rootservices").mock(return_value=httpx.Response(200, text=RM_ROOTSERVICES_XML))
    respx.get(f"{BASE}/rm/oslc_rm/catalog").mock(return_value=httpx.Response(200, text=RM_CATALOG_XML))
    respx.get(f"{BASE}/rm/oslc_rm/_rmproj/services.xml").mock(return_value=httpx.Response(200, text=RM_SERVICES_XML))


# =====================================================================
# 1. create_requirement: discovery + POST 201 with primaryText XHTML
# =====================================================================
@respx.mock
async def test_create_requirement_posts_xhtml_primarytext_and_returns_uri():
    """create_requirement must discover the RM factory, POST RDF/XML whose body
    carries jazz_rm:primaryText parseType="Literal" with NON-escaped XHTML
    (a literal <div ...>), and return the created uri/identifier from Location."""
    _mock_rm_discovery()
    post_route = respx.post(RM_FACTORY_URL).mock(
        return_value=httpx.Response(
            201,
            headers={"Location": CREATED_RM_URI},
            text=f"""<?xml version="1.0"?>
<rdf:Description xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#"
    xmlns:dcterms="http://purl.org/dc/terms/" rdf:about="{CREATED_RM_URI}">
    <dcterms:identifier>42</dcterms:identifier>
</rdf:Description>""",
        )
    )
    reread_route = respx.get(CREATED_RM_URI).mock(return_value=httpx.Response(200, text=CREATED_RM_RDF))
    result = await create_requirement(
        project="MY RM PROJECT",
        title="Novo Requisito",
        content="<p>Conteúdo <b>rico</b></p>",
        type=RM_SHAPE_URI,
    )
    assert post_route.called
    assert reread_route.called
    assert result["identifier"] == "42"
    # Returns the created resource URI and/or identifier
    returned = str(result.get("uri") or result.get("url") or "") + str(result.get("identifier") or "")
    assert CREATED_RM_URI in returned or "42" in returned
    # POST body carries primaryText with parseType="Literal" and a NON-escaped XHTML div
    body = post_route.calls[0].request.content.decode()
    assert "jazz_rm:primaryText" in body
    assert 'parseType="Literal"' in body
    assert '<div xmlns="http://www.w3.org/1999/xhtml">' in body  # literal, not &lt;div
    assert "&lt;div" not in body  # XHTML must NOT be escaped inside the Literal


# =====================================================================
# 2. create_requirement accepts 200 as well as 201
# =====================================================================
@respx.mock
async def test_create_requirement_accepts_200():
    """publicar_elm aceita 200 OU 201 no RM — 200 não deve ser tratado como erro."""
    _mock_rm_discovery()
    respx.post(RM_FACTORY_URL).mock(return_value=httpx.Response(200, headers={"Location": CREATED_RM_URI}, text=""))
    respx.get(CREATED_RM_URI).mock(return_value=httpx.Response(200, text=CREATED_RM_RDF))
    result = await create_requirement(
        project="MY RM PROJECT",
        title="Req 200",
        content="<p>ok</p>",
        type=RM_SHAPE_URI,
    )
    assert isinstance(result, dict)
    returned = str(result.get("uri") or result.get("url") or "")
    assert CREATED_RM_URI in returned


# =====================================================================
# 3. create_requirement with folder= generates nav:parent
# =====================================================================
@respx.mock
async def test_create_requirement_folder_emits_nav_parent():
    """ONDE folder for informado, o corpo deve conter <nav:parent rdf:resource=folder/>."""
    _mock_rm_discovery()
    folder_uri = f"{BASE}/rm/folders/_folderA"
    post_route = respx.post(RM_FACTORY_URL).mock(
        return_value=httpx.Response(201, headers={"Location": CREATED_RM_URI}, text="")
    )
    respx.get(CREATED_RM_URI).mock(return_value=httpx.Response(200, text=CREATED_RM_RDF))
    await create_requirement(
        project="MY RM PROJECT",
        title="Req com pasta",
        content="<p>x</p>",
        folder=folder_uri,
        type=RM_SHAPE_URI,
    )
    body = post_route.calls[0].request.content.decode()
    assert "nav:parent" in body
    assert folder_uri in body


# =====================================================================
# 4. sanitize_html_to_xhtml — pure unit test (no HTTP)
# =====================================================================
def test_sanitize_html_to_xhtml_closes_br_and_escapes_amp():
    """sanitizer must: <br> -> <br/>, lone & -> &amp;, close void tags. Mirrors
    publicar_elm.sanitizar_html (G0 §E pt.4)."""
    out = oslc.sanitize_html_to_xhtml("A<br>B & C <hr> <img src='x'>")
    assert "<br/>" in out
    assert "<br>" not in out
    assert "&amp;" in out
    # already-encoded entity must NOT be double-escaped
    assert "&amp;amp;" not in oslc.sanitize_html_to_xhtml("x &amp; y")
    # void tags closed
    assert "<hr/>" in out
    assert "<hr>" not in out
    assert "<img" in out and "/>" in out


def test_sanitize_html_to_xhtml_strips_html_body_wrappers():
    """html/body/meta wrappers removed (publicar_elm.sanitizar_html)."""
    out = oslc.sanitize_html_to_xhtml("<html><head><meta charset='utf-8'></head><body><p>oi</p></body></html>")
    assert "<html" not in out
    assert "<body" not in out
    assert "<meta" not in out
    assert "<p>oi</p>" in out


# =====================================================================
# 5. update_requirement: GET ETag -> PUT If-Match -> success dict
# =====================================================================
@respx.mock
async def test_update_requirement_get_etag_then_put_ifmatch():
    """update_requirement must GET rdf+xml for the ETag, then PUT with If-Match set
    to that ETag; on success it returns a dict."""
    _mock_auth()
    uri = CREATED_RM_URI
    existing_xml = f"""<?xml version="1.0"?>
<rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#"
    xmlns:dcterms="http://purl.org/dc/terms/"
    xmlns:jazz_rm="http://jazz.net/ns/rm#">
  <rdf:Description rdf:about="{uri}">
    <dcterms:title>Old Title</dcterms:title>
    <jazz_rm:primaryText rdf:parseType="Literal"><div xmlns="http://www.w3.org/1999/xhtml"><p>old</p></div></jazz_rm:primaryText>
  </rdf:Description>
</rdf:RDF>"""
    respx.get(uri).mock(return_value=httpx.Response(200, text=existing_xml, headers={"ETag": '"rm-etag-1"'}))
    put_route = respx.put(uri).mock(return_value=httpx.Response(200, text=existing_xml))
    result = await update_requirement(uri=uri, title="New Title", content="<p>new</p>")
    assert isinstance(result, dict)
    assert put_route.called
    assert put_route.calls[0].request.headers.get("If-Match") == '"rm-etag-1"'


# =====================================================================
# 6. update_requirement: PUT 412 -> ToolError (legible conflict)
# =====================================================================
@respx.mock
async def test_update_requirement_put_412_raises_tool_error_conflict():
    """SE o ETag estiver obsoleto (412), ENTÃO sinalizar conflito com ToolError
    legível. oslc.update_requirement raises ValueError on 412; the @tool boundary
    translates it to ToolError (match: conflito/ETag)."""
    _mock_auth()
    uri = CREATED_RM_URI
    existing_xml = f"""<?xml version="1.0"?>
<rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#"
    xmlns:dcterms="http://purl.org/dc/terms/">
  <rdf:Description rdf:about="{uri}"><dcterms:title>X</dcterms:title></rdf:Description>
</rdf:RDF>"""
    respx.get(uri).mock(return_value=httpx.Response(200, text=existing_xml, headers={"ETag": '"stale"'}))
    respx.put(uri).mock(return_value=httpx.Response(412, text="Precondition Failed"))
    with pytest.raises(ToolError, match="(?i)conflito|etag"):
        await update_requirement(uri=uri, title="New Title")


# =====================================================================
# 7. (edge) create_requirement with unknown project -> ToolError
# =====================================================================
@respx.mock
async def test_create_requirement_unknown_project_raises_tool_error():
    """@tool traduz o ValueError de _resolve_project_url (projeto inexistente) em ToolError."""
    _mock_rm_discovery()  # catalog only lists "MY RM PROJECT"
    with pytest.raises(ToolError, match="(?i)not found|não encontrad"):
        await create_requirement(
            project="PROJETO INEXISTENTE",
            title="X",
            content="<p>x</p>",
        )


# =====================================================================
# G5 regression tests (4 P1 found by Tuvok) — these would FAIL without
# the fixes: exact resourceType match, XML well-formedness, and anchored
# update edits.
# =====================================================================

# Service doc where a RequirementCollection factory comes BEFORE the Requirement
# one. A substring match on "rm#Requirement" would wrongly pick the collection.
RM_SERVICES_DECOY_XML = f"""<?xml version="1.0" encoding="UTF-8"?>
<rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#"
    xmlns:dcterms="http://purl.org/dc/terms/"
    xmlns:oslc="http://open-services.net/ns/core#">
    <oslc:Service>
        <oslc:creationFactory>
            <oslc:CreationFactory>
                <dcterms:title>Requirement Collection</dcterms:title>
                <oslc:resourceType rdf:resource="http://open-services.net/ns/rm#RequirementCollection"/>
                <oslc:creation rdf:resource="{BASE}/rm/oslc_rm/_rmproj/collectionFactory"/>
            </oslc:CreationFactory>
        </oslc:creationFactory>
        <oslc:creationFactory>
            <oslc:CreationFactory>
                <dcterms:title>Requirement</dcterms:title>
                <oslc:resourceType rdf:resource="http://open-services.net/ns/rm#Requirement"/>
                <oslc:creation rdf:resource="{RM_FACTORY_URL}"/>
            </oslc:CreationFactory>
        </oslc:creationFactory>
    </oslc:Service>
</rdf:RDF>
"""


@respx.mock
async def test_factory_discovery_skips_requirement_collection():
    """P1: must pick the Requirement factory, not RequirementCollection (which
    contains 'rm#Requirement' as a substring)."""
    _mock_auth()
    respx.get(f"{BASE}/rm/rootservices").mock(return_value=httpx.Response(200, text=RM_ROOTSERVICES_XML))
    respx.get(f"{BASE}/rm/oslc_rm/catalog").mock(return_value=httpx.Response(200, text=RM_CATALOG_XML))
    respx.get(f"{BASE}/rm/oslc_rm/_rmproj/services.xml").mock(
        return_value=httpx.Response(200, text=RM_SERVICES_DECOY_XML)
    )
    factory = await oslc._find_requirement_factory(f"{BASE}/rm/oslc_rm/_rmproj/services.xml")
    assert factory == RM_FACTORY_URL
    assert "collectionFactory" not in factory


@respx.mock
async def test_create_requirement_rejects_malformed_content():
    """P1: content that breaks out of primaryText (unbalanced tag) is rejected
    before the POST, not sent as malformed XML."""
    _mock_rm_discovery()
    posted = respx.post(RM_FACTORY_URL).mock(return_value=httpx.Response(201, headers={"Location": CREATED_RM_URI}))
    with pytest.raises(ToolError):
        await create_requirement(
            project="MY RM PROJECT",
            title="Injection",
            content="ok</div></jazz_rm:primaryText><evil/>",
        )
    assert not posted.called  # nothing was POSTed with a malformed body


# XML with TWO rdf:Description and TWO titles — the target is the second one.
_OTHER_URI = f"{BASE}/rm/resources/_other"
RM_MULTI_DESC_XML = f"""<?xml version="1.0" encoding="UTF-8"?>
<rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#"
    xmlns:dcterms="http://purl.org/dc/terms/"
    xmlns:jazz_rm="http://jazz.net/ns/rm#">
  <rdf:Description rdf:about="{_OTHER_URI}">
    <dcterms:title>OTHER — do not touch</dcterms:title>
  </rdf:Description>
  <rdf:Description rdf:about="{CREATED_RM_URI}">
    <dcterms:title>Old title</dcterms:title>
  </rdf:Description>
</rdf:RDF>
"""


@respx.mock
async def test_update_requirement_edits_only_target_description():
    """P1: the title edit must hit the target resource's Description, not the
    first <title> in the document (a linked resource)."""
    _mock_auth()
    captured = {}

    def _capture(request):
        captured["body"] = request.content.decode("utf-8")
        return httpx.Response(200)

    respx.get(CREATED_RM_URI).mock(
        return_value=httpx.Response(200, text=RM_MULTI_DESC_XML, headers={"ETag": "etag-xyz"})
    )
    respx.put(CREATED_RM_URI).mock(side_effect=_capture)

    await update_requirement(uri=CREATED_RM_URI, title="New title")

    body = captured["body"]
    assert "New title" in body
    assert "OTHER — do not touch" in body  # the other resource's title is intact
    assert "Old title" not in body  # the target's title was replaced
