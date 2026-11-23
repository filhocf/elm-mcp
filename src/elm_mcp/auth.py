"""Form-based Jazz authentication with cookie management."""

import asyncio
import logging
import weakref

import httpx

from .config import settings

# Suppress httpx info logs (interfere with MCP stdio)
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)

_client: httpx.AsyncClient | None = None


class SessionState:
    """Login serialization of one client: a lock and a generation that grows with every re-login."""

    def __init__(self) -> None:
        self.lock = asyncio.Lock()
        self.generation = 0


_sessions: "weakref.WeakKeyDictionary[httpx.AsyncClient, SessionState]" = weakref.WeakKeyDictionary()


def session_state(client: httpx.AsyncClient) -> SessionState:
    """The login state of ``client`` (created on first use; one per client, hence per event loop)."""
    state = _sessions.get(client)
    if state is None:
        state = _sessions[client] = SessionState()
    return state


async def _refuse_foreign_redirect(response: httpx.Response) -> None:
    """Response hook: a redirect that leaves the configured ELM origin is refused BEFORE it is followed.

    httpx follows redirects on its own (the session cookies would go with it); the hook sees the 3xx
    answer first and checks where ``Location`` points. Raises ValueError, as assert_same_origin does.
    """
    location = response.headers.get("location")
    if response.is_redirect and location:
        from .oslc import assert_same_origin  # lazy: oslc imports this module

        assert_same_origin(str(response.url.join(location)))


async def get_client() -> httpx.AsyncClient:
    """Get authenticated httpx client, re-authenticating if needed."""
    global _client
    if _client is None:
        client = httpx.AsyncClient(
            verify=False,
            timeout=60.0,
            follow_redirects=True,
            event_hooks={"response": [_refuse_foreign_redirect]},
        )
        try:
            await _authenticate(client)
        except BaseException:
            await client.aclose()  # never keep a client whose login failed
            raise
        _client = client
    return _client


async def _authenticate(client: httpx.AsyncClient) -> None:
    """Perform Jazz form-based authentication."""
    auth_url = f"{settings.elm_url}/jts/j_security_check"
    resp = await client.post(
        auth_url,
        data={"j_username": settings.elm_user, "j_password": settings.elm_password},
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    # Jazz returns 200 or 302 on success; an auth failure header (or a 401) means bad credentials
    if "X-com-ibm-team-repository-web-auth-msg" in resp.headers or resp.status_code == 401:
        raise RuntimeError(
            f"[credencial] ELM authentication failed for user '{settings.elm_user}': invalid user or password. "
            "Check ELM_USER and ELM_PASSWORD."
        )
    if resp.status_code == 403:
        raise RuntimeError(
            f"[credencial] ELM login refused (HTTP 403) for user '{settings.elm_user}': account locked or "
            "without permission to sign in. Check ELM_USER and ELM_PASSWORD."
        )
    if resp.status_code >= 400:
        raise RuntimeError(
            f"[http-{resp.status_code}] ELM login failed with HTTP {resp.status_code}: the server is "
            "unavailable or misconfigured. Retry later."
        )


async def close_client() -> None:
    """Close the HTTP client."""
    global _client
    if _client:
        await _client.aclose()
        _client = None
