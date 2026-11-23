"""Tests for OSLC discovery branches (CCM/QM) and query execution.

Covers branches not exercised by test_oslc.py (RM-only): CCM catalog parsing,
QueryCapability lookup (`_find_query_base`), catalog-not-found error, and the
`query_resources` JSON path. Mocks HTTP with respx (AGENTS.md convention).
"""

import httpx
import pytest
import respx

from elm_mcp import oslc
from elm_mcp.auth import close_client

BASE = "https://elm.example.com"

# CCM rootservices points at the CCM catalog via the cm 1.0 namespace.
CCM_ROOTSERVICES_XML = f"""<?xml version="1.0" encoding="UTF-8"?>
<rdf:Description xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#"
    xmlns:oslc_cm="http://open-services.net/xmlns/cm/1.0/">
    <oslc_cm:cmServiceProviders rdf:resource="{BASE}/ccm/oslc/workitems/catalog"/>
</rdf:Description>
"""

# rootservices with NO catalog element -> get_catalog_url must raise.
CCM_ROOTSERVICES_EMPTY_XML = """<?xml version="1.0" encoding="UTF-8"?>
<rdf:Description xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#"
    xmlns:oslc_cm="http://open-services.net/xmlns/cm/1.0/">
</rdf:Description>
"""

# CCM catalog: OSLC Core ServiceProvider entries with rdf:about + dcterms:title.
CCM_CATALOG_XML = f"""<?xml version="1.0" encoding="UTF-8"?>
<oslc:ServiceProviderCatalog
    xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#"
    xmlns:dcterms="http://purl.org/dc/terms/"
    xmlns:oslc="http://open-services.net/ns/core#">
    <oslc:serviceProvider>
        <oslc:ServiceProvider rdf:about="{BASE}/ccm/oslc/contexts/_proj1/workitems/services.xml">
            <dcterms:title>PROJ</dcterms:title>
        </oslc:ServiceProvider>
    </oslc:serviceProvider>
</oslc:ServiceProviderCatalog>
"""

# Services document with a QueryCapability for ChangeRequest.
CCM_SERVICES_XML = f"""<?xml version="1.0" encoding="UTF-8"?>
<rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#"
    xmlns:oslc="http://open-services.net/ns/core#">
    <oslc:Service>
        <oslc:queryCapability>
            <oslc:QueryCapability>
                <oslc:resourceType rdf:resource="http://open-services.net/ns/cm#ChangeRequest"/>
                <oslc:queryBase rdf:resource="{BASE}/ccm/oslc/contexts/_proj1/workitems"/>
            </oslc:QueryCapability>
        </oslc:queryCapability>
    </oslc:Service>
</rdf:RDF>
"""

# Services document with NO QueryCapability -> _find_query_base must raise.
CCM_SERVICES_NO_QC_XML = """<?xml version="1.0" encoding="UTF-8"?>
<rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#"
    xmlns:oslc="http://open-services.net/ns/core#">
    <oslc:Service/>
</rdf:RDF>
"""


@pytest.fixture(autouse=True)
async def cleanup(monkeypatch):
    monkeypatch.setattr("elm_mcp.config.settings.elm_url", BASE)
    yield
    await close_client()


@respx.mock
async def test_get_catalog_url_ccm():
    respx.post(f"{BASE}/jts/j_security_check").mock(return_value=httpx.Response(200))
    respx.get(f"{BASE}/ccm/rootservices").mock(return_value=httpx.Response(200, text=CCM_ROOTSERVICES_XML))
    url = await oslc.get_catalog_url("ccm")
    assert url == f"{BASE}/ccm/oslc/workitems/catalog"


@respx.mock
async def test_get_catalog_url_not_found_raises():
    respx.post(f"{BASE}/jts/j_security_check").mock(return_value=httpx.Response(200))
    respx.get(f"{BASE}/ccm/rootservices").mock(return_value=httpx.Response(200, text=CCM_ROOTSERVICES_EMPTY_XML))
    with pytest.raises(ValueError, match="Cannot find service provider catalog"):
        await oslc.get_catalog_url("ccm")


@respx.mock
async def test_list_service_providers_ccm():
    respx.post(f"{BASE}/jts/j_security_check").mock(return_value=httpx.Response(200))
    respx.get(f"{BASE}/ccm/rootservices").mock(return_value=httpx.Response(200, text=CCM_ROOTSERVICES_XML))
    respx.get(f"{BASE}/ccm/oslc/workitems/catalog").mock(return_value=httpx.Response(200, text=CCM_CATALOG_XML))
    providers = await oslc.list_service_providers("ccm")
    assert providers == [{"title": "PROJ", "url": f"{BASE}/ccm/oslc/contexts/_proj1/workitems/services.xml"}]


@respx.mock
async def test_find_query_base_ccm_success():
    services_url = f"{BASE}/ccm/oslc/contexts/_proj1/workitems/services"
    respx.post(f"{BASE}/jts/j_security_check").mock(return_value=httpx.Response(200))
    respx.get(services_url).mock(return_value=httpx.Response(200, text=CCM_SERVICES_XML))
    qb = await oslc._find_query_base("ccm", f"{BASE}/ccm/oslc/contexts/_proj1/workitems/services.xml")
    assert qb == f"{BASE}/ccm/oslc/contexts/_proj1/workitems"


@respx.mock
async def test_find_query_base_not_found_raises():
    # Both candidate URLs return a services doc with no QueryCapability.
    respx.post(f"{BASE}/jts/j_security_check").mock(return_value=httpx.Response(200))
    respx.get(url__startswith=f"{BASE}/ccm/oslc/contexts/_proj1/workitems").mock(
        return_value=httpx.Response(200, text=CCM_SERVICES_NO_QC_XML)
    )
    with pytest.raises(ValueError, match="No query capability found"):
        await oslc._find_query_base("ccm", f"{BASE}/ccm/oslc/contexts/_proj1/workitems/services.xml")


@respx.mock
async def test_query_resources_ccm_json():
    """Full CCM path: resolve project -> query base -> JSON results."""
    results_payload = {
        "oslc:results": [
            {"rdf:about": f"{BASE}/ccm/wi/1", "dcterms:title": "WI 1"},
            {"rdf:about": f"{BASE}/ccm/wi/2", "dcterms:title": "WI 2"},
        ]
    }
    services_url = f"{BASE}/ccm/oslc/contexts/_proj1/workitems/services"
    query_base = f"{BASE}/ccm/oslc/contexts/_proj1/workitems"

    respx.post(f"{BASE}/jts/j_security_check").mock(return_value=httpx.Response(200))
    respx.get(f"{BASE}/ccm/rootservices").mock(return_value=httpx.Response(200, text=CCM_ROOTSERVICES_XML))
    respx.get(f"{BASE}/ccm/oslc/workitems/catalog").mock(return_value=httpx.Response(200, text=CCM_CATALOG_XML))
    respx.get(services_url).mock(return_value=httpx.Response(200, text=CCM_SERVICES_XML))
    respx.get(query_base).mock(return_value=httpx.Response(200, json=results_payload))

    results = await oslc.query_resources("ccm", "PROJ", where='dcterms:title="X"', select="dcterms:title")
    assert len(results) == 2
    assert results[0]["dcterms:title"] == "WI 1"


@respx.mock
async def test_query_resources_xml_fallback_on_406():
    """JSON 406 -> XML fallback parsed into resource entries (CCM; QM is read by qm.query_members)."""
    xml_results = f"""<?xml version="1.0" encoding="UTF-8"?>
    <rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#"
        xmlns:dcterms="http://purl.org/dc/terms/">
        <rdf:Description rdf:about="{BASE}/ccm/tc/1">
            <dcterms:title>Test Case 1</dcterms:title>
        </rdf:Description>
    </rdf:RDF>"""
    services_url = f"{BASE}/ccm/oslc/contexts/_proj1/services"
    query_base = f"{BASE}/ccm/oslc/contexts/_proj1/testcases"

    ccm_rootservices = f"""<?xml version="1.0" encoding="UTF-8"?>
<rdf:Description xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#"
    xmlns:oslc_cm="http://open-services.net/xmlns/cm/1.0/">
    <oslc_cm:cmServiceProviders rdf:resource="{BASE}/ccm/catalog"/>
</rdf:Description>"""
    ccm_catalog = f"""<?xml version="1.0" encoding="UTF-8"?>
<oslc:ServiceProviderCatalog xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#"
    xmlns:dcterms="http://purl.org/dc/terms/" xmlns:oslc="http://open-services.net/ns/core#">
    <oslc:ServiceProvider rdf:about="{BASE}/ccm/oslc/contexts/_proj1">
        <dcterms:title>CCM Proj</dcterms:title>
    </oslc:ServiceProvider>
</oslc:ServiceProviderCatalog>"""
    ccm_services = f"""<?xml version="1.0" encoding="UTF-8"?>
<rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#"
    xmlns:oslc="http://open-services.net/ns/core#">
    <oslc:QueryCapability>
        <oslc:resourceType rdf:resource="http://open-services.net/ns/cm#ChangeRequest"/>
        <oslc:queryBase rdf:resource="{query_base}"/>
    </oslc:QueryCapability>
</rdf:RDF>"""

    respx.post(f"{BASE}/jts/j_security_check").mock(return_value=httpx.Response(200))
    respx.get(f"{BASE}/ccm/rootservices").mock(return_value=httpx.Response(200, text=ccm_rootservices))
    respx.get(f"{BASE}/ccm/catalog").mock(return_value=httpx.Response(200, text=ccm_catalog))
    respx.get(services_url).mock(return_value=httpx.Response(200, text=ccm_services))
    respx.get(query_base).mock(
        side_effect=[
            httpx.Response(406),
            httpx.Response(200, text=xml_results),
        ]
    )

    results = await oslc.query_resources("ccm", "CCM Proj")
    assert results == [{"rdf:about": f"{BASE}/ccm/tc/1", "dcterms:title": "Test Case 1"}]
