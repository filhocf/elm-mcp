"""G3 (RED) tests for the MCP SDK 1.x -> 2.x migration.

These tests pin the TARGET (post-migration) behavior and MUST FAIL on the current
state (still FastMCP / mcp 1.x). They turn GREEN once G4 lands the migration.

Why they are RED today
----------------------
1. The module-top imports below pull the 2.x symbols:
       from mcp.server.mcpserver import MCPServer
       from mcp.server.mcpserver.exceptions import ToolError
   On mcp 1.x the package `mcp.server.mcpserver` does not exist, so the ENTIRE
   file fails at COLLECTION with `ModuleNotFoundError`. That is valid RED — every
   test in this file is reported as an error until the SDK is on 2.x.

2. Even after the import resolves, the ToolError tests still fail unless the 5
   user-facing `raise ValueError(...)` in server.py are converted to
   `raise ToolError(...)`: `ToolError` is NOT a subclass of `ValueError`
   (verified: ToolError MRO = ToolError -> FastMCPError -> Exception), so
   `pytest.raises(ToolError)` against today's `ValueError` would report
   "DID NOT RAISE". A RED test and its post-fix GREEN are the SAME assertion.

3. The boot smoke tests assert the production server object is an `MCPServer`
   (2.x). Today `server.mcp` is a `FastMCP` instance, so those assertions fail.

SPEC: docs/SPEC-mcp2x-migration.md · PRD: docs/PRD-mcp2x-migration.md
Conventions: pytest + respx, asyncio_mode=auto (AGENTS.md).
"""

# --- 2.x API imports (TARGET). On mcp 1.x these raise ModuleNotFoundError at
#     collection time -> the whole module is RED. Do NOT wrap in try/except;
#     the collection error IS the intended RED signal. ---
import pytest
import respx
from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError

from elm_mcp import server
from elm_mcp.auth import close_client
from elm_mcp.server import (
    create_workitem,
    list_projects,
    transition_workitem,
    update_workitem,
)

from . import f1_fixtures as f1

# =============================================================================
# F1 — Boot / smoke: the server runs on the 2.x API (MCPServer), not FastMCP.
# =============================================================================


def test_server_object_is_mcpserver_2x():
    """server.mcp must be an MCPServer (mcp 2.x), not a FastMCP (1.x).

    RED now: on 1.x server.mcp is a FastMCP instance -> isinstance fails
    (and the file never even collects because the import of MCPServer explodes).
    """
    assert isinstance(server.mcp, MCPServer)


def test_server_imports_mcpserver_class_not_fastmcp():
    """server.py must reference the 2.x MCPServer class, not FastMCP.

    RED now: server.py does `from mcp.server.fastmcp import FastMCP` and builds
    `mcp = FastMCP(...)`; the class of server.mcp is FastMCP, not MCPServer.
    """
    assert type(server.mcp) is MCPServer
    # The class must come from the 2.x module path.
    assert type(server.mcp).__module__.startswith("mcp.server.mcpserver")


def test_server_still_exposes_the_ten_tools():
    """F1 compat: the 10 tools keep their names after the migration.

    RED now only via the collection error (this assertion itself would pass on
    1.x if the import succeeded — it is here to guard against a regression where
    the migration drops/renames tools, and to document the compat invariant).
    """
    expected = {
        "list_projects",
        "search_requirements",
        "get_requirement",
        "list_workitems",
        "get_workitem",
        "search_testcases",
        "list_iterations",
        "get_plan_items",
        "create_workitem",
        "update_workitem",
        "add_child_workitem",
        "transition_workitem",
    }
    # tool manager is a private attr on both FastMCP and MCPServer
    tool_names = {t.name for t in server.mcp._tool_manager.list_tools()}
    missing = expected - tool_names
    assert not missing, f"tools missing after migration: {missing}"


def test_server_main_runs_stdio_without_module_error(monkeypatch):
    """F1: `serve`/main boots over stdio under 2.x without ModuleNotFoundError.

    RED now: collection error (mcp.server.mcpserver absent). Post-migration this
    proves main() wires mcp.run(transport="stdio") on the 2.x object. We stub
    .run so no real transport is opened.
    """
    called = {}

    def fake_run(transport=None):
        called["transport"] = transport

    monkeypatch.setattr(server.mcp, "run", fake_run)
    server.main()
    assert called.get("transport") == "stdio"


# =============================================================================
# F2 — Validation tools raise ToolError (2.x) for invalid input, not ValueError.
#      ToolError is NOT a subclass of ValueError, so these are meaningful REDs
#      even once the 2.x import resolves but the raises are not yet converted.
# =============================================================================


async def test_list_projects_invalid_domain_raises_toolerror():
    """server.py L22: invalid domain -> ToolError (was ValueError).

    RED now: raises plain ValueError; pytest.raises(ToolError) reports no
    ToolError raised (ToolError is not a ValueError superclass).
    """
    with pytest.raises(ToolError, match="Invalid domain"):
        await list_projects("not-a-domain")


async def test_create_workitem_invalid_type_raises_toolerror():
    """server.py L237: unknown work-item type -> ToolError (was ValueError).

    RED now: raises plain ValueError on type='epic'.
    """
    with pytest.raises(ToolError, match="Invalid type"):
        await create_workitem(project="PROJ", title="X", type="epic")


async def test_update_workitem_no_fields_raises_toolerror():
    """server.py L306: empty update -> ToolError 'At least one field'.

    RED now: raises plain ValueError when no updatable field is given.
    """
    with pytest.raises(ToolError, match="At least one field"):
        await update_workitem(id="1")


@respx.mock
async def test_transition_workitem_invalid_action_raises_toolerror(monkeypatch):
    """F1/D10: an action unknown to the item's workflow -> ToolError (not ValueError) listing the ids.

    RED pre-F1: the fixed-word validation raises 'Invalid action' without ever reading the workflow.
    """
    monkeypatch.setattr("elm_mcp.config.settings.elm_url", f1.BASE)
    try:
        routes = f1.mock_workflow()
        with pytest.raises(ToolError) as exc:
            await transition_workitem(id=f1.WI_ID, action="invalid")
        assert f1.ACTION_PREFIX + "a12" in str(exc.value)
        assert routes["put"].call_count == 0
    finally:
        await close_client()


def test_toolerror_is_not_valueerror_subclass():
    """Guard: ToolError must be a distinct type from ValueError.

    This documents WHY the tests above are meaningful: once the 2.x import
    resolves, a leftover `raise ValueError` would NOT satisfy
    pytest.raises(ToolError). If some future SDK made ToolError subclass
    ValueError, these behavior tests would silently weaken — this test flags it.

    RED now: collection error (import of ToolError from 2.x path fails on 1.x).
    """
    assert not issubclass(ToolError, ValueError)
