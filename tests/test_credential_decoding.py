"""Issue #70: password decoding must not guess (no base64 heuristic on plaintext)."""

import base64
import importlib.util
import json

import httpx
import pytest
import respx

import elm_mcp.config as _config_mod
from elm_mcp import cli

URL = "https://elm.example.com"


def _fresh_config():
    """Private copy of elm_mcp.config: conftest stubs _load_creds_file in the real module."""
    spec = importlib.util.spec_from_file_location("elm_mcp._config_fresh", _config_mod.__file__)
    mod = importlib.util.module_from_spec(spec)
    mod.__package__ = "elm_mcp"
    spec.loader.exec_module(mod)
    return mod


def Settings():
    return _fresh_config().Settings()


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.chdir(tmp_path)  # no stray .env
    for var in ("ELM_URL", "ELM_USER", "ELM_PASSWORD"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setattr(cli, "CRED_FILE", str(tmp_path / ".elm_creds.json"))
    return tmp_path


def _b64(text: str) -> str:
    return base64.b64encode(text.encode()).decode()


def _env(monkeypatch, password: str) -> Settings:
    monkeypatch.setenv("ELM_URL", URL)
    monkeypatch.setenv("ELM_USER", "jdoe")
    monkeypatch.setenv("ELM_PASSWORD", password)
    return Settings()


def test_env_plaintext_that_looks_like_base64_is_kept(home, monkeypatch):
    # 'YWJjZDEy' is valid base64 that decodes to printable 'abcd12': it was typed as is, keep it
    assert _env(monkeypatch, "YWJjZDEy").elm_password == "YWJjZDEy"


def test_env_plaintext_other_base64_lookalike_is_kept(home, monkeypatch):
    assert _env(monkeypatch, "YWJjZDEyMzQ1").elm_password == "YWJjZDEyMzQ1"


def test_env_plain_non_base64_is_kept(home, monkeypatch):
    assert _env(monkeypatch, "s3nh@ forte!").elm_password == "s3nh@ forte!"


def test_env_explicit_b64_prefix_is_decoded(home, monkeypatch):
    assert _env(monkeypatch, "b64:YWJjZDEyMzQ=").elm_password == "abcd1234"


def _login(monkeypatch, password: str) -> None:
    monkeypatch.setattr("builtins.input", lambda *_: URL if "URL" in _[0] else "jdoe")
    monkeypatch.setattr("getpass.getpass", lambda *_: password)
    with respx.mock:
        respx.post(f"{URL}/jts/j_security_check").mock(return_value=httpx.Response(200))
        respx.get(f"{URL}/ccm/rootservices").mock(return_value=httpx.Response(200))
        cli.login()


@pytest.mark.parametrize("password", ["abcd1234", "YWJjZDEy", "Sênha-çãõ✓!", "p@ss w0rd/+=", "tab\tinside"])
def test_login_round_trip(home, monkeypatch, password):
    _login(monkeypatch, password)
    assert Settings().elm_password == password


def test_creds_file_password_always_decoded_even_if_not_printable_ascii(home):
    (home / ".elm_creds.json").write_text(json.dumps({"url": URL, "username": "jdoe", "password": _b64("ção✓")}))
    assert Settings().elm_password == "ção✓"


def test_legacy_creds_file_without_marker_is_decoded(home):
    (home / ".elm_creds.json").write_text(json.dumps({"url": URL, "username": "jdoe", "password": "YWJjZDEyMzQ="}))
    assert Settings().elm_password == "abcd1234"


def test_creds_file_base64_of_base64_lookalike_is_decoded_once(home):
    # stored value is base64('abcd1234'); a second heuristic pass would corrupt it
    (home / ".elm_creds.json").write_text(json.dumps({"url": URL, "username": "jdoe", "password": _b64("abcd1234")}))
    assert Settings().elm_password == "abcd1234"


def test_creds_file_password_with_control_char_is_decoded(home):
    # isprintable() is False for a tab: the heuristic would hand back the raw base64 text
    (home / ".elm_creds.json").write_text(json.dumps({"url": URL, "username": "jdoe", "password": _b64("a\tb")}))
    assert Settings().elm_password == "a\tb"
