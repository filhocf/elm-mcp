"""Tests for MCP tools — mocked OSLC responses."""

from unittest.mock import AsyncMock, patch

import pytest
from mcp.server.mcpserver.exceptions import ToolError

from elm_mcp import oslc
from elm_mcp.server import (
    get_requirement,
    get_workitem,
    list_projects,
    list_workitems,
    search_requirements,
    search_testcases,
)


@pytest.mark.asyncio
async def test_list_projects_rm():
    with patch("elm_mcp.server.oslc.list_service_providers", new_callable=AsyncMock) as mock:
        mock.return_value = [{"title": "PROJ", "url": "http://elm/rm/sp/1"}]
        result = await list_projects("rm")
        assert result == [{"title": "PROJ", "url": "http://elm/rm/sp/1"}]
        mock.assert_called_once_with("rm")


@pytest.mark.asyncio
async def test_list_projects_ccm():
    with patch("elm_mcp.server.oslc.list_service_providers", new_callable=AsyncMock) as mock:
        mock.return_value = [{"title": "RTC Project", "url": "http://elm/ccm/sp/1"}]
        result = await list_projects("ccm")
        assert len(result) == 1


@pytest.mark.asyncio
async def test_list_projects_qm():
    with patch("elm_mcp.server.oslc.list_service_providers", new_callable=AsyncMock) as mock:
        mock.return_value = []
        result = await list_projects("qm")
        assert result == []


@pytest.mark.asyncio
async def test_list_projects_invalid_domain_raises():
    with pytest.raises(ToolError, match="Invalid domain"):
        await list_projects("invalid")


@pytest.mark.asyncio
async def test_search_requirements_with_query():
    with patch("elm_mcp.server.oslc.query_resources", new_callable=AsyncMock) as mock:
        mock.return_value = [{"title": "REQ-001", "uri": "http://elm/rm/req/1"}]
        result = await search_requirements("PROJ", "dcterms:title='REQ-001'")
        mock.assert_called_once_with("rm", "PROJ", "dcterms:title='REQ-001'")
        assert result[0]["title"] == "REQ-001"


@pytest.mark.asyncio
async def test_search_requirements_no_query():
    with patch("elm_mcp.server.oslc.query_resources", new_callable=AsyncMock) as mock:
        mock.return_value = []
        result = await search_requirements("PROJ", None)
        mock.assert_called_once_with("rm", "PROJ", None)
        assert result == []


@pytest.mark.asyncio
async def test_get_requirement():
    with patch("elm_mcp.server.oslc.get_resource", new_callable=AsyncMock) as mock:
        mock.return_value = {"dcterms:title": "Login Feature", "dcterms:identifier": "REQ-42"}
        result = await get_requirement("http://elm/rm/req/42")
        mock.assert_called_once_with("http://elm/rm/req/42")
        assert result["dcterms:identifier"] == "REQ-42"


@pytest.mark.asyncio
async def test_list_workitems():
    with patch("elm_mcp.server.oslc.query_resources", new_callable=AsyncMock) as mock:
        mock.return_value = oslc.QueryResults(
            [{"dcterms:title": "Bug #123", "dcterms:identifier": "123", "rdf:about": "http://x"}]
        )
        result = await list_workitems("PROJ", "oslc_cm:type='defect'")
        mock.assert_called_once_with(
            "ccm",
            "PROJ",
            "oslc_cm:type='defect'",
            select="dcterms:title,dcterms:identifier,dcterms:type,oslc_cm:status,dcterms:contributor",
            limit=None,
        )
        assert result["truncated"] is False
        assert len(result["items"]) == 1
        assert result["items"][0]["title"] == "Bug #123"


@pytest.mark.asyncio
async def test_get_workitem():
    with patch("elm_mcp.server.oslc.get_resource", new_callable=AsyncMock) as mock:
        mock.return_value = {"dcterms:title": "Fix login", "dcterms:identifier": "123"}
        result = await get_workitem("123")
        assert "123" in mock.call_args[0][0]
        assert result["dcterms:title"] == "Fix login"


@pytest.mark.asyncio
async def test_search_testcases():
    with patch("elm_mcp.server.oslc.query_resources", new_callable=AsyncMock) as mock:
        mock.return_value = [{"title": "TC-001"}]
        result = await search_testcases("QA Project")
        mock.assert_called_once_with("qm", "QA Project", None)
        assert result[0]["title"] == "TC-001"
