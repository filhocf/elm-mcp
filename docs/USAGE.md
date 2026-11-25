# Usage Patterns

Examples use placeholder values. Replace the server host (`https://elm.company.com`),
project names, user logins and resource URIs with the ones discovered for your own
ELM project area.

## Query Syntax (list_workitems, search_requirements)

OSLC query syntax. Examples:

```python
# By title
list_workitems(project="MY PROJECT (KEY)", query='dcterms:title="Some title"')

# By iteration (plannedFor)
list_workitems(
    project="MY PROJECT (KEY)", query="rtc_cm:plannedFor=<https://elm.company.com/ccm/oslc/iterations/_ITERATION_OID>"
)

# By type
list_workitems(project="MY PROJECT (KEY)", query='dcterms:type="Task"')

# By state
list_workitems(
    project="MY PROJECT (KEY)",
    query="rtc_cm:state=<https://elm.company.com/ccm/oslc/workflows/_WORKFLOW_OID/states/com.ibm.team.workitem.taskWorkflow/com.ibm.team.workitem.taskWorkflow.state.s1>",
)
```

## Discovery (before you write)

Writing a requirement or work item needs shape and field values that differ per project area.
Discover them instead of hardcoding — nothing about a specific organization is baked into the tools.

```python
# 1. What resource types (shapes) exist in this project area?
list_types(domain="ccm", project="MY PROJECT (KEY)")
# -> [{"title": "Task", "shape_uri": "https://elm.company.com/ccm/oslc/.../shape/...", "resource_type": "..."}, ...]

# 2. What fields does a given type require (and which values are allowed)?
list_fields(domain="ccm", project="MY PROJECT (KEY)", type="Task")
# -> {"type": "Task", "shape_uri": "...", "required": [...], "optional": [{"name": "...", "allowed_values": [...]}]}

# 3. RM only: what folders can a requirement be filed under?
list_folders(project="MY RM PROJECT")
# -> [{"title": "Specifications", "uri": "https://elm.company.com/rm/folders/_OID"}, ...]

# 4. Learn a project area's concrete values by reading a real work item
inspect_workitem(id="628430")
# -> {"id": "628430", "type": "Task", "shape_uri": "...", "factory_uri": "...", "fields": {...}}
```

Typical pattern: `list_types` to find the shape title → `list_fields` to see what is required →
fill those fields on `create_workitem` / `create_requirement`. For RM, resolve the folder with
`list_folders` and the `instanceShape` URI from `list_types`.

## Creating Requirements (RM / DOORS Next)

```python
# Minimal — title + HTML content (sanitized to XHTML for primaryText)
create_requirement(
    project="MY RM PROJECT",
    title="HF-001 User login",
    content="<p>As a user I want to log in...</p>",
)

# With an explicit type (instanceShape URI from list_types) and target folder (from list_folders)
create_requirement(
    project="MY RM PROJECT",
    title="HF-001 User login",
    content="<p>As a user I want to log in...</p>",
    type="https://elm.company.com/rm/types/_SHAPE_OID",
    folder="https://elm.company.com/rm/folders/_FOLDER_OID",
)

# Update an existing requirement (OSLC PUT with ETag; pass the requirement's OSLC URI)
update_requirement(
    uri="https://elm.company.com/rm/resources/_REQ_OID",
    title="HF-001 User login (revised)",
    content="<p>Updated text...</p>",
)
```

> `type` is an `instanceShape` **URI**, not a title. Resolve the requirement kind you want via
> `list_types(domain="rm", project=...)` and pass its `shape_uri`. `content` is HTML and is
> sanitized to XHTML before being stored in `jazz_rm:primaryText`.

### Content format and large bodies

`content_format` (`create_requirement`, `update_requirement`) selects how `content` is read:
`"html"` (default; sanitized, `script`/`style`/`iframe`/`object`/`embed` and characters above U+FFFF removed),
`"xhtml"` (only validated and wrapped; refused if not well-formed) or `"markdown"` (headings, paragraphs,
bold/italic/inline code, fenced code, lists with one nesting level, blockquote, `---`, pipe tables, links and
images by `http`/`https`/`mailto` URL; raw HTML is escaped). Any other value is refused before any request.
The stored text always has exactly one `<div xmlns="http://www.w3.org/1999/xhtml">` root, also when the content
already comes with it.

```python
create_requirement(
    project="MY RM PROJECT",
    title="HF-002 Export",
    type="...",
    content="## Rule\n\n- item one\n- item two",
    content_format="markdown",
)
```

`ELM_RM_CREATE_FALLBACK_BYTES` (default 4096, read per call; zero, negative or invalid uses 4096): if the server answers the creation POST with an
explicit 403 and the content is larger than this many bytes, the requirement is created without text and the
content is applied by PUT with `If-Match`; the PUT must be accepted (2xx) and the re-read confirms that the
requirement exists and its ETag changed (the content itself is not compared). The result then carries
`content_applied`; if the PUT fails it is `false`, the requirement exists (`uri`) and a `warning` says so;
nothing is deleted. A POST is never resent after a timeout.

Limits and cautions for `content`:

- `ELM_RM_MAX_CONTENT_BYTES` (default 1048576, read per call): larger content is refused before any request.
- `content_format="xhtml"` only validates and wraps: it does NOT remove active content (`script`, `style`,
  `iframe`...). Use `html` (sanitized) or `markdown` for untrusted text.
- In `html`, an unclosed `<script>x` (or a `<script ` without `>`) removes everything up to the end of the text.
- `html` and `xhtml` refuse comments (`<!--`), CDATA (`<![CDATA[`) and processing instructions (`<?`).
- Markdown links accept one level of balanced parentheses in the URL (`[a](http://x/a(b))`); labels are limited to
  1000 and URLs to 2000 characters (longer ones stay as literal text).
- `update_requirement` warns when the re-read after the PUT returns the same ETag as before.

## Creating Work Items

```python
# Minimal (iteration auto-discovered; the server applies its default category)
create_workitem(project="MY PROJECT (KEY)", title="My Task", type="task")
# -> {"id": "629800", "uri": "https://elm.company.com/ccm/resource/itemName/com.ibm.team.workitem.WorkItem/629800",
#     "title": "My Task", "type": "Task", "state": "New", "created": True}
# verbose=True returns the whole work item as the server sent it (about 125 keys), plus "created"

# With explicit fields (project-specific attributes)
create_workitem(
    project="MY PROJECT (KEY)",
    title="My Task",
    type="task",
    owner="jdoe",
    filed_against="https://elm.company.com/ccm/resource/itemOid/com.ibm.team.workitem.Category/_CATEGORY_OID",
    custom_fields={
        "rtc_ext:com.company.team.workitem.attribute.customattr": {
            "rdf:resource": "https://elm.company.com/ccm/oslc/enumerations/_PROJECT_AREA_OID/com.company.team.workitem.enumeration.customattr/com.company.team.workitem.enumeration.customattr.literal.l10"
        }
    },
)

# Child task (under a parent work item)
add_child_workitem(parent_id="628430", title="Sub-task", project="MY PROJECT (KEY)", type="task")
# -> same summary as create_workitem (verbose=True for the whole item)
```

> Project-specific attributes (category, iteration, custom `rtc_ext:*` fields) vary per
> server and per project area. Discover them for your project with the discovery tools
> (`list_types`, `list_fields`, `inspect_workitem`) rather than copying the placeholders above.

## Updating Work Items

```python
# Set owner + estimate
update_workitem(id="629772", owner="jdoe", estimate_hours=16)
# -> {"id": "629772", "uri": "...", "title": "...", "type": "Task", "state": "In Progress", "updated": True}
# verbose=True returns the whole re-read work item

# Set iteration
update_workitem(id="629772", planned_for="https://elm.company.com/ccm/oslc/iterations/_ITERATION_OID")

# Custom fields
update_workitem(id="629772", custom_fields={"rtc_cm:someField": "value"})
```

## Notes

- The category is not auto-discovered: without `filed_against` the server applies its own default;
  pass `filed_against` (a category URI from `list_fields`/`inspect_workitem`) to choose it.
- Current-iteration auto-discovery prefers bounded iterations (with an end date); pass
  `planned_for` explicitly if your sprint is not picked up.
- `list_iterations` fetches details per iteration and can be slow on projects with many.
- State changes go through `transition_workitem` (workflow action), not `update_workitem`.
- RM (DOORS Next) discovery differs from CCM (OSLC 1.0 vs 2.0 catalog shapes).
- QM (ETM) is read-only today; test-case write and cross-domain OSLC links are planned.

## Connection settings (TLS and password)

| Variable | Effect |
|----------|--------|
| `ELM_CA_BUNDLE` | Path to a CA bundle (PEM) used to verify the server certificate, or `system` for the OS trust store. A missing file is an error naming `ELM_CA_BUNDLE`. |
| `ELM_VERIFY_TLS` | `false`, `0`, `no` or `off` (any case) disables verification and wins over `ELM_CA_BUNDLE`. One warning is logged per process. Default: verification on. |
| `ELM_PASSWORD` | Used as is. Prefix with `b64:` to pass it base64-encoded (UTF-8; surrounding whitespace after the prefix is ignored). `b64:b64:x` is the literal text `b64:x`. `b64:` followed by invalid base64 fails at authentication, naming `ELM_PASSWORD`. An empty password is sent as is and refused by the server. |
| `~/.elm_creds.json` | Written by `elm-mcp login`; the `password` is always base64 of UTF-8. A plaintext or invalid `password` is an error at authentication (no fallback): run `elm-mcp login` again. |

**Breaking (#70):** a base64 `ELM_PASSWORD` without the `b64:` prefix is now used as raw text (write `b64:<base64>`), and a plaintext `password` in `~/.elm_creds.json` is now an error (run `elm-mcp login`).

```bash
export ELM_CA_BUNDLE=/etc/ssl/certs/company-ca.pem   # internal CA
export ELM_VERIFY_TLS=false                          # or, not recommended: skip verification
```
