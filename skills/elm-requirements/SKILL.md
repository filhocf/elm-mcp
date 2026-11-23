---
name: elm-requirements
description: Search, read, create and update requirements in an IBM ELM RM (DOORS Next) project area through elm-mcp. Use when you need to find existing requirements, read one by URI, or author/revise a requirement with HTML content filed under the right folder and shape.
metadata:
  version: "0.1.0"
  keywords: "requirement, requirements, rm, doors, doors next, create, update, search, oslc, primarytext, xhtml, folder, instanceshape, elm"
---

# ELM Requirements — RM / DOORS Next

## Purpose

Work with requirements in an IBM ELM **RM** (DOORS Next) project area: find them, read
them, create new ones and revise existing ones. Covers:
`search_requirements`, `get_requirement`, `create_requirement`, `update_requirement`.

Creating a requirement needs a target folder and (optionally) a specific requirement type
(`instanceShape`). Neither is hardcoded — you discover them first. See the
**`elm-discovery`** skill, which is the prerequisite for the write steps here.

## When to use

- Search for requirements by title/text, or read a specific requirement by its OSLC URI.
- Author a new requirement with HTML content under a chosen folder and type.
- Revise an existing requirement's title or body.

## Prerequisites

- The `elm-mcp` MCP server is registered (typically `uvx elm-mcp`).
- Credentials via `elm-mcp login` or env `ELM_URL` / `ELM_USER` / `ELM_PASSWORD`.
- For creating: run discovery first (`list_types`, `list_folders` on the `rm` domain) —
  see the `elm-discovery` skill, `docs/TOOLS.md` and `docs/USAGE.md`.

## Workflow

### Read

1. **Search.** `search_requirements(project, query=None)` runs an OSLC query over the RM
   project. Omit `query` to list; pass an OSLC query to filter (see `docs/USAGE.md`).
2. **Read one.** `get_requirement(uri)` fetches a single requirement by its OSLC URI (the
   URI comes from a search result or is already known).

### Create (discover first)

3. **Resolve the type (instanceShape URI).** `list_types(domain="rm", project=...)` returns
   `[{"title", "shape_uri", ...}]`. Pick the entry whose `title` is the requirement kind you
   want and keep its `shape_uri` — that is the value for `create_requirement(type=...)`.
   `type` is a **URI, not a title.**
4. **Resolve the folder URI.** `list_folders(project)` returns `[{"title", "uri"}]`; take the
   `uri` of your target folder for `create_requirement(folder=...)`.
5. **Create.** `create_requirement(project, title, content, folder=None, type=None)`.
   `content` is **HTML** — it is sanitized to XHTML and stored in `jazz_rm:primaryText`.
   If you omit `type`/`folder`, the server uses defaults for the project area.

### Update

6. **Update.** `update_requirement(uri, title=None, content=None)` performs an OSLC PUT with
   an ETag (handled for you). Pass the requirement's OSLC URI and only the fields you want to
   change; `content` is again HTML sanitized to XHTML.

## Examples

```python
# Search requirements (OSLC query optional)
search_requirements(project="MY RM PROJECT")
search_requirements(project="MY RM PROJECT", query='dcterms:title="User login"')

# Read one by its OSLC URI
get_requirement(uri="https://elm.company.com/rm/resources/_REQ_OID")

# --- Create: discover shape + folder first ---
list_types(domain="rm", project="MY RM PROJECT")
# -> pick the kind you want; keep its shape_uri
list_folders(project="MY RM PROJECT")
# -> pick the target folder; keep its uri

# Minimal create (title + HTML content; server picks default folder/type)
create_requirement(
    project="MY RM PROJECT",
    title="REQ-001 User login",
    content="<p>As a user I want to log in so that I can access my account.</p>",
)

# Create with explicit type (instanceShape URI) and folder (folder URI)
create_requirement(
    project="MY RM PROJECT",
    title="REQ-001 User login",
    content="<p>As a user I want to log in...</p>",
    type="https://elm.company.com/rm/types/_SHAPE_OID",  # from list_types -> shape_uri
    folder="https://elm.company.com/rm/folders/_FOLDER_OID",  # from list_folders -> uri
)

# Update an existing requirement (OSLC PUT with ETag, handled internally)
update_requirement(
    uri="https://elm.company.com/rm/resources/_REQ_OID",
    title="REQ-001 User login (revised)",
    content="<p>Updated acceptance criteria...</p>",
)
```

## Notes / Gotchas

- **`type` is an `instanceShape` URI, not a title.** Resolve the requirement kind via
  `list_types(domain="rm", ...)` and pass its `shape_uri`. Passing a title will not work.
- **`content` is HTML.** It is sanitized to XHTML before being stored in
  `jazz_rm:primaryText`; well-formed fragments (`<p>…</p>`, lists, etc.) are safest.
- **`folder` is a URI** from `list_folders`, not a folder name.
- **Update is partial.** `update_requirement` only changes the fields you pass; omit what you
  do not want to touch. The ETag is fetched and applied for you.
- Everything project-specific (which shapes/folders exist) is discovered, never hardcoded —
  reuse the `elm-discovery` skill rather than copying the placeholder URIs above.
