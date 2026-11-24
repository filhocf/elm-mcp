# Architecture

elm-mcp is an MCP server that exposes IBM Engineering Lifecycle Management (ELM) over the
open **OSLC REST** protocol. It is an independent implementation (not a fork; no IBM client
library) and vendor-neutral: the package carries no organization-specific values. See
`docs/adr/ADR-001` (OSLC, not a fork) and `docs/adr/ADR-003` (generic core + adaptation layer).

## Domains

IBM ELM exposes three OSLC domains, each a separate Jazz application:

| Domain | Application | Root services |
|--------|-------------|---------------|
| RM  | DOORS Next (Requirements)        | `/rm/rootservices`  |
| CCM | EWM / RTC (Change & Config Mgmt) | `/ccm/rootservices` |
| QM  | ETM (Test Management)            | `/qm/rootservices`  |

## Module layout (`src/elm_mcp/`)

| Module | Responsibility |
|--------|----------------|
| `cli.py`     | Entry point: `login` (credential setup) and `serve` (MCP over stdio). |
| `config.py`  | Settings via pydantic-settings: `ELM_URL`/`ELM_USER`/`ELM_PASSWORD` env, or `~/.elm_creds.json`. |
| `tls.py`     | `ELM_CA_BUNDLE` / `ELM_VERIFY_TLS` to the `verify` setting of httpx; imports nothing from `config`. |
| `auth.py`    | Jazz form-based auth; a singleton async `httpx` client holding the session cookies. |
| `oslc.py`    | OSLC service discovery and query/CRUD. Pure ELM/OSLC — **no MCP SDK dependency**. |
| `server.py`  | `MCPServer` (mcp 2.x) and the tools. The `@tool` decorator translates domain errors into `ToolError` at the boundary. |

## Data flow

```
MCP client → MCPServer (stdio) → server.py tools → oslc.py → auth.py (cookies) → ELM (OSLC XML/JSON)
```

`server.py` is the MCP boundary: the only layer that depends on the MCP SDK and the only one that
raises `ToolError`. `oslc.py` raises plain `ValueError`/`RuntimeError`; the `@tool` decorator
re-raises them as `ToolError` with the message preserved (see `docs/adr/ADR-002`).

## OSLC discovery

```
rootservices → ServiceProviderCatalog → ServiceProvider → QueryCapability → queryBase
```

Each domain uses a slightly different catalog shape (CCM/QM are OSLC Core 2.0; RM/DOORS Next uses a
Discovery 1.0 variant). `oslc.py` handles the three patterns.

## Error handling

- Invalid user input in a tool → `ToolError` (readable message to the MCP client).
- Domain failures bubbling from `oslc.py` (project not found, no query capability) → translated to
  `ToolError` at the `@tool` boundary. Without this, `MCPServer` wraps them as `UnexpectedToolError`
  and drops the message.

## Core vs adaptation layer

The **core** (`elm_mcp`) speaks generic ELM/OSLC: IBM/Jazz namespaces, discovery, CRUD, links.
The **adaptation layer** (per project-area profile) supplies concrete values — server host, project
name, required custom fields, custom work-item types, workflow state map, RM folders — discovered via
OSLC discovery and never hardcoded. See `docs/adr/ADR-003`. This keeps the published package reusable
by any organization.

## Tools

Twelve MCP tools today (read + CCM work-item CRUD). Full reference: `docs/TOOLS.md`. Planned
capabilities (RM write, discovery tools, OSLC links, QM write, reporting) are tracked in
`docs/roadmap.md` and `docs/specs/`.

## Constraints

- TLS verification is on by default; internal CAs via `ELM_CA_BUNDLE`, opt-out via `ELM_VERIFY_TLS=false`.
- Single-server configuration via environment or credentials file.
- Creating iterations/sprints is not possible via OSLC CCM (manual in EWM); the MCP only links
  (`plannedFor`).
