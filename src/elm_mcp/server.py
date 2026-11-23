"""MCP server for IBM ELM (Engineering Lifecycle Management)."""

import functools
import logging
from collections.abc import Awaitable, Callable
from typing import TypeVar
from urllib.parse import quote

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import ToolAnnotations

from . import cache, oslc, profile, qm
from .config import settings

logger = logging.getLogger(__name__)

mcp = MCPServer("elm-mcp", instructions="IBM ELM (DOORS Next, RTC, ETM) via OSLC")

VALID_DOMAINS = ("rm", "ccm", "qm")

# Creation errors that may come from a stale shape or value (validation): rediscover once and resend once.
_STALE_SHAPE_STATUSES = (400, 409)

_T = TypeVar("_T")


def _register(func: Callable[..., Awaitable[_T]], read_only: bool) -> Callable[..., Awaitable[_T]]:
    """Register an async MCP tool, translating domain errors into ToolError.

    ``read_only`` is published as the MCP ``readOnlyHint`` annotation (part of the versioned
    contract in tests/contract/tools.snapshot.json).

    OSLC/discovery failures surface from oslc.py as ValueError/RuntimeError
    (e.g. "Project not found", "No query capability found"). Without this wrap
    the MCPServer rewraps them as UnexpectedToolError and the diagnostic message
    is dropped — the client only sees "Error executing tool <name>". Re-raising
    as ToolError preserves the message for the user. oslc.py stays free of any
    MCP SDK dependency (the translation happens here, at the tool boundary).
    """

    @functools.wraps(func)
    async def wrapper(*args, **kwargs):
        try:
            return await func(*args, **kwargs)
        except ToolError:
            raise
        except (ValueError, RuntimeError) as exc:
            raise ToolError(str(exc)) from exc

    return mcp.tool(annotations=ToolAnnotations(readOnlyHint=read_only))(wrapper)


def tool(func: Callable[..., Awaitable[_T]]) -> Callable[..., Awaitable[_T]]:
    """Register a WRITE tool (readOnlyHint=False)."""
    return _register(func, read_only=False)


def read_tool(func: Callable[..., Awaitable[_T]]) -> Callable[..., Awaitable[_T]]:
    """Register a READ-ONLY tool (readOnlyHint=True)."""
    return _register(func, read_only=True)


@read_tool
async def list_projects(domain: str) -> list[dict]:
    """List projects for a domain (rm=DOORS Next, ccm=RTC, qm=ETM)."""
    domain = domain.lower()
    if domain not in VALID_DOMAINS:
        raise ToolError(f"Invalid domain '{domain}'. Must be one of: {VALID_DOMAINS}")
    return await oslc.list_service_providers(domain)


@read_tool
async def search_requirements(project: str, query: str | None = None, limit: int | None = None) -> dict:
    """Search requirements in a DOORS Next project. Query uses oslc.where syntax.

    The project's configuration (stream) is discovered and sent as Configuration-Context; the first
    stream listed is used. Follows the server's pages until `limit` items (default and maximum 1000).
    Returns {"items": [{"uri", "title", "identifier", "type", "modified", "folder"?}], "truncated": bool,
    "total"?: int, "configuration"?: stream URI}; truncated=True means the query has more items than were
    returned; total (matches reported by the server) and configuration (the stream sent) appear when known.
    """
    results = await oslc.query_resources(
        "rm", project, query, limit=limit if limit is not None else oslc.MAX_QUERY_LIMIT
    )
    result = {"items": list(results), "truncated": getattr(results, "truncated", False)}
    for name in ("total", "configuration"):
        if getattr(results, name, None) is not None:
            result[name] = getattr(results, name)
    return result


@read_tool
async def get_requirement(uri: str) -> dict:
    """Get a single requirement by its OSLC URI."""
    return await oslc.get_resource(uri)


@read_tool
async def list_workitems(
    project: str,
    query: str | None = None,
    limit: int | None = None,
    type: str | None = None,
    state: str | None = None,
    owner: str | None = None,
    planned_for: str | None = None,
    include_hidden: bool = False,
) -> dict:
    """Search work items in an RTC project. Query uses oslc.where syntax.

    Optional filters, applied by the server and combined with `query` using "and":
        type: work item type id, alias or title (e.g. "task", "Story"); resolved by the project's
            discovery and sent as rtc_cm:type="<type id>"
        state: state id (e.g. "com.ibm.team.apt.storyWorkflow.state.s3", see list_workflow) sent as
            rtc_cm:state; a state title is accepted only together with `type` (resolved by that type's
            workflow); otherwise pass the id
        owner: user id (e.g. "jdoe") or user URI, always sent as a URI (dcterms:contributor)
        planned_for: iteration URI (rtc_cm:plannedFor, see list_iterations)
        include_hidden: false (default) leaves out, on the client, the states the local profile lists in
            hidden_states (e.g. closed ones); true returns them too. A `state` filter is never hidden.
    A filter value with a double quote, angle bracket, backslash or non-printable character is refused.
    The raw `query` is combined with the filters only with `and`: `or` is not supported (oslc.where has
    no disjunction), so a `query` containing `or` is not guaranteed to work.

    Follows the server's pages until `limit` items (default 50, maximum 1000).
    Returns {"items": [...], "truncated": bool}; truncated=True means the query has more items.
    """
    clauses = [query] if query else []
    clauses += await oslc.workitem_filters(project, type=type, state=state, owner=owner, planned_for=planned_for)
    query = " and ".join(clauses) or None
    select = "dcterms:title,dcterms:identifier,dcterms:type,oslc_cm:status,dcterms:contributor"
    hidden: set[str] = set()
    warning = None
    if not include_hidden and not state and profile.profile_dir().is_dir():  # no profile directory: no extra request
        project_url = await oslc._resolve_project_url("ccm", project)
        try:
            hidden = profile.hidden_states(profile.load(oslc._project_area_id(project_url)))
        except ValueError as exc:  # a broken profile must not take the listing down
            warning = f"The local profile was ignored (hidden_states not applied): {exc}"
        if hidden:
            select += ",rtc_cm:state"
    results = await oslc.query_resources("ccm", project, query, select=select, limit=limit)
    parsed = []
    for item in results:
        state_uri = oslc._resource_uri(item.get("rtc_cm:state"))
        if hidden and state_uri and oslc._last_segment(state_uri) in hidden:
            continue
        entry = {"rdf:about": item.get("rdf:about", item.get("rdf:resource", ""))}
        if "dcterms:title" in item:
            entry["title"] = item["dcterms:title"]
        if "dcterms:identifier" in item:
            entry["id"] = item["dcterms:identifier"]
        if "dcterms:type" in item:
            t = item["dcterms:type"]
            entry["type"] = t.get("rdf:resource", t) if isinstance(t, dict) else t
        if "oslc_cm:status" in item:
            entry["status"] = item["oslc_cm:status"]
        if "dcterms:contributor" in item:
            c = item["dcterms:contributor"]
            entry["owner"] = c.get("rdf:resource", c) if isinstance(c, dict) else c
        parsed.append(entry)
    result = {"items": parsed, "truncated": getattr(results, "truncated", False)}
    if warning:
        result["warning"] = warning
    return result


@read_tool
async def get_workitem(id: str) -> dict:
    """Get a single work item by ID (constructs URI from CCM base)."""
    uri = f"{settings.elm_url}/ccm/resource/itemName/com.ibm.team.workitem.WorkItem/{id}"
    return await oslc.get_resource(uri)


@read_tool
async def search_testcases(project: str, query: str | None = None) -> dict:
    """Search test cases in an ETM project. Query uses oslc.where syntax.

    One item per test case ({uri, title, identifier, short_id, state, type}). Follows the server's pages
    up to 1000 items. Returns {"items": [...], "truncated": bool}; truncated=True means more items exist.
    """
    return await qm.search_testcases(project, query)


@read_tool
async def list_qm_resources(project: str, type: str, query: str | None = None, limit: int | None = None) -> dict:
    """List ETM resources of one type, one item per resource ({uri, title, identifier, short_id, state, type}).

    Args:
        project: ETM project name
        type: TestPlan | TestCase | TestScript | TestScriptStep | TestSuite | TestExecutionRecord |
            TestSuiteExecutionRecord | TestResult | TestSuiteResult | TestData | Keyword | TestEnvironment |
            BuildRecord | BuildDefinition (case-insensitive)
        query: optional oslc.where
        limit: items to return (default 50, maximum 1000)

    Returns {"items": [...], "truncated": bool, "total": oslc:totalCount or None}.
    """
    return await qm.list_resources(project, type, query, limit)


@read_tool
async def get_qm_resource(uri: str) -> dict:
    """Get one ETM resource: {uri, type, title, identifier, short_id, state, links}.

    `links` groups the destination URIs: validates_requirement (RM), tests_change_request (CCM),
    uses_test_script, runs_test_case, executes_test_script, test_results, ...
    """
    return await qm.get_resource(uri)


@read_tool
async def get_test_coverage(project: str) -> dict:
    """Traceability per test case: the requirements it validates and the work items it tests.

    Returns {"items": [{uri, title, short_id, requirements, work_items}], "truncated": bool}.
    """
    return await qm.get_test_coverage(project)


@read_tool
async def get_test_results(project: str, test_case: str | None = None) -> dict:
    """Test results with verdict, points per status and the execution record they came from.

    Args:
        project: ETM project name
        test_case: optional test case URI; keeps only the results that report on it

    Returns {"items": [{uri, title, verdict, verdict_name, points, execution_record, test_case}], "truncated": bool}.
    verdict is the URI (fragment) of the verdict resource; verdict_name is its readable last part
    (e.g. "inconclusive"). Zeroed points come as 0.
    """
    return await qm.get_test_results(project, test_case)


@read_tool
async def list_iterations(project: str) -> list[dict]:
    """List iterations (sprints/releases) for an RTC project with title and dates."""
    project_url = await oslc._resolve_project_url("ccm", project)
    parts = project_url.split("/contexts/")
    project_area_id = parts[1].split("/")[0] if len(parts) > 1 else project_url.rstrip("/").split("/")[-1]
    client = await oslc.get_client()
    url = f"{settings.elm_url}/ccm/oslc/iterations?projectArea={project_area_id}"
    resp = await oslc.fetch(
        client, "GET", url, "list_iterations", headers={"Accept": "application/json", "OSLC-Core-Version": "2.0"}
    )
    results = resp.json().get("oslc:results", [])
    iterations = []
    for r in results:
        iter_uri = r.get("rdf:resource")
        if not iter_uri:
            continue
        detail = await client.get(
            oslc.assert_same_origin(iter_uri), headers={"Accept": "application/json", "OSLC-Core-Version": "2.0"}
        )
        if detail.status_code != 200:
            continue
        d = detail.json()
        iterations.append(
            {
                "title": d.get("dcterms:title", ""),
                "identifier": d.get("dcterms:identifier", ""),
                "start_date": (d.get("rtc_cm:startDate") or "")[:10] or None,
                "end_date": (d.get("rtc_cm:endDate") or "")[:10] or None,
                "uri": iter_uri,
            }
        )
    return iterations


@read_tool
async def get_plan_items(project: str, plan: str | None = None, item_ids: list[str] | None = None) -> dict:
    """Get hierarchical view of Items de Backlog with their child Tarefas expanded.

    RTC/EWM hierarchy: Plan → Sprint → Item de Backlog (grouper) → Tarefa (actual work).
    This tool resolves the hierarchy in one call, avoiding N+1 queries.

    Two modes:
    - item_ids: expand specific IBs by ID (fastest, use when you know the IDs)
    - plan: match iteration by title substring, then find IBs planned for it

    Args:
        project: Project name (e.g., "MY PROJECT (KEY)")
        plan: Iteration title substring to match (e.g., "Entrega 5"). Optional.
        item_ids: List of IB IDs to expand directly (e.g., ["622299", "622289"]). Optional.
    """
    if not plan and not item_ids:
        return {"error": "Provide either 'plan' (iteration name) or 'item_ids' (list of IB IDs)"}

    iteration_info = None
    truncated = False

    if item_ids:
        # Direct mode: expand given IB IDs
        ib_ids_to_fetch = item_ids
    else:
        # Iteration mode: find matching iteration, then get IBs
        iterations = await list_iterations(project)
        matched = [i for i in iterations if plan.lower() in i["title"].lower()]
        if not matched:
            return {
                "error": f"No iteration matching '{plan}'",
                "available": [i["title"] for i in iterations if i["start_date"]],
            }
        iteration_info = matched[0]
        iter_uri = iteration_info["uri"]

        # Get all IBs (type=story) and filter by plannedFor
        all_items = await oslc.query_resources(
            "ccm", project, None, select="dcterms:title,dcterms:identifier,dcterms:type", limit=oslc.MAX_QUERY_LIMIT
        )
        truncated = getattr(all_items, "truncated", False)
        ib_ids_to_fetch = []
        for item in all_items:
            item_type = item.get("dcterms:type", "")
            if isinstance(item_type, dict):
                item_type = item_type.get("rdf:resource", "")
            if "story" in str(item_type).lower():
                ib_id = item.get("dcterms:identifier", "")
                if ib_id:
                    ib_ids_to_fetch.append(ib_id)

    # Fetch each IB and build hierarchy
    result_items = []
    for ib_id in ib_ids_to_fetch:
        try:
            full = await get_workitem(ib_id)
        except Exception as exc:  # noqa: BLE001 — skip unreadable items, keep building the plan
            logger.debug("get_plan_items: skipping work item %s (%s)", ib_id, exc)
            continue

        # If filtering by iteration, check plannedFor
        if iteration_info:
            planned = full.get("rtc_cm:plannedFor", {})
            planned_uri = planned.get("rdf:resource", "") if isinstance(planned, dict) else str(planned)
            if iter_uri not in planned_uri:
                continue

        # Extract children
        children_raw = full.get("rtc_cm:com.ibm.team.workitem.linktype.parentworkitem.children", [])
        children = []
        for child in children_raw:
            raw_title = child.get("dcterms:title", "")
            child_id = raw_title.split(":")[0].strip() if ":" in raw_title else ""
            child_title = raw_title.split(": ", 1)[1] if ": " in raw_title else raw_title
            children.append({"id": child_id, "title": child_title, "uri": child.get("rdf:resource", "")})

        status = full.get("oslc_cm:status", "")
        owner = full.get("dcterms:contributor", {})
        if isinstance(owner, dict):
            owner = owner.get("rdf:resource", "").split("/")[-1]

        result_items.append(
            {
                "id": ib_id,
                "type": "Item de Backlog",
                "title": full.get("dcterms:title", ""),
                "status": status,
                "owner": owner,
                "children_count": len(children),
                "children": children,
            }
        )

    result = {"items_count": len(result_items), "items": result_items, "truncated": truncated}
    if iteration_info:
        result["iteration"] = iteration_info["title"]
        result["start_date"] = iteration_info["start_date"]
        result["end_date"] = iteration_info["end_date"]
    return result


@tool
async def create_workitem(
    project: str,
    title: str,
    type: str = "task",
    description: str | None = None,
    parent_id: str | None = None,
    owner: str | None = None,
    filed_against: str | None = None,
    custom_fields: dict | None = None,
    if_exists: str = "error",
) -> dict:
    """Create a work item in an RTC project via OSLC.

    Before creating, a work item of the same type, title and parent is looked up (see if_exists). The server
    compares the title EXACTLY (case and inner spaces count; only leading/trailing spaces of the request are
    dropped); the returned titles are then compared ignoring case and repeated spaces, which only matters when
    the server returned them. Required fields the call leaves out are filled from the local profile
    (defaults of the type, see `elm-mcp profile draft`); any still missing are refused, listed.
    Returns the created work item plus `created: true`; an existing one (if_exists="return") comes as
    {"rdf:about", "dcterms:identifier", "dcterms:title", "created": false}.

    Args:
        project: Project name (e.g., "MY PROJECT (KEY)")
        title: Work item title (required)
        type: Any work item type of the project (default: "task"): the id or title shown by list_types, or a short alias such as task or story. An unknown type is refused listing the available ones.
        description: HTML description (optional)
        parent_id: Parent work item ID (optional)
        owner: Username of the owner, stored in dcterms:contributor (optional, e.g., "jdoe")
        filed_against: Category URI (optional). If not provided, the property is not sent and the server applies its own default. Valid values: list_fields (property filedAgainst, allowedValues).
        custom_fields: Dict of extra RDF fields to inject (e.g., {"rtc_ext:campo": "valor"}); wins over the profile
        if_exists: What to do when the same work item already exists. "error" (default) refuses naming it; "return" returns it
            with created=false and writes nothing; "create" creates anyway. If the duplicate check itself fails the creation
            is refused (except "create"), also when the check was truncated (more than 1000 candidates). A timeout
            after the request was sent never resends it: the check runs again. After a 400/409 the check also runs
            again before the single resend, and a work item that appeared meanwhile is returned instead.
    """
    oslc.check_if_exists(if_exists)
    project_url = await oslc._resolve_project_url("ccm", project)
    try:
        return await _create_workitem_once(
            project, project_url, title, type, description, parent_id, owner, filed_against, custom_fields, if_exists
        )
    except oslc.WriteRejected as exc:
        if exc.status_code not in _STALE_SHAPE_STATUSES:
            raise
        seen = getattr(exc, "before", [])
    # 400/409 may mean a stale shape or value: drop the project area's discovery, rediscover and resend ONCE
    # (SPEC-cache-shape). A second refusal is reported as it came. Extension point for the local profile
    # (SPEC-perfil-template): the discovery fallback plugs into cache.get_or_fetch(fallback=...).
    cache.invalidate(oslc._project_area_id(project_url))
    project_url = await oslc._resolve_project_url("ccm", project)
    # The server may have created the item before answering 400/409: the duplicate check runs again
    # (items already seen before the first POST do not count) and the POST is resent only if nothing new shows up.
    return await _create_workitem_once(
        project,
        project_url,
        title,
        type,
        description,
        parent_id,
        owner,
        filed_against,
        custom_fields,
        if_exists,
        known=seen,
    )


async def _create_workitem_once(
    project: str,
    project_url: str,
    title: str,
    type: str,
    description: str | None,
    parent_id: str | None,
    owner: str | None,
    filed_against: str | None,
    custom_fields: dict | None,
    if_exists: str,
    known: list[dict] | None = None,
) -> dict:
    factory = oslc._resolve_ccm_type(await oslc._list_ccm_creation_factories(project_url), type)
    type_id = factory["id"]
    project_area_id = oslc._project_area_id(project_url)
    payload: dict = {
        "dcterms:title": title,
        "dcterms:type": "http://open-services.net/ns/cm#ChangeRequest",
        "rtc_cm:type": {"rdf:resource": f"{settings.elm_url}/ccm/oslc/types/{project_area_id}/{type_id}"},
    }
    if description:
        payload["dcterms:description"] = description
    if parent_id:
        payload["rtc_cm:com.ibm.team.workitem.linktype.parentworkitem.parent"] = [
            {"rdf:resource": f"{settings.elm_url}/ccm/resource/itemName/com.ibm.team.workitem.WorkItem/{parent_id}"}
        ]
    if owner:
        payload["dcterms:contributor"] = {"rdf:resource": f"{settings.elm_url}/jts/users/{quote(owner, safe='')}"}
    if filed_against:
        payload["rtc_cm:filedAgainst"] = {"rdf:resource": filed_against}
    # Inject custom fields (project-specific customizations); they win over the local profile
    if custom_fields:
        payload.update({oslc.canonical_property_key(k): v for k, v in custom_fields.items()})
    for key, value in profile.type_defaults(profile.load(project_area_id), type_id).items():
        payload.setdefault(key, value)
    # Auto-discover current iteration
    if "rtc_cm:plannedFor" not in payload:
        planned_for = await oslc._find_current_iteration(project_url)
        if planned_for:
            payload["rtc_cm:plannedFor"] = {"rdf:resource": planned_for}
    missing = await oslc.missing_required_fields(factory, payload)
    if missing:
        raise ValueError(
            f"Required fields missing for type '{type_id}': {', '.join(missing)}. Pass them in custom_fields or "
            "define defaults for the type in the local profile (elm-mcp profile draft <project>)."
        )

    async def find() -> list[dict]:
        return await oslc.find_workitem_duplicates(project, title, type_id, parent_id)

    async def create() -> dict:
        return await oslc.create_resource("ccm", project, payload, factory=factory, project_url=project_url)

    return await oslc.create_idempotent(if_exists, find, create, "work item", known=known)


@tool
async def update_workitem(
    id: str,
    title: str | None = None,
    description: str | None = None,
    owner: str | None = None,
    estimate_hours: float | None = None,
    planned_for: str | None = None,
    custom_fields: dict | None = None,
) -> dict:
    """Update fields of an existing work item via a partial OSLC PUT (only the given fields are sent).

    Args:
        id: Work item ID (number)
        title: New title (optional)
        description: New description (optional)
        owner: Username to assign (e.g., "jdoe")
        estimate_hours: Estimate in hours (e.g., 8 = 8h)
        planned_for: Iteration URI (e.g., from get_workitem response)
        custom_fields: Dict of extra properties to send (each key is listed in oslc.properties)
    """
    payload: dict = {}
    if title is not None:
        payload["dcterms:title"] = title
    if description is not None:
        payload["dcterms:description"] = description
    if owner is not None:
        payload["dcterms:contributor"] = {"rdf:resource": f"{settings.elm_url}/jts/users/{quote(owner, safe='')}"}
    if estimate_hours is not None:
        payload["rtc_cm:estimate"] = int(estimate_hours * 3600000)
    if planned_for is not None:
        payload["rtc_cm:plannedFor"] = {"rdf:resource": planned_for}
    if custom_fields:
        payload.update(custom_fields)
    if not payload:
        raise ToolError("At least one field must be provided")
    uri = f"{settings.elm_url}/ccm/resource/itemName/com.ibm.team.workitem.WorkItem/{id}"
    return await oslc.update_resource(uri, payload)


@tool
async def add_child_workitem(
    parent_id: str,
    title: str,
    project: str,
    type: str = "task",
    description: str | None = None,
    owner: str | None = None,
    if_exists: str = "error",
) -> dict:
    """Create a child work item under a parent in an RTC project (same duplicate check as create_workitem).

    if_exists: "error" (default) refuses a work item of the same type, title and parent; "return" returns it
    with created=false; "create" creates anyway.
    """
    return await create_workitem(
        project=project,
        title=title,
        type=type,
        description=description,
        parent_id=parent_id,
        owner=owner,
        if_exists=if_exists,
    )


@tool
async def transition_workitem(id: str, action: str) -> str:
    """Transition a work item's workflow state by executing a workflow action.

    Args:
        id: Work item ID
        action: Id (or id suffix, e.g. "a27") of a workflow action of the item's workflow.
            A unique case-insensitive substring also works (e.g. "start" for an id such as
            "...action.startWorking"). Words like start/resolve/close/reopen only work where
            the workflow's action ids contain them. If it does not resolve to exactly one
            action, the error lists the available action ids.

    Returns "Work item {id}: {previous state} -> {new state} (action: {action id})".
    """
    uri = f"{settings.elm_url}/ccm/resource/itemName/com.ibm.team.workitem.WorkItem/{id}"
    result = await oslc.transition_workitem(uri, action)
    return f"Work item {id}: {result['from']} -> {result['to']} (action: {result['action']})"


@tool
async def add_comment(id: str, text: str) -> dict:
    """Add a comment to a work item and confirm it appears in the discussion.

    Args:
        id: Work item ID (number)
        text: Comment text

    Returns the new comment {"id", "author", "created", "text"}. If the server accepts the POST but
    the comment does not appear, the call fails WITHOUT resending it (check the item before retrying).
    """
    return await oslc.add_comment(id, text)


@read_tool
async def list_comments(id: str) -> list[dict]:
    """List the comments of a work item: [{"id", "author", "created", "text"}] in thread order."""
    return await oslc.list_comments(id)


@tool
async def link_workitems(source: str, target: str, link_type: str) -> dict:
    """Link work item `source` to `target`, keeping the existing links of that type.

    Args:
        source: Work item ID that receives the link
        target: Work item ID linked to
        link_type: parent | blocks | depends_on | related | duplicate_of | resolves
            ("parent" replaces the current parent: a work item has one parent)

    Confirmed by re-reading the source. Returns {"source", "target", "link_type", "property", "changed"}.
    """
    return await oslc.link_workitems(source, target, link_type)


@tool
async def unlink_workitems(source: str, target: str, link_type: str) -> dict:
    """Remove only the `link_type` link from `source` to `target` (other links are kept).

    Args:
        source: Work item ID that holds the link
        target: Work item ID to unlink
        link_type: parent | blocks | depends_on | related | duplicate_of | resolves

    Confirmed by re-reading the source. Returns {"source", "target", "link_type", "property", "changed"}.
    """
    return await oslc.unlink_workitems(source, target, link_type)


@tool
async def link_artifacts(source_uri: str, target_uri: str, link_type: str) -> dict:
    """Link a work item to an artifact of another domain (requirement, test case...), keeping the other links.

    Args:
        source_uri: URI of the work item that receives the link
        target_uri: URI of the destination (RM or QM), on the same ELM server
        link_type: implementsRequirement | tracksRequirement | affectsRequirement | testedByTestCase |
            relatedTestCase | relatedTestScript | relatedTestPlan | relatedTestExecutionRecord |
            affectsPlanItem | affectedByDefect | affectsTestResult | blocksTestExecutionRecord |
            relatedChangeRequest

    Partial PUT of oslc_cm:<link_type> with If-Match, confirmed by re-reading the source. Writing is to be
    confirmed in live acceptance. Returns {"source_uri", "target_uri", "link_type", "property", "changed"}.
    """
    return await oslc.link_artifacts(source_uri, target_uri, link_type)


@tool
async def unlink_artifacts(source_uri: str, target_uri: str, link_type: str) -> dict:
    """Remove only the `link_type` link from the work item `source_uri` to `target_uri` (others are kept).

    Same link types as link_artifacts. Confirmed by re-reading the source.
    Returns {"source_uri", "target_uri", "link_type", "property", "changed"}.
    """
    return await oslc.unlink_artifacts(source_uri, target_uri, link_type)


@tool
async def set_field(id: str, field: str, value: str) -> dict:
    """Set one field of a work item via a partial PUT (If-Match).

    Args:
        id: Work item ID (number)
        field: Prefixed property (e.g. "rtc_cm:storyPoints", "dcterms:title") or the shape's
            property name (e.g. "storyPoints", see list_fields)
        value: The value. For an enumerated field: the literal title (e.g. "5 pts") or literal id;
            a value outside allowedValues is refused listing the valid ones.

    Returns {"id", "field", "value", "warnings"}; warnings say when "Unassigned" was written to a
    required attribute (the server treats it as empty).
    """
    return await oslc.set_field(id, field, value)


@read_tool
async def list_workflow(project: str, type: str, refresh: bool = False) -> dict:
    """List the workflow of a work item type: states (id, title, group) and action ids.

    Args:
        project: Project name
        type: Type id, title or short alias (as in list_types / create_workitem)
        refresh: true ignores the discovery cache (ELM_CACHE_TTL, default 300 s) and reloads it

    Returns {"type", "workflow", "states": [{"id", "title", "group"}], "actions": [...]}.
    The action ids are the values accepted by transition_workitem.
    """
    with cache.refresh_scope(refresh):
        return await oslc.list_workflow(project, type)


@tool
async def create_requirement(
    project: str,
    title: str,
    content: str,
    folder: str | None = None,
    type: str | None = None,
    if_exists: str = "error",
) -> dict:
    """Create a requirement in a DOORS Next (RM) project via OSLC.

    The HTML content is sanitized to XHTML and stored in jazz_rm:primaryText.
    Before creating, a requirement of the same type, title and folder is looked up in the project's configuration
    (see if_exists). The server compares the title EXACTLY (case and inner spaces count; only leading/trailing
    spaces of the request are dropped). Without `folder`, a requirement in any folder counts as a duplicate
    (accepted false positive): pass `folder`, or use if_exists="return"/"create". A truncated check (more than
    1000 candidates) refuses the creation.
    Returns {"uri", "identifier", "created"}, uri and identifier read back from the server.

    Args:
        project: RM project name (e.g., "MY RM PROJECT")
        title: Requirement title (required)
        content: HTML content (sanitized to XHTML for primaryText)
        folder: Folder URI to link the requirement to (nav:parent), optional
        type: REQUIRED. Requirement type: the shape URI or the type title, both as shown by
            list_types("rm", project). Without it, or with an unknown title, the call is refused
            listing the valid types (the server would answer 403 CRRRS6404E).
        if_exists: What to do when the same requirement already exists. "error" (default) refuses naming its URI and
            identifier; "return" returns it with created=false and writes nothing; "create" creates anyway. If the
            duplicate check itself fails the creation is refused (except "create"). A timeout after the request was
            sent never resends it: the check runs again.
    """
    oslc.check_if_exists(if_exists)
    xhtml = oslc.sanitize_html_to_xhtml(content)
    return await oslc.create_requirement(project, title, xhtml, folder=folder, shape=type, if_exists=if_exists)


@tool
async def update_requirement(
    uri: str,
    title: str | None = None,
    content: str | None = None,
) -> dict:
    """Update a requirement in DOORS Next (RM) via OSLC PUT with ETag.

    Args:
        uri: OSLC URI of the requirement to update
        title: New title (optional)
        content: New HTML content, sanitized to XHTML (optional)
    """
    return await oslc.update_requirement(uri, title=title, xhtml=content)


@tool
async def delete_requirement(uri: str) -> dict:
    """Delete a requirement in DOORS Next (RM) via OSLC DELETE and confirm by re-reading it.

    Irreversible. The deletion is confirmed when the re-read answers 404 or 410; if the
    requirement is still readable the call fails.

    Args:
        uri: OSLC URI of the requirement to delete

    Returns {"uri", "deleted": true}.
    """
    return await oslc.delete_requirement(uri)


@read_tool
async def list_types(domain: str, project: str, refresh: bool = False) -> list[dict]:
    """List resource types available in a project area.

    Args:
        domain: rm (DOORS Next) | ccm (RTC) | qm (ETM)
        project: Project name (as shown by list_projects)
        refresh: true ignores the discovery cache (ELM_CACHE_TTL, default 300 s) and reloads it

    Returns [{"title", "shape_uri", "resource_type"}] for rm/qm. For ccm, one item per
    creation factory of a work item type:
    [{"title", "id", "factory_title", "factory_uri", "shape_uri", "resource_type"}]
    ("id" is valid for list_fields and create_workitem, which also take the title or a short alias.)
    """
    domain = domain.lower()
    if domain not in VALID_DOMAINS:
        raise ToolError(f"Invalid domain '{domain}'. Must be one of: {VALID_DOMAINS}")
    with cache.refresh_scope(refresh):
        return await oslc.list_resource_shapes(domain, project)


@read_tool
async def list_fields(domain: str, project: str, type: str, refresh: bool = False) -> dict:
    """List required/optional fields of a type, with allowedValues when enumerated.

    Args:
        domain: rm | ccm | qm
        project: Project name
        type: Shape title (as shown by list_types); matched case-insensitively.
            For ccm: the type id or title from list_types, or a short alias (task, story...).
        refresh: true ignores the discovery cache (ELM_CACHE_TTL, default 300 s) and reloads it

    Returns {"type", "shape_uri", "required":[...], "optional":[...]}; each field has
    name, title, occurs, read_only, property_uri, range_uri and allowed_values.
    """
    domain = domain.lower()
    if domain not in VALID_DOMAINS:
        raise ToolError(f"Invalid domain '{domain}'. Must be one of: {VALID_DOMAINS}")
    with cache.refresh_scope(refresh):
        return await oslc.list_fields(domain, project, type)


@read_tool
async def inspect_workitem(id: str) -> dict:
    """Read a real work item and derive its shape/factory and filled fields.

    Args:
        id: Work item ID (number)

    Returns {"id", "type", "shape_uri", "factory_uri", "fields":{...}}.
    """
    return await oslc.inspect_workitem(id)


@read_tool
async def list_folders(project: str) -> list[dict]:
    """List RM (DOORS Next) folders for a project. Returns [{"title", "uri"}]."""
    return await oslc.list_folders(project)


def main():
    """Entry point for the MCP server."""
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
