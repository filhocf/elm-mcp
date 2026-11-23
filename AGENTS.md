# AGENTS.md — elm-mcp

## Project Overview

MCP server providing read-only access to IBM Engineering Lifecycle Management (ELM) via OSLC REST APIs. Covers DOORS Next (RM), RTC (CCM), and ETM (QM) domains with form-based Jazz authentication. Tested against ELM 7.0.3.

## Architecture

```
src/elm_mcp/
├── __init__.py       # Package version
├── cli.py            # CLI: 'login' (credential setup) or 'serve' (MCP stdio)
├── config.py         # Settings via pydantic-settings (env vars + ~/.elm_creds.json fallback)
├── auth.py           # Jazz form-based auth, singleton httpx.AsyncClient with cookies
├── oslc.py           # OSLC service discovery + query (handles 3 different ELM API patterns)
└── server.py         # MCP server with 25 tools (14 read-only, 11 write)

tests/
├── test_auth.py      # Auth flow mocking
├── test_oslc.py      # OSLC discovery + query mocking
├── test_server.py    # MCP server integration
├── test_tools.py     # Tool-level tests
└── test_errors.py    # Error handling paths

docs/
└── SPEC.md           # OSLC architecture, auth flow, resource types
```

## Data Flow

```
Client → FastMCP (stdio) → server.py → oslc.py → auth.py (cookies) → ELM server (OSLC XML/JSON)
```

## Key Conventions

- **Config**: env vars `ELM_URL`, `ELM_USER`, `ELM_PASSWORD` or `~/.elm_creds.json` (password base64-encoded)
- **Auth**: Jazz form-based (`/jts/j_security_check`). Failure detected via `X-com-ibm-team-repository-web-auth-msg` header
- **SSL**: `verify=False` (internal certificates)
- **Naming**: snake_case functions, PascalCase classes
- **Queries**: `oslc.query_resources(..., limit=None)` follows `oslc:nextPage` (default 50, cap 1000, dedup by `rdf:about`) and returns a list with a `truncated` attribute; `list_workitems` wraps it as `{"items", "truncated"}`.
- **Work item types**: never hardcode a type map or organization type id; `create_workitem` resolves the type from the project's typed creation factories (`_resolve_ccm_type`).

## Domain-Specific Patterns (ELM 7.0.3)

Each ELM domain uses a different OSLC variant:

| Domain | Catalog Namespace | Query Endpoint | Response Format |
|--------|-------------------|----------------|-----------------|
| **CCM** (RTC) | OSLC Core 2.0 | `/workitems/services` → QueryCapability → queryBase | JSON |
| **RM** (DOORS Next) | Discovery 1.0 | `/views?oslc.query=true&projectURL=...` | JSON |
| **QM** (ETM) | OSLC Core 2.0 | QueryCapability → queryBase | XML (406 on JSON) |

### CCM Discovery
```
rootservices → oslc_cm_v1:cmServiceProviders → catalog (OSLC Core ServiceProvider with rdf:about)
→ ServiceProvider URL + "/services" → QueryCapability → queryBase
```

### RM Discovery
```
rootservices → oslc_rm_v1:rmServiceProviders → catalog (Discovery 1.0 entries)
→ services.xml URL → extract project_id → /views?oslc.query=true&projectURL=.../rm-projects/{id}
```

### QM Discovery
```
rootservices → oslc_qm_v1:qmServiceProviders → catalog (OSLC Core ServiceProvider)
→ ServiceProvider URL + "/services" → QueryCapability → queryBase (XML response only)
```

## Adding a New Tool

1. Add tool function in `server.py` with `@mcp.tool()` decorator
2. Implement OSLC logic in `oslc.py` (reuse `get_client()`, `_get_xml()`, `_get_json()`)
3. Add test in `tests/test_tools.py` using `respx` to mock HTTP
4. Run: `uv run pytest tests/ -v && uv run ruff check src/ tests/`

## Tests

```bash
uv sync --group dev
uv run pytest tests/ -v --cov=elm_mcp --cov-report=term-missing
uv run ruff check src/ tests/
uv run ruff format --check src/ tests/
```

Tool contract: `tests/contract/tools.snapshot.json` freezes tool names, parameters and the read-only flag.
After an intentional change to a tool, regenerate it with `uv run python scripts/update_contract.py` and note
it in `CHANGELOG.md`.

Mock HTTP with `respx`. All tests are async (`asyncio_mode = "auto"`).
Coverage excludes `cli.py` (interactive terminal code).
