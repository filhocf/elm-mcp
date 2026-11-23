"""Shared tenant-neutral RM fixtures for the W1 tests (https://elm.example.com, MY PROJECT (KEY))."""

import httpx
import respx

BASE = "https://elm.example.com"
PROJECT = "MY PROJECT (KEY)"

PROJECT_AREA_URI = f"{BASE}/process/project-areas/_PA1"
COMPONENT_URI = f"{BASE}/rm/cm/component/_compA"
FOLDERS_URL = f"{BASE}/rm/folders"
FACTORY_URL = f"{BASE}/rm/oslc_rm/_rmproj/requirementFactory"
SERVICES_URL = f"{BASE}/rm/oslc_rm/_rmproj/services.xml"

SHAPE_REQ = f"{BASE}/rm/types/_shapeReq"
SHAPE_FEATURE = f"{BASE}/rm/types/_shapeFeature"
CREATED_URI = f"{BASE}/rm/resources/_req42"

ROOTSERVICES_XML = f"""<?xml version="1.0" encoding="UTF-8"?>
<rdf:Description xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#"
    xmlns:oslc_rm="http://open-services.net/xmlns/rm/1.0/">
    <oslc_rm:rmServiceProviders rdf:resource="{BASE}/rm/oslc_rm/catalog"/>
</rdf:Description>
"""

CATALOG_XML = f"""<?xml version="1.0" encoding="UTF-8"?>
<oslc:ServiceProviderCatalog
    xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#"
    xmlns:dcterms="http://purl.org/dc/terms/"
    xmlns:oslc="http://open-services.net/ns/core#">
    <oslc:serviceProvider>
        <oslc:ServiceProvider>
            <dcterms:title>{PROJECT}</dcterms:title>
            <oslc:services rdf:resource="{SERVICES_URL}"/>
        </oslc:ServiceProvider>
    </oslc:serviceProvider>
</oslc:ServiceProviderCatalog>
"""

# Service provider: project area (oslc:details), component, two Requirement shapes.
SERVICES_XML = f"""<?xml version="1.0" encoding="UTF-8"?>
<rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#"
    xmlns:dcterms="http://purl.org/dc/terms/"
    xmlns:oslc="http://open-services.net/ns/core#"
    xmlns:oslc_config="http://open-services.net/ns/config#">
    <oslc:ServiceProvider>
        <oslc:details rdf:resource="{PROJECT_AREA_URI}"/>
        <oslc_config:component rdf:resource="{COMPONENT_URI}"/>
        <oslc:service>
            <oslc:Service>
                <oslc:creationFactory>
                    <oslc:CreationFactory>
                        <dcterms:title>Requirement</dcterms:title>
                        <oslc:resourceType rdf:resource="http://open-services.net/ns/rm#Requirement"/>
                        <oslc:creation rdf:resource="{FACTORY_URL}"/>
                        <oslc:resourceShape rdf:resource="{SHAPE_REQ}"/>
                        <oslc:resourceShape rdf:resource="{SHAPE_FEATURE}"/>
                    </oslc:CreationFactory>
                </oslc:creationFactory>
            </oslc:Service>
        </oslc:service>
    </oslc:ServiceProvider>
</rdf:RDF>
"""


def _shape_xml(uri: str, title: str) -> str:
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#"
    xmlns:dcterms="http://purl.org/dc/terms/"
    xmlns:oslc="http://open-services.net/ns/core#">
    <oslc:ResourceShape rdf:about="{uri}">
        <dcterms:title>{title}</dcterms:title>
    </oslc:ResourceShape>
</rdf:RDF>
"""


FOLDERS_JSON = {
    f"{BASE}/rm/folders/_root": {"http://purl.org/dc/terms/title": [{"value": "root"}]},
    f"{BASE}/rm/folders/_reqs": {"http://purl.org/dc/terms/title": [{"value": "Requirements"}]},
}

CREATED_RDF = f"""<?xml version="1.0" encoding="UTF-8"?>
<rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#"
    xmlns:dcterms="http://purl.org/dc/terms/">
    <rdf:Description rdf:about="{CREATED_URI}">
        <dcterms:identifier>42</dcterms:identifier>
        <dcterms:title>New Requirement</dcterms:title>
    </rdf:Description>
</rdf:RDF>
"""


def mock_rm_discovery() -> None:
    respx.post(f"{BASE}/jts/j_security_check").mock(return_value=httpx.Response(200))
    respx.get(f"{BASE}/rm/rootservices").mock(return_value=httpx.Response(200, text=ROOTSERVICES_XML))
    respx.get(f"{BASE}/rm/oslc_rm/catalog").mock(return_value=httpx.Response(200, text=CATALOG_XML))
    respx.get(SERVICES_URL).mock(return_value=httpx.Response(200, text=SERVICES_XML))
    respx.get(SHAPE_REQ).mock(return_value=httpx.Response(200, text=_shape_xml(SHAPE_REQ, "Requirement")))
    respx.get(SHAPE_FEATURE).mock(return_value=httpx.Response(200, text=_shape_xml(SHAPE_FEATURE, "Feature")))
