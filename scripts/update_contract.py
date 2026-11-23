"""Regenerate tests/contract/tools.snapshot.json from the tools registered on the server.

Usage (from the repository root):

    uv run python scripts/update_contract.py

Run it only when a change to the tools is intentional (name, parameters, read-only flag),
then record the change in CHANGELOG.md. Needs no network and no credentials.

Format: {"tools": [{"name", "read_only", "parameters": [{"name", "type", "required"}]}]},
tools sorted by name, parameters in declaration order. The format is the one compared by
tests/test_w1_contract.py.
"""

import asyncio
import json
from pathlib import Path

from elm_mcp.server import mcp

SNAPSHOT = Path(__file__).resolve().parent.parent / "tests" / "contract" / "tools.snapshot.json"


def _param_type(schema: dict) -> str:
    """JSON Schema type; anyOf as sorted types joined by '|'; arrays as 'array[item]'."""
    if "anyOf" in schema:
        return "|".join(sorted(_param_type(s) for s in schema["anyOf"]))
    kind = schema.get("type", "any")
    if kind == "array" and "items" in schema:
        return f"array[{_param_type(schema['items'])}]"
    return kind


async def build_contract() -> dict:
    """Contract of the tools really registered on the server."""
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


def main() -> None:
    contract = asyncio.run(build_contract())
    SNAPSHOT.parent.mkdir(parents=True, exist_ok=True)
    SNAPSHOT.write_text(json.dumps(contract, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"wrote {SNAPSHOT} ({len(contract['tools'])} tools)")


if __name__ == "__main__":
    main()
