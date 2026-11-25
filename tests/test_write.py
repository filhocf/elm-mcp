"""Tests for write operations (create/update work items)."""

import httpx
import pytest
import respx
from mcp.server.mcpserver.exceptions import ToolError

from elm_mcp import oslc
from elm_mcp.auth import close_client
from elm_mcp.server import add_child_workitem, create_workitem, update_workitem

BASE = "https://elm.example.com"

CCM_ROOTSERVICES_XML = f"""<?xml version="1.0" encoding="UTF-8"?>
<rdf:Description xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#"
    xmlns:oslc_cm="http://open-services.net/xmlns/cm/1.0/">
    <oslc_cm:cmServiceProviders
        rdf:resource="{BASE}/ccm/oslc/workitems/catalog"/>
</rdf:Description>
"""

CCM_CATALOG_XML = f"""<?xml version="1.0" encoding="UTF-8"?>
<rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#"
    xmlns:oslc="http://open-services.net/ns/core#"
    xmlns:dcterms="http://purl.org/dc/terms/">
    <oslc:ServiceProvider rdf:about="{BASE}/ccm/oslc/contexts/_MWxBEJB7Ee-fe_bes9r78g">
        <dcterms:title>MY PROJECT (KEY)</dcterms:title>
    </oslc:ServiceProvider>
</rdf:RDF>
"""

CCM_SERVICES_XML = f"""<?xml version="1.0" encoding="UTF-8"?>
<rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#"
    xmlns:oslc="http://open-services.net/ns/core#"
    xmlns:dcterms="http://purl.org/dc/terms/">
    <oslc:CreationFactory>
        <dcterms:title>Location for creation of Defect change requests </dcterms:title>
        <oslc:resourceType rdf:resource="http://open-services.net/ns/cm#ChangeRequest"/>
        <oslc:creation rdf:resource="{BASE}/ccm/oslc/contexts/_MWxBEJB7Ee-fe_bes9r78g/workitems/defect"/>
        <oslc:resourceShape rdf:resource="{BASE}/ccm/oslc/context/_MWxBEJB7Ee-fe_bes9r78g/shapes/workitems/defect"/>
    </oslc:CreationFactory>
    <oslc:CreationFactory>
        <dcterms:title>Location for creation of Task change requests </dcterms:title>
        <oslc:resourceType rdf:resource="http://open-services.net/ns/cm#ChangeRequest"/>
        <oslc:creation rdf:resource="{BASE}/ccm/oslc/contexts/_MWxBEJB7Ee-fe_bes9r78g/workitems/task"/>
        <oslc:resourceShape rdf:resource="{BASE}/ccm/oslc/context/_MWxBEJB7Ee-fe_bes9r78g/shapes/workitems/task"/>
    </oslc:CreationFactory>
    <oslc:CreationFactory>
        <dcterms:title>Location for creation of Story change requests </dcterms:title>
        <oslc:resourceType rdf:resource="http://open-services.net/ns/cm#ChangeRequest"/>
        <oslc:creation rdf:resource="{BASE}/ccm/oslc/contexts/_MWxBEJB7Ee-fe_bes9r78g/workitems/com.ibm.team.apt.workItemType.story"/>
        <oslc:resourceShape rdf:resource="{BASE}/ccm/oslc/context/_MWxBEJB7Ee-fe_bes9r78g/shapes/workitems/story"/>
    </oslc:CreationFactory>
    <oslc:CreationFactory>
        <dcterms:title>Location for creation of Defect change requests </dcterms:title>
        <oslc:resourceType rdf:resource="http://open-services.net/ns/cm#ChangeRequest"/>
        <oslc:creation rdf:resource="{BASE}/ccm/oslc/contexts/_MWxBEJB7Ee-fe_bes9r78g/workitems"/>
        <oslc:resourceShape rdf:resource="{BASE}/ccm/oslc/context/_MWxBEJB7Ee-fe_bes9r78g/shapes/workitems/defect"/>
    </oslc:CreationFactory>
</rdf:RDF>
"""

CREATED_WI = {
    "dcterms:title": "New Task",
    "dcterms:identifier": "42",
    "rdf:about": f"{BASE}/ccm/resource/itemName/com.ibm.team.workitem.WorkItem/42",
}


@pytest.fixture(autouse=True)
async def cleanup(monkeypatch):
    monkeypatch.setattr("elm_mcp.config.settings.elm_url", BASE)
    yield
    await close_client()


def _mock_auth():
    respx.post(f"{BASE}/jts/j_security_check").mock(return_value=httpx.Response(200))


def _mock_ccm_discovery():
    _mock_auth()
    respx.get(f"{BASE}/ccm/rootservices").mock(return_value=httpx.Response(200, text=CCM_ROOTSERVICES_XML))
    respx.get(f"{BASE}/ccm/oslc/workitems/catalog").mock(return_value=httpx.Response(200, text=CCM_CATALOG_XML))
    respx.get(f"{BASE}/ccm/oslc/contexts/_MWxBEJB7Ee-fe_bes9r78g/services").mock(
        return_value=httpx.Response(200, text=CCM_SERVICES_XML)
    )
    # Session refresh GET (create_resource does this before POST)
    respx.get(f"{BASE}/ccm/oslc/contexts/_MWxBEJB7Ee-fe_bes9r78g/workitems").mock(
        return_value=httpx.Response(200, json={})
    )
    # create_workitem reads the shape of the type to list missing required fields (none here)
    respx.get(url__regex=r".*/shapes/workitems/.*").mock(
        return_value=httpx.Response(
            200, text='<rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#"></rdf:RDF>'
        )
    )


@respx.mock
async def test_create_resource():
    _mock_ccm_discovery()
    respx.post(f"{BASE}/ccm/oslc/contexts/_MWxBEJB7Ee-fe_bes9r78g/workitems").mock(
        return_value=httpx.Response(201, json=CREATED_WI)
    )
    result = await oslc.create_resource("ccm", "MY PROJECT (KEY)", {"dcterms:title": "New Task"})
    assert result["dcterms:identifier"] == "42"


@respx.mock
async def test_update_resource():
    _mock_auth()
    uri = f"{BASE}/ccm/resource/itemName/com.ibm.team.workitem.WorkItem/42"
    updated = {**CREATED_WI, "dcterms:title": "Updated Title"}
    # F1/D11: the return value is the re-read item (second GET), not the partial PUT response.
    respx.get(uri).mock(
        side_effect=[
            httpx.Response(200, json=CREATED_WI, headers={"ETag": '"abc123"'}),
            httpx.Response(200, json=updated, headers={"ETag": '"abc124"'}),
        ]
    )
    respx.put(uri).mock(return_value=httpx.Response(200, json={"rdf:about": uri}))
    result = await oslc.update_resource(uri, {"dcterms:title": "Updated Title"})
    assert result.get("dcterms:title") == "Updated Title"


@respx.mock
async def test_create_workitem_tool():
    _mock_ccm_discovery()
    respx.post(f"{BASE}/ccm/oslc/contexts/_MWxBEJB7Ee-fe_bes9r78g/workitems/task").mock(
        return_value=httpx.Response(201, json=CREATED_WI)
    )
    result = await create_workitem(project="MY PROJECT (KEY)", title="New Task", type="task", if_exists="create")
    assert result["id"] == "42"


@respx.mock
async def test_create_workitem_with_parent():
    _mock_ccm_discovery()
    respx.post(f"{BASE}/ccm/oslc/contexts/_MWxBEJB7Ee-fe_bes9r78g/workitems/task").mock(
        return_value=httpx.Response(201, json=CREATED_WI)
    )
    result = await create_workitem(
        project="MY PROJECT (KEY)", title="Child Task", type="task", parent_id="10", if_exists="create"
    )
    assert result["id"] == "42"


@respx.mock
async def test_update_workitem_tool():
    _mock_auth()
    uri = f"{BASE}/ccm/resource/itemName/com.ibm.team.workitem.WorkItem/42"
    updated = {**CREATED_WI, "dcterms:title": "New Title"}
    respx.get(uri).mock(
        side_effect=[
            httpx.Response(200, json=CREATED_WI, headers={"ETag": '"etag1"'}),
            httpx.Response(200, json=updated, headers={"ETag": '"etag2"'}),
        ]
    )
    respx.put(uri).mock(return_value=httpx.Response(200, json={"rdf:about": uri}))
    result = await update_workitem(id="42", title="New Title", verbose=True)
    assert result.get("dcterms:title") == "New Title"


@respx.mock
async def test_add_child_workitem_tool():
    _mock_ccm_discovery()
    respx.post(f"{BASE}/ccm/oslc/contexts/_MWxBEJB7Ee-fe_bes9r78g/workitems/task").mock(
        return_value=httpx.Response(201, json=CREATED_WI)
    )
    result = await add_child_workitem(parent_id="10", title="Sub-task", project="MY PROJECT (KEY)", if_exists="create")
    assert result["id"] == "42"


async def test_add_child_workitem_requires_project():
    """project é obrigatório (sem default de tenant): chamar sem ele é TypeError."""
    with pytest.raises(TypeError):
        await add_child_workitem(parent_id="10", title="Sub-task", if_exists="create")


async def test_create_workitem_invalid_type(w2_router, monkeypatch):
    """Unknown type: refused with the project's available types (discovered; no network, nothing created)."""
    from .w2_fixtures import PROJECT, mock_ccm

    monkeypatch.setattr("elm_mcp.config.settings.elm_url", BASE)
    mock_ccm(w2_router)
    with pytest.raises(ToolError, match="Available types: .*task") as exc:
        await create_workitem(project=PROJECT, title="X", type="epic", if_exists="create")
    assert "epic" in str(exc.value)


async def test_update_workitem_no_fields():
    with pytest.raises(ToolError, match="At least one field"):
        await update_workitem(id="1")


@respx.mock
async def test_create_resource_post_400():
    _mock_ccm_discovery()
    respx.post(f"{BASE}/ccm/oslc/contexts/_MWxBEJB7Ee-fe_bes9r78g/workitems").mock(
        return_value=httpx.Response(400, text="Bad Request")
    )
    with pytest.raises(ValueError, match="HTTP 400"):
        await oslc.create_resource("ccm", "MY PROJECT (KEY)", {"dcterms:title": "X"})


@respx.mock
async def test_create_resource_post_401():
    _mock_ccm_discovery()
    respx.post(f"{BASE}/ccm/oslc/contexts/_MWxBEJB7Ee-fe_bes9r78g/workitems").mock(
        return_value=httpx.Response(401, text="Unauthorized")
    )
    with pytest.raises(ValueError, match="sessao"):
        await oslc.create_resource("ccm", "MY PROJECT (KEY)", {"dcterms:title": "X"})


@respx.mock
async def test_update_resource_put_409():
    _mock_auth()
    uri = f"{BASE}/ccm/resource/itemName/com.ibm.team.workitem.WorkItem/99"
    respx.get(uri).mock(return_value=httpx.Response(200, json=CREATED_WI, headers={"ETag": '"e1"'}))
    respx.put(uri).mock(return_value=httpx.Response(409, text="Conflict"))
    with pytest.raises(ValueError, match="HTTP 409"):
        await oslc.update_resource(uri, {"dcterms:title": "X"})


@respx.mock
async def test_update_resource_put_412():
    _mock_auth()
    uri = f"{BASE}/ccm/resource/itemName/com.ibm.team.workitem.WorkItem/99"
    respx.get(uri).mock(return_value=httpx.Response(200, json=CREATED_WI, headers={"ETag": '"e1"'}))
    put_route = respx.put(uri).mock(return_value=httpx.Response(412, text="Precondition Failed"))
    # F1/D11: 412 is reported as an ETag conflict (was a bare HTTPStatusError).
    with pytest.raises(ValueError, match=r"(?i)conflit|conflict"):
        await oslc.update_resource(uri, {"dcterms:title": "X"})
    assert put_route.call_count == 1


@respx.mock
async def test_update_resource_no_etag():
    _mock_auth()
    uri = f"{BASE}/ccm/resource/itemName/com.ibm.team.workitem.WorkItem/99"
    respx.get(uri).mock(return_value=httpx.Response(200, json=CREATED_WI))
    with pytest.raises(ValueError, match="did not return an ETag"):
        await oslc.update_resource(uri, {"dcterms:title": "X"})


@respx.mock
async def test_create_resource_posts_to_workitems_url():
    """create_resource must POST to /contexts/{id}/workitems regardless of services.xml content."""
    _mock_ccm_discovery()
    post_route = respx.post(f"{BASE}/ccm/oslc/contexts/_MWxBEJB7Ee-fe_bes9r78g/workitems").mock(
        return_value=httpx.Response(201, json=CREATED_WI)
    )
    result = await oslc.create_resource("ccm", "MY PROJECT (KEY)", {"dcterms:title": "X"})
    assert post_route.called
    assert result["dcterms:identifier"] == "42"


async def test_create_resource_non_ccm_domain():
    with pytest.raises(NotImplementedError, match="only supports 'ccm'"):
        await oslc.create_resource("rm", "SomeProject", {"dcterms:title": "X"})


# --- Tests for custom_fields and _find_current_iteration ---


ITERATIONS_JSON = {
    "oslc:results": [
        {"rdf:resource": f"{BASE}/ccm/oslc/iterations/_iter1"},
        {"rdf:resource": f"{BASE}/ccm/oslc/iterations/_iter2"},
        {"rdf:resource": f"{BASE}/ccm/oslc/iterations/_iter3"},
    ]
}

ITER_DETAIL_1 = {
    "dcterms:title": "Entrega 4",
    "rtc_cm:startDate": "2026-04-01T00:00:00.000Z",
    "rtc_cm:endDate": "2026-04-30T00:00:00.000Z",
}
ITER_DETAIL_2 = {
    "dcterms:title": "Sprint 2",
    "rtc_cm:startDate": "2026-05-01T00:00:00.000Z",
    "rtc_cm:endDate": "2026-12-31T00:00:00.000Z",
}
ITER_DETAIL_3 = {
    "dcterms:title": "Entrega 6",
    "rtc_cm:startDate": "2027-01-01T00:00:00.000Z",
    "rtc_cm:endDate": "2027-06-30T00:00:00.000Z",
}


def _mock_iteration_details():
    respx.get(f"{BASE}/ccm/oslc/iterations/_iter1").mock(return_value=httpx.Response(200, json=ITER_DETAIL_1))
    respx.get(f"{BASE}/ccm/oslc/iterations/_iter2").mock(return_value=httpx.Response(200, json=ITER_DETAIL_2))
    respx.get(f"{BASE}/ccm/oslc/iterations/_iter3").mock(return_value=httpx.Response(200, json=ITER_DETAIL_3))


@respx.mock
async def test_find_current_iteration_returns_current():
    _mock_auth()
    respx.get(f"{BASE}/ccm/oslc/iterations").mock(return_value=httpx.Response(200, json=ITERATIONS_JSON))
    _mock_iteration_details()
    project_url = f"{BASE}/ccm/oslc/contexts/_MWxBEJB7Ee-fe_bes9r78g"
    result = await oslc._find_current_iteration(project_url)
    # _iter2 has date range that includes "now" (2026-05-01 to 2026-12-31)
    assert result == f"{BASE}/ccm/oslc/iterations/_iter2"


@respx.mock
async def test_find_current_iteration_fallback_returns_none_if_all_past():
    """If no iteration contains 'now', return None."""
    _mock_auth()
    past_iters = {
        "oslc:results": [
            {"rdf:resource": f"{BASE}/ccm/oslc/iterations/_a"},
            {"rdf:resource": f"{BASE}/ccm/oslc/iterations/_b"},
        ]
    }
    respx.get(f"{BASE}/ccm/oslc/iterations").mock(return_value=httpx.Response(200, json=past_iters))
    respx.get(f"{BASE}/ccm/oslc/iterations/_a").mock(
        return_value=httpx.Response(
            200,
            json={
                "dcterms:title": "Old 1",
                "rtc_cm:startDate": "2024-01-01T00:00:00.000Z",
                "rtc_cm:endDate": "2024-06-30T00:00:00.000Z",
            },
        )
    )
    respx.get(f"{BASE}/ccm/oslc/iterations/_b").mock(
        return_value=httpx.Response(
            200,
            json={
                "dcterms:title": "Old 2",
                "rtc_cm:startDate": "2024-07-01T00:00:00.000Z",
                "rtc_cm:endDate": "2024-12-31T00:00:00.000Z",
            },
        )
    )
    project_url = f"{BASE}/ccm/oslc/contexts/_MWxBEJB7Ee-fe_bes9r78g"
    result = await oslc._find_current_iteration(project_url)
    assert result is None


@respx.mock
async def test_find_current_iteration_empty_returns_none():
    _mock_auth()
    respx.get(f"{BASE}/ccm/oslc/iterations").mock(return_value=httpx.Response(200, json={"oslc:results": []}))
    project_url = f"{BASE}/ccm/oslc/contexts/_MWxBEJB7Ee-fe_bes9r78g"
    result = await oslc._find_current_iteration(project_url)
    assert result is None


@respx.mock
async def test_find_current_iteration_http_error_returns_none():
    _mock_auth()
    respx.get(f"{BASE}/ccm/oslc/iterations").mock(return_value=httpx.Response(500, text="Server Error"))
    project_url = f"{BASE}/ccm/oslc/contexts/_MWxBEJB7Ee-fe_bes9r78g"
    result = await oslc._find_current_iteration(project_url)
    assert result is None


@respx.mock
async def test_create_workitem_with_custom_fields():
    """custom_fields dict should be injected into the RDF/XML payload."""
    _mock_ccm_discovery()
    respx.get(f"{BASE}/ccm/oslc/iterations").mock(return_value=httpx.Response(200, json={"oslc:results": []}))
    # Capture the POST body to verify custom fields are included
    post_route = respx.post(f"{BASE}/ccm/oslc/contexts/_MWxBEJB7Ee-fe_bes9r78g/workitems/task").mock(
        return_value=httpx.Response(201, json=CREATED_WI)
    )
    result = await create_workitem(
        project="MY PROJECT (KEY)",
        title="Test Task",
        type="task",
        custom_fields={"rtc_ext:com.company.team.workitem.attribute.categoriatarefa": "literal.l10"},
        if_exists="create",
    )
    assert result["id"] == "42"
    # Verify custom field is in the posted body
    posted_body = post_route.calls[0].request.content.decode()
    assert "categoriatarefa" in posted_body
    assert "literal.l10" in posted_body


@respx.mock
async def test_create_workitem_without_custom_fields():
    """Without custom_fields, no extra fields should be injected. plannedFor only if iteration found."""
    _mock_ccm_discovery()
    respx.get(f"{BASE}/ccm/oslc/iterations").mock(return_value=httpx.Response(200, json={"oslc:results": []}))
    post_route = respx.post(f"{BASE}/ccm/oslc/contexts/_MWxBEJB7Ee-fe_bes9r78g/workitems/task").mock(
        return_value=httpx.Response(201, json=CREATED_WI)
    )
    result = await create_workitem(
        project="MY PROJECT (KEY)",
        title="Test Task",
        type="task",
        if_exists="create",
    )
    assert result["id"] == "42"
    # No iterations available → no plannedFor in payload
    posted_body = post_route.calls[0].request.content.decode()
    assert "plannedFor" not in posted_body


# === GOLDEN PAYLOAD TESTS (based on real server validation 22/mai/2026) ===
# These tests verify the EXACT behavior that creates WIs successfully on elm.company.com
# Reference: WI #628430 (manual), WI #629768 (automated)

PROJECT_AREA_ID = "_MWxBEJB7Ee-fe_bes9r78g"
CATEGORY_OID = "_ekfVwJB7Ee-fe_bes9r78g"
ITERATION_OID = "_361O8T5XEfGJQth8TJaPLA"


@respx.mock
async def test_golden_creation_url_is_the_type_factory_not_the_generic_one():
    """F1/D9: the POST goes to the factory OF THE TYPE (…/workitems/task), never the generic …/workitems."""
    _mock_ccm_discovery()
    respx.get(f"{BASE}/ccm/oslc/iterations").mock(return_value=httpx.Response(200, json={"oslc:results": []}))
    post_route = respx.post(f"{BASE}/ccm/oslc/contexts/{PROJECT_AREA_ID}/workitems/task").mock(
        return_value=httpx.Response(201, json=CREATED_WI)
    )
    generic_route = respx.post(f"{BASE}/ccm/oslc/contexts/{PROJECT_AREA_ID}/workitems").mock(
        return_value=httpx.Response(201, json=CREATED_WI)
    )
    result = await create_workitem(project="MY PROJECT (KEY)", title="Test", type="task", if_exists="create")
    assert result["id"] == "42"
    assert post_route.call_count == 1
    assert generic_route.call_count == 0


@respx.mock
async def test_golden_content_type_is_rdf_xml():
    """POST Content-Type must be application/rdf+xml."""
    _mock_ccm_discovery()
    respx.get(f"{BASE}/ccm/oslc/iterations").mock(return_value=httpx.Response(200, json={"oslc:results": []}))
    post_route = respx.post(f"{BASE}/ccm/oslc/contexts/{PROJECT_AREA_ID}/workitems/task").mock(
        return_value=httpx.Response(201, json=CREATED_WI)
    )
    await create_workitem(project="MY PROJECT (KEY)", title="Test", type="task", if_exists="create")
    assert post_route.calls[0].request.headers["content-type"] == "application/rdf+xml"


@respx.mock
async def test_golden_category_uses_itemoid_format():
    """An explicit filed_against in /resource/itemOid/com.ibm.team.workitem.Category/{oid} format
    is sent as is; the shape default is ignored (D13 revised)."""
    _mock_ccm_discovery()
    post_route = respx.post(f"{BASE}/ccm/oslc/contexts/{PROJECT_AREA_ID}/workitems/task").mock(
        return_value=httpx.Response(201, json=CREATED_WI)
    )
    category = f"{BASE}/ccm/resource/itemOid/com.ibm.team.workitem.Category/{CATEGORY_OID}"
    await create_workitem(
        project="MY PROJECT (KEY)", title="Test", type="task", filed_against=category, if_exists="create"
    )
    body = post_route.calls[0].request.content.decode()
    assert f"resource/itemOid/com.ibm.team.workitem.Category/{CATEGORY_OID}" in body
    assert "/categories/_MWxBEJB7Ee-fe_bes9r78g/default" not in body


@respx.mock
async def test_create_workitem_without_filed_against_omits_it_and_ignores_shape_default():
    """No filed_against -> body has no filedAgainst (decoy shape default not used)."""
    _mock_ccm_discovery()
    post_route = respx.post(f"{BASE}/ccm/oslc/contexts/{PROJECT_AREA_ID}/workitems/task").mock(
        return_value=httpx.Response(201, json=CREATED_WI)
    )
    await create_workitem(project="MY PROJECT (KEY)", title="Test", type="task", if_exists="create")
    assert "filedAgainst" not in post_route.calls[0].request.content.decode()


@respx.mock
async def test_golden_custom_fields_as_rdf_resource():
    """custom_fields with rdf:resource values must render as rdf:resource attributes in XML."""
    _mock_ccm_discovery()
    respx.get(f"{BASE}/ccm/oslc/iterations").mock(return_value=httpx.Response(200, json={"oslc:results": []}))
    post_route = respx.post(f"{BASE}/ccm/oslc/contexts/{PROJECT_AREA_ID}/workitems/task").mock(
        return_value=httpx.Response(201, json=CREATED_WI)
    )
    await create_workitem(
        project="MY PROJECT (KEY)",
        title="Test",
        type="task",
        custom_fields={
            "rtc_ext:com.company.team.workitem.attribute.categoriatarefa": {
                "rdf:resource": f"{BASE}/ccm/oslc/enumerations/{PROJECT_AREA_ID}/com.company.team.workitem.enumeration.categoriatarefa/com.company.team.workitem.enumeration.categoriatarefa.literal.l10"
            }
        },
        if_exists="create",
    )
    body = post_route.calls[0].request.content.decode()
    assert "categoriatarefa" in body
    assert "rdf:resource" in body
    assert "literal.l10" in body


@respx.mock
async def test_golden_type_id_is_task_not_requirementchangerequest():
    """rtc_cm:type URI must use 'task' (not 'requirementChangeRequest' which is the calm:id)."""
    _mock_ccm_discovery()
    respx.get(f"{BASE}/ccm/oslc/iterations").mock(return_value=httpx.Response(200, json={"oslc:results": []}))
    post_route = respx.post(f"{BASE}/ccm/oslc/contexts/{PROJECT_AREA_ID}/workitems/task").mock(
        return_value=httpx.Response(201, json=CREATED_WI)
    )
    await create_workitem(project="MY PROJECT (KEY)", title="Test", type="task", if_exists="create")
    body = post_route.calls[0].request.content.decode()
    assert f"/types/{PROJECT_AREA_ID}/task" in body


@respx.mock
async def test_golden_plannedfor_iteration_format():
    """plannedFor must use /oslc/iterations/{iterationOid} (without projectAreaId in path)."""
    _mock_ccm_discovery()
    respx.get(f"{BASE}/ccm/oslc/iterations").mock(
        return_value=httpx.Response(
            200, json={"oslc:results": [{"rdf:resource": f"{BASE}/ccm/oslc/iterations/{ITERATION_OID}"}]}
        )
    )
    # Mock iteration detail with current date range
    respx.get(f"{BASE}/ccm/oslc/iterations/{ITERATION_OID}").mock(
        return_value=httpx.Response(
            200,
            json={
                "dcterms:title": "Sprint 2",
                "rtc_cm:startDate": "2026-01-01T00:00:00.000Z",
                "rtc_cm:endDate": "2027-12-31T00:00:00.000Z",
            },
        )
    )
    post_route = respx.post(f"{BASE}/ccm/oslc/contexts/{PROJECT_AREA_ID}/workitems/task").mock(
        return_value=httpx.Response(201, json=CREATED_WI)
    )
    await create_workitem(project="MY PROJECT (KEY)", title="Test", type="task", if_exists="create")
    body = post_route.calls[0].request.content.decode()
    assert f"/oslc/iterations/{ITERATION_OID}" in body


# === #20: update_workitem expanded (owner, planned_for, estimate_hours, custom_fields) ===


@respx.mock
async def test_update_workitem_owner():
    """owner parameter must set dcterms:contributor with rdf:resource."""
    _mock_auth()
    uri = f"{BASE}/ccm/resource/itemName/com.ibm.team.workitem.WorkItem/42"
    updated = {**CREATED_WI, "dcterms:contributor": {"rdf:resource": f"{BASE}/jts/users/jdoe"}}
    respx.get(uri).mock(
        side_effect=[
            httpx.Response(200, json=CREATED_WI, headers={"ETag": '"e1"'}),
            httpx.Response(200, json=updated, headers={"ETag": '"e2"'}),
        ]
    )
    put_route = respx.put(uri).mock(return_value=httpx.Response(200, json={"rdf:about": uri}))
    result = await update_workitem(id="42", owner="jdoe", verbose=True)
    assert result.get("dcterms:contributor") == {"rdf:resource": f"{BASE}/jts/users/jdoe"}
    body = put_route.calls[0].request.content
    import json as _json

    sent = _json.loads(body)
    assert sent["dcterms:contributor"] == {"rdf:resource": f"{BASE}/jts/users/jdoe"}


@respx.mock
async def test_update_workitem_estimate_hours():
    """estimate_hours must set rtc_cm:estimate in milliseconds."""
    _mock_auth()
    uri = f"{BASE}/ccm/resource/itemName/com.ibm.team.workitem.WorkItem/42"
    respx.get(uri).mock(return_value=httpx.Response(200, json=CREATED_WI, headers={"ETag": '"e1"'}))
    updated = {**CREATED_WI, "rtc_cm:estimate": 28800000}
    put_route = respx.put(uri).mock(return_value=httpx.Response(200, json=updated))
    await update_workitem(id="42", estimate_hours=8)
    body = put_route.calls[0].request.content
    import json as _json

    sent = _json.loads(body)
    assert sent["rtc_cm:estimate"] == 28800000  # 8h * 3600000


@respx.mock
async def test_update_workitem_planned_for():
    """planned_for must set rtc_cm:plannedFor with rdf:resource."""
    _mock_auth()
    uri = f"{BASE}/ccm/resource/itemName/com.ibm.team.workitem.WorkItem/42"
    respx.get(uri).mock(return_value=httpx.Response(200, json=CREATED_WI, headers={"ETag": '"e1"'}))
    iteration_uri = f"{BASE}/ccm/oslc/iterations/_361O8T5XEfGJQth8TJaPLA"
    updated = {**CREATED_WI, "rtc_cm:plannedFor": {"rdf:resource": iteration_uri}}
    put_route = respx.put(uri).mock(return_value=httpx.Response(200, json=updated))
    await update_workitem(id="42", planned_for=iteration_uri)
    body = put_route.calls[0].request.content
    import json as _json

    sent = _json.loads(body)
    assert sent["rtc_cm:plannedFor"] == {"rdf:resource": iteration_uri}


@respx.mock
async def test_update_workitem_custom_fields():
    """custom_fields dict must be merged into the PUT payload."""
    _mock_auth()
    uri = f"{BASE}/ccm/resource/itemName/com.ibm.team.workitem.WorkItem/42"
    respx.get(uri).mock(return_value=httpx.Response(200, json=CREATED_WI, headers={"ETag": '"e1"'}))
    respx.put(uri).mock(return_value=httpx.Response(200, json=CREATED_WI))
    await update_workitem(id="42", custom_fields={"rtc_cm:someField": "value"})


@respx.mock
async def test_update_workitem_multiple_fields():
    """Multiple fields can be updated in a single call."""
    _mock_auth()
    uri = f"{BASE}/ccm/resource/itemName/com.ibm.team.workitem.WorkItem/42"
    respx.get(uri).mock(return_value=httpx.Response(200, json=CREATED_WI, headers={"ETag": '"e1"'}))
    put_route = respx.put(uri).mock(return_value=httpx.Response(200, json=CREATED_WI))
    await update_workitem(id="42", title="New Title", owner="jdoe", estimate_hours=16)
    body = put_route.calls[0].request.content
    import json as _json

    sent = _json.loads(body)
    assert sent["dcterms:title"] == "New Title"
    assert sent["dcterms:contributor"] == {"rdf:resource": f"{BASE}/jts/users/jdoe"}
    assert sent["rtc_cm:estimate"] == 57600000


# === #11: list_iterations tool ===

ITERATIONS_LIST_JSON = {
    "oslc:results": [
        {"rdf:resource": f"{BASE}/ccm/oslc/iterations/_iter1"},
        {"rdf:resource": f"{BASE}/ccm/oslc/iterations/_iter2"},
        {"rdf:resource": f"{BASE}/ccm/oslc/iterations/_iter3"},
    ]
}

ITER1_JSON = {"dcterms:title": "Backlog", "dcterms:identifier": "Backlog"}
ITER2_JSON = {
    "dcterms:title": "Sprint 1",
    "dcterms:identifier": "Sprint 1",
    "rtc_cm:startDate": "2026-05-01T00:00:00.000Z",
    "rtc_cm:endDate": "2026-05-15T00:00:00.000Z",
}
ITER3_JSON = {
    "dcterms:title": "Sprint 2",
    "dcterms:identifier": "Sprint 2",
    "rtc_cm:startDate": "2026-05-18T00:00:00.000Z",
    "rtc_cm:endDate": "2026-06-06T00:00:00.000Z",
}


@respx.mock
async def test_list_iterations():
    """list_iterations must return iterations with title, dates, and URI."""
    from elm_mcp.server import list_iterations

    _mock_ccm_discovery()
    respx.get(f"{BASE}/ccm/oslc/iterations").mock(return_value=httpx.Response(200, json=ITERATIONS_LIST_JSON))
    respx.get(f"{BASE}/ccm/oslc/iterations/_iter1").mock(return_value=httpx.Response(200, json=ITER1_JSON))
    respx.get(f"{BASE}/ccm/oslc/iterations/_iter2").mock(return_value=httpx.Response(200, json=ITER2_JSON))
    respx.get(f"{BASE}/ccm/oslc/iterations/_iter3").mock(return_value=httpx.Response(200, json=ITER3_JSON))
    result = await list_iterations(project="MY PROJECT (KEY)")
    assert len(result) == 3
    assert result[1]["title"] == "Sprint 1"
    assert result[1]["start_date"] == "2026-05-01"
    assert result[1]["uri"] == f"{BASE}/ccm/oslc/iterations/_iter2"
