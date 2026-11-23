"""Shared pytest fixtures.

Safety net (autouse, applies to EVERY test):
- ``elm_mcp.config.settings`` points at a fictitious tenant (https://elm.example.com, jdoe) and the
  ``~/.elm_creds.json`` fallback is unreachable, so a test can never log in with real credentials.
- Any real network connection raises ``RealNetworkBlocked("real network blocked ...")``, a
  ``BaseException`` (not an ``Exception``): production code with ``except Exception`` cannot swallow it
  and turn an accidental real call into a silent pass. respx routers
  (``@respx.mock``, ``w2_router``, ...) keep working: respx patches the same httpcore layer on top of
  this guard, and only calls it for pass-through routes.

``w2_router`` is a private respx router: every request of the test must match a route
registered on it (unmatched requests raise), so a W2 test can never reach a real server.
"""

import os
import shutil
import socket
import tempfile

# Neutralize the environment BEFORE ``elm_mcp.config`` is imported (it builds ``settings`` at import time
# and falls back to ~/.elm_creds.json): HOME -> empty temp dir, fictitious ELM_* variables.
_EMPTY_HOME = tempfile.mkdtemp(prefix="elm-mcp-tests-home-")
os.environ["HOME"] = _EMPTY_HOME
os.environ.pop("USERPROFILE", None)
os.environ["ELM_URL"] = "https://elm.example.com"
os.environ["ELM_USER"] = "jdoe"
os.environ["ELM_PASSWORD"] = "not-a-real-password"

import httpcore
import pytest
import respx

FAKE_URL = "https://elm.example.com"
FAKE_USER = "jdoe"
FAKE_PASSWORD = "not-a-real-password"


class RealNetworkBlocked(BaseException):
    """A test tried to reach a real server without a respx route."""


_BLOCKED = (
    "real network blocked by tests/conftest.py: this request did not match any respx route. "
    "Mock it (respx.mock / w2_router); tests must never reach a real server."
)


def pytest_sessionfinish(session, exitstatus):
    """Remove the temporary HOME created at import time."""
    shutil.rmtree(_EMPTY_HOME, ignore_errors=True)


@pytest.fixture(autouse=True)
def _no_real_credentials(monkeypatch, tmp_path):
    """Fictitious settings + no credentials-file fallback."""
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv("USERPROFILE", raising=False)
    monkeypatch.setattr("elm_mcp.config._load_creds_file", dict)
    monkeypatch.setattr("elm_mcp.config.settings.elm_url", FAKE_URL)
    monkeypatch.setattr("elm_mcp.config.settings.elm_user", FAKE_USER)
    monkeypatch.setattr("elm_mcp.config.settings.elm_password", FAKE_PASSWORD)


@pytest.fixture(autouse=True)
def _block_real_network(monkeypatch):
    """Raise on any real connection; respx mocks (installed later, on top) are unaffected."""

    # The names must stay ``handle_async_request``/``handle_request``: respx only wraps a patched
    # method whose ``__name__`` is the original one (otherwise its mock would be silently skipped).
    async def handle_async_request(self, request):
        raise RealNetworkBlocked(_BLOCKED)

    def handle_request(self, request):
        raise RealNetworkBlocked(_BLOCKED)

    real_connect = socket.socket.connect

    def _guarded_connect(self, address):
        if self.family in (socket.AF_INET, socket.AF_INET6):
            raise RealNetworkBlocked(_BLOCKED)
        return real_connect(self, address)

    monkeypatch.setattr(httpcore.AsyncConnectionPool, "handle_async_request", handle_async_request)
    monkeypatch.setattr(httpcore.ConnectionPool, "handle_request", handle_request)
    monkeypatch.setattr(socket.socket, "connect", _guarded_connect)


@pytest.fixture
def w2_router():
    """Active respx router with ``assert_all_called=False`` (alias routes are optional for the dev)."""
    with respx.MockRouter(assert_all_called=False) as router:
        yield router
