"""RED tests (G3) for SPEC-diagnostico-gate, wave W1 (release v0.6.0).

Target behavior: every HTTP error from a read or write tool becomes a ToolError that
NAMES the gate that probably blocked the call and the action for the user.

Single diagnosis function (name suggested by the SPEC, the developer may rename it
and update this file):

    elm_mcp.oslc.diagnose_http_error(resp, operation, uri) -> str

Gate table (SPEC):

    401 at login (auth-failure header)   credencial
    401 after a valid session            sessao   (client re-authenticates ONCE first)
    403 with oslc:message                regra-de-processo (server message verbatim)
    403 without message after re-auth    permissao
    404 on a discovered resource         recurso-inexistente-ou-invisivel
    412                                  conflito
    406 on QM                            formato  (QM only answers RDF/XML)

"Suggested action" is checked with a loose per-gate keyword pattern (ACTION_HINT),
not an exact sentence, so the wording stays free. Gate names are the literal ASCII
tokens of the SPEC table.

Everything is tenant-neutral: https://elm.example.com, user jdoe.
"""

import re

import httpx
import pytest
import respx
from mcp.server.mcpserver.exceptions import ToolError

from elm_mcp import oslc, server
from elm_mcp.auth import close_client

from .w1_fixtures import BASE, FACTORY_URL, PROJECT, SHAPE_REQ, mock_rm_discovery

LOGIN_URL = f"{BASE}/jts/j_security_check"
AUTH_FAIL_HEADER = "X-com-ibm-team-repository-web-auth-msg"
RM_URI = f"{BASE}/rm/resources/_req1"
QM_URI = f"{BASE}/qm/service/com.ibm.rqm.integration.service.IIntegrationService/resources/_proj/testcase/urn:com.ibm.rqm:testcase:1"
PASSWORD = "s3cr3t-pw"
SESSION_COOKIE = "JSESSIONID=abc123def456"
BASIC_AUTH = "Basic amRvZTpzM2NyM3QtcHc="

# Loose "suggested action" keywords per gate (case-insensitive, PT-BR or EN).
ACTION_HINT = {
    "credencial": r"ELM_USER|ELM_PASSWORD|user|usu[aá]rio|password|senha",
    "sessao": r"re-?authent|reautentic|expir|session|sess[aã]o|retry|tente",
    "permissao": r"role|papel|licen[sc]|permission|permiss|visib",
    "recurso-inexistente-ou-invisivel": r"visib|URI|project area|exist",
    "conflito": r"re-?read|reler|recarreg|reload|retry|repet|tente",
    "formato": r"RDF/XML",
}

SERVER_MESSAGE = "CRRRS6404E: Operação \"save\" bloqueada: campo 'Owner' é obrigatório & <b> não permitido"


def _json_message(message: str) -> httpx.Response:
    return httpx.Response(403, json={"oslc:message": message, "oslc:statusCode": "403"})


@pytest.fixture(autouse=True)
async def cleanup(monkeypatch):
    monkeypatch.setattr("elm_mcp.config.settings.elm_url", BASE)
    monkeypatch.setattr("elm_mcp.config.settings.elm_user", "jdoe")
    monkeypatch.setattr("elm_mcp.config.settings.elm_password", PASSWORD)
    yield
    await close_client()


def _response(status: int, *, headers=None, text="", content=None, uri=RM_URI) -> httpx.Response:
    request = httpx.Request("GET", uri, headers={"Authorization": BASIC_AUTH, "Cookie": SESSION_COOKIE})
    all_headers = {"Set-Cookie": SESSION_COOKIE, **(headers or {})}
    if content is not None:
        return httpx.Response(status, headers=all_headers, content=content, request=request)
    return httpx.Response(status, headers=all_headers, text=text, request=request)


def _assert_gate(message: str, gate: str) -> None:
    assert isinstance(message, str)
    assert gate in message, f"gate {gate!r} not named in: {message!r}"
    if gate in ACTION_HINT:
        assert re.search(ACTION_HINT[gate], message, re.IGNORECASE), f"no suggested action for {gate!r}: {message!r}"


# ======================================================================
# The single diagnosis function, tested directly
# ======================================================================
UNRECOGNIZABLE_BODIES = [
    pytest.param({"text": ""}, id="empty-body"),
    pytest.param({"text": "<html><body>Oops</body></html>"}, id="html-body"),
    pytest.param({"content": b"\xff\xfe\x00garbage"}, id="binary-body"),
]

# (status, extra headers, operation, uri, expected gate)
STATUS_CASES = [
    pytest.param(
        401, {AUTH_FAIL_HEADER: "authfailed"}, "login", f"{BASE}/jts/j_security_check", "credencial", id="401-login"
    ),
    pytest.param(401, {}, "get_requirement", RM_URI, "sessao", id="401-session"),
    pytest.param(403, {}, "update_requirement", RM_URI, "permissao", id="403-no-message"),
    pytest.param(404, {}, "get_requirement", RM_URI, "recurso-inexistente-ou-invisivel", id="404"),
    pytest.param(412, {}, "update_requirement", RM_URI, "conflito", id="412"),
    pytest.param(406, {}, "search_testcases", QM_URI, "formato", id="406-qm"),
]


@pytest.mark.parametrize(("status", "headers", "operation", "uri", "gate"), STATUS_CASES)
def test_diagnose_names_gate_and_action_for_each_status(status, headers, operation, uri, gate):
    """FAILS today: elm_mcp.oslc.diagnose_http_error does not exist (AttributeError)."""
    message = oslc.diagnose_http_error(_response(status, headers=headers, uri=uri), operation, uri)
    _assert_gate(message, gate)


@pytest.mark.parametrize("body", UNRECOGNIZABLE_BODIES)
@pytest.mark.parametrize(("status", "headers", "operation", "uri", "gate"), STATUS_CASES)
def test_diagnose_names_gate_even_without_recognizable_body(body, status, headers, operation, uri, gate):
    """FAILS today: no diagnosis function. The gate must come from the status alone."""
    message = oslc.diagnose_http_error(_response(status, headers=headers, uri=uri, **body), operation, uri)
    _assert_gate(message, gate)


def test_diagnose_403_with_oslc_message_json_passes_message_verbatim_as_process_rule():
    """FAILS today: no diagnosis function. Message intact, gate is regra-de-processo, not permissao."""
    resp = httpx.Response(403, json={"oslc:message": SERVER_MESSAGE}, request=httpx.Request("PUT", RM_URI))
    message = oslc.diagnose_http_error(resp, "update_requirement", RM_URI)
    assert "regra-de-processo" in message
    assert SERVER_MESSAGE in message
    assert "permissao" not in message


def test_diagnose_403_with_oslc_message_xml_passes_message_verbatim_as_process_rule():
    """FAILS today: no diagnosis function. The XML form of oslc:message is also recognized."""
    text = "Operação bloqueada pela regra de processo: campo Owner obrigatório"
    body = (
        f'<oslc:Error xmlns:oslc="http://open-services.net/ns/core#"><oslc:message>{text}</oslc:message></oslc:Error>'
    )
    resp = httpx.Response(403, text=body, request=httpx.Request("POST", FACTORY_URL))
    message = oslc.diagnose_http_error(resp, "create_requirement", FACTORY_URL)
    assert "regra-de-processo" in message
    assert text in message


@pytest.mark.parametrize(
    ("status", "headers", "gate"),
    [
        (401, {AUTH_FAIL_HEADER: "authfailed"}, "credencial"),
        (401, {}, "sessao"),
        (403, {}, "permissao"),
        (404, {}, "recurso-inexistente-ou-invisivel"),
        (412, {}, "conflito"),
    ],
)
def test_diagnose_never_leaks_password_cookie_or_authorization(status, headers, gate):
    """FAILS today: no diagnosis function. Request/response secrets must stay out of the message."""
    message = oslc.diagnose_http_error(_response(status, headers=headers), "get_requirement", RM_URI)
    assert gate in message
    for secret in (PASSWORD, SESSION_COOKIE, "abc123def456", BASIC_AUTH, "Bearer"):
        assert secret not in message


# ======================================================================
# End to end through the tools — READ (get_requirement)
# ======================================================================
@respx.mock
async def test_read_404_raises_toolerror_naming_gate():
    """FAILS today: raise_for_status() leaks a bare HTTPStatusError (not a ToolError)."""
    respx.post(LOGIN_URL).mock(return_value=httpx.Response(200))
    respx.get(RM_URI).mock(return_value=httpx.Response(404, text="Not Found"))

    with pytest.raises(ToolError) as exc_info:
        await server.get_requirement(RM_URI)

    _assert_gate(str(exc_info.value), "recurso-inexistente-ou-invisivel")


@respx.mock
async def test_read_403_with_oslc_message_passes_message_intact():
    """FAILS today: HTTPStatusError instead of a ToolError carrying the server message."""
    respx.post(LOGIN_URL).mock(return_value=httpx.Response(200))
    respx.get(RM_URI).mock(return_value=_json_message(SERVER_MESSAGE))

    with pytest.raises(ToolError) as exc_info:
        await server.get_requirement(RM_URI)

    message = str(exc_info.value)
    assert "regra-de-processo" in message
    assert SERVER_MESSAGE in message


@respx.mock
async def test_read_403_without_message_reauthenticates_once_then_reports_permissao():
    """FAILS today: no reauthentication on a read, and a bare HTTPStatusError."""
    login = respx.post(LOGIN_URL).mock(return_value=httpx.Response(200))
    get_route = respx.get(RM_URI).mock(return_value=httpx.Response(403, text="Forbidden"))

    with pytest.raises(ToolError) as exc_info:
        await server.get_requirement(RM_URI)

    _assert_gate(str(exc_info.value), "permissao")
    assert login.call_count == 2, "expected the initial login plus exactly one re-authentication"
    assert get_route.call_count == 2


@respx.mock
async def test_read_401_after_session_reauthenticates_once_then_reports_sessao():
    """FAILS today: no reauthentication on a read, and a bare HTTPStatusError."""
    login = respx.post(LOGIN_URL).mock(return_value=httpx.Response(200))
    get_route = respx.get(RM_URI).mock(return_value=httpx.Response(401, text="Unauthorized"))

    with pytest.raises(ToolError) as exc_info:
        await server.get_requirement(RM_URI)

    _assert_gate(str(exc_info.value), "sessao")
    assert login.call_count == 2, "expected the initial login plus exactly one re-authentication"
    assert get_route.call_count == 2


@respx.mock
async def test_read_401_then_ok_after_reauthentication_returns_the_resource():
    """FAILS today: the first 401 is fatal; the read is never retried after re-authenticating."""
    login = respx.post(LOGIN_URL).mock(return_value=httpx.Response(200))
    respx.get(RM_URI).mock(
        side_effect=[httpx.Response(401, text="Unauthorized"), httpx.Response(200, json={"dcterms:title": "ok"})]
    )

    result = await server.get_requirement(RM_URI)

    assert result == {"dcterms:title": "ok"}
    assert login.call_count == 2


@respx.mock
async def test_read_406_on_qm_uri_names_formato_gate():
    """FAILS today: bare HTTPStatusError. QM only answers RDF/XML."""
    respx.post(LOGIN_URL).mock(return_value=httpx.Response(200))
    respx.get(QM_URI).mock(return_value=httpx.Response(406, text="Not Acceptable"))

    with pytest.raises(ToolError) as exc_info:
        await server.get_requirement(QM_URI)

    _assert_gate(str(exc_info.value), "formato")


@pytest.mark.parametrize("login_status", [200, 401])
@respx.mock
async def test_login_failure_names_credencial_gate_without_leaking_password(login_status):
    """FAILS today: RuntimeError 'ELM authentication failed for user ...' has no gate name."""
    respx.post(LOGIN_URL).mock(return_value=httpx.Response(login_status, headers={AUTH_FAIL_HEADER: "authfailed"}))

    with pytest.raises(ToolError) as exc_info:
        await server.get_requirement(RM_URI)

    message = str(exc_info.value)
    _assert_gate(message, "credencial")
    assert PASSWORD not in message


# ======================================================================
# End to end through the tools — WRITE
# ======================================================================
@respx.mock
async def test_write_create_requirement_403_with_message_passes_message_intact():
    """FAILS today: after the retry, raise_for_status() leaks a bare HTTPStatusError."""
    mock_rm_discovery()
    respx.post(FACTORY_URL).mock(return_value=_json_message(SERVER_MESSAGE))

    with pytest.raises(ToolError) as exc_info:
        await server.create_requirement(
            project=PROJECT, title="Req", content="<p>x</p>", type=SHAPE_REQ, if_exists="create"
        )

    message = str(exc_info.value)
    assert "regra-de-processo" in message
    assert SERVER_MESSAGE in message


@respx.mock
async def test_write_create_requirement_403_without_message_reports_permissao_after_reauth():
    """FAILS today: bare HTTPStatusError; the gate is never named."""
    mock_rm_discovery()
    post_route = respx.post(FACTORY_URL).mock(return_value=httpx.Response(403, text="Forbidden"))

    with pytest.raises(ToolError) as exc_info:
        await server.create_requirement(
            project=PROJECT, title="Req", content="<p>x</p>", type=SHAPE_REQ, if_exists="create"
        )

    _assert_gate(str(exc_info.value), "permissao")
    assert post_route.call_count == 2, "one write, one re-authenticated retry, then report"


@respx.mock
async def test_write_update_workitem_412_names_conflito_gate():
    """FAILS today: the message is English ('Conflict: ...') and never names the gate 'conflito'."""
    respx.post(LOGIN_URL).mock(return_value=httpx.Response(200))
    uri = f"{BASE}/ccm/resource/itemName/com.ibm.team.workitem.WorkItem/99"
    respx.get(uri).mock(return_value=httpx.Response(200, json={"dcterms:title": "t"}, headers={"ETag": '"e1"'}))
    respx.put(uri).mock(return_value=httpx.Response(412, text="Precondition Failed"))

    with pytest.raises(ToolError) as exc_info:
        await server.update_workitem(id="99", title="X")

    _assert_gate(str(exc_info.value), "conflito")
