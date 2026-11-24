---
name: elm-requirements
description: Search, read, create and update requirements in an IBM ELM RM (DOORS Next) project area through elm-mcp. Use when you need to find existing requirements, read one by URI, or author/revise a requirement with HTML content filed under the right folder and shape.
metadata:
  version: "0.2.0"
  keywords: "requirement, requirements, rm, doors, doors next, create, update, search, oslc, primarytext, xhtml, folder, instanceshape, elm"
---

# ELM Requirements — RM / DOORS Next

## Purpose

Work with requirements in an IBM ELM **RM** (DOORS Next) project area: find them, read
them, create new ones and revise existing ones. Covers:
`search_requirements`, `get_requirement`, `create_requirement`, `update_requirement`,
`delete_requirement`.

Creating a requirement needs a requirement **type** (required) and, usually, a target folder.
Neither is hardcoded — you discover them first. See the
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

1. **Search.** `search_requirements(project, query=None, limit=None)` runs an OSLC query over the RM
   project (in the project's configuration/stream). Omit `query` to list; pass an OSLC query to filter
   (see `docs/USAGE.md`). It returns `{"items": [...], "truncated": bool}` plus `total` and `configuration` (the stream used)
   when the server reports them; `truncated=true` means there are more items than `limit`.
2. **Read one.** `get_requirement(uri)` fetches a single requirement by its OSLC URI (the
   URI comes from a search result or is already known).

### Create (discover first)

3. **Resolve the type.** `list_types(domain="rm", project=...)` returns
   `[{"title", "shape_uri", ...}]`. Pick the entry for the requirement kind you want; its `title`
   or its `shape_uri` is the value for `create_requirement(type=...)`.
4. **Resolve the folder URI.** `list_folders(project)` returns `[{"title", "uri"}]`; take the
   `uri` of your target folder for `create_requirement(folder=...)`.
5. **Create.** `create_requirement(project, title, content, folder=None, type=None,
   if_exists="error", content_format="html")`. **`type` is required at runtime**: pass the
   `shape_uri` or the `title` from `list_types(domain="rm", ...)`; without it (or with an unknown
   title) the call is refused listing the valid types.
   `content` is stored in `jazz_rm:primaryText`, which only accepts well-formed **XHTML** with one
   root `<div xmlns="http://www.w3.org/1999/xhtml">` (the tool adds it, never doubles it).
   `content_format` says what you are sending:
   - `html` (default): sanitized (`script`/`style`/`iframe`/`object`/`embed` and emoji removed, void
     tags closed, lone `&` escaped);
   - `markdown`: converted by the tool (headings, lists, tables, code, emphasis, links and images by
     http/https/mailto URL; raw HTML is escaped, never passed through);
   - `xhtml`: only validated and wrapped — it does **not** remove active content, so use it only for
     content you already trust.
   The ALM accepts neither Markdown nor plain text on its own: send one of the three formats above.
   In `html` and `xhtml`, comments (`<!--`), CDATA and processing instructions are refused (Markdown
   escapes them instead). Content above `ELM_RM_MAX_CONTENT_BYTES` (default 1 MB) is refused in every
   format.
6. **Duplicates.** Before creating, the tool looks for a requirement of the same type, title and
   folder. `if_exists="error"` (default) refuses and names the existing one, `"return"` hands it back
   with `created: false`, `"create"` creates anyway. Without `folder` any folder matches (pass
   `folder`, or use `"return"`/`"create"`). A timeout after the POST re-queries; it never resends.
7. **Large content.** If the server answers 403 to a large creation POST (over
   `ELM_RM_CREATE_FALLBACK_BYTES`, default 4096), the tool creates the requirement without text and
   applies the content by PUT. Check `content_applied` in the result: when it is `false` the
   requirement exists (see `uri`) but the text was **not** applied — fix it with `update_requirement`,
   do not create it again.

### Update

8. **Update.** `update_requirement(uri, title=None, content=None, content_format="html")` performs an
   OSLC PUT with an ETag (handled for you). Pass the requirement's OSLC URI and only the fields you
   want to change; `content` follows the same `content_format`. The tool re-reads after the PUT and
   warns if the ETag did not change.
9. **Delete.** `delete_requirement(uri)` is irreversible and unconditional (no ETag check); it is
   confirmed by re-reading (404/410). Ask the user before calling it.

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

# Create from Markdown (type = a title or shape_uri from list_types)
create_requirement(
    project="MY RM PROJECT",
    title="REQ-001 User login",
    content="## Goal\nAs a user I want to log in.\n\n- valid user\n- invalid password",
    type="Functional Requirement",
    content_format="markdown",
)

# Create with explicit type (instanceShape URI) and folder (folder URI), HTML content
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

- **`type` is required** and is a `shape_uri` or a type title, both from `list_types(domain="rm", ...)`.
- **`content` is HTML by default** (Markdown with `content_format="markdown"`). It is always turned into
  well-formed XHTML before being stored in `jazz_rm:primaryText`; reading a requirement back returns
  that XHTML, not your original text.
- **`folder` is a URI** from `list_folders`, not a folder name.
- **Update is partial.** `update_requirement` only changes the fields you pass; omit what you
  do not want to touch. The ETag is fetched and applied for you.
- Everything project-specific (which shapes/folders exist) is discovered, never hardcoded —
  reuse the `elm-discovery` skill rather than copying the placeholder URIs above.
