"""MCP server for IBM ELM (Engineering Lifecycle Management)."""

import functools
import logging
from collections.abc import Awaitable, Callable
from typing import TypeVar
from urllib.parse import quote

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError

from . import oslc
from .config import settings

logger = logging.getLogger(__name__)

mcp = MCPServer("elm-mcp", instructions="IBM ELM (DOORS Next, RTC, ETM) via OSLC")

VALID_DOMAINS = ("rm", "ccm", "qm")

_T = TypeVar("_T")


def tool(func: Callable[..., Awaitable[_T]]) -> Callable[..., Awaitable[_T]]:
    """Register an async MCP tool, translating domain errors into ToolError.

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

    return mcp.tool()(wrapper)


@tool
async def list_projects(domain: str) -> list[dict]:
    """List projects for a domain (rm=DOORS Next, ccm=RTC, qm=ETM)."""
    domain = domain.lower()
    if domain not in VALID_DOMAINS:
        raise ToolError(f"Invalid domain '{domain}'. Must be one of: {VALID_DOMAINS}")
    return await oslc.list_service_providers(domain)


@tool
async def search_requirements(project: str, query: str | None = None) -> list[dict]:
    """Search requirements in a DOORS Next project. Query uses oslc.where syntax."""
    return await oslc.query_resources("rm", project, query)


@tool
async def get_requirement(uri: str) -> dict:
    """Get a single requirement by its OSLC URI."""
    return await oslc.get_resource(uri)


@tool
async def list_workitems(project: str, query: str | None = None) -> list[dict]:
    """Search work items in an RTC project. Query uses oslc.where syntax."""
    select = "dcterms:title,dcterms:identifier,dcterms:type,oslc_cm:status,dcterms:contributor"
    results = await oslc.query_resources("ccm", project, query, select=select)
    parsed = []
    for item in results:
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
    return parsed


@tool
async def get_workitem(id: str) -> dict:
    """Get a single work item by ID (constructs URI from CCM base)."""
    uri = f"{settings.elm_url}/ccm/resource/itemName/com.ibm.team.workitem.WorkItem/{id}"
    return await oslc.get_resource(uri)


@tool
async def search_testcases(project: str, query: str | None = None) -> list[dict]:
    """Search test cases in an ETM project. Query uses oslc.where syntax."""
    return await oslc.query_resources("qm", project, query)


TYPE_IDS = oslc.CCM_TYPE_IDS


@tool
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
        detail = await client.get(iter_uri, headers={"Accept": "application/json", "OSLC-Core-Version": "2.0"})
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


@tool
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
            "ccm", project, None, select="dcterms:title,dcterms:identifier,dcterms:type"
        )
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

    result = {"items_count": len(result_items), "items": result_items}
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
) -> dict:
    """Create a work item in an RTC project via OSLC.

    Args:
        project: Project name (e.g., "MY PROJECT (KEY)")
        title: Work item title (required)
        type: "task" or "story" (default: "task"). Other types from list_types are not accepted yet (SPEC-wi-custom-types).
        description: HTML description (optional)
        parent_id: Parent work item ID (optional)
        owner: Username of the owner, stored in dcterms:contributor (optional, e.g., "jdoe")
        filed_against: Category URI (optional). If not provided, the property is not sent and the server applies its own default. Valid values: list_fields (property filedAgainst, allowedValues).
        custom_fields: Dict of extra RDF fields to inject (e.g., {"rtc_ext:campo": "valor"})
    """
    if type not in TYPE_IDS:
        raise ToolError(f"Invalid type '{type}'. Must be one of: {list(TYPE_IDS.keys())}")
    project_url = await oslc._resolve_project_url("ccm", project)
    project_area_id = oslc._project_area_id(project_url)
    payload: dict = {
        "dcterms:title": title,
        "dcterms:type": "http://open-services.net/ns/cm#ChangeRequest",
        "rtc_cm:type": {"rdf:resource": f"{settings.elm_url}/ccm/oslc/types/{project_area_id}/{TYPE_IDS[type]}"},
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
    # Auto-discover current iteration
    planned_for = await oslc._find_current_iteration(project_url)
    if planned_for:
        payload["rtc_cm:plannedFor"] = {"rdf:resource": planned_for}
    # Inject custom fields (project-specific customizations)
    if custom_fields:
        payload.update(custom_fields)
    return await oslc.create_resource("ccm", project, payload, wi_type=type)


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
) -> dict:
    """Create a child work item under a parent in an RTC project."""
    return await create_workitem(
        project=project,
        title=title,
        type=type,
        description=description,
        parent_id=parent_id,
        owner=owner,
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
async def create_requirement(
    project: str,
    title: str,
    content: str,
    folder: str | None = None,
    type: str | None = None,
) -> dict:
    """Create a requirement in a DOORS Next (RM) project via OSLC.

    The HTML content is sanitized to XHTML and stored in jazz_rm:primaryText.
    Returns {"uri", "identifier"}, both read back from the server.

    Args:
        project: RM project name (e.g., "MY RM PROJECT")
        title: Requirement title (required)
        content: HTML content (sanitized to XHTML for primaryText)
        folder: Folder URI to link the requirement to (nav:parent), optional
        type: REQUIRED. Requirement type: the shape URI or the type title, both as shown by
            list_types("rm", project). Without it, or with an unknown title, the call is refused
            listing the valid types (the server would answer 403 CRRRS6404E).
    """
    xhtml = oslc.sanitize_html_to_xhtml(content)
    return await oslc.create_requirement(project, title, xhtml, folder=folder, shape=type)


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


@tool
async def list_types(domain: str, project: str) -> list[dict]:
    """List resource types available in a project area.

    Args:
        domain: rm (DOORS Next) | ccm (RTC) | qm (ETM)
        project: Project name (as shown by list_projects)

    Returns [{"title", "shape_uri", "resource_type"}] for rm/qm. For ccm, one item per
    creation factory of a work item type:
    [{"title", "id", "factory_title", "factory_uri", "shape_uri", "resource_type"}]
    ("id" is valid for list_fields; create_workitem accepts only "task" or "story" for now.
    Arbitrary types are planned in SPEC-wi-custom-types.)
    """
    domain = domain.lower()
    if domain not in VALID_DOMAINS:
        raise ToolError(f"Invalid domain '{domain}'. Must be one of: {VALID_DOMAINS}")
    return await oslc.list_resource_shapes(domain, project)


@tool
async def list_fields(domain: str, project: str, type: str) -> dict:
    """List required/optional fields of a type, with allowedValues when enumerated.

    Args:
        domain: rm | ccm | qm
        project: Project name
        type: Shape title (as shown by list_types); matched case-insensitively.
            For ccm: the type id or title from list_types, or the alias task/story.

    Returns {"type", "shape_uri", "required":[...], "optional":[...]}; each field has
    name, title, occurs, read_only, property_uri, range_uri and allowed_values.
    """
    domain = domain.lower()
    if domain not in VALID_DOMAINS:
        raise ToolError(f"Invalid domain '{domain}'. Must be one of: {VALID_DOMAINS}")
    return await oslc.list_fields(domain, project, type)


@tool
async def inspect_workitem(id: str) -> dict:
    """Read a real work item and derive its shape/factory and filled fields.

    Args:
        id: Work item ID (number)

    Returns {"id", "type", "shape_uri", "factory_uri", "fields":{...}}.
    """
    return await oslc.inspect_workitem(id)


@tool
async def list_folders(project: str) -> list[dict]:
    """List RM (DOORS Next) folders for a project. Returns [{"title", "uri"}]."""
    return await oslc.list_folders(project)


def main():
    """Entry point for the MCP server."""
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
