"""Tests for MCP server tool registration."""

import pytest
import respx
from mcp.server.mcpserver.exceptions import ToolError

from elm_mcp.auth import close_client
from elm_mcp.server import list_projects, mcp, transition_workitem

from . import f1_fixtures as f1
from .f1_fixtures import BASE

VALID_DOMAINS = ("rm", "ccm", "qm")


async def test_list_projects_invalid_domain():
    with pytest.raises(ToolError, match="Invalid domain"):
        await list_projects("invalid")


def test_mcp_tools_registered():
    """Verify all 6 tools are registered."""
    tool_names = [t.name for t in mcp._tool_manager.list_tools()]
    expected = [
        "list_projects",
        "search_requirements",
        "get_requirement",
        "list_workitems",
        "get_workitem",
        "search_testcases",
    ]
    for name in expected:
        assert name in tool_names, f"Tool '{name}' not registered"


def test_transition_workitem_registered():
    """Verify transition_workitem tool is registered."""
    tool_names = [t.name for t in mcp._tool_manager.list_tools()]
    assert "transition_workitem" in tool_names


@pytest.fixture
async def _elm(monkeypatch):
    monkeypatch.setattr("elm_mcp.config.settings.elm_url", BASE)
    yield
    await close_client()


@respx.mock
async def test_transition_workitem_invalid_action(_elm):
    """F1/D10: an action that resolves to no workflow action -> ToolError listing the available ids; no write."""
    routes = f1.mock_workflow()
    with pytest.raises(ToolError) as exc:
        await transition_workitem(id=f1.WI_ID, action="invalid")
    for action_id in f1.ACTION_IDS:
        assert action_id in str(exc.value)
    assert routes["put"].call_count == 0


@respx.mock
async def test_transition_workitem_success(_elm):
    """F1/D10: successful transition returns 'Work item {id}: {from} -> {to} (action: {id})'."""
    routes = f1.mock_workflow()
    result = await transition_workitem(id=f1.WI_ID, action="startWorking")
    action_id = f1.ACTION_PREFIX + "startWorking"
    assert result == f"Work item {f1.WI_ID}: Idea -> In Progress (action: {action_id})"
    assert routes["put"].call_count == 1
    assert routes["put"].calls[0].request.url.params.get("_action") == action_id
