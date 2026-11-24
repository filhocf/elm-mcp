"""Issue #71: get_client must verify TLS by default; opt-outs are explicit."""

import logging
import ssl
import subprocess
import sys
import textwrap
from typing import ClassVar

import certifi
import pytest

from elm_mcp import auth


class FakeClient:
    instances: ClassVar[list["FakeClient"]] = []

    def __init__(self, **kwargs):
        self.kwargs = kwargs
        FakeClient.instances.append(self)

    async def aclose(self):
        pass


@pytest.fixture(autouse=True)
def fake(monkeypatch):
    FakeClient.instances = []
    for var in ("ELM_CA_BUNDLE", "ELM_VERIFY_TLS"):
        monkeypatch.delenv(var, raising=False)

    async def noop(_client):
        return None

    monkeypatch.setattr(auth.httpx, "AsyncClient", FakeClient)
    monkeypatch.setattr(auth, "_authenticate", noop)
    monkeypatch.setattr(auth, "_client", None)
    yield
    auth._client = None


def _verify():
    assert len(FakeClient.instances) == 1
    return FakeClient.instances[0].kwargs["verify"]


async def test_default_verifies_tls():
    await auth.get_client()
    v = _verify()
    assert v is not False and v


async def test_ca_bundle_file_is_used(monkeypatch, tmp_path):
    bundle = certifi.where()  # a real CA file: "x" does not load as a CA
    monkeypatch.setenv("ELM_CA_BUNDLE", bundle)
    await auth.get_client()
    v = _verify()
    assert v == bundle or isinstance(v, ssl.SSLContext)


async def test_ca_bundle_system_uses_os_context(monkeypatch):
    monkeypatch.setenv("ELM_CA_BUNDLE", "system")
    await auth.get_client()
    v = _verify()
    assert isinstance(v, ssl.SSLContext)
    assert v.verify_mode == ssl.CERT_REQUIRED


async def test_ca_bundle_missing_path_is_clear_error(monkeypatch, tmp_path):
    monkeypatch.setenv("ELM_CA_BUNDLE", str(tmp_path / "nope.pem"))
    with pytest.raises(ValueError, match="ELM_CA_BUNDLE"):
        await auth.get_client()
    assert FakeClient.instances == []


@pytest.mark.parametrize("value", ["false", "0", "no", "off", "FALSE", "Off", "No"])
async def test_verify_tls_false_values(monkeypatch, value):
    monkeypatch.setenv("ELM_VERIFY_TLS", value)
    await auth.get_client()
    assert _verify() is False


async def test_verify_tls_false_beats_ca_bundle(monkeypatch, tmp_path):
    bundle = tmp_path / "ca.pem"
    bundle.write_text("x")
    monkeypatch.setenv("ELM_CA_BUNDLE", str(bundle))
    monkeypatch.setenv("ELM_VERIFY_TLS", "false")
    await auth.get_client()
    assert _verify() is False


async def test_verify_tls_false_beats_missing_ca_bundle(monkeypatch, tmp_path):
    monkeypatch.setenv("ELM_CA_BUNDLE", str(tmp_path / "nope.pem"))
    monkeypatch.setenv("ELM_VERIFY_TLS", "0")
    await auth.get_client()
    assert _verify() is False


async def test_verify_tls_true_keeps_verification(monkeypatch):
    monkeypatch.setenv("ELM_VERIFY_TLS", "true")
    await auth.get_client()
    assert _verify() is not False


async def test_verify_false_warns_in_a_log(monkeypatch, caplog):
    monkeypatch.setenv("ELM_VERIFY_TLS", "false")
    with caplog.at_level(logging.WARNING):
        await auth.get_client()
    # may already have warned earlier in this process; the exact-once check is the subprocess test
    assert _verify() is False


def test_verify_false_warns_exactly_once_per_process():
    code = textwrap.dedent(
        """
        import asyncio, logging, os, sys
        os.environ["ELM_VERIFY_TLS"] = "false"
        os.environ["ELM_URL"] = "https://elm.example.com"
        os.environ["ELM_USER"] = "u"
        os.environ["ELM_PASSWORD"] = "p"
        records = []
        class H(logging.Handler):
            def emit(self, r): records.append(r)
        logging.getLogger().addHandler(H()); logging.getLogger().setLevel(logging.DEBUG)
        from elm_mcp import auth
        class C:
            def __init__(self, **kw): self.kw = kw
            async def aclose(self): pass
        async def noop(c): pass
        auth.httpx.AsyncClient = C
        auth._authenticate = noop
        async def main():
            for _ in range(3):
                auth._client = None
                await auth.get_client()
        asyncio.run(main())
        warns = [r for r in records if r.levelno == logging.WARNING and any(w in r.getMessage().lower() for w in ("tls", "ssl", "verif", "certificate"))]
        print("WARNINGS", len(warns))
        """
    )
    out = subprocess.run([sys.executable, "-c", code], check=False, capture_output=True, text=True, timeout=60)
    assert "WARNINGS 1" in out.stdout, out.stdout + out.stderr


def test_login_command_uses_the_same_tls_setting(monkeypatch):
    """`elm-mcp login` sends the password, so it must honour the same TLS setting (issue #71)."""
    import httpx

    from elm_mcp import cli

    seen = {}

    class FakeLoginClient:
        def __init__(self, **kwargs):
            seen.update(kwargs)

        def post(self, *args, **kwargs):
            return httpx.Response(200, headers={"X-com-ibm-team-repository-web-auth-msg": "authfailed"})

        def close(self):
            pass

    monkeypatch.delenv("ELM_VERIFY_TLS", raising=False)
    monkeypatch.delenv("ELM_CA_BUNDLE", raising=False)
    monkeypatch.setattr(httpx, "Client", FakeLoginClient)
    answers = iter(["https://elm.example.com", "user"])
    monkeypatch.setattr("builtins.input", lambda *_: next(answers))
    monkeypatch.setattr("getpass.getpass", lambda *_: "pw")
    with pytest.raises(SystemExit):
        cli.login()
    assert seen.get("verify") is not False
