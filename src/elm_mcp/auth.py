"""Form-based Jazz authentication with cookie management."""

import logging

import httpx

from .config import settings

# Suppress httpx info logs (interfere with MCP stdio)
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)

_client: httpx.AsyncClient | None = None


async def get_client() -> httpx.AsyncClient:
    """Get authenticated httpx client, re-authenticating if needed."""
    global _client
    if _client is None:
        client = httpx.AsyncClient(verify=False, timeout=60.0, follow_redirects=True)
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


async def close_client() -> None:
    """Close the HTTP client."""
    global _client
    if _client:
        await _client.aclose()
        _client = None
