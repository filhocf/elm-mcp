"""G3 (RED) contract tests for SPEC-qm-write, wave W6 (release v0.6.0).

The five new tools are registered on ``elm_mcp.server.mcp`` with the agreed names, parameters (in order,
with the required flag) and as WRITE tools (no ``readOnlyHint``); the versioned snapshot
``tests/contract/tools.snapshot.json`` (rebuilt by ``scripts/update_contract.py`` at G4) lists them. The old
tools keep their read-only flags. No network is needed.

New symbols are reached by attribute INSIDE each test so each test fails by itself.
"""

import json
from pathlib import Path

import pytest

from elm_mcp import server

ROOT = Path(__file__).resolve().parent.parent
SNAPSHOT = ROOT / "tests" / "contract" / "tools.snapshot.json"

NEW_TOOLS = {
    "create_qm_resource": (["project", "type", "title", "description", "if_exists"], {"project", "type", "title"}),
    "update_qm_resource": (["uri", "title", "description"], {"uri"}),
    "delete_qm_resource": (["uri"], {"uri"}),
    "link_test_case": (["test_case", "target", "link_type"], {"test_case", "target", "link_type"}),
    "unlink_test_case": (["test_case", "target", "link_type"], {"test_case", "target", "link_type"}),
}


async def _registered() -> dict:
    return {t.name: t for t in await server.mcp.list_tools()}


@pytest.mark.parametrize("name", sorted(NEW_TOOLS))
async def test_tool_is_registered_with_the_agreed_parameters(name):
    params, required = NEW_TOOLS[name]
    tools = await _registered()
    assert name in tools, f"tool not registered: {name}"
    schema = tools[name].input_schema or {}
    assert list(schema.get("properties", {})) == params
    assert set(schema.get("required", [])) == required


@pytest.mark.parametrize("name", sorted(NEW_TOOLS))
async def test_tool_is_a_write_tool_and_never_flagged_read_only(name):
    tools = await _registered()
    assert name in tools, f"tool not registered: {name}"
    annotations = tools[name].annotations
    assert annotations is not None
    assert annotations.read_only_hint is False


async def test_create_tool_documents_the_supported_and_the_deferred_types():
    """The limitation (deferred types, only title/description) must be written where the agent reads it."""
    tool = (await _registered()).get("create_qm_resource")
    assert tool is not None, "tool not registered: create_qm_resource"
    text = (tool.description or "").lower()
    for supported in ("testcase", "testplan", "testsuite", "testscript"):
        assert supported in text
    assert "testexecutionrecord" in text
    assert any(word in text for word in ("deferred", "adiad"))
    assert "if_exists" in text


async def test_link_tools_document_the_two_link_types():
    tools = await _registered()
    for name in ("link_test_case", "unlink_test_case"):
        assert name in tools, f"tool not registered: {name}"
        text = tools[name].description or ""
        assert "validatesRequirement" in text
        assert "testsChangeRequest" in text


def _snapshot() -> dict[str, dict]:
    assert SNAPSHOT.is_file()
    return {t["name"]: t for t in json.loads(SNAPSHOT.read_text(encoding="utf-8"))["tools"]}


@pytest.mark.parametrize("name", sorted(NEW_TOOLS))
def test_snapshot_lists_the_new_tool_as_a_write_tool(name):
    """FAILS until ``scripts/update_contract.py`` is rerun at G4 with the tools in place."""
    entry = _snapshot().get(name)
    assert entry is not None, f"{name} missing from tests/contract/tools.snapshot.json"
    assert entry["read_only"] is False
    assert [p["name"] for p in entry["parameters"]] == NEW_TOOLS[name][0]
    assert {p["name"] for p in entry["parameters"] if p["required"]} == NEW_TOOLS[name][1]


def test_snapshot_pins_the_defaults_of_create_qm_resource():
    entry = _snapshot().get("create_qm_resource")
    assert entry is not None, "create_qm_resource missing from tests/contract/tools.snapshot.json"
    params = {p["name"]: p for p in entry["parameters"]}
    assert params["description"] == {"name": "description", "type": "null|string", "required": False, "default": None}
    assert params["if_exists"] == {"name": "if_exists", "type": "string", "required": False, "default": "error"}
