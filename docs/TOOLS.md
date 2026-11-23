# Tools reference

The twenty-five MCP tools exposed today (fourteen read-only, eleven write). All are async and validated through the `@tool` boundary
(invalid input or domain failures surface as `ToolError`). `domain` is one of `rm`, `ccm`, `qm`.
See `docs/USAGE.md` for query syntax and examples.

## Read

| Tool | Signature | Description |
|------|-----------|-------------|
| `list_projects`     | `(domain)` | List projects (service providers) for a domain. |
| `search_requirements` | `(project, query=None)` | OSLC query over RM requirements (DOORS Next). Follows the pages up to 1000 items; returns `{"items": [...], "truncated": bool}`. |
| `get_requirement`   | `(uri)` | Fetch a single requirement by its OSLC URI. RM answers HTTP 415 to JSON, so it falls back to `application/rdf+xml` and returns a flat dict (`rdf:about`, `prefix:name` -> text, XML string for `parseType="Literal"`, or URI; repeated properties become lists). |
| `list_workitems`    | `(project, query=None, limit=None, type=None, state=None, owner=None, planned_for=None)` | OSLC query over CCM work items. Optional filters, applied by the server in `oslc.where` and combined with `query` by `and`: `type` (type title, `dcterms:type`), `state` (state title, `oslc_cm:status`), `owner` (user id or URI, `dcterms:contributor`), `planned_for` (iteration URI, `rtc_cm:plannedFor`); a filter value with a quote, angle bracket, backslash or non-printable character is refused before any request; the raw `query` is joined to the filters only with `and` (no `or`). Results follow the server's pages until `limit` items (default 50, maximum 1000, no duplicates). Returns `{"items": [{id, title, type, status, owner}], "truncated": bool}`; `truncated=true` means the query has more items. An expired session is re-authenticated once per page; a page that keeps failing raises an error naming the gate and how many items had been collected. |
| `get_workitem`      | `(id)` | Fetch a single work item by ID. |
| `search_testcases`  | `(project, query=None)` | OSLC query over QM test cases (ETM). Follows the pages up to 1000 items; returns `{"items": [...], "truncated": bool}`. |
| `list_iterations`   | `(project)` | List a project's iterations (title, dates, URI). |
| `list_comments`     | `(id)` | Comments of a work item (`oslc:discussedBy` -> `oslc:comment`), in thread order: `[{"id", "author", "created", "text"}]`. An item without discussion returns `[]`. |
| `list_workflow`     | `(project, type)` | Workflow of a work item type (`type` as in `create_workitem`): `{"type", "workflow", "states": [{"id", "title", "group"}], "actions": [...]}`. The action ids are what `transition_workitem` accepts. A type whose shape does not expose its workflow is an error. |
| `get_plan_items`    | `(project, plan=None, item_ids=None)` | Hierarchical view: backlog items with child tasks expanded in one call. Select by `plan` (iteration title substring) or `item_ids`. The result carries `truncated` (true when the backlog query had more than 1000 items). |

## Discovery

Read-only introspection of a project area's metadata. Use these **before** writing, so a create/update
fills the fields the server actually requires — nothing is hardcoded per project.

| Tool | Signature | Description |
|------|-----------|-------------|
| `list_types`       | `(domain, project)` | List resource types available in a project area. `rm`/`qm`: `[{"title", "shape_uri", "resource_type"}]`. `ccm`: one item per work item type creation factory, `[{"title", "id", "factory_title", "factory_uri", "shape_uri", "resource_type"}]`; `id` is the type id used by `list_fields` and `create_workitem`. |
| `list_fields`      | `(domain, project, type)` | List required/optional fields of a type, with `allowedValues` when the field is enumerated. `type` is a shape title from `list_types` (case-insensitive); for `ccm` it is the type `id` or `title` from `list_types`, or a short alias (`task`, `story`...). Returns `{"type", "shape_uri", "required":[...], "optional":[...]}`; each field has `name`, `title`, `occurs` (e.g. `Exactly-one`), `read_only`, `property_uri`, `range_uri` and `allowed_values`. |
| `inspect_workitem` | `(id)` | Read a real work item and derive its shape/factory URIs and filled fields. Returns `{"id", "type", "shape_uri", "factory_uri", "fields":{...}}`. Useful to learn a project area's concrete field values from an existing item. |
| `list_folders`     | `(project)` | List RM (DOORS Next) folders for a project (queried by the project area URI). Returns `[{"title", "uri", "subfolders_query"}]`: only real folders (`nav#folder`), including the root folder (a valid `folder` parent for `create_requirement`); `subfolders_query` is the URI of the child-folder query, or `null` (informational, not navigable yet). Fails with "project area not advertised by the service provider" when the project does not announce its project area (`oslc:details`). |

## Write (RM requirements)

| Tool | Signature | Description |
|------|-----------|-------------|
| `create_requirement` | `(project, title, content, folder=None, type=None)` | Create a requirement in a DOORS Next (RM) project. `content` is HTML, sanitized to XHTML and stored in `jazz_rm:primaryText`. `folder` is a folder URI (`nav:parent`, from `list_folders`). `type` is **required at runtime** (the schema keeps it optional so the refusal can list the valid types): a shape URI or a type title, both from `list_types("rm", project)`; without it (or with an unknown title) the call is refused listing the valid types. Returns `{"uri", "identifier"}` read back from the server; if that re-read fails after the POST, the requirement exists anyway and the result carries a short `warning` (do not create it again). |
| `update_requirement` | `(uri, title=None, content=None)` | Update a requirement via OSLC PUT with ETag. `content` is HTML, sanitized to XHTML. Returns `{"uri", "identifier"}`, the identifier re-read from the server (a failed re-read never raises; the result carries a `warning`). |
| `delete_requirement` | `(uri)` | Delete a requirement via OSLC DELETE (direct mode, irreversible) and confirm by re-reading it (404 or 410). The DELETE is unconditional (no `If-Match`). Fails if it is still readable, and reports "deletion not confirmed" when the confirmation read is denied (401/403; the DELETE was sent). Returns `{"uri", "deleted": true}`. |

## Write (CCM work items)

| Tool | Signature | Description |
|------|-----------|-------------|
| `create_workitem`     | `(project, title, type="task", description=None, parent_id=None, owner=None, filed_against=None, custom_fields=None)` | Create a work item by POSTing to the creation factory of the type. `type` is any type of the project: the `id` or `title` from `list_types`, or a short alias (`task`, `story`...); an unknown type is refused listing the available ones. `owner` is stored in `dcterms:contributor`. If `filed_against` is omitted, it is not sent and the server applies its own default; the iteration is auto-discovered. A refused creation surfaces the server's message in the error, with a `list_fields` hint when it concerns the category. |
| `update_workitem`     | `(id, title=None, description=None, owner=None, estimate_hours=None, planned_for=None, custom_fields=None)` | Partial update: PUTs only the given properties (`?oslc.properties=...`) with `If-Match`. A stale ETag (412) is reported as a conflict. `owner` is stored in `dcterms:contributor`. |
| `add_child_workitem`  | `(parent_id, title, type="task", description=None, owner=None, project=...)` | Create a work item linked as a child of `parent_id`. |
| `transition_workitem` | `(id, action)` | Execute a workflow action on a work item (`PUT ?_action=<id>`) and re-read its state. `action` is the id of a workflow action, its id suffix (e.g. `a27`), or a unique case-insensitive substring of the id; `start`/`resolve`/`close`/`reopen` work only where the workflow's action ids contain those words. An unresolved or ambiguous `action` lists the available ids; an action that does not change the state is an error. Returns `Work item {id}: {from} -> {to} (action: {action id})`. |
| `add_comment`         | `(id, text)` | POST the comment as OSLC CM 1.0 (`application/x-oslc-cm-change-request+json`, no `OSLC-Core-Version`: the 2.0 endpoint answers 405) and confirm it in `list_comments`. Returns the new comment. If the server accepts the POST but the comment does not appear, the call fails and the POST is **not** resent (check the item before retrying). |
| `link_workitems`      | `(source, target, link_type)` | Add a link from `source` to `target`: partial PUT of `rtc_cm:com.ibm.team.workitem.linktype.<type>` with `If-Match`, resending the links of that type already there; confirmed by re-reading `source`. `link_type`: `parent` (`parentworkitem.parent`, replaces the current parent), `blocks` (`blocksworkitem.blocks`), `depends_on` (`blocksworkitem.dependsOn`), `related` (`relatedworkitem.related`), `duplicate_of` (`duplicateworkitem.duplicateOf`), `resolves` (`resolvesworkitem.resolves`). An unknown type is refused listing the valid ones. |
| `unlink_workitems`    | `(source, target, link_type)` | Remove only that link (the PUT resends the remaining links, an empty list for the last one); confirmed by re-reading. A target that is not linked is an error, nothing written. |
| `set_field`           | `(id, field, value)` | Partial PUT of one field with `If-Match`. `field` is the prefixed property (`rtc_cm:storyPoints`) or the shape's property name (`storyPoints`). For an enumerated field `value` is the literal title (`5 pts`) or id, validated against `allowedValues` (a value outside them is refused listing the valid titles). Returns `{"id", "field", "value", "warnings"}`; writing `Unassigned` to a required attribute adds a warning (the server treats it as empty). |

## Errors

A failed HTTP call surfaces as a `ToolError` that starts with the gate that probably blocked it, then the
status, the operation and the action to take. The main reads and all writes re-authenticate once on a 401 or on a 403
without `oslc:message` before reporting (a few direct reads do not yet: see "Dívida conhecida" in `docs/roadmap.md`). A login that answers 403 or 5xx fails with `credencial` or `http-<status>`.

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
- `create_workitem` resolves the type from the project's own discovery (the same data as `list_types`), so custom
  types work without code changes (`docs/specs/SPEC-wi-custom-types.md`).
- `custom_fields` injects project-specific RDF attributes; values are discovered per project area
  (via the discovery tools), not hardcoded.
- QM (ETM) is read-only through these tools; test-case write and cross-domain OSLC links
  (Implements Requirement / Tested By / Affects) are planned (`docs/specs/`, `docs/roadmap.md`).
- State changes go through `transition_workitem` (workflow action), not `update_workitem`.

## Flat dict from RDF/XML (`get_resource` on RM)

`get_resource` asks for JSON first. The HTTP 415 fallback to `application/rdf+xml` applies **only to RM URIs** (path starting with `/rm/`); a 415 on any other URI (e.g. `/ccm/`) is reported as an error without a second call. The main `rdf:Description` is the one whose `rdf:about` equals the requested URI (else the first one). A malformed body, or one with a DTD/`<!ENTITY>`, raises `ValueError` (reported as a tool error).

Limitations of the flat dict (lossy by design):

- `rdf:nodeID` references and nested child nodes (a property whose value is an inline `rdf:Description`) are not preserved; such a property yields its text, usually empty.
- `rdf:datatype` is dropped: typed literals come back as plain strings.
- `rdf:parseType="Literal"` is returned as an XHTML string (`<div xmlns="http://www.w3.org/1999/xhtml">...</div>`) rebuilt from the parsed tree, not the original bytes (whitespace between attributes and namespace declarations may differ).
