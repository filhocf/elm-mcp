# Changelog

All notable changes to this project will be documented in this file.
Format: [Keep a Changelog](https://keepachangelog.com/en/1.1.0/). Versioning: [SemVer](https://semver.org/).

## [Unreleased]

### Fixed

CCM corrections verified against ELM 7.0.1 (SPEC-ccm-correcoes, defects D1-D11 of slice F1):

- **D1** `list_types("ccm")` returned property titles ("Children", "Verified"). It now lists one item per typed
  creation factory: `title`, `id`, `factory_title`, `factory_uri`, `shape_uri`, `resource_type`
  (generic factories are not listed).
- **D2** `list_fields("ccm", ...)` failed with `Type 'story' not found`. The type is now resolved by id, title or
  alias (`task`/`story`); fields gain `occurs` and `read_only` (additive, also for rm/qm). Loop 1: the shape parser
  now reads the format the CCM server really uses (`rdf:Description` typed `core#Property`, children in any order,
  `dcterms:title` with `rdf:parseType="Literal"`; the ResourceShape's own Description is ignored) besides the
  `<oslc:Property>` blocks of RM/QM. A shape that references properties but yields none raises an error instead of `[]`.
  `allowedValues` resources that list `oslc:allowedValue` references no longer return their own URI as a value.
- **D6** `create_workitem` / `add_child_workitem` sent `rtc_cm:ownedBy` (HTTP 409). `owner` is now written to
  `dcterms:contributor`, as `update_workitem` already did.
- **D9** `create_workitem` returned HTTP 500 for stories (generic factory). It now POSTs to the factory of the
  type, and a refused write raises a `ToolError` carrying the server's `oslc:message`. Re-authentication is
  retried only on 401 or a login redirect (a 403 with a message is a business error, not resent).
- **D10** `transition_workitem` looked up actions at a URL that does not exist and fell back to a PUT on
  `rtc_cm:state` that the server ignores. It now resolves `action` (id, id suffix or unique substring) against
  the item's workflow, executes `PUT ?_action=<id>` with `If-Match`, re-reads the item and fails when the state
  did not change. Returns `Work item {id}: {from} -> {to} (action: {action id})`.
- **D11** `update_resource` / `update_workitem` PUT the whole merged resource (130+ fields). It now PUTs only the
  changed properties (`?oslc.properties=...`) with `If-Match`, returns the re-read item, and reports a stale
  ETag (412) as a conflict.

- **D13** `create_workitem` without `filed_against` no longer sends a category (the server applies its own default) and
  no longer looks one up; a refusal about the category now adds a hint to use `list_fields` (property `filedAgainst`).

### Changed

- `transition_workitem`: `action` is now the id or id suffix of a workflow action; `start`/`resolve`/`close`/
  `reopen` work only where the workflow's action ids contain those words.

## [0.5.0] - 2026-11-23

RM write and discovery: the agentic membrane for DOORS Next.

### Added

- Discovery tools `list_types`, `list_fields`, `inspect_workitem` and `list_folders` — enumerate types, shape fields and folders per project area, so callers adapt to each project instead of hardcoding (#45).
- `create_requirement` tool — create a requirement in DOORS Next (RM) via OSLC; HTML content is
  sanitized to XHTML and stored in `jazz_rm:primaryText`; optional `folder` (nav:parent) and `type`
  (instanceShape URI).
- `update_requirement` tool — update an RM requirement (title/content) via OSLC PUT with ETag; a 412
  conflict surfaces as a readable `ToolError`.

## [0.4.0] - 2026-10-28

Renamed from `elm-alm-py` to **`elm-mcp`** and professionalized into a first-class OSS project.

### Changed

- **Renamed package `elm-alm-py` → `elm-mcp`** (distribution, module `elm_mcp`, CLI command `elm-mcp`, repository).
- Migrated to MCP SDK 2.x: `FastMCP` → `MCPServer`; `ToolError` for user-facing errors; pinned `mcp>=2.3,<3`.
- `add_child_workitem` requires an explicit `project` (removed the hardcoded default).

### Removed

- Dead code: `_find_creation_factory` (no production caller) and its two tests.
- All organization-specific references from code and tests (vendor-neutral: generic placeholders only).

### Added

- `get_plan_items` tool — hierarchical sprint view returning backlog items with their child tasks expanded in a single call, selected by `item_ids` or by `plan` (iteration title substring) (#26)
- `transition_workitem` tool — move a work item through its workflow (`start`, `resolve`, `close`, `reopen`), discovering the target state from the work item's workflow actions (#25)

### Changed

- `list_workitems` requests key fields via `oslc.select` and returns `id`, `title`, `type`, `status` and `owner` inline instead of bare resource URIs (#23)

### Fixed

- Pin `mcp<2`: mcp 2.x removed `mcp.server.fastmcp`, so `elm-alm-py serve` crashed on startup with `ModuleNotFoundError` (#33)
- `create_workitem` no longer fails with HTTP 500 after an idle session: the session is refreshed before the POST, with re-authentication on 401/403 (#24)

## [0.3.0] - 2026-05-22

### Added

- `update_workitem` accepts `owner`, `estimate_hours`, `planned_for` and `custom_fields` (#20)
- `list_iterations` tool — list a project's sprints/releases with title, start/end dates and URI (#11)
- `docs/USAGE.md` with working OSLC query patterns, examples and known limitations (#13)

### Changed

- README rewritten to cover write operations, `custom_fields` and OSLC details

### Fixed

- Current-iteration discovery picks the iteration whose start/end dates contain today, preferring bounded iterations over open-ended ones (#19)

## [0.2.3] - 2026-05-22

Tagged but not published to PyPI (package version was not bumped); these changes first shipped in 0.3.0.

### Fixed

- `create_workitem` POSTs directly to the project's `/ccm/oslc/contexts/{projectAreaId}/workitems` endpoint instead of relying on creation-factory discovery (#15, #17)
- Extract `projectAreaId` correctly from project URLs ending in `/workitems/services.xml`

## [0.2.2] - 2026-05-22

Tagged but not published to PyPI (package version was not bumped); these changes first shipped in 0.3.0.

### Added

- `custom_fields` parameter on `create_workitem` for project-specific RDF fields
- `create_workitem` sets `plannedFor` to the project's current iteration automatically

## [0.2.1] - 2026-05-22

### Fixed

- Correct creation factory selection by work item type — no longer picks wrong factory (e.g., defect instead of task) (#7)
- Auto-discover default category (filedAgainst) for create_workitem when not provided (#8)
- Use RDF/XML format (application/rdf+xml) for resource creation POST instead of JSON (#9)

### Changed

- Remove all hardcoded organization-specific references from README, code, tests, and docs
- ELM URL is now fully configurable via ELM_URL env var or ~/.elm_creds.json (no default)
- Bump actions/checkout from 4 to 6, actions/setup-python from 5 to 6, astral-sh/setup-uv from 4 to 7, github/codeql-action from 3 to 4

## [0.2.0] - 2026-05-21

### Added

- **Write operations** for CCM work items:
  - `create_workitem` — create task/story with parent, owner, description
  - `update_workitem` — update title/description via OSLC PUT with ETag
  - `add_child_workitem` — convenience wrapper for creating children
- OSLC CreationFactory discovery for CCM domain
- ETag-based optimistic locking on updates
- Error handling tests for HTTP 400/401/409/412

### Fixed

- RM (DOORS Next): support Discovery 1.0 namespace in catalog + /views query endpoint
- QM (ETM): fallback to XML when JSON returns 406
- CCM (RTC): derive /services endpoint from catalog URL (was failing on services.xml)
- Handle query_base URLs with existing query parameters (RM projectURL)

### Added

- AGENTS.md with domain-specific OSLC patterns documented
- MEMORY.md for session handoff
- CodeQL security scanning workflow
- Dependabot for pip + github-actions
- Publish workflow (PyPI trusted publisher on tag push)
- pytest-cov with 60% threshold (cli.py excluded)

## [0.1.0] - 2026-05-18

### Added

- Initial implementation: MCP server for IBM ELM via OSLC REST
- 6 tools: list_projects, search_requirements, get_requirement, list_workitems, get_workitem, search_testcases
- Jazz form-based authentication with cookie management
- OSLC service discovery (rootservices → catalog → ServiceProvider → QueryCapability)
- CLI with `login` (credential setup) and `serve` (MCP stdio) commands
- Auto-load credentials from `~/.elm_creds.json` with base64 decode
- Support for ELM 7.0.3 OSLC namespaces (v1 + v2 fallback)
- Comprehensive test suite with respx mocking (21 tests)
- CI: GitHub Actions (ruff lint + pytest on Python 3.10-3.13)
