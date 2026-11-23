"""Tests for error handling — timeouts, auth failures, malformed XML."""

from unittest.mock import AsyncMock, patch
from xml.etree import ElementTree as ET

import httpx
import pytest

from elm_mcp.oslc import get_catalog_url, get_resource, query_resources


@pytest.mark.asyncio
async def test_catalog_url_timeout():
    with patch("elm_mcp.oslc.get_client", new_callable=AsyncMock) as mock_client:
        client = AsyncMock()
        client.get.side_effect = httpx.TimeoutException("Connection timed out")
        mock_client.return_value = client
        with pytest.raises(httpx.TimeoutException):
            await get_catalog_url("rm")


@pytest.mark.asyncio
async def test_catalog_url_401():
    # _authenticate is patched: the re-login of the 401 is not what this test is about
    # (with the AsyncMock client its response would be a mock, not an HTTP status).
    with (
        patch("elm_mcp.oslc.get_client", new_callable=AsyncMock) as mock_client,
        patch("elm_mcp.oslc._authenticate", new_callable=AsyncMock),
    ):
        client = AsyncMock()
        resp = httpx.Response(401, request=httpx.Request("GET", "http://test"))
        client.get.return_value = resp
        mock_client.return_value = client
        with pytest.raises(ValueError, match="sessao"):
            await get_catalog_url("rm")


@pytest.mark.asyncio
async def test_get_resource_404():
    with patch("elm_mcp.oslc.get_client", new_callable=AsyncMock) as mock_client:
        client = AsyncMock()
        resp = httpx.Response(404, request=httpx.Request("GET", "http://test/rm/req/999"))
        client.get.return_value = resp
        mock_client.return_value = client
        with pytest.raises(ValueError, match="recurso-inexistente-ou-invisivel"):
            await get_resource("http://test/rm/req/999")


@pytest.mark.asyncio
async def test_catalog_malformed_xml():
    with patch("elm_mcp.oslc.get_client", new_callable=AsyncMock) as mock_client:
        client = AsyncMock()
        resp = httpx.Response(200, text="<not-valid-xml>", request=httpx.Request("GET", "http://test"))
        client.get.return_value = resp
        mock_client.return_value = client
        with pytest.raises((ET.ParseError, ValueError)):  # malformed/unexpected XML
            await get_catalog_url("rm")


@pytest.mark.asyncio
async def test_query_resources_project_not_found():
    with patch("elm_mcp.oslc.list_service_providers", new_callable=AsyncMock) as mock:
        mock.return_value = [{"title": "Other", "url": "http://elm/sp/1"}]
        with pytest.raises(ValueError, match="not found"):
            await query_resources("rm", "NonExistent", None)
