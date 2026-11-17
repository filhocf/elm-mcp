"""RED tests (G3) for the discovery tools of SPEC-discovery-tools (v0.5.0).

These pin the TARGET behavior of the four discovery tools BEFORE implementation
(G3 → G4). Per G0-discovery-tools.md they are ADDITIVE read-only paths:

  server.list_types(domain, project)            -> list[dict]
  server.list_fields(domain, project, type)     -> dict
  server.inspect_workitem(id)                    -> dict
  server.list_folders(project)                   -> list[dict]

backed by new oslc helpers (list_resource_shapes / list_shape_properties /
list_allowed_values / list_fields / inspect_workitem / list_folders).

RED expectation: the four tools do NOT exist yet, so the module-top import
`from elm_mcp.server import list_types, list_fields, inspect_workitem, list_folders`
FAILS at COLLECTION with ImportError. That is valid RED — every test in this
file is collected-but-errored until G4 lands the tools. We do NOT guard the
import with try/except (that would hide the absence and make the suite green).

Conventions mirror tests/test_oslc_discovery.py and tests/test_rm_write.py:
respx HTTP mock, asyncio_mode=auto (pyproject), BASE="https://elm.example.com",
autouse cleanup fixture with close_client, j_security_check auth stub, RM/CCM
rootservices+catalog fixtures reused (regex-parsed, ET fails in MCP stdio).

--------------------------------------------------------------------------------
ASSUMPTIONS (G0 §7/§8 left some cases "DECIDIR no G4"; chosen conservatively so
the RED test pins a concrete, realistic degrade behavior — revisit at G4 review):

  A1. list_types with NO matching CreationFactory for the domain type
      -> returns [] (empty list), does NOT raise. (G0 §7 case 5.)
  A2. list_fields: a property WITHOUT oslc:allowedValues carries
      allowed_values == [] (empty), key present, does not break. (G0 §7 case 8.)
  A3. inspect_workitem with NO oslc:instanceShape -> shape_uri is None and
      factory_uri is None (degrades, does not raise). (G0 §7 case 14.)
  A4. list_folders when the service provider has NO oslc_config:component
      -> returns [] (empty list), does NOT raise. (G0 §7 case 17.)
  A5. list_fields resolves `type` by title with case-insensitive equality;
      an unknown title raises ValueError -> ToolError at the @tool boundary.
      (G0 §8 pt.6 recommendation.)
  A6. system-folder filter excludes exactly "root" and "Folder Query Results"
      by title, tenant-neutral (no org-specific replace). (G0 §3.)
  A7. inspect_workitem 404 -> httpx HTTPStatusError inside get_resource ->
      the @tool boundary surfaces it as ToolError. (G0 §7 case 13.)

Shape/allowedValues fixtures use VARIED namespace prefixes (oslc:/oslc_shape:,
dcterms:/dc:, rdf default) so the G4 regex can't assume a single prefix.
Tenant-neutral: only PROJ / "Field A" / example.com style identifiers.
--------------------------------------------------------------------------------
"""

import httpx
import pytest
import respx
from mcp.server.mcpserver.exceptions import ToolError

from elm_mcp.auth import close_client

# NOTE: this import is expected to FAIL at collection TODAY (RED) — the four
# discovery tools do not exist yet. When G4 implements them, collection
# succeeds and the behavior assertions below drive the implementation.
from elm_mcp.server import (
    inspect_workitem,
    list_fields,
    list_folders,
    list_types,
)

BASE = "https://elm.example.com"

# ======================================================================
# Reused discovery fixtures (rootservices -> catalog -> service provider)
# ======================================================================

# RM rootservices -> RM catalog (rm 1.0 namespace).
RM_ROOTSERVICES_XML = f"""<?xml version="1.0" encoding="UTF-8"?>
<rdf:Description xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#"
    xmlns:oslc_rm="http://open-services.net/xmlns/rm/1.0/">
    <oslc_rm:rmServiceProviders rdf:resource="{BASE}/rm/oslc_rm/catalog"/>
</rdf:Description>
"""

# RM catalog: regex-parsed (ServiceProvider / title / services rdf:resource).
RM_CATALOG_XML = f"""<?xml version="1.0" encoding="UTF-8"?>
<oslc:ServiceProviderCatalog
    xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#"
    xmlns:dcterms="http://purl.org/dc/terms/"
    xmlns:oslc="http://open-services.net/ns/core#">
    <oslc:serviceProvider>
        <oslc:ServiceProvider>
            <dcterms:title>PROJ</dcterms:title>
            <oslc:services rdf:resource="{BASE}/rm/oslc_rm/_rmproj/services.xml"/>
        </oslc:ServiceProvider>
    </oslc:serviceProvider>
</oslc:ServiceProviderCatalog>
"""

# CCM rootservices -> CCM catalog (cm 1.0 namespace).
CCM_ROOTSERVICES_XML = f"""<?xml version="1.0" encoding="UTF-8"?>
<rdf:Description xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#"
    xmlns:oslc_cm="http://open-services.net/xmlns/cm/1.0/">
    <oslc_cm:cmServiceProviders rdf:resource="{BASE}/ccm/oslc/workitems/catalog"/>
</rdf:Description>
"""

# CCM catalog: OSLC Core ServiceProvider with rdf:about + dcterms:title.
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

# ======================================================================
# NEW fixtures (G0 §7)
# ======================================================================

# Shape URIs (title of each type is read by GETting the shape).
RM_SHAPE_URI = f"{BASE}/rm/types/_shapeReq"
CCM_SHAPE_URI = f"{BASE}/ccm/oslc/context/_proj1/shapes/workitems/_defect"

# RM service provider services.xml WITH a resourceShape on the Requirement
# CreationFactory. Varied prefixes: oslc: for factory, default rdf ns for shape.
RM_SERVICES_WITH_SHAPES_XML = f"""<?xml version="1.0" encoding="UTF-8"?>
<rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#"
    xmlns:dcterms="http://purl.org/dc/terms/"
    xmlns:oslc="http://open-services.net/ns/core#">
    <oslc:Service>
        <oslc:creationFactory>
            <oslc:CreationFactory>
                <dcterms:title>Requirement</dcterms:title>
                <oslc:resourceType rdf:resource="http://open-services.net/ns/rm#Requirement"/>
                <oslc:creation rdf:resource="{BASE}/rm/oslc_rm/_rmproj/requirementFactory"/>
                <oslc:resourceShape rdf:resource="{RM_SHAPE_URI}"/>
            </oslc:CreationFactory>
        </oslc:creationFactory>
    </oslc:Service>
</rdf:RDF>
"""

# RM service provider with a Requirement factory but NO resourceShape AND a
# decoy RequirementCollection factory (exact-match guard, mistake note ed14f77c).
RM_SERVICES_NO_SHAPE_XML = f"""<?xml version="1.0" encoding="UTF-8"?>
<rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#"
    xmlns:dcterms="http://purl.org/dc/terms/"
    xmlns:oslc="http://open-services.net/ns/core#">
    <oslc:Service>
        <oslc:creationFactory>
            <oslc:CreationFactory>
                <dcterms:title>Requirement Collection</dcterms:title>
                <oslc:resourceType rdf:resource="http://open-services.net/ns/rm#RequirementCollection"/>
                <oslc:resourceShape rdf:resource="{BASE}/rm/types/_collectionShape"/>
            </oslc:CreationFactory>
        </oslc:creationFactory>
    </oslc:Service>
</rdf:RDF>
"""

# CCM service provider services.xml WITH a ChangeRequest factory + shape.
CCM_SERVICES_WITH_SHAPES_XML = f"""<?xml version="1.0" encoding="UTF-8"?>
<rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#"
    xmlns:dcterms="http://purl.org/dc/terms/"
    xmlns:oslc="http://open-services.net/ns/core#">
    <oslc:Service>
        <oslc:creationFactory>
            <oslc:CreationFactory>
                <dcterms:title>Defect</dcterms:title>
                <oslc:resourceType rdf:resource="http://open-services.net/ns/cm#ChangeRequest"/>
                <oslc:creation rdf:resource="{BASE}/ccm/oslc/contexts/_proj1/workitems/_defect"/>
                <oslc:resourceShape rdf:resource="{CCM_SHAPE_URI}"/>
            </oslc:CreationFactory>
        </oslc:creationFactory>
    </oslc:Service>
</rdf:RDF>
"""

# allowedValues URI referenced by the enumerated property below.
ALLOWED_VALUES_URI = f"{BASE}/rm/types/_allowedPriority"
ALLOWED_VALUES_ALT_URI = f"{BASE}/rm/types/_allowedStatus"

# resourceShape XML: title of the type + two properties.
#   - required, enumerated ("Priority", occurs Exactly-one, has allowedValues)
#   - optional, free-text ("Field A", occurs Zero-or-one, no allowedValues)
# Varied prefixes: oslc:/oslc_shape: for Property/name/occurs, dcterms:/dc: for title.
SHAPE_XML = f"""<?xml version="1.0" encoding="UTF-8"?>
<rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#"
    xmlns:dcterms="http://purl.org/dc/terms/"
    xmlns:dc="http://purl.org/dc/terms/"
    xmlns:oslc="http://open-services.net/ns/core#"
    xmlns:oslc_shape="http://open-services.net/ns/core#">
    <oslc:ResourceShape rdf:about="{RM_SHAPE_URI}">
        <dcterms:title>Requirement</dcterms:title>
        <oslc:property>
            <oslc_shape:Property>
                <oslc:name>priority</oslc:name>
                <dc:title>Priority</dc:title>
                <oslc:occurs rdf:resource="http://open-services.net/ns/core#Exactly-one"/>
                <oslc:propertyDefinition rdf:resource="{BASE}/rm/defs/priority"/>
                <oslc:range rdf:resource="http://open-services.net/ns/core#LocalResource"/>
                <oslc:allowedValues rdf:resource="{ALLOWED_VALUES_URI}"/>
            </oslc_shape:Property>
        </oslc:property>
        <oslc:property>
            <oslc_shape:Property>
                <oslc:name>fieldA</oslc:name>
                <dcterms:title>Field A</dcterms:title>
                <oslc:occurs rdf:resource="http://open-services.net/ns/core#Zero-or-one"/>
                <oslc:propertyDefinition rdf:resource="{BASE}/rm/defs/fieldA"/>
            </oslc_shape:Property>
        </oslc:property>
    </oslc:ResourceShape>
</rdf:RDF>
"""

# Shape where a property's dc:title appears BEFORE the type's dcterms:title.
# RDF/XML serialization order is NOT guaranteed by the server, so _shape_title
# must not return the first title in the whole body (G5 P2 regression guard).
SHAPE_XML_PROPERTY_FIRST = f"""<?xml version="1.0" encoding="UTF-8"?>
<rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#"
    xmlns:dcterms="http://purl.org/dc/terms/"
    xmlns:dc="http://purl.org/dc/terms/"
    xmlns:oslc="http://open-services.net/ns/core#"
    xmlns:oslc_shape="http://open-services.net/ns/core#">
    <oslc:ResourceShape rdf:about="{RM_SHAPE_URI}">
        <oslc:property>
            <oslc_shape:Property>
                <oslc:name>priority</oslc:name>
                <dc:title>Priority</dc:title>
                <oslc:occurs rdf:resource="http://open-services.net/ns/core#Exactly-one"/>
            </oslc_shape:Property>
        </oslc:property>
        <dcterms:title>Requirement</dcterms:title>
    </oslc:ResourceShape>
</rdf:RDF>
"""

# allowedValues format 1: oslc:allowedValue rdf:resource (URIs only).
ALLOWED_VALUES_XML = f"""<?xml version="1.0" encoding="UTF-8"?>
<rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#"
    xmlns:oslc="http://open-services.net/ns/core#">
    <oslc:AllowedValues rdf:about="{ALLOWED_VALUES_URI}">
        <oslc:allowedValue rdf:resource="{BASE}/rm/values/low"/>
        <oslc:allowedValue rdf:resource="{BASE}/rm/values/high"/>
    </oslc:AllowedValues>
</rdf:RDF>
"""

# allowedValues format 2 (alternative): rdf:Description + dcterms:title.
ALLOWED_VALUES_ALT_XML = f"""<?xml version="1.0" encoding="UTF-8"?>
<rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#"
    xmlns:dcterms="http://purl.org/dc/terms/"
    xmlns:oslc="http://open-services.net/ns/core#">
    <oslc:AllowedValues rdf:about="{ALLOWED_VALUES_ALT_URI}">
        <rdf:Description rdf:about="{BASE}/rm/values/open">
            <dcterms:title>Open</dcterms:title>
        </rdf:Description>
        <rdf:Description rdf:about="{BASE}/rm/values/closed">
            <dcterms:title>Closed</dcterms:title>
        </rdf:Description>
    </oslc:AllowedValues>
</rdf:RDF>
"""

# ---- inspect_workitem fixtures ----
WI_ID = "12345"
WI_URI = f"{BASE}/ccm/resource/itemName/com.ibm.team.workitem.WorkItem/{WI_ID}"
WI_SHAPE_URI = f"{BASE}/ccm/oslc/context/_proj1/shapes/workitems/_defect"
# factory derived from shape path: /shapes/workitems/_defect -> /workitems/_defect
WI_FACTORY_FRAGMENT = "/workitems/_defect"

WI_JSON = {
    "dcterms:identifier": WI_ID,
    "dcterms:type": "Defect",
    "dcterms:title": "A work item",
    "oslc:instanceShape": {"rdf:resource": WI_SHAPE_URI},
    "rtc_cm:filedAgainst": {"rdf:resource": f"{BASE}/ccm/oslc/categories/_catA"},
    "rtc_ext:customText": "some value",
}

# WI with NO oslc:instanceShape (degrade path).
WI_JSON_NO_SHAPE = {
    "dcterms:identifier": WI_ID,
    "dcterms:type": "Task",
    "dcterms:title": "Shapeless WI",
    "rtc_cm:filedAgainst": {"rdf:resource": f"{BASE}/ccm/oslc/categories/_catA"},
}

# ---- list_folders fixtures ----
RM_COMPONENT_URI = f"{BASE}/rm/cm/component/_compA"
FOLDERS_URL = f"{BASE}/rm/folders"

# RM service provider rdf+xml exposing oslc_config:component. Varied prefix.
RM_SP_WITH_COMPONENT_XML = f"""<?xml version="1.0" encoding="UTF-8"?>
<rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#"
    xmlns:dcterms="http://purl.org/dc/terms/"
    xmlns:oslc="http://open-services.net/ns/core#"
    xmlns:oslc_config="http://open-services.net/ns/config#">
    <oslc:ServiceProvider>
        <oslc_config:component rdf:resource="{RM_COMPONENT_URI}"/>
    </oslc:ServiceProvider>
</rdf:RDF>
"""

# RM service provider WITHOUT a component (config mgmt disabled).
RM_SP_NO_COMPONENT_XML = """<?xml version="1.0" encoding="UTF-8"?>
<rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#"
    xmlns:oslc="http://open-services.net/ns/core#">
    <oslc:ServiceProvider/>
</rdf:RDF>
"""

# Folders JSON: {uri: {dcterms:title: [{value: name}]}} incl. system folders.
FOLDERS_JSON = {
    f"{BASE}/rm/folders/_root": {
        "http://purl.org/dc/terms/title": [{"value": "root"}],
    },
    f"{BASE}/rm/folders/_queryres": {
        "http://purl.org/dc/terms/title": [{"value": "Folder Query Results"}],
    },
    f"{BASE}/rm/folders/_reqs": {
        "http://purl.org/dc/terms/title": [{"value": "Requirements"}],
    },
    f"{BASE}/rm/folders/_designs": {
        "http://purl.org/dc/terms/title": [{"value": "Designs"}],
    },
}


@pytest.fixture(autouse=True)
async def cleanup(monkeypatch):
    monkeypatch.setattr("elm_mcp.config.settings.elm_url", BASE)
    yield
    await close_client()


def _mock_auth():
    respx.post(f"{BASE}/jts/j_security_check").mock(return_value=httpx.Response(200))


def _mock_rm_discovery(services_xml=RM_SERVICES_WITH_SHAPES_XML):
    """rootservices -> catalog -> RM service provider services.xml."""
    _mock_auth()
    respx.get(f"{BASE}/rm/rootservices").mock(return_value=httpx.Response(200, text=RM_ROOTSERVICES_XML))
    respx.get(f"{BASE}/rm/oslc_rm/catalog").mock(return_value=httpx.Response(200, text=RM_CATALOG_XML))
    respx.get(f"{BASE}/rm/oslc_rm/_rmproj/services.xml").mock(return_value=httpx.Response(200, text=services_xml))


def _mock_ccm_discovery(services_xml=CCM_SERVICES_WITH_SHAPES_XML):
    """rootservices -> catalog -> CCM service provider services.xml."""
    _mock_auth()
    respx.get(f"{BASE}/ccm/rootservices").mock(return_value=httpx.Response(200, text=CCM_ROOTSERVICES_XML))
    respx.get(f"{BASE}/ccm/oslc/workitems/catalog").mock(return_value=httpx.Response(200, text=CCM_CATALOG_XML))
    respx.get(f"{BASE}/ccm/oslc/contexts/_proj1/workitems/services.xml").mock(
        return_value=httpx.Response(200, text=services_xml)
    )


# ======================================================================
# list_types
# ======================================================================
@respx.mock
async def test_list_types_rm_returns_shapes():
    """1. RM: rootservices->catalog->SP->shape; each entry has title + shape_uri."""
    _mock_rm_discovery()
    respx.get(RM_SHAPE_URI).mock(return_value=httpx.Response(200, text=SHAPE_XML))
    result = await list_types("rm", "PROJ")
    assert isinstance(result, list)
    assert len(result) >= 1
    entry = result[0]
    assert entry["shape_uri"] == RM_SHAPE_URI
    assert entry["title"] == "Requirement"
    assert entry["resource_type"] == "http://open-services.net/ns/rm#Requirement"


@respx.mock
async def test_list_types_ccm_returns_shapes():
    """2. CCM: same flow with resourceType=cm#ChangeRequest."""
    _mock_ccm_discovery()
    respx.get(CCM_SHAPE_URI).mock(return_value=httpx.Response(200, text=SHAPE_XML))
    result = await list_types("ccm", "PROJ")
    assert isinstance(result, list)
    assert len(result) >= 1
    assert result[0]["shape_uri"] == CCM_SHAPE_URI
    assert result[0]["resource_type"] == "http://open-services.net/ns/cm#ChangeRequest"


@respx.mock
async def test_list_types_invalid_domain_raises_toolerror():
    """3. domain='xx' -> ToolError BEFORE any network call."""
    with pytest.raises(ToolError, match="(?i)invalid domain"):
        await list_types("xx", "PROJ")


@respx.mock
async def test_list_types_project_not_found_raises_toolerror():
    """4. catalog without PROJ -> _resolve_project_url ValueError -> ToolError."""
    _mock_rm_discovery()
    with pytest.raises(ToolError, match="(?i)not found|não encontrad"):
        await list_types("rm", "GHOST PROJECT")


@respx.mock
async def test_list_types_no_matching_factory_returns_empty():
    """5. SP with no Requirement CreationFactory (only Collection) -> [] (A1)."""
    _mock_rm_discovery(services_xml=RM_SERVICES_NO_SHAPE_XML)
    result = await list_types("rm", "PROJ")
    assert result == []


@respx.mock
async def test_list_types_shape_title_ignores_property_titles():
    """5b. (G5 P2) shape with a property dc:title BEFORE the type dcterms:title
    must still report the TYPE title ('Requirement'), not the property's."""
    _mock_rm_discovery()
    respx.get(RM_SHAPE_URI).mock(return_value=httpx.Response(200, text=SHAPE_XML_PROPERTY_FIRST))
    result = await list_types("rm", "PROJ")
    assert result[0]["title"] == "Requirement"


# ======================================================================
# list_fields
# ======================================================================
@respx.mock
async def test_list_fields_required_and_optional():
    """6. partitions required/optional by oslc:occurs (Exactly-one vs Zero-or-one)."""
    _mock_rm_discovery()
    respx.get(RM_SHAPE_URI).mock(return_value=httpx.Response(200, text=SHAPE_XML))
    respx.get(ALLOWED_VALUES_URI).mock(return_value=httpx.Response(200, text=ALLOWED_VALUES_XML))
    result = await list_fields("rm", "PROJ", "Requirement")
    assert result["type"] == "Requirement"
    assert result["shape_uri"] == RM_SHAPE_URI
    required_names = {f["name"] for f in result["required"]}
    optional_names = {f["name"] for f in result["optional"]}
    assert "priority" in required_names
    assert "fieldA" in optional_names


@respx.mock
async def test_list_fields_with_allowed_values():
    """7. enumerated field resolves allowed_values_uri -> value list."""
    _mock_rm_discovery()
    respx.get(RM_SHAPE_URI).mock(return_value=httpx.Response(200, text=SHAPE_XML))
    respx.get(ALLOWED_VALUES_URI).mock(return_value=httpx.Response(200, text=ALLOWED_VALUES_XML))
    result = await list_fields("rm", "PROJ", "Requirement")
    priority = next(f for f in result["required"] if f["name"] == "priority")
    value_uris = {v["value_uri"] for v in priority["allowed_values"]}
    assert f"{BASE}/rm/values/low" in value_uris
    assert f"{BASE}/rm/values/high" in value_uris


@respx.mock
async def test_list_fields_field_without_allowed_values():
    """8. optional field without oslc:allowedValues -> allowed_values == [] (A2)."""
    _mock_rm_discovery()
    respx.get(RM_SHAPE_URI).mock(return_value=httpx.Response(200, text=SHAPE_XML))
    respx.get(ALLOWED_VALUES_URI).mock(return_value=httpx.Response(200, text=ALLOWED_VALUES_XML))
    result = await list_fields("rm", "PROJ", "Requirement")
    field_a = next(f for f in result["optional"] if f["name"] == "fieldA")
    assert field_a["allowed_values"] == []


@respx.mock
async def test_list_fields_type_not_found_raises_toolerror():
    """9. unknown type title among shapes -> ValueError -> ToolError (A5)."""
    _mock_rm_discovery()
    respx.get(RM_SHAPE_URI).mock(return_value=httpx.Response(200, text=SHAPE_XML))
    with pytest.raises(ToolError, match="(?i)not found|não encontrad|invalid type"):
        await list_fields("rm", "PROJ", "Nonexistent Type")


@respx.mock
async def test_list_fields_allowed_values_alt_format():
    """10. allowedValues in rdf:Description/dcterms:title format -> titles resolved."""
    _mock_rm_discovery()
    # SHAPE_XML's priority points at ALLOWED_VALUES_URI; serve the ALT format there.
    respx.get(RM_SHAPE_URI).mock(return_value=httpx.Response(200, text=SHAPE_XML))
    respx.get(ALLOWED_VALUES_URI).mock(return_value=httpx.Response(200, text=ALLOWED_VALUES_ALT_XML))
    result = await list_fields("rm", "PROJ", "Requirement")
    priority = next(f for f in result["required"] if f["name"] == "priority")
    titles = {v.get("title") for v in priority["allowed_values"]}
    assert "Open" in titles
    assert "Closed" in titles


# ======================================================================
# inspect_workitem
# ======================================================================
@respx.mock
async def test_inspect_workitem_derives_shape_and_factory():
    """11. WI JSON -> shape_uri, factory_uri (regex-derived), type."""
    _mock_auth()
    respx.get(WI_URI).mock(return_value=httpx.Response(200, json=WI_JSON))
    result = await inspect_workitem(WI_ID)
    assert result["id"] == WI_ID
    assert result["type"] == "Defect"
    assert result["shape_uri"] == WI_SHAPE_URI
    assert result["factory_uri"] is not None
    assert WI_FACTORY_FRAGMENT in result["factory_uri"]
    assert "/shapes/" not in result["factory_uri"]


@respx.mock
async def test_inspect_workitem_collects_filled_fields():
    """12. filled rtc_cm:* / rtc_ext:* fields appear in the returned 'fields'."""
    _mock_auth()
    respx.get(WI_URI).mock(return_value=httpx.Response(200, json=WI_JSON))
    result = await inspect_workitem(WI_ID)
    fields = result["fields"]
    assert fields["rtc_cm:filedAgainst"] == f"{BASE}/ccm/oslc/categories/_catA"
    assert fields["rtc_ext:customText"] == "some value"


@respx.mock
async def test_inspect_workitem_not_found_raises_toolerror():
    """13. GET 404 -> HTTPStatusError -> ToolError at the @tool boundary (A7)."""
    _mock_auth()
    respx.get(WI_URI).mock(return_value=httpx.Response(404, text="Not Found"))
    with pytest.raises(ToolError):
        await inspect_workitem(WI_ID)


@respx.mock
async def test_inspect_workitem_no_instance_shape_degrades():
    """14. WI without oslc:instanceShape -> shape_uri/factory_uri None (A3)."""
    _mock_auth()
    respx.get(WI_URI).mock(return_value=httpx.Response(200, json=WI_JSON_NO_SHAPE))
    result = await inspect_workitem(WI_ID)
    assert result["shape_uri"] is None
    assert result["factory_uri"] is None
    assert result["type"] == "Task"


# ======================================================================
# list_folders
# ======================================================================
@respx.mock
async def test_list_folders_returns_titles_and_uris():
    """15. SP->component->folders JSON; non-system folders have title + uri."""
    _mock_rm_discovery(services_xml=RM_SP_WITH_COMPONENT_XML)
    respx.get(FOLDERS_URL).mock(return_value=httpx.Response(200, json=FOLDERS_JSON))
    result = await list_folders("PROJ")
    titles = {f["title"] for f in result}
    assert "Requirements" in titles
    assert "Designs" in titles
    reqs = next(f for f in result if f["title"] == "Requirements")
    assert reqs["uri"] == f"{BASE}/rm/folders/_reqs"


@respx.mock
async def test_list_folders_filters_system_folders():
    """16. 'root' and 'Folder Query Results' excluded (A6, tenant-neutral)."""
    _mock_rm_discovery(services_xml=RM_SP_WITH_COMPONENT_XML)
    respx.get(FOLDERS_URL).mock(return_value=httpx.Response(200, json=FOLDERS_JSON))
    result = await list_folders("PROJ")
    titles = {f["title"] for f in result}
    assert "root" not in titles
    assert "Folder Query Results" not in titles


@respx.mock
async def test_list_folders_no_component_returns_empty():
    """17. SP without oslc_config:component -> [] (A4, degrades)."""
    _mock_rm_discovery(services_xml=RM_SP_NO_COMPONENT_XML)
    result = await list_folders("PROJ")
    assert result == []


@respx.mock
async def test_list_folders_project_not_found_raises_toolerror():
    """18. unknown project -> _resolve_project_url ValueError -> ToolError."""
    _mock_rm_discovery(services_xml=RM_SP_WITH_COMPONENT_XML)
    with pytest.raises(ToolError, match="(?i)not found|não encontrad"):
        await list_folders("GHOST PROJECT")
