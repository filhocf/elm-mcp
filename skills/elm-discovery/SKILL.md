---
name: elm-discovery
description: Discover a project area's shapes, required fields, folders and concrete field values from the elm-mcp server BEFORE creating or updating anything. Use this first whenever you are about to write a requirement or a work item, or when you need to know what types/fields/enumerations a project area expects.
metadata:
  version: "0.1.0"
  keywords: "discover, discovery, shape, shapes, fields, required, types, folders, inspect, metadata, oslc, elm, doors, rtc, ewm, etm"
---

# ELM Discovery — know the shape before you write

## Purpose

IBM ELM (Engineering Lifecycle Management, OSLC) does not have a fixed schema: every
project area defines its own resource types (shapes), required/optional fields and
enumerations. The `elm-mcp` server deliberately hardcodes **nothing** about any specific
organization — so before you create or update a requirement or work item you must *discover*
what that project area actually requires and fill those fields.

This skill covers the read-only discovery tools:
`list_projects`, `list_types`, `list_fields`, `list_folders`, `inspect_workitem`.

It is the prerequisite for the `elm-requirements` and `elm-workitems` skills.

## When to use

- Before any `create_*` / `update_*` call, to learn the mandatory fields and allowed values.
- When a write failed because a required field was missing or a value was not allowed.
- When you need the `instanceShape` URI (RM) or a folder URI, which cannot be guessed.
- When you want to copy a real item's concrete field values rather than inventing them.

## Prerequisites

- The `elm-mcp` MCP server is registered with your agent (typically run via `uvx elm-mcp`).
- Credentials are configured, either interactively with `elm-mcp login` or via environment
  variables `ELM_URL`, `ELM_USER`, `ELM_PASSWORD`.
- You know (or can list) the project area you are targeting. Domains are:
  `rm` = requirements (DOORS Next), `ccm` = work items (RTC/EWM), `qm` = test cases (ETM).

See `docs/TOOLS.md` and `docs/USAGE.md` for full signatures and query syntax.

## Workflow

The core pattern is **list_types → list_fields**, with `list_folders` for RM and
`inspect_workitem` to learn concrete values from a real item.

1. **Find the project area.** Use `list_projects(domain=...)` to get the project names
   (service providers) available for the domain you want.
2. **List the resource types (shapes).** `list_types(domain, project)` returns
   `[{"title", "shape_uri", "resource_type"}]`. For `ccm` each item is a work item type
   creation factory with extra `id`, `factory_title` and `factory_uri` (the `id` is the type id).
   The `title` is what you pass to `list_fields`; the `shape_uri` is the `instanceShape` URI you pass to
   `create_requirement(type=...)`.
3. **List the fields of a type.** `list_fields(domain, project, type)` returns
   `{"type", "shape_uri", "required": [...], "optional": [...]}`. `type` is a shape *title*
   from step 2 (case-insensitive); for `ccm` the type `id` or `title`, or the alias `task`/`story`.
   Each field also carries `occurs` and `read_only`. Enumerated fields come with their `allowed_values` — use
   exactly those values, do not invent them.
4. **RM only — list folders.** `list_folders(project)` returns `[{"title", "uri"}]`
   (system folders are filtered out). The `uri` is what `create_requirement(folder=...)`
   expects.
5. **Learn from a real item (optional but powerful).** `inspect_workitem(id)` reads an
   existing work item and derives `{"id", "type", "shape_uri", "factory_uri", "fields": {...}}`.
   Copy the `fields` values (categories, iterations, custom `rtc_ext:*` attributes) into your
   own create/update instead of guessing them.

## Examples

```python
# 1. Which project areas exist for work items?
list_projects(domain="ccm")
# -> ["MY PROJECT (KEY)", "ANOTHER PROJECT (KEY2)", ...]

# 2. What resource types (shapes) does this project area define?
list_types(domain="ccm", project="MY PROJECT (KEY)")
# -> [{"title": "Task",
#      "shape_uri": "https://elm.company.com/ccm/oslc/.../shape/_SHAPE_OID",
#      "resource_type": "..."}, ...]

# 3. What does a "Task" require, and which values are allowed?
list_fields(domain="ccm", project="MY PROJECT (KEY)", type="Task")
# -> {"type": "Task",
#     "shape_uri": "...",
#     "required": [{"name": "dcterms:title"}, ...],
#     "optional": [{"name": "...", "allowed_values": ["Low", "Medium", "High"]}, ...]}

# 4. RM: which folders can a requirement be filed under?
list_folders(project="MY RM PROJECT")
# -> [{"title": "Specifications", "uri": "https://elm.company.com/rm/folders/_FOLDER_OID"}, ...]

# RM: resolve the instanceShape URI for the kind you want to create
list_types(domain="rm", project="MY RM PROJECT")
# -> pick the entry whose "title" matches, then use its "shape_uri" as create_requirement(type=...)

# 5. Learn a project area's concrete values from a real work item
inspect_workitem(id="100200")
# -> {"id": "100200",
#     "type": "Task",
#     "shape_uri": "...",
#     "factory_uri": "...",
#     "fields": {"filed_against": "...", "planned_for": "...", "rtc_ext:...": {...}}}
```

## Notes / Gotchas

- **`list_fields` takes a title, `create_requirement(type=...)` takes a URI.** Do not confuse
  them: `type` in `list_fields` is a shape *title* (case-insensitive), while the `type` of
  `create_requirement` is the `instanceShape` *URI* (the `shape_uri` from `list_types`).
- **RM vs CCM discovery differ.** RM (DOORS Next) uses OSLC 1.0 catalog shapes, CCM (RTC/EWM)
  uses OSLC 2.0; the tools abstract this but the shapes returned look different per domain.
- **Enumerations:** when `list_fields` reports `allowed_values` for a field, you must use one
  of those exact values; arbitrary strings are rejected by the server.
- **`inspect_workitem` is CCM-only** and is the fastest way to discover custom `rtc_ext:*`
  attribute URIs that no catalog lists plainly.
- **`list_folders` is RM-only.** It filters out system folders (root, query results).
- QM (ETM) is read-only today — use `search_testcases` (one item per test case) and `list_qm_resources`; no writes.
