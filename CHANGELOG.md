# Changelog

All notable changes to this project will be documented in this file.
Format: [Keep a Changelog](https://keepachangelog.com/en/1.1.0/). Versioning: [SemVer](https://semver.org/).

## [Unreleased]

### Added

ETM reading and cross-domain links (SPEC-qm-leitura, SPEC-oslc-links, wave W3):

- `list_qm_resources(project, type, query=None, limit=None)`: lists any of the 14 ETM types, one item per resource, with `total` from `oslc:totalCount`.
- `get_qm_resource(uri)`: normalized properties and grouped links (`validates_requirement`, `tests_change_request`, scripts, results).
- `get_test_coverage(project)`: requirements validated and work items tested, per test case.
- `get_test_results(project, test_case=None)`: verdict, points per status and originating execution record.
- `link_artifacts(source_uri, target_uri, link_type)`: links a work item to an RM/QM artifact (13 `oslc_cm` types); partial PUT with `If-Match`, confirmed by re-reading. The write format is to be confirmed in live acceptance.
- `unlink_artifacts(source_uri, target_uri, link_type)`: removes only that link, keeping the others.

CCM collaboration (SPEC-ccm-colaboracao, wave W2):

- `add_comment(id, text)`: POSTs the comment as OSLC CM 1.0 (`application/x-oslc-cm-change-request+json`,
  no `OSLC-Core-Version`) and confirms it appears in the discussion; if it does not, the call fails without
  resending the POST.
- `list_comments(id)`: comments of a work item (`oslc:discussedBy` -> `oslc:comment`) as
  `[{"id", "author", "created", "text"}]`; an item without discussion returns `[]`.
- `link_workitems(source, target, link_type)` / `unlink_workitems(...)`: partial PUT of the link property
  (`rtc_cm:com.ibm.team.workitem.linktype.*`) with `If-Match`, keeping the other links of that type, confirmed by
  re-reading the source. Types: `parent`, `blocks`, `depends_on`, `related`, `duplicate_of`, `resolves`
  (`parent` replaces the current parent).
- `set_field(id, field, value)`: partial PUT of one field; an enumerated field takes the literal title
  (e.g. `5 pts`) or id, validated against `allowedValues`, and warns when `Unassigned` is written to a required
  attribute.
- `list_workflow(project, type)`: states (`id`, `title`, `group`) and action ids of a type's workflow.

- Tool contract snapshot (`tests/contract/tools.snapshot.json`) with a network-free test; regenerate with `uv run python scripts/update_contract.py`. Tools now publish the MCP `readOnlyHint` annotation (read tools `true`, write tools `false`), so clients can tell them apart.
- `delete_requirement` tool: delete an RM requirement (direct mode) and confirm by re-reading it (404 or 410); fails if it is still readable.

### Changed

- `list_workitems` gains the optional filters `type`, `state`, `owner` and `planned_for`, sent to the server in
  `oslc.where` (combined with `query` by `and`); a filter value with a quote, angle bracket or backslash is refused.
- `list_workitems` now returns `{"items": [...], "truncated": bool}` instead of a list, follows `oslc:nextPage` up to `limit` items (default 50, max 1000) and re-authenticates once per page.
- **Breaking (W2, deliberate):** `search_requirements` and `search_testcases` now return `{"items": [...], "truncated": bool}` instead of a list (same shape as `list_workitems`), follow the pages up to 1000 items and no longer cut silently at 50. `get_plan_items` also queries up to 1000 items and gains a `truncated` key.
- Pagination (`oslc:nextPage`) stops on a repeated page URL, on a page without any new item and after 100 pages (`truncated=true`); `limit` of zero or less is an error instead of silently using the default.
- Security: every URL followed from a server response (next pages, workflow states, comment and iteration references, shapes) must be on the same scheme, host and port as `ELM_URL`; otherwise the call fails. The same applies to the URI given to `get_requirement`.
- `add_comment` sends the text HTML-escaped (line breaks as `<br/>`) and confirms the comment by its new id (without the created id, only a new comment with the same normalized text confirms it; the sent and the stored text go through the same escape + normalization, so `<b>` and `&` match); the POST is still sent once.
- Work item type resolution has an explicit precedence (exact id, then the last segment of the id, then title); two matches at the same level are an error listing the ids.
- `set_field` refuses a malformed field name (`prefix:name` only), a read-only field and a field outside the item's shape (use `list_fields`), and converts the value to the field's range (integer family, boolean, decimal). Integers are plain ASCII (`1_000` and non-ASCII digits are refused) and respect the sign of their range (`nonNegativeInteger`, `positiveInteger`, `negativeInteger`, `unsigned*`...).
- `create_workitem` discovers the project and its factories once instead of twice.
- `link_workitems` / `unlink_workitems` refuse a link of an item to itself; list_workitems filters also refuse non-printable characters.
- `create_workitem` accepts any work item type the project exposes (id, title or short alias such as `task`/`story`); an unknown type is refused listing the available ones.

- **Breaking:** `create_requirement` now requires `type` (a shape URI or a type title from `list_types("rm")`). Without it, or with an unknown title, the call is refused with the list of valid types and nothing is sent; before, the server answered 403 CRRRS6404E.
- Every HTTP error of a read or write tool is now a `ToolError` that names the probable gate (`credencial`, `sessao`, `regra-de-processo`, `permissao`, `recurso-inexistente-ou-invisivel`, `conflito`, `formato`) and the action to take, instead of a bare `HTTPStatusError`; the server's `oslc:message` is passed through unchanged.
- The main reads now re-authenticate once on a 401 or a 403 without `oslc:message` (writes already did); a failed login no longer leaves a half-authenticated client cached. A login answering 403 or 5xx now fails (`[credencial]` / `[http-<status>]`) instead of being accepted; three direct reads still do not re-authenticate (see `docs/roadmap.md`, "Dívida conhecida").
- `list_folders` now raises "project area not advertised by the service provider" instead of returning `[]` when `oslc:details` is missing.
- `create_requirement`: if the re-read of D8 fails after the POST, it no longer raises (the requirement exists): it returns the URI, the POST-body identifier (or `null`) and a `warning`, and logs a warning.
- `delete_requirement`: a denied (401/403) confirmation read reports "deletion not confirmed" (the DELETE was sent); the DELETE is unconditional (no `If-Match`).
- The contract snapshot grew: each parameter now records its `default` when it declares one; `scripts/update_contract.py` accepts an output path.
- `transition_workitem`: `action` is now the id or id suffix of a workflow action; `start`/`resolve`/`close`/
  `reopen` work only where the workflow's action ids contain those words.

### Fixed

- D12: `search_testcases` returned one `raw_xml` blob of the first query capability (TestExecutionRecord); it now picks the TestCase capability by `oslc:resourceType` and returns one item per test case.

Live acceptance of W2 (read-only, ELM 7.0.1):

- `list_workitems` filters now use what the server accepts in `oslc.where`: `type` is resolved (id, alias or
  title) by the project's discovery and sent as `rtc_cm:type="<id>"` (`dcterms:type="<title>"` matched nothing);
  `state` is sent as `rtc_cm:state="<state id>"` (a title is resolved by the workflow of the given `type`,
  otherwise the call fails asking for the id; `oslc_cm:status` was an HTTP 400); `owner` is always a user URI.
- `list_workflow` no longer fails with "does not expose its workflow": it reads the allowedValues document of the
  shape's `state` property (`.../property/internalState/allowedValues`) and derives the workflow from the state
  URIs; if the titles document cannot be read, it still returns the state ids.

W1 live-acceptance fixes (ELM 7.0.1):

- `list_folders` returned the query/description resources and dropped the root folder. It now keeps only `nav#folder`
  resources (root included) and returns `{"title", "uri", "subfolders_query"}`.
- `get_requirement` failed with HTTP 415 on RM resources. It now retries with `application/rdf+xml` and returns a flat dict.
- `update_requirement` returned `identifier: null`. It now re-reads the identifier (a failed re-read only adds a `warning`).

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

- **D3** `list_folders` failed with HTTP 400 because it queried folders by the component URI. It now queries by the project area URI announced by the service provider (`oslc:details`).
- **D8** `create_requirement` returned `identifier: null` when the POST body was empty. It now re-reads the created artifact and returns the server's `dcterms:identifier`.
- **D13** `create_workitem` without `filed_against` no longer sends a category (the server applies its own default) and
  no longer looks one up; a refusal about the category now adds a hint to use `list_fields` (property `filedAgainst`).

### Security

- URLs followed from server responses, and the URI given to `get_requirement`/`get_resource`, are accepted only
  on the same origin as `ELM_URL` (scheme, host and port; default ports 80/443 are normalized), otherwise the call
  fails before any request (`assert_same_origin`). Set `ELM_URL` to the exact public URL the server announces.
- HTTP redirects are checked too: a 3xx whose `Location` leaves that origin is refused before it is followed
  (response hook on the client). The login flow shares the rule, so an http->https or other-host redirect at login fails.

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
