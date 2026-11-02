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

## Creating Work Items

```python
# Minimal (auto-discovers category and iteration)
create_workitem(project="MY PROJECT (KEY)", title="My Task", type="task")

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
add_child_workitem(parent_id="628430", title="Sub-task", type="task")
```

> Project-specific attributes (category, iteration, custom `rtc_ext:*` fields) vary per
> server and per project area. Discover them for your project with the discovery tools
> (`list_types`, `list_fields`, `inspect_workitem`) rather than copying the placeholders above.

## Updating Work Items

```python
# Set owner + estimate
update_workitem(id="629772", owner="jdoe", estimate_hours=16)

# Set iteration
update_workitem(id="629772", planned_for="https://elm.company.com/ccm/oslc/iterations/_ITERATION_OID")

# Custom fields
update_workitem(id="629772", custom_fields={"rtc_cm:someField": "value"})
```

## Notes

- Category auto-discovery picks the first category of the project; pass `filed_against`
  explicitly when the project has more than one.
- Current-iteration auto-discovery prefers bounded iterations (with an end date); pass
  `planned_for` explicitly if your sprint is not picked up.
- `list_iterations` fetches details per iteration and can be slow on projects with many.
- State changes go through `transition_workitem` (workflow action), not `update_workitem`.
- RM (DOORS Next) discovery differs from CCM (OSLC 1.0 vs 2.0 catalog shapes).
- QM (ETM) is read-only today; test-case write and cross-domain OSLC links are planned.
