"""The test suite can never reach a real server nor use real credentials (conftest safety net)."""

import os

import httpx
import pytest
import respx

from elm_mcp import auth, config
from elm_mcp.config import settings

from .conftest import RealNetworkBlocked


async def test_unmocked_request_fails_fast_with_a_clear_error():
    async with httpx.AsyncClient() as client:
        with pytest.raises(RealNetworkBlocked, match="real network blocked"):
            await client.get("https://elm.example.com/ccm/rootservices")


def test_unmocked_sync_request_fails_fast_with_a_clear_error():
    with httpx.Client() as client, pytest.raises(RealNetworkBlocked, match="real network blocked"):
        client.get("https://elm.example.com/ccm/rootservices")


async def test_login_without_a_mock_does_not_reach_the_server():
    """The exact accident of W2 part 1: get_client() with no mock must not log in anywhere."""
    with pytest.raises(RealNetworkBlocked, match="real network blocked"):
        await auth.get_client()
    await auth.close_client()


async def test_respx_routes_still_work_over_the_guard():
    with respx.mock(assert_all_called=True) as router:
        router.get("https://elm.example.com/x").mock(return_value=httpx.Response(200, text="ok"))
        async with httpx.AsyncClient() as client:
            assert (await client.get("https://elm.example.com/x")).text == "ok"


def test_settings_are_fictitious():
    assert settings.elm_url == "https://elm.example.com"
    assert settings.elm_user == "jdoe"


def test_credentials_file_fallback_is_unreachable():
    assert config._load_creds_file() == {}
    assert not os.path.exists(os.path.expanduser("~/.elm_creds.json"))
