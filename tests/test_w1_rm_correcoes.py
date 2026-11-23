"""RED tests (G3) for SPEC-rm-correcoes, wave W1 (release v0.6.0).

Scope: D3, D7, D8 and ``delete_requirement`` in ``direct`` mode ONLY. The changeset
mode and ``rm_write_mode`` are postponed by the manager decision (Gate -1); nothing
here exercises them.

Target behavior (EARS from the SPEC):

  D3  list_folders(project) queries folders whose parent is the PROJECT AREA URI
      (not the oslc_config:component URI, which the server rejects with a 400).
  D7  create_requirement without ``type`` is refused with a ToolError that lists the
      types of ``list_types("rm")``; NO POST is sent. ``type`` may also be a type
      title, resolved to its shape URI through the same discovery.
  D8  create_requirement returns the URI and the identifier READ FROM THE SERVER by
      re-reading the created artifact (the POST response body can be empty).
  --  delete_requirement(uri): DELETE, then confirm by re-reading (404 or 410);
      ToolError if the artifact is still readable. The tool must be registered.

Assumptions (revisit at G4 against a live server):
  A1. The service provider announces the project area through ``oslc:details``
      (``.../process/project-areas/<id>``). The test only pins the OBSERVABLE
      behavior: the ``oslc.where`` of the folder query is ``public_rm:parent=<that URI>``.
  A2. ``delete_requirement`` return value is not pinned beyond mentioning the URI.

Symbols that do not exist yet are reached through the ``server`` module attribute,
so each test fails individually (AttributeError at the call) instead of the whole
file failing at collection.

Fixtures are tenant-neutral: https://elm.example.com, "MY PROJECT (KEY)".
"""

import httpx
import pytest
import respx
from mcp.server.mcpserver.exceptions import ToolError

from elm_mcp import server
from elm_mcp.auth import close_client

from .w1_fixtures import (
    BASE,
    COMPONENT_URI,
    CREATED_RDF,
    CREATED_URI,
    FACTORY_URL,
    FOLDERS_JSON,
    FOLDERS_URL,
    PROJECT,
    PROJECT_AREA_URI,
    SHAPE_FEATURE,
    SHAPE_REQ,
    mock_rm_discovery,
)


@pytest.fixture(autouse=True)
async def cleanup(monkeypatch):
    monkeypatch.setattr("elm_mcp.config.settings.elm_url", BASE)
    yield
    await close_client()


# ======================================================================
# D3 — list_folders must use the project area URI as the parent
# ======================================================================
@respx.mock
async def test_d3_list_folders_queries_parent_equal_to_project_area_uri():
    """FAILS today: the query sends public_rm:parent=<component URI> (the 400 of D3)."""
    mock_rm_discovery()
    folders_route = respx.get(FOLDERS_URL).mock(return_value=httpx.Response(200, json=FOLDERS_JSON))

    result = await server.list_folders(PROJECT)

    assert folders_route.call_count == 1
    where = folders_route.calls[0].request.url.params.get("oslc.where")
    assert where == f"public_rm:parent={PROJECT_AREA_URI}", f"folder query used {where!r}"
    assert [f["title"] for f in result] == ["root", "Requirements"]


@respx.mock
async def test_d3_list_folders_does_not_send_component_uri():
    """FAILS today: the component URI must not appear anywhere in the folder query."""
    mock_rm_discovery()
    folders_route = respx.get(FOLDERS_URL).mock(return_value=httpx.Response(200, json=FOLDERS_JSON))

    await server.list_folders(PROJECT)

    assert folders_route.called
    assert COMPONENT_URI not in folders_route.calls[0].request.url.params.get("oslc.where", "")


# ======================================================================
# D7 — create_requirement requires a type
# ======================================================================
@respx.mock
async def test_d7_create_requirement_without_type_lists_rm_types_and_sends_no_post():
    """FAILS today: the tool POSTs without instanceShape (server answers 403 CRRRS6404E)."""
    mock_rm_discovery()
    post_route = respx.post(FACTORY_URL).mock(
        return_value=httpx.Response(201, headers={"Location": CREATED_URI}, text="")
    )

    with pytest.raises(ToolError) as exc_info:
        await server.create_requirement(project=PROJECT, title="Req", content="<p>x</p>", if_exists="create")

    message = str(exc_info.value)
    assert "Requirement" in message
    assert "Feature" in message
    assert post_route.call_count == 0


@respx.mock
async def test_d7_create_requirement_unknown_type_title_lists_valid_types_and_sends_no_post():
    """FAILS today: an unknown type string is blindly sent as the instanceShape."""
    mock_rm_discovery()
    post_route = respx.post(FACTORY_URL).mock(
        return_value=httpx.Response(201, headers={"Location": CREATED_URI}, text="")
    )

    with pytest.raises(ToolError) as exc_info:
        await server.create_requirement(
            project=PROJECT, title="Req", content="<p>x</p>", type="No Such Type", if_exists="create"
        )

    message = str(exc_info.value)
    assert "Requirement" in message
    assert "Feature" in message
    assert post_route.call_count == 0


@respx.mock
async def test_d7_create_requirement_type_title_is_resolved_to_shape_uri():
    """FAILS today: type='Feature' is written verbatim as oslc:instanceShape rdf:resource="Feature"."""
    mock_rm_discovery()
    post_route = respx.post(FACTORY_URL).mock(
        return_value=httpx.Response(201, headers={"Location": CREATED_URI}, text="")
    )
    respx.get(CREATED_URI).mock(return_value=httpx.Response(200, text=CREATED_RDF))

    await server.create_requirement(
        project=PROJECT, title="Req", content="<p>x</p>", type="Feature", if_exists="create"
    )

    assert post_route.call_count == 1
    body = post_route.calls[0].request.content.decode()
    assert f'<oslc:instanceShape rdf:resource="{SHAPE_FEATURE}"/>' in body


# ======================================================================
# D8 — identifier and URI come from re-reading the created artifact
# ======================================================================
@respx.mock
async def test_d8_create_requirement_returns_uri_and_identifier_read_from_server():
    """FAILS today: the POST body is empty, so identifier is None (no re-read)."""
    mock_rm_discovery()
    respx.post(FACTORY_URL).mock(return_value=httpx.Response(201, headers={"Location": CREATED_URI}, text=""))
    reread_route = respx.get(CREATED_URI).mock(return_value=httpx.Response(200, text=CREATED_RDF))

    result = await server.create_requirement(
        project=PROJECT,
        title="New Requirement",
        content="<p>x</p>",
        type=SHAPE_REQ,
        if_exists="create",
    )

    assert reread_route.called, "the created artifact was never re-read"
    assert result["uri"] == CREATED_URI
    assert result["identifier"] == "42"


# ======================================================================
# delete_requirement (direct mode)
# ======================================================================
async def test_delete_requirement_tool_is_registered():
    """FAILS today: no tool named delete_requirement, and it must take a required 'uri'."""
    tools = {t.name: t for t in await server.mcp.list_tools()}
    assert "delete_requirement" in tools
    assert "uri" in tools["delete_requirement"].input_schema["required"]


def _mock_delete_flow(uri: str, delete_status: int, reread_status_after_delete: int) -> respx.Route:
    """GET answers 200 until the DELETE was sent, then reread_status_after_delete."""
    respx.post(f"{BASE}/jts/j_security_check").mock(return_value=httpx.Response(200))
    delete_route = respx.delete(uri).mock(return_value=httpx.Response(delete_status))

    def _get(_request: httpx.Request) -> httpx.Response:
        if delete_route.called:
            return httpx.Response(reread_status_after_delete)
        return httpx.Response(200, text=CREATED_RDF, headers={"ETag": '"e1"'})

    respx.get(uri).mock(side_effect=_get)
    return delete_route


@pytest.mark.parametrize("delete_status", [200, 204])
@pytest.mark.parametrize("reread_status", [404, 410])
@respx.mock
async def test_delete_requirement_deletes_and_confirms_by_reread(delete_status, reread_status):
    """FAILS today: server.delete_requirement does not exist (AttributeError)."""
    delete_route = _mock_delete_flow(CREATED_URI, delete_status, reread_status)

    result = await server.delete_requirement(CREATED_URI)

    assert delete_route.call_count == 1
    assert CREATED_URI in str(result)


@respx.mock
async def test_delete_requirement_still_readable_after_delete_raises_toolerror():
    """FAILS today: no tool. When the re-read still returns 200 the delete is NOT confirmed."""
    respx.post(f"{BASE}/jts/j_security_check").mock(return_value=httpx.Response(200))
    delete_route = respx.delete(CREATED_URI).mock(return_value=httpx.Response(204))
    respx.get(CREATED_URI).mock(return_value=httpx.Response(200, text=CREATED_RDF, headers={"ETag": '"e1"'}))

    with pytest.raises(ToolError) as exc_info:
        await server.delete_requirement(CREATED_URI)

    assert delete_route.call_count == 1
    assert CREATED_URI in str(exc_info.value)
