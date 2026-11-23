"""RED tests (G3) for the G5 corrections, loop 2 (wave W1).

Items: create_requirement re-read never raises after the POST (HIGH), login with
403/5xx fails and is not cached (MEDIUM), list_folders without project area (MEDIUM),
delete_requirement with an unreadable confirmation (LOW).
"""

import logging

import httpx
import pytest
import respx
from mcp.server.mcpserver.exceptions import ToolError

from elm_mcp import server
from elm_mcp.auth import _authenticate, close_client, get_client

from .w1_fixtures import (
    BASE,
    CREATED_RDF,
    CREATED_URI,
    FACTORY_URL,
    FOLDERS_URL,
    PROJECT,
    SERVICES_URL,
    SERVICES_XML,
    SHAPE_REQ,
    mock_rm_discovery,
)

LOGIN_URL = f"{BASE}/jts/j_security_check"


@pytest.fixture(autouse=True)
async def cleanup(monkeypatch):
    monkeypatch.setattr("elm_mcp.config.settings.elm_url", BASE)
    yield
    await close_client()


# ======================================================================
# 1. create_requirement: the re-read (D8) must never raise after the POST
# ======================================================================
def _reread_failures():
    def _auth_runtime_error(route_calls: list) -> httpx.Response:
        route_calls.append(1)
        if len(route_calls) == 1:  # first login (get_client) succeeds
            return httpx.Response(200)
        return httpx.Response(200, headers={"X-com-ibm-team-repository-web-auth-msg": "authfailed"})

    return _auth_runtime_error


@pytest.mark.parametrize("failure", ["timeout", "http-500", "auth-runtime-error"])
@respx.mock
async def test_create_requirement_reread_failure_never_propagates(failure, caplog):
    mock_rm_discovery()
    post = respx.post(FACTORY_URL).mock(
        return_value=httpx.Response(201, headers={"Location": CREATED_URI}, text=CREATED_RDF)
    )
    if failure == "timeout":
        respx.get(CREATED_URI).mock(side_effect=httpx.ReadTimeout("slow"))
    elif failure == "http-500":
        respx.get(CREATED_URI).mock(return_value=httpx.Response(500))
    else:
        calls: list = []
        respx.post(LOGIN_URL).mock(side_effect=lambda _r: _reread_failures()(calls))
        respx.get(CREATED_URI).mock(return_value=httpx.Response(401))
    with caplog.at_level(logging.WARNING, logger="elm_mcp.oslc"):
        result = await server.create_requirement(
            project=PROJECT,
            title="New Requirement",
            content="<p>x</p>",
            type=SHAPE_REQ,
            if_exists="create",
        )

    assert post.call_count == 1, "the creation already happened: no second POST"
    assert result["uri"] == CREATED_URI
    assert result["identifier"] == "42", "identifier falls back to the POST body"
    assert result["warning"]
    assert any(r.levelno == logging.WARNING and CREATED_URI in r.getMessage() for r in caplog.records)


@respx.mock
async def test_create_requirement_reread_failure_with_empty_post_body_returns_none_identifier():
    mock_rm_discovery()
    respx.post(FACTORY_URL).mock(return_value=httpx.Response(201, headers={"Location": CREATED_URI}, text=""))
    respx.get(CREATED_URI).mock(return_value=httpx.Response(500))

    result = await server.create_requirement(
        project=PROJECT,
        title="New Requirement",
        content="<p>x</p>",
        type=SHAPE_REQ,
        if_exists="create",
    )

    assert result["uri"] == CREATED_URI
    assert result["identifier"] is None
    assert "warning" in result


@respx.mock
async def test_create_requirement_successful_reread_has_no_warning():
    mock_rm_discovery()
    respx.post(FACTORY_URL).mock(return_value=httpx.Response(201, headers={"Location": CREATED_URI}, text=""))
    respx.get(CREATED_URI).mock(return_value=httpx.Response(200, text=CREATED_RDF))

    result = await server.create_requirement(
        project=PROJECT,
        title="New Requirement",
        content="<p>x</p>",
        type=SHAPE_REQ,
        if_exists="create",
    )

    assert result == {"uri": CREATED_URI, "identifier": "42", "created": True}


# ======================================================================
# 2. login with 403 / 5xx fails and the client is not cached
# ======================================================================
@pytest.mark.parametrize(("status", "gate"), [(503, "[http-503]"), (500, "[http-500]"), (403, "[credencial]")])
@respx.mock
async def test_login_error_status_fails_with_gate(status, gate):
    respx.post(LOGIN_URL).mock(return_value=httpx.Response(status))
    client = httpx.AsyncClient(verify=False)
    try:
        with pytest.raises(RuntimeError, match=r"\[http-5\d\d\]|\[credencial\]") as exc_info:
            await _authenticate(client)
    finally:
        await client.aclose()
    assert gate in str(exc_info.value)


@respx.mock
async def test_login_503_is_not_cached_and_next_get_client_authenticates_again():
    login = respx.post(LOGIN_URL).mock(side_effect=[httpx.Response(503), httpx.Response(200)])

    with pytest.raises(RuntimeError, match=r"\[http-503\]"):
        await get_client()
    assert login.call_count == 1

    client = await get_client()

    assert login.call_count == 2, "the failed login must not leave a cached client"
    assert client is await get_client()
    assert login.call_count == 2


# ======================================================================
# 3. list_folders: project area not advertised is an error, not []
# ======================================================================
@respx.mock
async def test_list_folders_without_project_area_raises_clear_error():
    mock_rm_discovery()
    respx.get(SERVICES_URL).mock(
        return_value=httpx.Response(
            200, text=SERVICES_XML.replace(f'<oslc:details rdf:resource="{BASE}/process/project-areas/_PA1"/>', "")
        )
    )
    folders = respx.get(FOLDERS_URL)

    with pytest.raises(ToolError, match="project area not advertised by the service provider"):
        await server.list_folders(PROJECT)

    assert not folders.called


# ======================================================================
# 4. delete_requirement: unreadable confirmation = not confirmed (DELETE was sent)
# ======================================================================
@pytest.mark.parametrize("status", [401, 403])
@respx.mock
async def test_delete_requirement_confirmation_denied_reports_not_confirmed(status):
    respx.post(LOGIN_URL).mock(return_value=httpx.Response(200))
    delete_route = respx.delete(CREATED_URI).mock(return_value=httpx.Response(204))
    respx.get(CREATED_URI).mock(return_value=httpx.Response(status))

    with pytest.raises(ToolError) as exc_info:
        await server.delete_requirement(CREATED_URI)

    message = str(exc_info.value)
    assert delete_route.call_count == 1
    assert "not confirmed" in message
    assert "DELETE was sent" in message
    assert CREATED_URI in message
