"""RED tests for the live-acceptance fixes of W1 (defects A, B, C).

A  list_folders keeps only ``nav#folder`` resources (root included) and exposes ``subfolders_query``.
B  get_requirement falls back to application/rdf+xml on HTTP 415 and flattens the RDF/XML.
C  update_requirement re-reads the identifier (never raising after the PUT).

Fixtures are tenant-neutral (https://elm.example.com, _PA1, jdoe).
"""

import httpx
import pytest
import respx

from elm_mcp import oslc
from elm_mcp.auth import close_client

BASE = "https://elm.example.com"
REQ_URI = f"{BASE}/rm/resources/TX_abc"
SHAPE_URI = f"{BASE}/rm/types/_shape1"
USER_URI = f"{BASE}/jts/users/jdoe"

REQ_RDF = f"""<?xml version="1.0" encoding="UTF-8"?>
<rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#"
    xmlns:dcterms="http://purl.org/dc/terms/"
    xmlns:oslc="http://open-services.net/ns/core#"
    xmlns:jazz_rm="http://jazz.net/ns/rm#">
  <rdf:Description rdf:about="{REQ_URI}">
    <dcterms:identifier>42</dcterms:identifier>
    <dcterms:title>Login &amp; access</dcterms:title>
    <jazz_rm:primaryText rdf:parseType="Literal"><div xmlns="http://www.w3.org/1999/xhtml"><p>text</p></div></jazz_rm:primaryText>
    <oslc:instanceShape rdf:resource="{SHAPE_URI}"/>
    <dcterms:contributor rdf:resource="{USER_URI}"/>
    <dcterms:contributor rdf:resource="{BASE}/jts/users/other"/>
  </rdf:Description>
</rdf:RDF>
"""


@pytest.fixture(autouse=True)
async def cleanup(monkeypatch):
    monkeypatch.setattr("elm_mcp.config.settings.elm_url", BASE)
    yield
    await close_client()


def _auth():
    respx.post(f"{BASE}/jts/j_security_check").mock(return_value=httpx.Response(200))


# ---- B ----
@respx.mock
async def test_get_resource_415_falls_back_to_rdfxml_flat_dict():
    _auth()
    seen = []

    def handler(request):
        seen.append(request.headers["Accept"])
        if request.headers["Accept"] == "application/json":
            return httpx.Response(415, text="[http-415]")
        return httpx.Response(200, text=REQ_RDF, headers={"Content-Type": "application/rdf+xml"})

    respx.get(REQ_URI).mock(side_effect=handler)
    data = await oslc.get_resource(REQ_URI)
    assert seen == ["application/json", "application/rdf+xml"]
    assert data["rdf:about"] == REQ_URI
    assert data["dcterms:identifier"] == "42"
    assert data["dcterms:title"] == "Login & access"
    assert data["oslc:instanceShape"] == SHAPE_URI
    assert data["dcterms:contributor"] == [USER_URI, f"{BASE}/jts/users/other"]
    assert "<p>text</p>" in data["jazz_rm:primaryText"]


@respx.mock
async def test_get_resource_json_ok_does_not_retry():
    _auth()
    route = respx.get(f"{BASE}/ccm/resource/itemName/WI1").mock(
        return_value=httpx.Response(200, json={"dcterms:identifier": "1"})
    )
    data = await oslc.get_resource(f"{BASE}/ccm/resource/itemName/WI1")
    assert data == {"dcterms:identifier": "1"}
    assert route.call_count == 1


# ---- C ----
_UPD_XML = f"""<?xml version="1.0"?>
<rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#"
    xmlns:dcterms="http://purl.org/dc/terms/">
  <rdf:Description rdf:about="{REQ_URI}">
    <dcterms:title>Old</dcterms:title>
  </rdf:Description>
</rdf:RDF>"""


@respx.mock
async def test_update_requirement_rereads_identifier():
    _auth()
    calls = {"get": 0}

    def get_handler(request):
        calls["get"] += 1
        if calls["get"] == 1:
            return httpx.Response(200, text=_UPD_XML, headers={"ETag": '"e1"'})
        return httpx.Response(200, text=REQ_RDF)

    respx.get(REQ_URI).mock(side_effect=get_handler)
    respx.put(REQ_URI).mock(return_value=httpx.Response(200, text=""))
    result = await oslc.update_requirement(REQ_URI, title="New")
    assert result["identifier"] == "42"
    assert result["uri"] == REQ_URI


@respx.mock
async def test_update_requirement_reread_failure_never_raises():
    _auth()
    calls = {"get": 0}

    def get_handler(request):
        calls["get"] += 1
        if calls["get"] == 1:
            return httpx.Response(200, text=_UPD_XML, headers={"ETag": '"e1"'})
        raise httpx.ConnectError("boom")

    respx.get(REQ_URI).mock(side_effect=get_handler)
    respx.put(REQ_URI).mock(return_value=httpx.Response(200, text=""))
    result = await oslc.update_requirement(REQ_URI, title="New")
    assert result["uri"] == REQ_URI
    assert result["identifier"] is None
    assert "warning" in result
