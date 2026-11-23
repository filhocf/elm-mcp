# Tools reference

The eighteen MCP tools exposed today. All are async and validated through the `@tool` boundary
(invalid input or domain failures surface as `ToolError`). `domain` is one of `rm`, `ccm`, `qm`.
See `docs/USAGE.md` for query syntax and examples.

## Read

| Tool | Signature | Description |
|------|-----------|-------------|
| `list_projects`     | `(domain)` | List projects (service providers) for a domain. |
| `search_requirements` | `(project, query=None)` | OSLC query over RM requirements (DOORS Next). |
| `get_requirement`   | `(uri)` | Fetch a single requirement by its OSLC URI. |
| `list_workitems`    | `(project, query=None)` | OSLC query over CCM work items; returns id/title/type/status/owner inline. |
| `get_workitem`      | `(id)` | Fetch a single work item by ID. |
| `search_testcases`  | `(project, query=None)` | OSLC query over QM test cases (ETM). |
| `list_iterations`   | `(project)` | List a project's iterations (title, dates, URI). |
| `get_plan_items`    | `(project, plan=None, item_ids=None)` | Hierarchical view: backlog items with child tasks expanded in one call. Select by `plan` (iteration title substring) or `item_ids`. |

## Discovery

Read-only introspection of a project area's metadata. Use these **before** writing, so a create/update
fills the fields the server actually requires — nothing is hardcoded per project.

| Tool | Signature | Description |
|------|-----------|-------------|
| `list_types`       | `(domain, project)` | List resource types available in a project area. `rm`/`qm`: `[{"title", "shape_uri", "resource_type"}]`. `ccm`: one item per work item type creation factory, `[{"title", "id", "factory_title", "factory_uri", "shape_uri", "resource_type"}]`; `id` is the type id used by `list_fields` and `create_workitem`. |
| `list_fields`      | `(domain, project, type)` | List required/optional fields of a type, with `allowedValues` when the field is enumerated. `type` is a shape title from `list_types` (case-insensitive); for `ccm` it is the type `id` or `title` from `list_types`, or the alias `task`/`story`. Returns `{"type", "shape_uri", "required":[...], "optional":[...]}`; each field has `name`, `title`, `occurs` (e.g. `Exactly-one`), `read_only`, `property_uri`, `range_uri` and `allowed_values`. |
| `inspect_workitem` | `(id)` | Read a real work item and derive its shape/factory URIs and filled fields. Returns `{"id", "type", "shape_uri", "factory_uri", "fields":{...}}`. Useful to learn a project area's concrete field values from an existing item. |
| `list_folders`     | `(project)` | List RM (DOORS Next) folders for a project (queried by the project area URI). Returns `[{"title", "uri"}]`. System folders (root, query results) are filtered out. |

## Write (RM requirements)

| Tool | Signature | Description |
|------|-----------|-------------|
| `create_requirement` | `(project, title, content, folder=None, type=None)` | Create a requirement in a DOORS Next (RM) project. `content` is HTML, sanitized to XHTML and stored in `jazz_rm:primaryText`. `folder` is a folder URI (`nav:parent`, from `list_folders`). `type` is **required at runtime** (the schema keeps it optional so the refusal can list the valid types): a shape URI or a type title, both from `list_types("rm", project)`; without it (or with an unknown title) the call is refused listing the valid types. Returns `{"uri", "identifier"}` read back from the server. |
| `update_requirement` | `(uri, title=None, content=None)` | Update a requirement via OSLC PUT with ETag. `content` is HTML, sanitized to XHTML. |
| `delete_requirement` | `(uri)` | Delete a requirement via OSLC DELETE (direct mode, irreversible) and confirm by re-reading it (404 or 410). Fails if it is still readable. Returns `{"uri", "deleted": true}`. |

## Write (CCM work items)

| Tool | Signature | Description |
|------|-----------|-------------|
| `create_workitem`     | `(project, title, type="task", description=None, parent_id=None, owner=None, filed_against=None, custom_fields=None)` | Create a work item by POSTing to the creation factory of the type. `type` is `task` or `story`. `owner` is stored in `dcterms:contributor`. If `filed_against` is omitted, it is not sent and the server applies its own default; the iteration is auto-discovered. A refused creation surfaces the server's message in the error, with a `list_fields` hint when it concerns the category. |
| `update_workitem`     | `(id, title=None, description=None, owner=None, estimate_hours=None, planned_for=None, custom_fields=None)` | Partial update: PUTs only the given properties (`?oslc.properties=...`) with `If-Match`. A stale ETag (412) is reported as a conflict. `owner` is stored in `dcterms:contributor`. |
| `add_child_workitem`  | `(parent_id, title, type="task", description=None, owner=None, project=...)` | Create a work item linked as a child of `parent_id`. |
| `transition_workitem` | `(id, action)` | Execute a workflow action on a work item (`PUT ?_action=<id>`) and re-read its state. `action` is the id of a workflow action, its id suffix (e.g. `a27`), or a unique case-insensitive substring of the id; `start`/`resolve`/`close`/`reopen` work only where the workflow's action ids contain those words. An unresolved or ambiguous `action` lists the available ids; an action that does not change the state is an error. Returns `Work item {id}: {from} -> {to} (action: {action id})`. |

## Errors

A failed HTTP call surfaces as a `ToolError` that starts with the gate that probably blocked it, then the
status, the operation and the action to take. Reads and writes re-authenticate once on a 401 or on a 403
without `oslc:message` before reporting.

| Gate | Status | Meaning / action |
|---|---|---|
| `credencial` | 401 at login | Invalid user or password: check `ELM_USER` / `ELM_PASSWORD`. |
| `sessao` | 401 after login | Session expired and re-authentication did not help: retry. |
| `regra-de-processo` | 403 with `oslc:message` | A process rule refused the write; the server message is repassed unchanged. |
| `permissao` | 403 without message | Role, license or project area visibility insufficient. |
| `recurso-inexistente-ou-invisivel` | 404 | The URI does not exist or the project area is not visible to the user. |
| `conflito` | 412 | The resource changed since it was read: re-read and retry. |
| `formato` | 406 | The server does not offer the format (QM only answers RDF/XML). |

Other statuses are reported as `[http-<status>]` with the server message, if any. Credentials, cookies and
the `Authorization` header never appear in the message.

## Notes

- **Discover before you write.** `create_requirement`/`create_workitem` need shape and field values
  that vary per project area. Use `list_types` → `list_fields` (and `list_folders` for RM, or
  `inspect_workitem` to copy a real item's values) first. Nothing about a specific organization is
  baked into the package (see `docs/ARCHITECTURE.md`, core vs adaptation).
- `create_requirement` requires `type`: pass the `shape_uri` or the title from `list_types` on the `rm` domain.
- `create_workitem` currently supports `task` and `story`. Additional types (defect, technical debt,
  risk, impediment) are on the roadmap (`docs/specs/SPEC-wi-custom-types.md`).
- `custom_fields` injects project-specific RDF attributes; values are discovered per project area
  (via the discovery tools), not hardcoded.
- QM (ETM) is read-only through these tools; test-case write and cross-domain OSLC links
  (Implements Requirement / Tested By / Affects) are planned (`docs/specs/`, `docs/roadmap.md`).
- State changes go through `transition_workitem` (workflow action), not `update_workitem`.
