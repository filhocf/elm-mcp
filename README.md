<p align="center">
  <img src="docs/assets/elm-mcp-light.svg" alt="elm-mcp" width="360">
</p>

<p align="center"><strong>Bring IBM Engineering Lifecycle Management to your AI agent.</strong><br>
An MCP server that reads and writes requirements, work items and test cases in IBM ELM
over the open OSLC REST protocol — so an agent can work the ALM as a first-class tool.</p>

<p align="center">
  <a href="https://pypi.org/project/elm-mcp/"><img src="https://img.shields.io/pypi/v/elm-mcp" alt="PyPI"></a>
  <img src="https://img.shields.io/badge/python-3.10%2B-blue" alt="Python 3.10+">
  <a href="LICENSE"><img src="https://img.shields.io/badge/License-MIT-blue.svg" alt="License: MIT"></a>
  <a href="https://github.com/filhocf/elm-mcp/actions/workflows/ci.yml"><img src="https://github.com/filhocf/elm-mcp/actions/workflows/ci.yml/badge.svg" alt="CI"></a>
</p>

---

**elm-mcp** exposes IBM ELM's three OSLC domains — **DOORS Next (RM)**, **EWM/RTC (CCM)** and
**ETM (QM)** — as MCP tools. An AI assistant (Kiro, Claude, OpenCode, Hermes, …) can then list
projects, query and create requirements, work items and test cases, and move them through their
workflow, without a human clicking through the Jazz web UI.

It is an **independent implementation** over the open [OSLC](https://open-services.net/) REST
protocol — **not a fork** of IBM software, and it does **not** bundle any IBM client library.
The package is **vendor-neutral**: no organization-specific host, project or namespace is baked in;
those come from your own configuration at runtime.

## Why

ALM tools like IBM ELM hold the official record — requirements, backlog, tests, traceability — but
they live behind a web UI and OSLC APIs that an agent cannot use out of the box. elm-mcp is the
bridge: it lets an agentic workflow read context from the ALM and write artifacts back to it, over
the Model Context Protocol, so the agent and the traditional process share one source of truth.

## Install

Run it with [`uv`](https://docs.astral.sh/uv/) — no clone, no manual virtualenv:

```bash
uvx elm-mcp            # runs the MCP server (stdio)
```

Or install from PyPI:

```bash
pip install elm-mcp
```

## Configure

One-time credential setup (interactive):

```bash
elm-mcp login
```

Or via environment variables:

```bash
export ELM_URL=https://elm.company.com
export ELM_USER=your_username
export ELM_PASSWORD=secret
```

`ELM_URL` must be exactly the public URL the server announces (same scheme, host and port that appear in the
URLs of its responses). The client only follows URLs, and redirects, on that origin; a server that announces
another host name (internal name, http vs https, different port) makes calls fail with "outside the configured
ELM server". Redirects are checked before being followed, so a 302 to another host is refused.

### Register with your MCP client

```json
{
  "mcpServers": {
    "elm": {
      "command": "uvx",
      "args": ["elm-mcp"]
    }
  }
}
```

## Tools

**Read:** `list_projects` · `search_requirements` · `get_requirement` · `list_workitems` ·
`get_workitem` · `search_testcases` · `list_iterations` · `get_plan_items` ·
`list_qm_resources` · `get_qm_resource` · `get_test_coverage` · `get_test_results`

**Discovery:** `list_types` · `list_fields` · `inspect_workitem` · `list_folders`

**Write (RM requirements):** `create_requirement` · `update_requirement` · `delete_requirement`

**Write (CCM work items):** `create_workitem` · `update_workitem` · `add_child_workitem` ·
`transition_workitem` · `link_artifacts` · `unlink_artifacts`

Full reference with signatures: [docs/TOOLS.md](docs/TOOLS.md). Query syntax and examples:
[docs/USAGE.md](docs/USAGE.md).

```python
# Discover the shape and required fields before writing — nothing is hardcoded per project
list_types(domain="ccm", project="MY PROJECT (KEY)")
list_fields(domain="ccm", project="MY PROJECT (KEY)", type="Task")

# Create a task and run a workflow action (id or id suffix of an action of the item's workflow)
create_workitem(project="MY PROJECT (KEY)", title="Wire up OSLC discovery", type="task")
transition_workitem(id="12345", action="a27")
```

## How it works

```
MCP client → elm-mcp (MCPServer, stdio) → OSLC discovery + CRUD → IBM ELM (RM / CCM / QM)
```

Architecture, the generic-core vs. per-project adaptation layer, and design decisions are documented
under [docs/](docs/): [ARCHITECTURE](docs/ARCHITECTURE.md) · [ADRs](docs/adr/) ·
[roadmap](docs/roadmap.md).

## Develop

```bash
uv sync --group dev
uv run pytest tests/ -v --cov=elm_mcp
uvx ruff@latest check src/ tests/
```

Conventions (language, tenant-neutrality, EARS requirements, traceability) are in
[docs/CONVENTIONS.md](docs/CONVENTIONS.md).

## Status

Early but structured. Core read + CCM work-item write (including comments, work item links, field edits and workflow listing) is working; requirement write (RM), discovery
tools, OSLC links, test write (QM) and reporting are on the [roadmap](docs/roadmap.md).
Tested against IBM ELM 7.0.3.

## License

[MIT](LICENSE)
