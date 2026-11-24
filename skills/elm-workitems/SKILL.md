---
name: elm-workitems
description: Search, read, create, update, transition and organize work items in an IBM ELM CCM (RTC / EWM) project area through elm-mcp, including parent/child hierarchy, iterations and workflow state changes. Use when managing tasks/stories, building a backlog hierarchy, assigning owners, planning into iterations, or moving items through their workflow.
metadata:
  version: "0.2.0"
  keywords: "workitem, work item, ccm, rtc, ewm, task, story, backlog, iteration, plannedfor, transition, state, parent, child, hierarchy, owner, custom fields, comment, link, hours, timesheet, attachment, duplicate, if_exists, profile, elm"
---

# ELM Work Items — CCM / RTC / EWM

## Purpose

Manage work items in an IBM ELM **CCM** (RTC / EWM) project area: find them, read them,
create tasks and stories, build parent/child hierarchy, assign owners, plan them into
iterations, set custom fields, and move them through their workflow. Covers:
`list_workitems`, `get_workitem`, `create_workitem`, `update_workitem`,
`add_child_workitem`, `transition_workitem`, `list_iterations`, `get_plan_items`,
`inspect_workitem`, and the collaboration tools `add_comment`, `list_comments`,
`link_workitems`, `unlink_workitems`, `set_field`, `list_workflow`, `list_teams`, and the read-only hours and
attachment tools `list_time`, `get_hours`, `list_attachments`, `download_attachment`.
Links from a work item to requirements and test artifacts are in the **`elm-traceability`** skill.

Category, iteration and custom `rtc_ext:*` attributes vary per project area and are **not**
hardcoded — discover them first with the **`elm-discovery`** skill.

## When to use

- Query or read work items; view a backlog hierarchy with child tasks.
- Create a task or story, optionally as a child of an existing item.
- Set owner, estimate, iteration (`plannedFor`) or project-specific custom fields.
- Move an item through its workflow (start / resolve / close / reopen).

## Prerequisites

- The `elm-mcp` MCP server is registered (typically `uvx elm-mcp`).
- Credentials via `elm-mcp login` or env `ELM_URL` / `ELM_USER` / `ELM_PASSWORD`.
- For creates/updates with project-specific fields: discover them first via
  `list_types` / `list_fields` / `inspect_workitem` — see the `elm-discovery` skill,
  `docs/TOOLS.md` and `docs/USAGE.md`.

## Workflow

### Read

1. **Query.** `list_workitems(project, query=None, limit=None, type=None, state=None,
   owner=None, planned_for=None)` runs an OSLC query. The filters (`type` id/alias/title,
   `state` id from `list_workflow` (a title only together with `type`), `owner` user id or URI,
   `planned_for` iteration URI) are sent to the server in
   `oslc.where` and combined with `query` only with `and` (no `or`); a value with a quote,
   angle bracket, backslash or non-printable character is refused. It follows the server's
   pages until `limit` items (default 50, max 1000; `limit` must be positive) and returns
   `{"items": [{id, title, type, status, owner}], "truncated": bool}`; `truncated=true` means
   there are more items. `search_requirements` and `search_testcases` use the same `{"items", "truncated"}` envelope
   (up to 1000 items; their items have their own fields), and `get_plan_items` adds a `truncated` key.
2. **Read one.** `get_workitem(id)` fetches a single work item by ID.
3. **Hierarchy view.** `get_plan_items(project, plan=None, item_ids=None)` returns backlog
   items with their child tasks expanded in one call. Select by `plan` (iteration-title
   substring) or by explicit `item_ids`.
4. **Iterations.** `list_iterations(project)` lists iterations (title, dates, URI) — use a
   URI as the `planned_for` value when assigning an item to an iteration.

### Create

5. **Discover first (if you need project-specific fields).** `list_types` / `list_fields`
   for the shape and required fields; `inspect_workitem(id)` to copy concrete values
   (category, iteration, custom attributes) from a real item.
6. **Create.** `create_workitem(project, title, type="task", description=None,
   parent_id=None, owner=None, filed_against=None, custom_fields=None, if_exists="error")`.
   `type` is any work item type of the project (id, title or alias from `list_types`; an ambiguous
   one is refused listing the ids).
   - **The tool never picks a category.** If `filed_against` is omitted it is not sent and the server
     applies its own default (often "Unassigned"), unless the local profile has a default for it
     (`defaults.<type id>`). If the server refuses the creation, the error carries its message, with a
     `list_fields` hint when the refusal concerns the category; pass `filed_against` explicitly (a
     category URI from `list_fields` or `inspect_workitem`). The iteration is auto-discovered.
   - **Required fields.** Missing required fields are filled from the local profile
     (`defaults.<type id>`; your explicit arguments win); any the shape still
     requires are refused, listed, **before** any write.
   - **Duplicates.** Before the POST the tool looks for a work item of the same type, normalized title
     and parent. `if_exists="error"` (default) refuses and names the existing one, `"return"` returns
     it with `created: false`, `"create"` creates anyway. After a 400/409 the check runs again before
     the single resend, and a timeout never resends the POST.
   - **Team area.** Take the `uri` of a team from `list_teams(project)` and pass
     `custom_fields={"rtc_cm:teamArea": {"rdf:resource": uri}}`. Writing `teamArea` this way has **not**
     been validated against a live server yet (try it on a disposable item first), and `set_field` is not
     suitable for it (it would send a plain string).
   Pass `parent_id` to create it already linked under a parent.
7. **Child item.** `add_child_workitem(parent_id, title, type="task", description=None,
   owner=None, project=...)` creates a work item linked as a child of `parent_id` — use this
   to build a backlog hierarchy under a story/epic.

### Collaboration

- `list_comments(id)` -> `[{"id", "author", "created", "text"}]`; `add_comment(id, text)` sends
  the text HTML-escaped (line breaks as `<br/>`), POSTs once (never resent) and confirms the new
  comment by its id.
- `link_workitems(source, target, link_type)` / `unlink_workitems(...)`: work item to work item;
  `link_type` is `parent` (replaces the current parent), `blocks`, `depends_on`, `related`,
  `duplicate_of` or `resolves`; an item cannot be linked to itself. Confirmed by re-reading.
- `set_field(id, field, value)`: `field` is `prefix:name` (or a shape property name); it must be in
  the item's shape and not read-only (otherwise use `list_fields`); the value is converted to the
  field's range (integer, boolean, decimal) and an enumerated field takes the label (as `list_fields` shows it; an ambiguous label is refused listing the ids), the id or the URI.
- `list_workflow(project, type, refresh=False)`: states (`id`, `title`, `group`) and action ids for
  `transition_workitem`.

### Hours and attachments (read only)

- `list_time(id)` -> entries of the item's time sheet (`person`, `date`, `hours`, `time_code`) and
  `total_hours`. `get_hours(item_ids=None, plan=None, project=None, group_by="person"|"item",
  max_entries=500)` sums hours for items and their direct children; above `max_entries` it returns a
  partial result (`partial`, `unread_entries`) instead of failing.
- `list_attachments(id)` -> metadata only. `download_attachment(id, attachment, dest, overwrite=False)`
  saves one attachment to a **local** file (it never writes to the ALM) and is therefore **not** read
  only: ask before running it. With `ELM_DOWNLOAD_DIR` set, `dest` must be inside it.
- **Logging hours and uploading attachments are not available** (they need an internal, unsupported
  service). Do not try to emulate them by writing `rtc_cm:timeSpent` through
  `update_workitem(custom_fields=...)`: per `docs/specs/SPEC-ccm-interno.md` the time sheet is written
  only by an internal service, and `timeSpent` is just the total.

### Update & transition

8. **Update fields.** `update_workitem(id, title=None, description=None, owner=None,
   estimate_hours=None, planned_for=None, custom_fields=None)` — partial PUT with `If-Match`;
   pass only the fields you want to change (only those are sent). A stale ETag is a conflict error. Use `planned_for` (an iteration URI) to plan into a sprint.
9. **Change state.** `transition_workitem(id, action)` where `action` is the id (or id suffix,
   e.g. `a27`) of a workflow action of the item's workflow. A unique case-insensitive substring
   also works. The words `start`/`resolve`/`close`/`reopen` work only where your workflow's
   action ids contain them. If `action` is unknown or ambiguous the error lists the available ids;
   if the action does not change the state you get an error. **State changes go through the
   workflow action, not `update_workitem`** — you cannot set state by writing a field.

## Examples

```python
# Query / read
list_workitems(project="MY PROJECT (KEY)")
list_workitems(project="MY PROJECT (KEY)", query='dcterms:title="My Task"')
get_workitem(id="100300")

# Hierarchy view: backlog items of an iteration with child tasks expanded
get_plan_items(project="MY PROJECT (KEY)", plan="Sprint 12")

# Iterations (use a URI as planned_for)
list_iterations(project="MY PROJECT (KEY)")
# -> [{"title": "Sprint 12", "identifier": "...", "start_date": "...", "end_date": "...",
#      "uri": "https://elm.company.com/ccm/oslc/iterations/_ITERATION_OID"}, ...]

# --- Create ---
# Minimal (category + iteration auto-discovered)
create_workitem(project="MY PROJECT (KEY)", title="Implement login", type="task")

# Story with explicit, DISCOVERED project-specific fields
create_workitem(
    project="MY PROJECT (KEY)",
    title="User authentication",
    type="story",
    owner="jdoe",
    filed_against="https://elm.company.com/ccm/resource/itemOid/com.ibm.team.workitem.Category/_CATEGORY_OID",
    custom_fields={  # values discovered via list_fields / inspect_workitem, not invented
        "rtc_ext:com.example.team.workitem.attribute.severity": {
            "rdf:resource": "https://elm.company.com/ccm/oslc/enumerations/_PA_OID/.../literal.l10"
        }
    },
)

# Child task under a parent (build hierarchy)
add_child_workitem(parent_id="100300", title="Write login form", type="task")

# --- Update ---
update_workitem(id="100300", owner="jdoe", estimate_hours=16)
update_workitem(
    id="100300",
    planned_for="https://elm.company.com/ccm/oslc/iterations/_ITERATION_OID",  # from list_iterations
)

# --- Transition (workflow action, NOT update_workitem) ---
transition_workitem(id="100300", action="a27")  # id suffix of a workflow action
transition_workitem(id="100300", action="start")  # only if a unique action id contains "start"
```

## Notes / Gotchas

- **`type`** on `create_workitem`: any type shown by `list_types` (precedence: exact id, then
  the last segment of the id, then title; two matches at the same level are an error).
- **Only URLs on the configured `ELM_URL` are followed** (next pages, workflow states, comments);
  anything else is refused.
- **State is a workflow action, not a field.** Use `transition_workitem(action=...)` with the id
  (or id suffix) of a workflow action; do not try to set state via `update_workitem`.
  The valid actions depend on the item's workflow and current state; a call with an
  unknown action returns the list of action ids.
- **Auto-discovery.** The category is **not** auto-discovered (see Create): pass `filed_against`
  when the server's default is not what you want. Current-iteration auto-discovery prefers bounded
  iterations (with an end date) — pass `planned_for` if your sprint is not picked up.
- **`custom_fields` are project-specific.** Discover the exact attribute URIs and allowed
  values with `list_fields` / `inspect_workitem` (see `elm-discovery`) — never hardcode the
  placeholder URIs above.
- **Updates are partial and ETag-based.** Pass only the fields you want to change; the ETag is
  handled for you.
- `list_iterations` fetches details per iteration and can be slow on projects with many.
- **`list_workitems(include_hidden=False)`** leaves out the states the local profile lists in
  `hidden_states` (never when you filter by `state`); with no profile nothing is hidden.
- **`transition_workitem`** resolves `action` as a real workflow action first (id, id suffix or unique
  substring); only if that fails does it try the profile's `action_aliases`, so an alias never hides a
  real action.
- Links to requirements and test artifacts (`link_artifacts`), and test cases (`elm-qm`), are covered
  by the **`elm-traceability`** and **`elm-qm`** skills.
