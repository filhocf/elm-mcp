"""RED tests (G3) for SPEC-contrato-versionado, wave W1 (release v0.6.0).

The contract of the MCP tools (names, parameters with type and required flag,
read-only flag) is frozen in ``tests/contract/tools.snapshot.json``. This test
compares the tools REALLY registered on ``elm_mcp.server.mcp`` with that snapshot.
It needs no network and no credential.

Snapshot format (the developer's ``scripts/update_contract.py`` must produce it;
the format is defined here, by ``build_contract``):

    {"tools": [{"name": str,
                "read_only": bool,
                "parameters": [{"name": str, "type": str, "required": bool}, ...]}, ...]}

- tools sorted by name, parameters kept in declaration order;
- ``type`` is the JSON Schema type; ``anyOf`` is rendered as the sorted types joined
  by "|" (e.g. "null|string"); arrays append the item type ("array[string]");
- ``read_only`` is the MCP ``readOnlyHint`` annotation of the tool.

RED expectation: the snapshot and ``scripts/update_contract.py`` do not exist yet
(created by the developer at G4), so the comparison tests fail for that reason.
The mutation tests work on a COPY of the real snapshot (they also fail today, because
the snapshot is missing) and prove that removing/renaming a tool or a parameter, or
adding a required parameter, makes the comparison fail.
"""

import asyncio
import copy
import json
import subprocess
import sys
from pathlib import Path

import httpx
import pytest

from elm_mcp.server import mcp

ROOT = Path(__file__).resolve().parent.parent
SNAPSHOT = ROOT / "tests" / "contract" / "tools.snapshot.json"
UPDATE_SCRIPT = ROOT / "scripts" / "update_contract.py"

# Tools that must be flagged read-only in the snapshot; every other tool writes.
READ_ONLY_TOOLS = {
    "list_projects",
    "search_requirements",
    "get_requirement",
    "list_workitems",
    "get_workitem",
    "search_testcases",
    "list_iterations",
    "get_plan_items",
    "list_types",
    "list_fields",
    "inspect_workitem",
    "list_folders",
}
WRITE_TOOLS = {
    "create_workitem",
    "update_workitem",
    "add_child_workitem",
    "transition_workitem",
    "create_requirement",
    "update_requirement",
    "delete_requirement",
}


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    """The contract test must never touch the network."""

    def _blocked(*args, **kwargs):
        raise AssertionError("the contract test must not use the network")

    monkeypatch.setattr(httpx.AsyncClient, "send", _blocked)


def _param_type(schema: dict) -> str:
    if "anyOf" in schema:
        return "|".join(sorted(_param_type(s) for s in schema["anyOf"]))
    kind = schema.get("type", "any")
    if kind == "array" and "items" in schema:
        return f"array[{_param_type(schema['items'])}]"
    return kind


async def build_contract() -> dict:
    """Contract of the tools really registered on the server (no network)."""
    tools = []
    for tool in sorted(await mcp.list_tools(), key=lambda t: t.name):
        schema = tool.input_schema or {}
        required = set(schema.get("required", []))
        tools.append(
            {
                "name": tool.name,
                "read_only": bool(tool.annotations and tool.annotations.read_only_hint),
                "parameters": [
                    {"name": name, "type": _param_type(prop), "required": name in required}
                    for name, prop in schema.get("properties", {}).items()
                ],
            }
        )
    return {"tools": tools}


def diff_contracts(expected: dict, actual: dict) -> list[str]:
    """Human-readable differences between a snapshot (expected) and the real tools (actual)."""
    problems: list[str] = []
    exp = {t["name"]: t for t in expected["tools"]}
    act = {t["name"]: t for t in actual["tools"]}
    for name in sorted(exp.keys() - act.keys()):
        problems.append(f"tool removed or renamed: {name}")
    for name in sorted(act.keys() - exp.keys()):
        problems.append(f"tool added: {name}")
    for name in sorted(exp.keys() & act.keys()):
        if exp[name]["read_only"] != act[name]["read_only"]:
            problems.append(f"{name}: read_only changed")
        if exp[name]["parameters"] != act[name]["parameters"]:
            problems.append(f"{name}: parameters changed {exp[name]['parameters']} -> {act[name]['parameters']}")
    return problems


def _load_snapshot() -> dict:
    assert SNAPSHOT.is_file(), f"contract snapshot missing: {SNAPSHOT.relative_to(ROOT)}"
    return json.loads(SNAPSHOT.read_text(encoding="utf-8"))


# ======================================================================
# The contract test itself
# ======================================================================
async def test_real_tools_match_the_versioned_snapshot():
    """FAILS today: tests/contract/tools.snapshot.json does not exist."""
    snapshot = _load_snapshot()
    assert diff_contracts(snapshot, await build_contract()) == []


async def test_snapshot_flags_read_only_tools_correctly():
    """FAILS today: no snapshot (and tools carry no readOnlyHint, so a generated one would be all False)."""
    by_name = {t["name"]: t for t in _load_snapshot()["tools"]}
    for name in READ_ONLY_TOOLS:
        assert by_name[name]["read_only"] is True, f"{name} must be read-only in the contract"
    for name in WRITE_TOOLS:
        assert by_name[name]["read_only"] is False, f"{name} must not be read-only in the contract"


async def test_snapshot_records_name_type_and_required_for_every_parameter():
    """FAILS today: no snapshot. Spot-checks the pinned shape on create_requirement."""
    by_name = {t["name"]: t for t in _load_snapshot()["tools"]}
    params = {p["name"]: p for p in by_name["create_requirement"]["parameters"]}
    assert params["project"] == {"name": "project", "type": "string", "required": True}
    assert params["title"] == {"name": "title", "type": "string", "required": True}
    assert params["type"] == {"name": "type", "type": "null|string", "required": False}


# ======================================================================
# Changing a tool must break the comparison (mutations of a COPY of the snapshot)
# ======================================================================
def _mutated(snapshot: dict, mutate) -> dict:
    copy_ = copy.deepcopy(snapshot)
    mutate(copy_)
    return copy_


def _tool(snapshot: dict, name: str) -> dict:
    return next(t for t in snapshot["tools"] if t["name"] == name)


@pytest.mark.parametrize(
    ("label", "mutate", "expected_hint"),
    [
        ("remove a parameter", lambda s: _tool(s, "create_requirement")["parameters"].pop(), "create_requirement"),
        (
            "rename a parameter",
            lambda s: _tool(s, "get_requirement")["parameters"][0].update(name="renamed_uri"),
            "get_requirement",
        ),
        (
            "make an optional parameter required",
            lambda s: _tool(s, "create_requirement")["parameters"][-1].update(required=True),
            "create_requirement",
        ),
        (
            "change a parameter type",
            lambda s: _tool(s, "list_folders")["parameters"][0].update(type="integer"),
            "list_folders",
        ),
        ("rename a tool", lambda s: _tool(s, "list_folders").update(name="list_dirs"), "list_folders"),
        ("remove a tool", lambda s: s["tools"].remove(_tool(s, "list_types")), "list_types"),
        ("flip the read-only flag", lambda s: _tool(s, "list_types").update(read_only=False), "list_types"),
    ],
    ids=[
        "remove-param",
        "rename-param",
        "optional-to-required",
        "change-type",
        "rename-tool",
        "remove-tool",
        "flip-read-only",
    ],
)
async def test_mutating_the_snapshot_makes_the_comparison_fail(label, mutate, expected_hint):
    """FAILS today: no snapshot to copy. Once it exists, each mutation must be reported."""
    snapshot = _load_snapshot()
    problems = diff_contracts(_mutated(snapshot, mutate), await build_contract())
    assert problems, f"mutation not detected: {label}"
    assert any(expected_hint in p for p in problems)


async def test_adding_a_tool_to_the_server_breaks_the_comparison():
    """FAILS today: no snapshot. A new tool on the server (not in the snapshot) must be reported."""
    snapshot = _load_snapshot()

    async def brand_new_tool(x: str) -> str:
        return x

    mcp.add_tool(brand_new_tool)
    try:
        problems = diff_contracts(snapshot, await build_contract())
    finally:
        mcp.remove_tool("brand_new_tool")
    assert any("brand_new_tool" in p for p in problems)


# ======================================================================
# Regeneration command (documented in the SPEC)
# ======================================================================
def test_update_script_exists():
    """FAILS today: scripts/update_contract.py does not exist."""
    assert UPDATE_SCRIPT.is_file(), "scripts/update_contract.py is the documented regeneration command"


async def test_update_script_regenerates_a_snapshot_the_comparison_accepts():
    """FAILS today: the script is missing. Running it must write a snapshot equal to the real tools."""
    assert UPDATE_SCRIPT.is_file(), "scripts/update_contract.py is the documented regeneration command"
    original = SNAPSHOT.read_bytes() if SNAPSHOT.exists() else None
    try:
        proc = await asyncio.to_thread(
            subprocess.run,
            [sys.executable, str(UPDATE_SCRIPT)],
            check=False,
            cwd=ROOT,
            capture_output=True,
            text=True,
            timeout=120,
        )
        assert proc.returncode == 0, proc.stderr
        assert diff_contracts(_load_snapshot(), await build_contract()) == []
    finally:
        if original is None:
            SNAPSHOT.unlink(missing_ok=True)
        else:
            SNAPSHOT.write_bytes(original)
