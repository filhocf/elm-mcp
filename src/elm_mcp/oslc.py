"""OSLC service discovery and query builder for IBM ELM."""

import logging
import re as _re
from xml.etree import ElementTree as ET
from xml.sax.saxutils import escape as _xml_escape

import httpx

from .auth import get_client
from .config import settings

logger = logging.getLogger(__name__)

# OSLC XML namespaces (ELM 7.x uses OSLC 1.0 namespaces in rootservices)
NS = {
    "rdf": "http://www.w3.org/1999/02/22-rdf-syntax-ns#",
    "oslc": "http://open-services.net/ns/core#",
    "oslc_rm": "http://open-services.net/ns/rm#",
    "oslc_cm": "http://open-services.net/ns/cm#",
    "oslc_qm": "http://open-services.net/ns/qm#",
    "oslc_rm_v1": "http://open-services.net/xmlns/rm/1.0/",
    "oslc_cm_v1": "http://open-services.net/xmlns/cm/1.0/",
    "oslc_qm_v1": "http://open-services.net/xmlns/qm/1.0/",
    "dcterms": "http://purl.org/dc/terms/",
    "jfs": "http://jazz.net/xmlns/prod/jazz/jfs/1.0/",
}

DOMAIN_PATHS = {
    "rm": "/rm/rootservices",
    "ccm": "/ccm/rootservices",
    "qm": "/qm/rootservices",
}

# Catalog discovery XPath per domain — try v1 namespace first (ELM 7.x), fallback to v2
CATALOG_XPATH = {
    "rm": [".//oslc_rm_v1:rmServiceProviders", ".//oslc_rm:rmServiceProviders"],
    "ccm": [".//oslc_cm_v1:cmServiceProviders", ".//oslc_cm:cmServiceProviders"],
    "qm": [".//oslc_qm_v1:qmServiceProviders", ".//oslc_qm:qmServiceProviders"],
}

# Query capability resource types per domain
QUERY_RESOURCE_TYPE = {
    "rm": "http://open-services.net/ns/rm#Requirement",
    "ccm": "http://open-services.net/ns/cm#ChangeRequest",
    "qm": "http://open-services.net/ns/qm#TestCase",
}


async def _get_xml(client: httpx.AsyncClient, url: str) -> ET.Element:
    """Fetch URL and parse as XML."""
    resp = await client.get(url, headers={"Accept": "application/rdf+xml, application/xml"})
    resp.raise_for_status()
    return ET.fromstring(resp.text)


async def _get_json(client: httpx.AsyncClient, url: str) -> dict:
    """Fetch URL as JSON (OSLC JSON response)."""
    resp = await client.get(url, headers={"Accept": "application/json", "OSLC-Core-Version": "2.0"})
    resp.raise_for_status()
    return resp.json()


async def get_catalog_url(domain: str) -> str:
    """Discover the service provider catalog URL from rootservices."""
    client = await get_client()
    root_url = f"{settings.elm_url}{DOMAIN_PATHS[domain]}"
    root = await _get_xml(client, root_url)
    for xpath in CATALOG_XPATH[domain]:
        elem = root.find(xpath, NS)
        if elem is not None:
            return elem.get(f"{{{NS['rdf']}}}resource")
    raise ValueError(f"Cannot find service provider catalog for domain '{domain}'")


async def list_service_providers(domain: str) -> list[dict]:
    """List all service providers (projects) for a domain."""
    import re

    client = await get_client()
    catalog_url = await get_catalog_url(domain)
    resp = await client.get(catalog_url, headers={"Accept": "application/rdf+xml, application/xml"})
    resp.raise_for_status()
    xml_text = resp.text
    providers = []

    # RM (Discovery 1.0): use regex directly — ET has issues in MCP stdio context
    if domain == "rm":
        entries = re.findall(
            r"<\w+:ServiceProvider>\s*"
            r"<(?:\w+:)?title>([^<]+)</(?:\w+:)?title>\s*.*?"
            r"<\w+:services\s+[^>]*rdf:resource=\"([^\"]+)\"",
            xml_text,
            re.DOTALL,
        )
        for title, svc_url in entries:
            providers.append({"title": title, "url": svc_url})
        return providers

    # CCM/QM: parse with ElementTree (OSLC Core namespace)
    catalog = ET.fromstring(xml_text)
    for sp in catalog.findall(".//{http://open-services.net/ns/core#}ServiceProvider"):
        title_el = sp.find("{http://purl.org/dc/terms/}title")
        about = sp.get(f"{{{NS['rdf']}}}about")
        if title_el is not None:
            providers.append({"title": title_el.text, "url": about})

    return providers


async def _find_query_base(domain: str, project_url: str) -> str:
    """Find the OSLC query base URL for a project's service provider."""
    client = await get_client()
    resource_type = QUERY_RESOURCE_TYPE[domain]

    # RM (DOORS Next): uses /views endpoint with projectURL parameter
    if domain == "rm" and project_url and "services.xml" in project_url:
        # Extract project ID from services.xml URL
        # Pattern: /rm/oslc_rm/{project_id}/services.xml
        parts = project_url.split("/")
        idx = parts.index("oslc_rm") if "oslc_rm" in parts else -1
        if idx >= 0 and idx + 1 < len(parts):
            project_id = parts[idx + 1]
            return (
                f"{settings.elm_url}/rm/views?oslc.query=true&projectURL={settings.elm_url}/rm/rm-projects/{project_id}"
            )

    # CCM/QM: derive services endpoint from catalog URL
    urls_to_try = []
    if project_url and not project_url.endswith("services.xml"):
        urls_to_try = [project_url.rstrip("/") + "/services", project_url]
    else:
        urls_to_try = [project_url.replace("/services.xml", "/services"), project_url]

    for url in urls_to_try:
        try:
            sp = await _get_xml(client, url)
        except (httpx.HTTPError, ET.ParseError) as exc:
            logger.debug("OSLC discovery: skipping %s (%s)", url, exc)
            continue
        for qc in sp.iter(f"{{{NS['oslc']}}}QueryCapability"):
            rt_el = qc.find(f"{{{NS['oslc']}}}resourceType")
            if rt_el is not None and rt_el.get(f"{{{NS['rdf']}}}resource") == resource_type:
                qb = qc.find(f"{{{NS['oslc']}}}queryBase")
                if qb is not None:
                    return qb.get(f"{{{NS['rdf']}}}resource")
        # Fallback: any queryBase
        for qc in sp.iter(f"{{{NS['oslc']}}}QueryCapability"):
            qb = qc.find(f"{{{NS['oslc']}}}queryBase")
            if qb is not None:
                return qb.get(f"{{{NS['rdf']}}}resource")

    raise ValueError(f"No query capability found for {domain} in project '{project_url}'")


async def _resolve_project_url(domain: str, project_name: str) -> str:
    """Resolve project name to service provider URL."""
    providers = await list_service_providers(domain)
    for p in providers:
        if p["title"] and p["title"].lower() == project_name.lower():
            return p["url"]
    available = [p["title"] for p in providers]
    raise ValueError(f"Project '{project_name}' not found. Available: {available}")


async def query_resources(domain: str, project: str, where: str | None = None, select: str | None = None) -> list[dict]:
    """Execute an OSLC query on a project, returning JSON results."""
    project_url = await _resolve_project_url(domain, project)
    query_base = await _find_query_base(domain, project_url)
    client = await get_client()
    params = {}
    if where:
        params["oslc.where"] = where
    if select:
        params["oslc.select"] = select
    params["oslc.pageSize"] = "50"

    # If query_base already has query params (e.g., RM /views?oslc.query=true&projectURL=...),
    # merge our params into the existing URL
    if "?" in query_base:
        from urllib.parse import parse_qs, urlparse, urlunparse

        parsed = urlparse(query_base)
        existing_params = parse_qs(parsed.query, keep_blank_values=True)
        # Flatten single-value lists
        merged = {k: v[0] if len(v) == 1 else v for k, v in existing_params.items()}
        merged.update(params)
        url = urlunparse(parsed._replace(query=""))
        params = merged
    else:
        url = query_base

    resp = await client.get(
        url,
        params=params,
        headers={"Accept": "application/json", "OSLC-Core-Version": "2.0"},
    )
    # Some domains (QM) don't support JSON — fallback to XML
    if resp.status_code == 406:
        resp = await client.get(
            url,
            params=params,
            headers={"Accept": "application/xml", "OSLC-Core-Version": "2.0"},
        )
        resp.raise_for_status()
        # Parse XML response into list of resource URIs
        from xml.etree import ElementTree as ET

        root = ET.fromstring(resp.text)
        results = []
        for member in root.iter(f"{{{NS['rdf']}}}Description"):
            about = member.get(f"{{{NS['rdf']}}}about")
            title_el = member.find("{http://purl.org/dc/terms/}title")
            entry = {"rdf:about": about}
            if title_el is not None:
                entry["dcterms:title"] = title_el.text
            results.append(entry)
        return results if results else [{"raw_xml": resp.text[:2000]}]

    resp.raise_for_status()
    data = resp.json()
    # OSLC JSON responses have members in rdfs:member or oslc:results
    return data.get("oslc:results", data.get("rdfs:member", data.get("results", [data])))


async def get_resource(uri: str) -> dict:
    """Fetch a single OSLC resource by URI."""
    client = await get_client()
    return await _get_json(client, uri)


async def _find_default_category(project_url: str) -> str | None:
    """Discover the default category (filedAgainst) for a CCM project area."""
    client = await get_client()
    # RTC exposes categories at /ccm/oslc/categories?projectArea={id}
    project_area_id = project_url.rstrip("/").split("/")[-1]
    categories_url = f"{settings.elm_url}/ccm/oslc/categories?projectArea={project_area_id}"
    try:
        resp = await client.get(
            categories_url,
            headers={"Accept": "application/json", "OSLC-Core-Version": "2.0"},
        )
        resp.raise_for_status()
        data = resp.json()
        # Categories are in the response; pick the first (default) one
        members = data.get("oslc:results", data.get("rdfs:member", []))
        if members and isinstance(members, list):
            first = members[0]
            # Return the rdf:about URI of the category
            return first.get("rdf:about", first.get("rdf:resource"))
    except Exception as exc:  # noqa: BLE001 — discovery is best-effort, degrade to None
        logger.debug("Iteration discovery failed: %s", exc)
    return None


async def _find_current_iteration(project_url: str) -> str | None:
    """Discover the current iteration (plannedFor) for a CCM project area.

    Finds the iteration whose date range contains today. Falls back to last iteration with dates.
    """
    from datetime import datetime, timezone

    client = await get_client()
    parts = project_url.split("/contexts/")
    project_area_id = parts[1].split("/")[0] if len(parts) > 1 else project_url.rstrip("/").split("/")[-1]
    iterations_url = f"{settings.elm_url}/ccm/oslc/iterations?projectArea={project_area_id}"
    try:
        resp = await client.get(
            iterations_url,
            headers={"Accept": "application/json", "OSLC-Core-Version": "2.0"},
        )
        resp.raise_for_status()
        members = resp.json().get("oslc:results", [])
        now = datetime.now(timezone.utc)
        candidates = []  # (uri, start_dt, has_end)
        for m in members:
            iter_uri = m.get("rdf:resource")
            if not iter_uri:
                continue
            detail = await client.get(iter_uri, headers={"Accept": "application/json", "OSLC-Core-Version": "2.0"})
            if detail.status_code != 200:
                continue
            d = detail.json()
            start = d.get("rtc_cm:startDate")
            end = d.get("rtc_cm:endDate")
            if not start:
                continue
            start_dt = datetime.fromisoformat(start.replace("Z", "+00:00"))
            end_dt = datetime.fromisoformat(end.replace("Z", "+00:00")) if end else None
            is_current = start_dt <= now and (end_dt is None or now <= end_dt)
            if is_current:
                candidates.append((iter_uri, start_dt, end_dt is not None))
        if candidates:
            # Prefer: bounded (has end_date) > unbounded, then most recent start
            candidates.sort(key=lambda x: (x[2], x[1]), reverse=True)
            return candidates[0][0]
        return None
    except Exception as exc:  # noqa: BLE001 — discovery is best-effort, degrade to None
        logger.debug("Iteration discovery failed: %s", exc)
    return None


def _payload_to_rdfxml(payload: dict) -> str:
    """Convert a payload dict to RDF/XML format for OSLC resource creation."""
    # Namespace prefixes used in payloads
    ns_map = {
        "dcterms": "http://purl.org/dc/terms/",
        "rdf": "http://www.w3.org/1999/02/22-rdf-syntax-ns#",
        "oslc_cm": "http://open-services.net/ns/cm#",
        "rtc_cm": "http://jazz.net/xmlns/prod/jazz/rtc/cm/1.0/",
        "rtc_ext": "http://jazz.net/xmlns/prod/jazz/rtc/ext/1.0/",
    }
    # Build namespace declarations
    ns_decls = " ".join(f'xmlns:{p}="{u}"' for p, u in ns_map.items())
    elements = []
    for key, value in payload.items():
        if isinstance(value, dict) and "rdf:resource" in value:
            elements.append(f'  <{key} rdf:resource="{value["rdf:resource"]}"/>')
        elif isinstance(value, list):
            for item in value:
                if isinstance(item, dict) and "rdf:resource" in item:
                    elements.append(f'  <{key} rdf:resource="{item["rdf:resource"]}"/>')
        else:
            from xml.sax.saxutils import escape

            elements.append(f"  <{key}>{escape(str(value))}</{key}>")
    body = "\n".join(elements)
    return f"<rdf:RDF {ns_decls}>\n<oslc_cm:ChangeRequest>\n{body}\n</oslc_cm:ChangeRequest>\n</rdf:RDF>"


def _project_area_id(project_url: str) -> str:
    """Extract the project area (context) id from a service provider URL.

    The URL may be .../contexts/{id}, .../contexts/{id}/workitems/services.xml, or end in the id.
    """
    parts = project_url.split("/contexts/")
    return parts[1].split("/")[0] if len(parts) > 1 else project_url.rstrip("/").split("/")[-1]


def _write_message(resp: httpx.Response) -> str | None:
    """Extract the server's oslc:message (JSON, or XML as a fallback) from a refused write."""
    try:
        data = resp.json()
    except ValueError:
        data = None
    if isinstance(data, dict) and isinstance(data.get("oslc:message"), str) and data["oslc:message"].strip():
        return data["oslc:message"].strip()
    m = _re.search(r"<\w*:?message\b[^>]*>([^<]+)</\w*:?message>", resp.text or "")
    return m.group(1).strip() if m else None


def _raise_write_error(resp: httpx.Response) -> None:
    """Raise a readable ValueError for a refused write that carries an oslc:message.

    The @tool boundary only translates ValueError/RuntimeError, so the server's message
    must leave this module as one of them. Without a message, falls back to the HTTP error.
    """
    message = _write_message(resp)
    if message:
        raise ValueError(f"Server refused the write ({resp.status_code}): {message}")
    resp.raise_for_status()
    raise RuntimeError(f"Unexpected response status {resp.status_code}")


# CCM type aliases accepted by create_workitem/list_fields. Any other type must come from list_types.
CCM_TYPE_IDS = {
    "task": "task",
    "story": "com.ibm.team.apt.workItemType.story",
}


def _resolve_ccm_type(factories: list[dict], type_arg: str) -> dict:
    """Pick the typed factory for a type id, title (case-insensitive) or alias (task/story)."""
    wanted = type_arg.strip()
    match = next((f for f in factories if f["id"] == wanted), None)
    if match is None:
        match = next((f for f in factories if (f["title"] or "").lower() == wanted.lower()), None)
    if match is None:
        alias = CCM_TYPE_IDS.get(wanted.lower())
        match = next((f for f in factories if alias and f["id"] == alias), None)
    if match is None:
        available = [f"{f['id']} ({f['title']})" for f in factories]
        raise ValueError(f"Type '{type_arg}' not found. Available: {available}")
    return match


async def create_resource(domain: str, project: str, payload: dict, wi_type: str | None = None) -> dict:
    """POST a new resource (RDF/XML) to the creation factory.

    With wi_type, the POST goes to the factory of that type (id, title or alias); the
    generic /workitems factory is only used when no type is given.
    """
    if domain != "ccm":
        raise NotImplementedError(f"create_resource only supports 'ccm' domain, got '{domain}'")
    project_url = await _resolve_project_url(domain, project)
    generic_url = f"{settings.elm_url}/ccm/oslc/contexts/{_project_area_id(project_url)}/workitems"
    creation_url = generic_url
    if wi_type:
        factories = await _list_ccm_creation_factories(project_url)
        creation_url = _resolve_ccm_type(factories, wi_type)["factory_uri"]
    client = await get_client()
    rdfxml_body = _payload_to_rdfxml(payload)
    # Refresh session (prevents CSRF stale after idle); any authenticated GET works, keep the generic URL
    await client.get(
        generic_url,
        headers={"Accept": "application/json", "OSLC-Core-Version": "2.0"},
    )
    post_headers = {
        "Content-Type": "application/rdf+xml",
        "Accept": "application/json",
        "OSLC-Core-Version": "2.0",
    }
    resp = await client.post(creation_url, content=rdfxml_body, headers=post_headers)
    # Re-authenticate only on an expired session (401 / login redirect). A 403 carries the
    # server's business rule (oslc:message) and must not be resent.
    if resp.status_code == 401 or "j_security_check" in str(resp.headers.get("location", "")):
        from .auth import _authenticate

        await _authenticate(client)
        resp = await client.post(creation_url, content=rdfxml_body, headers=post_headers)
    if resp.status_code != 201:
        _raise_write_error(resp)
    return resp.json()


async def update_resource(uri: str, payload: dict) -> dict:
    """Partially update a resource: PUT only the changed properties (with If-Match).

    Sends ?oslc.properties=<changed props> and a body with just those properties, then
    returns the re-read resource.
    """
    client = await get_client()
    # GET to obtain the ETag (and the prefix map, reused so custom prefixes resolve)
    get_resp = await client.get(uri, headers={"Accept": "application/json", "OSLC-Core-Version": "2.0"})
    get_resp.raise_for_status()
    etag = get_resp.headers.get("ETag", "")
    if not etag:
        raise ValueError(f"Server did not return an ETag for resource '{uri}'. Cannot safely update.")
    body = dict(payload)
    prefixes = get_resp.json().get("prefixes")
    if isinstance(prefixes, dict):
        body["prefixes"] = prefixes
    resp = await client.put(
        uri,
        params={"oslc.properties": ",".join(payload)},
        json=body,
        headers={
            "Content-Type": "application/json",
            "Accept": "application/json",
            "OSLC-Core-Version": "2.0",
            "If-Match": etag,
        },
    )
    _check_put(resp)
    return await _get_json(client, uri)


def _check_put(resp: httpx.Response) -> None:
    """Map a refused conditional PUT to a readable error (412 = ETag conflict)."""
    if resp.status_code == 412:
        raise ValueError(
            "Conflict: the item was changed by someone else since it was read (stale ETag); re-read it and try again."
        )
    if resp.status_code >= 400:
        _raise_write_error(resp)


_STATE_URI_RE = _re.compile(r"/oslc/workflows/([^/]+)/states/([^/]+)/([^/?#]+)$")


def _last_segment(uri: str) -> str:
    """Last path (or fragment) segment of a URI, e.g. .../core#Exactly-one -> Exactly-one."""
    return _re.split(r"[/#]", uri.rstrip("/"))[-1]


def _resolve_workflow_action(action: str, action_ids: list[str]) -> str:
    """Resolve `action` to one workflow action id: exact, then '.{action}' suffix, then unique substring."""
    if action in action_ids:
        return action
    suffixed = [a for a in action_ids if a.endswith(f".{action}")]
    if len(suffixed) == 1:
        return suffixed[0]
    if not suffixed:
        needle = action.lower()
        contained = [a for a in action_ids if needle in a.lower()]
        if len(contained) == 1:
            return contained[0]
        suffixed = contained
    if suffixed:
        raise ValueError(f"Action '{action}' is ambiguous. Matching actions: {suffixed}")
    raise ValueError(f"Action '{action}' not found for this work item's workflow. Available actions: {action_ids}")


def _action_ids(data: dict) -> list[str]:
    """Extract action ids (last URI segment) from the workflow actions listing."""
    members = data.get("oslc:results", data.get("rdfs:member", []))
    ids: list[str] = []
    for m in members if isinstance(members, list) else []:
        if not isinstance(m, dict):
            continue
        uri = m.get("rdf:about") or m.get("rdf:resource")
        ident = m.get("dcterms:identifier") or (_last_segment(uri) if uri else None)
        if ident:
            ids.append(ident)
    return ids


async def _state_titles(client: httpx.AsyncClient, states_url: str) -> dict[str, str]:
    """Map state id -> dcterms:title from the workflow states listing (best effort)."""
    try:
        resp = await client.get(states_url, headers={"Accept": "application/json", "OSLC-Core-Version": "2.0"})
        if resp.status_code != 200:
            return {}
        members = resp.json().get("oslc:results", [])
    except (httpx.HTTPError, ValueError):
        return {}
    return {
        m["dcterms:identifier"]: m["dcterms:title"]
        for m in members
        if isinstance(m, dict) and m.get("dcterms:identifier") and m.get("dcterms:title")
    }


def _item_state_uri(item: dict) -> str:
    state = item.get("rtc_cm:state")
    uri = state.get("rdf:resource") if isinstance(state, dict) else None
    if not uri:
        raise ValueError("The work item has no rtc_cm:state; cannot resolve its workflow.")
    return uri


async def transition_workitem(item_uri: str, action: str) -> dict:
    """Run a workflow action on a work item via PUT ?_action=<id> and verify the new state.

    Resolves `action` against the actions of the item's workflow, executes it with If-Match,
    re-reads the item and fails if the state did not change. Never writes rtc_cm:state.
    Returns {"from", "to", "action"} (state titles when available, else the last URI segment).
    """
    client = await get_client()
    json_headers = {"Accept": "application/json", "OSLC-Core-Version": "2.0"}
    get_resp = await client.get(item_uri, headers=json_headers)
    get_resp.raise_for_status()
    etag = get_resp.headers.get("ETag", "")
    if not etag:
        raise ValueError(f"Server did not return an ETag for work item '{item_uri}'. Cannot safely transition.")
    item = get_resp.json()
    before_uri = _item_state_uri(item)
    m = _STATE_URI_RE.search(before_uri)
    if m is None:
        raise ValueError(f"Unrecognized workflow state URI '{before_uri}'; cannot resolve its workflow.")
    area, workflow = m.group(1), m.group(2)
    title = item.get("dcterms:title")
    if not title:
        raise ValueError("The work item has no dcterms:title; cannot execute the transition.")

    workflows = f"{settings.elm_url}/ccm/oslc/workflows/{area}"
    actions_resp = await client.get(f"{workflows}/actions/{workflow}", headers=json_headers)
    actions_resp.raise_for_status()
    action_id = _resolve_workflow_action(action, _action_ids(actions_resp.json()))

    resp = await client.put(
        item_uri,
        params={"_action": action_id, "oslc.properties": "dcterms:title"},
        json={"dcterms:title": title},
        headers={**json_headers, "Content-Type": "application/json", "If-Match": etag},
    )
    _check_put(resp)

    after = await _get_json(client, item_uri)
    after_uri = _item_state_uri(after)
    if after_uri == before_uri:
        raise ValueError(
            f"Action '{action_id}' does not apply to the current state of work item "
            f"(state unchanged: {_last_segment(before_uri)})."
        )
    titles = await _state_titles(client, f"{workflows}/states/{workflow}")
    state_from, state_to = _last_segment(before_uri), _last_segment(after_uri)
    return {"from": titles.get(state_from, state_from), "to": titles.get(state_to, state_to), "action": action_id}


# =====================================================================
# RM (DOORS Next) write path — ADDITIVE (G0-rm-write.md §E).
# Fully separate from the CCM create_resource/_payload_to_rdfxml path to
# avoid regression on the single write gargalo of CCM. Uses regex (not ET)
# for RM discovery — ET fails in the MCP stdio context (memória 0c1b2aa4).
# =====================================================================

# RM-specific namespaces (jazz_rm:primaryText, nav:parent). Additive to NS.
NS["jazz_rm"] = "http://jazz.net/ns/rm#"
NS["nav"] = "http://jazz.net/ns/rm/navigation#"

# RDF/XML namespace block for RM requirement bodies (own block — does NOT touch
# the CCM _payload_to_rdfxml ns_map).
_RM_NS_DECLS = (
    'xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#" '
    'xmlns:dcterms="http://purl.org/dc/terms/" '
    'xmlns:oslc="http://open-services.net/ns/core#" '
    'xmlns:oslc_rm="http://open-services.net/ns/rm#" '
    'xmlns:jazz_rm="http://jazz.net/ns/rm#" '
    'xmlns:nav="http://jazz.net/ns/rm/navigation#"'
)

_RM_REQUIREMENT_TYPE = "http://open-services.net/ns/rm#Requirement"


def sanitize_html_to_xhtml(html: str) -> str:
    """Convert HTML to XHTML valid for the RM primaryText.

    Mirrors publicar_elm.sanitizar_html (G0 §E pt.4): strip html/head/body/meta
    wrappers, close void tags (br/hr/img/input), escape lone ampersands without
    double-escaping existing entities, and drop emojis that break the ALM XML.
    """
    if not html:
        return ""
    content = html
    # Remove html/head/body/meta wrappers
    content = _re.sub(r"<html[^>]*>", "", content, flags=_re.IGNORECASE)
    content = _re.sub(r"</html>", "", content, flags=_re.IGNORECASE)
    content = _re.sub(r"<head[^>]*>.*?</head>", "", content, flags=_re.IGNORECASE | _re.DOTALL)
    content = _re.sub(r"<body[^>]*>", "", content, flags=_re.IGNORECASE)
    content = _re.sub(r"</body>", "", content, flags=_re.IGNORECASE)
    content = _re.sub(r"<meta[^>]*/?>", "", content, flags=_re.IGNORECASE)
    # Close void tags for XHTML
    content = _re.sub(r"<hr\s*/?>", "<hr/>", content, flags=_re.IGNORECASE)
    content = _re.sub(r"<br\s*/?>", "<br/>", content, flags=_re.IGNORECASE)
    content = _re.sub(r"<img([^>]*?)/?>", r"<img\1/>", content, flags=_re.IGNORECASE)
    content = _re.sub(r"<input([^>]*?)/?>", r"<input\1/>", content, flags=_re.IGNORECASE)
    # Escape lone ampersands (not already-encoded entities) — no double-escape
    content = _re.sub(r"&(?!amp;|lt;|gt;|quot;|apos;|#\d+;|#x[0-9a-fA-F]+;)", "&amp;", content)
    # Drop emojis (break ALM XML)
    content = _re.sub(r"[\U0001F600-\U0001F9FF]", "", content)
    content = _re.sub(r"[\u2600-\u27BF]", "", content)
    content = _re.sub(r"[\U0001FA00-\U0001FA6F]", "", content)
    content = _re.sub(r"[\U0001F300-\U0001F5FF]", "", content)
    return content.strip()


async def _find_requirement_factory(project_url: str) -> str:
    """Discover the RM CreationFactory URL (oslc:creation) for a service provider.

    Mirrors publicar_elm.descobrir_creation_factory: finds the CreationFactory
    whose oslc:resourceType is rm#Requirement and returns its oslc:creation URI.
    Uses regex (not ElementTree) — ET fails in the MCP stdio context.
    """
    client = await get_client()
    resp = await client.get(
        project_url,
        headers={"Accept": "application/rdf+xml, application/xml", "OSLC-Core-Version": "2.0"},
    )
    resp.raise_for_status()
    xml_text = resp.text

    # Match each CreationFactory block, then pick the one whose resourceType is
    # EXACTLY rm#Requirement — not a substring match, which would also accept
    # rm#RequirementCollection (DOORS Next exposes both). Mistake note ed14f77c.
    for block in _re.findall(r"<\w*:?CreationFactory\b.*?</\w*:?CreationFactory>", xml_text, _re.DOTALL):
        rt = _re.search(r"<\w*:?resourceType\s+[^>]*rdf:resource=\"([^\"]+)\"", block)
        if rt is None or rt.group(1) != _RM_REQUIREMENT_TYPE:
            continue
        m = _re.search(r"<\w*:?creation\s+[^>]*rdf:resource=\"([^\"]+)\"", block)
        if m:
            return m.group(1)
    raise ValueError(f"RM CreationFactory for Requirement not found in '{project_url}'")


def _assert_xhtml_wellformed(xhtml: str) -> None:
    """Reject XHTML content that is not well-formed, to prevent it from breaking
    out of the jazz_rm:primaryText literal (XML injection). The content goes in
    non-escaped (parseType="Literal"), so a stray </jazz_rm:primaryText> or
    unbalanced tag could inject sibling structure. Parse it in isolation first.
    """
    if not xhtml:
        return
    fragment = f'<div xmlns="http://www.w3.org/1999/xhtml">{xhtml}</div>'
    try:
        ET.fromstring(fragment)
    except ET.ParseError as exc:
        raise ValueError(f"requirement content is not well-formed XHTML: {exc}") from exc


def _requirement_to_rdfxml(title: str, xhtml: str, shape: str | None = None, folder: str | None = None) -> str:
    """Build the RM requirement RDF/XML body.

    The XHTML is embedded NON-escaped inside jazz_rm:primaryText with
    rdf:parseType="Literal" (different from the CCM _payload_to_rdfxml, which
    escapes everything). Title is escaped (it is plain text, not markup).
    The XHTML must be well-formed (checked by the caller) so it cannot break out
    of the literal.
    """
    shape_tag = f'    <oslc:instanceShape rdf:resource="{shape}"/>\n' if shape else ""
    folder_tag = f'    <nav:parent rdf:resource="{folder}"/>\n' if folder else ""
    primary_text = ""
    if xhtml:
        primary_text = (
            '    <jazz_rm:primaryText rdf:parseType="Literal">'
            '<div xmlns="http://www.w3.org/1999/xhtml">'
            f"{xhtml}</div></jazz_rm:primaryText>\n"
        )
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        f"<rdf:RDF {_RM_NS_DECLS}>\n"
        '  <rdf:Description rdf:about="">\n'
        f'    <rdf:type rdf:resource="{_RM_REQUIREMENT_TYPE}"/>\n'
        f"    <dcterms:title>{_xml_escape(title)}</dcterms:title>\n"
        f"{shape_tag}"
        f"{primary_text}"
        f"{folder_tag}"
        "  </rdf:Description>\n"
        "</rdf:RDF>"
    )


def _extract_identifier(xml_text: str) -> str | None:
    """Pull dcterms:identifier out of an RM response body (regex)."""
    if not xml_text:
        return None
    m = _re.search(r"<\w*:?identifier[^>]*>([^<]+)</\w*:?identifier>", xml_text)
    return m.group(1) if m else None


async def create_requirement(
    project: str, title: str, xhtml: str, folder: str | None = None, shape: str | None = None
) -> dict:
    """Create an RM requirement via OSLC POST to the requirementFactory.

    Resolves the project, discovers the factory, sanitizes nothing here (the
    caller passes already-sanitized XHTML), builds the RDF/XML, and POSTs it
    (Accept/Content-Type application/rdf+xml). Accepts 200 or 201. Retries once
    on 401/403 after a session refresh (same pattern as create_resource).
    Returns {"uri": ..., "identifier": ...}. Raises ValueError if the project
    does not exist (propagated from _resolve_project_url).
    """
    project_url = await _resolve_project_url("rm", project)
    factory_url = await _find_requirement_factory(project_url)
    _assert_xhtml_wellformed(xhtml)
    rdf_body = _requirement_to_rdfxml(title, xhtml, shape=shape, folder=folder)
    client = await get_client()
    post_headers = {
        "Content-Type": "application/rdf+xml",
        "Accept": "application/rdf+xml",
        "OSLC-Core-Version": "2.0",
    }
    resp = await client.post(factory_url, content=rdf_body.encode("utf-8"), headers=post_headers)
    if resp.status_code in (401, 403) or "j_security_check" in str(resp.headers.get("location", "")):
        from .auth import _authenticate

        await _authenticate(client)
        resp = await client.post(factory_url, content=rdf_body.encode("utf-8"), headers=post_headers)
    if resp.status_code not in (200, 201):
        resp.raise_for_status()
        raise RuntimeError(f"Expected 200/201 Created, got {resp.status_code}")
    location = resp.headers.get("Location", "")
    identifier = _extract_identifier(resp.text)
    return {"uri": location, "identifier": identifier}


async def update_requirement(uri: str, title: str | None = None, xhtml: str | None = None) -> dict:
    """Update an RM requirement via OSLC PUT with If-Match (ETag).

    GETs the resource as rdf+xml for its ETag, edits the XML as a string
    preserving parseType="Literal" (mirrors publicar_elm.atualizar_requisito),
    then PUTs with If-Match. Accepts 200/204. On 412 raises ValueError signalling
    an ETag conflict (the @tool boundary converts it to a legible ToolError).
    """
    client = await get_client()
    get_resp = await client.get(
        uri, headers={"Accept": "application/rdf+xml, application/xml", "OSLC-Core-Version": "2.0"}
    )
    get_resp.raise_for_status()
    etag = get_resp.headers.get("ETag", "")
    if not etag:
        raise ValueError(f"Server did not return an ETag for resource '{uri}'. Cannot safely update.")
    xml_content = get_resp.text
    if xhtml is not None:
        _assert_xhtml_wellformed(sanitize_html_to_xhtml(xhtml))

    # Edit ONLY inside the target resource's <rdf:Description> block, not the
    # first/any match in the whole document (a GET may return several Descriptions
    # — linked resources, folders). Anchor on rdf:about matching the uri; fall
    # back to the first Description if the server omits/abbreviates rdf:about.
    desc_re = _re.compile(
        r'(<\w*:?Description\b[^>]*rdf:about="' + _re.escape(uri) + r'"[^>]*>)(.*?)(</\w*:?Description>)',
        _re.DOTALL,
    )
    m = desc_re.search(xml_content)
    if m is None:
        desc_re = _re.compile(r"(<\w*:?Description\b[^>]*>)(.*?)(</\w*:?Description>)", _re.DOTALL)
        m = desc_re.search(xml_content)
    if m is None:
        raise ValueError(f"could not locate the requirement's rdf:Description in '{uri}'")

    open_tag, body, close_tag = m.group(1), m.group(2), m.group(3)

    if title is not None:
        body = _re.sub(
            r"(<\w*:?title[^>]*>)(.*?)(</\w*:?title>)",
            lambda t: t.group(1) + _xml_escape(title) + t.group(3),
            body,
            count=1,
            flags=_re.DOTALL,
        )

    if xhtml is not None:
        clean = sanitize_html_to_xhtml(xhtml)
        primary_content = f'<div xmlns="http://www.w3.org/1999/xhtml">{clean}</div>'
        if "jazz_rm:primaryText" in body:
            body = _re.sub(
                r"(<jazz_rm:primaryText[^>]*>)(.*?)(</jazz_rm:primaryText>)",
                lambda t: t.group(1) + primary_content + t.group(3),
                body,
                count=1,
                flags=_re.DOTALL,
            )
        else:
            primary_tag = f'<jazz_rm:primaryText rdf:parseType="Literal">{primary_content}</jazz_rm:primaryText>'
            body = body + f"    {primary_tag}\n  "

    xml_content = xml_content[: m.start()] + open_tag + body + close_tag + xml_content[m.end() :]

    put_resp = await client.put(
        uri,
        content=xml_content.encode("utf-8"),
        headers={
            "Content-Type": "application/rdf+xml",
            "Accept": "application/rdf+xml",
            "OSLC-Core-Version": "2.0",
            "If-Match": etag,
        },
    )
    if put_resp.status_code == 412:
        raise ValueError("conflito de ETag: o requisito foi alterado por outro cliente; recarregue e tente novamente")
    if put_resp.status_code not in (200, 204):
        put_resp.raise_for_status()
        raise RuntimeError(f"Expected 200/204, got {put_resp.status_code}")
    return {"uri": uri, "identifier": _extract_identifier(put_resp.text)}


# =====================================================================
# Discovery tools — shapes / fields / folders / work item inspection.
# ADDITIVE, read-only (G0-discovery-tools.md). Regex parsing (not ET —
# ET fails in the MCP stdio context, memória 0c1b2aa4); JSON is parsed
# natively where the entry point returns JSON (folders, inspect_workitem).
# =====================================================================

# Config-management + RM public namespaces. Additive to NS (like jazz_rm/nav).
NS["oslc_config"] = "http://open-services.net/ns/config#"
NS["public_rm"] = "http://jazz.net/xmlns/rm/public/1.0/"

# System folders filtered out of list_folders — tenant-neutral (no org string).
_SYSTEM_FOLDER_TITLES = {"root", "Folder Query Results"}


def _shape_title(xml_text: str) -> str | None:
    """Read the type title from a resourceShape body.

    Strips oslc:property blocks first so a property's dc:title cannot shadow the
    type's dcterms:title — RDF/XML serialization order is not guaranteed by the
    server, so the type title may appear after a property (G5 P2).
    """
    stripped = _re.sub(r"<\w*:?property\b.*?</\w*:?property>", "", xml_text, flags=_re.DOTALL)
    m = _re.search(r"<\w*:?title[^>]*>([^<]+)</\w*:?title>", stripped)
    return m.group(1).strip() if m else None


async def _get_xml_text(url: str) -> str:
    """GET a URL as rdf+xml/xml text (shared by the discovery helpers)."""
    client = await get_client()
    resp = await client.get(
        url,
        headers={"Accept": "application/rdf+xml, application/xml", "OSLC-Core-Version": "2.0"},
    )
    resp.raise_for_status()
    return resp.text


async def _fetch_service_xml(project_url: str) -> str:
    """GET the service provider XML. A services.xml URL is the document itself; otherwise try /services first."""
    if project_url.endswith("services.xml"):
        candidates = [project_url, project_url.replace("/services.xml", "/services")]
    else:
        candidates = [project_url.rstrip("/") + "/services", project_url]
    for url in candidates:
        try:
            return await _get_xml_text(url)
        except httpx.HTTPError as exc:
            logger.debug("OSLC discovery: skipping %s (%s)", url, exc)
    raise ValueError(f"Could not read the service provider document for '{project_url}'")


_FACTORY_TITLE_RE = _re.compile(r"^Location for creation of (.+?) change requests$")


async def _list_ccm_creation_factories(project_url: str) -> list[dict]:
    """List the typed CCM creation factories of a project area (one per work item type).

    Only factories whose oslc:resourceType is EXACTLY cm#ChangeRequest and whose
    oslc:creation has a type segment after /workitems/ are listed; the generic
    /workitems factories (incl. drafts) are skipped.
    Returns [{"title","id","factory_title","factory_uri","shape_uri","resource_type"}].
    """
    xml_text = await _fetch_service_xml(project_url)
    resource_type = QUERY_RESOURCE_TYPE["ccm"]
    factories: list[dict] = []
    for block in _re.findall(r"<\w*:?CreationFactory\b.*?</\w*:?CreationFactory>", xml_text, _re.DOTALL):
        rt = _re.search(r"<\w*:?resourceType\s+[^>]*rdf:resource=\"([^\"]+)\"", block)
        creation = _re.search(r"<\w*:?creation\s+[^>]*rdf:resource=\"([^\"]+)\"", block)
        if rt is None or rt.group(1) != resource_type or creation is None:
            continue
        type_m = _re.search(r"/workitems/([^/?#]+)/?$", creation.group(1))
        if type_m is None:
            continue
        shape = _re.search(r"<\w*:?resourceShape\s+[^>]*rdf:resource=\"([^\"]+)\"", block)
        title_m = _re.search(r"<\w*:?title[^>]*>([^<]*)</\w*:?title>", block)
        raw_title = title_m.group(1) if title_m else ""
        named = _FACTORY_TITLE_RE.match(raw_title.strip())
        factories.append(
            {
                "title": named.group(1) if named else raw_title.strip(),
                "id": type_m.group(1),
                "factory_title": raw_title,
                "factory_uri": creation.group(1),
                "shape_uri": shape.group(1) if shape else None,
                "resource_type": resource_type,
            }
        )
    return factories


async def list_resource_shapes(domain: str, project: str) -> list[dict]:
    """List the resource types (shapes) of a project area.

    For each CreationFactory whose oslc:resourceType EXACTLY matches the domain
    type (rm#Requirement / cm#ChangeRequest / qm#TestCase — never a substring,
    mistake note ed14f77c), extract its oslc:resourceShape URIs and GET each one
    to read its title. Returns [{"title", "shape_uri", "resource_type"}].

    CCM is different: it returns one item per typed creation factory (no shape GET), see
    _list_ccm_creation_factories.
    """
    project_url = await _resolve_project_url(domain, project)
    if domain == "ccm":
        return await _list_ccm_creation_factories(project_url)
    xml_text = await _get_xml_text(project_url)
    resource_type = QUERY_RESOURCE_TYPE[domain]

    shapes: list[dict] = []
    for block in _re.findall(r"<\w*:?CreationFactory\b.*?</\w*:?CreationFactory>", xml_text, _re.DOTALL):
        rt = _re.search(r"<\w*:?resourceType\s+[^>]*rdf:resource=\"([^\"]+)\"", block)
        if rt is None or rt.group(1) != resource_type:
            continue
        for sh in _re.findall(r"<\w*:?resourceShape\s+[^>]*rdf:resource=\"([^\"]+)\"", block):
            shape_xml = await _get_xml_text(sh)
            shapes.append(
                {
                    "title": _shape_title(shape_xml),
                    "shape_uri": sh,
                    "resource_type": resource_type,
                }
            )
    return shapes


def _occurs_required(occurs: str | None) -> bool:
    """A property is required when occurs is Exactly-one or One-or-many."""
    if not occurs:
        return False
    return ("Exactly-one" in occurs) or ("One-or-many" in occurs)


async def list_shape_properties(shape_uri: str) -> list[dict]:
    """GET a resourceShape and extract its properties.

    For each oslc:Property block: name, title, required (from oslc:occurs), occurs
    (last segment, e.g. Exactly-one), read_only, property_uri (propertyDefinition),
    range_uri, allowed_values_uri.
    Returns [{"name","title","required","occurs","read_only","property_uri","range_uri","allowed_values_uri"}].
    """
    xml_text = await _get_xml_text(shape_uri)
    props: list[dict] = []
    for block in _re.findall(r"<\w*:?Property\b.*?</\w*:?Property>", xml_text, _re.DOTALL):
        name_m = _re.search(r"<\w*:?name[^>]*>([^<]+)</\w*:?name>", block)
        title_m = _re.search(r"<\w*:?title[^>]*>([^<]+)</\w*:?title>", block)
        occurs_m = _re.search(r"<\w*:?occurs\s+[^>]*rdf:resource=\"([^\"]+)\"", block)
        propdef_m = _re.search(r"<\w*:?propertyDefinition\s+[^>]*rdf:resource=\"([^\"]+)\"", block)
        range_m = _re.search(r"<\w*:?range\s+[^>]*rdf:resource=\"([^\"]+)\"", block)
        allowed_m = _re.search(r"<\w*:?allowedValues\s+[^>]*rdf:resource=\"([^\"]+)\"", block)
        read_only_m = _re.search(r"<\w*:?readOnly\b[^>]*>([^<]+)</\w*:?readOnly>", block)
        props.append(
            {
                "name": name_m.group(1).strip() if name_m else None,
                "title": title_m.group(1).strip() if title_m else None,
                "required": _occurs_required(occurs_m.group(1) if occurs_m else None),
                "occurs": _last_segment(occurs_m.group(1)) if occurs_m else None,
                "read_only": bool(read_only_m and read_only_m.group(1).strip().lower() == "true"),
                "property_uri": propdef_m.group(1) if propdef_m else None,
                "range_uri": range_m.group(1) if range_m else None,
                "allowed_values_uri": allowed_m.group(1) if allowed_m else None,
            }
        )
    return props


async def list_allowed_values(allowed_values_uri: str) -> list[dict]:
    """GET an oslc:allowedValues resource and extract its values.

    Two formats: oslc:allowedValue rdf:resource (URI only), or
    rdf:Description with rdf:about + dcterms:title (URI + label).
    Returns [{"value_uri", "title"}] (title None when absent).
    """
    xml_text = await _get_xml_text(allowed_values_uri)
    values: list[dict] = []
    # Format 2 first: rdf:Description blocks carry a title.
    for block in _re.findall(r"<\w*:?Description\b[^>]*>.*?</\w*:?Description>", xml_text, _re.DOTALL):
        about_m = _re.search(r"rdf:about=\"([^\"]+)\"", block)
        title_m = _re.search(r"<\w*:?title[^>]*>([^<]+)</\w*:?title>", block)
        if about_m:
            values.append(
                {
                    "value_uri": about_m.group(1),
                    "title": title_m.group(1).strip() if title_m else None,
                }
            )
    if values:
        return values
    # Format 1: oslc:allowedValue rdf:resource (URIs only).
    for uri in _re.findall(r"<\w*:?allowedValue\s+[^>]*rdf:resource=\"([^\"]+)\"", xml_text):
        values.append({"value_uri": uri, "title": None})
    return values


async def list_fields(domain: str, project: str, type: str) -> dict:
    """List required/optional fields of a type, with allowedValues when enumerated.

    Resolves the shape by its title (case-insensitive equality) among the shapes
    of the project; for ccm the type is the id, title or alias (task/story) of a typed
    factory. An unknown type raises ValueError (ToolError at the boundary).
    Returns {"type","shape_uri","required":[...],"optional":[...]}; each field
    carries allowed_values (empty list when the field is not enumerated).
    """
    shapes = await list_resource_shapes(domain, project)
    if domain == "ccm":
        matched = _resolve_ccm_type(shapes, type)
    else:
        matched = next((s for s in shapes if (s["title"] or "").lower() == type.lower()), None)
        if matched is None:
            available = [s["title"] for s in shapes]
            raise ValueError(f"Type '{type}' not found. Available: {available}")
    shape_uri = matched["shape_uri"]
    if not shape_uri:
        raise ValueError(f"Type '{type}' has no resource shape")
    props = await list_shape_properties(shape_uri)

    required: list[dict] = []
    optional: list[dict] = []
    for p in props:
        allowed_values: list[dict] = []
        if p["allowed_values_uri"]:
            allowed_values = await list_allowed_values(p["allowed_values_uri"])
        field = {
            "name": p["name"],
            "title": p["title"],
            "occurs": p["occurs"],
            "read_only": p["read_only"],
            "property_uri": p["property_uri"],
            "range_uri": p["range_uri"],
            "allowed_values": allowed_values,
        }
        (required if p["required"] else optional).append(field)

    return {"type": matched["title"], "shape_uri": shape_uri, "required": required, "optional": optional}


def _derive_factory_uri(shape_uri: str) -> str | None:
    """Derive the CCM work item factory URI from an instanceShape path.

    /oslc/context[s]/<ctx>/shapes/workitems/<X> -> /oslc/context[s]/<ctx>/workitems/<X>
    (drops the /shapes/ segment). Returns None if the path has no shapes/workitems.
    """
    m = _re.search(r"/shapes/workitems/(.+)$", shape_uri)
    if m is None:
        return None
    shape_path = m.group(1)
    rewritten = _re.sub(
        r"(/oslc/(?:contexts|context)/[^/]+/)shapes/workitems/.*$",
        rf"\1workitems/{shape_path}",
        shape_uri,
    )
    return rewritten


async def inspect_workitem(id: str) -> dict:
    """Read a real work item (JSON) and derive its shape/factory and filled fields.

    Builds the WI URI like server.get_workitem, GETs it as JSON, extracts
    oslc:instanceShape, dcterms:type, and the filled rtc_cm:*/rtc_ext:*/process:*
    fields, and derives the factory URI from the shape path. Degrades to
    shape_uri/factory_uri None when there is no instanceShape (does not raise).
    Returns {"id","type","shape_uri","factory_uri","fields":{...}}.
    """
    uri = f"{settings.elm_url}/ccm/resource/itemName/com.ibm.team.workitem.WorkItem/{id}"
    try:
        data = await get_resource(uri)
    except httpx.HTTPStatusError as exc:
        # Surface a legible domain error (e.g. 404) through the @tool boundary,
        # which translates ValueError/RuntimeError into ToolError.
        raise ValueError(f"work item '{id}' not found ({exc.response.status_code})") from exc

    shape = data.get("oslc:instanceShape")
    shape_uri = shape.get("rdf:resource") if isinstance(shape, dict) else None
    factory_uri = _derive_factory_uri(shape_uri) if shape_uri else None

    fields: dict = {}
    for key, value in data.items():
        if not key.startswith(("rtc_cm:", "rtc_ext:", "process:")):
            continue
        if isinstance(value, dict) and "rdf:resource" in value:
            fields[key] = value["rdf:resource"]
        elif isinstance(value, (str, int, float, bool)):
            fields[key] = value

    return {
        "id": data.get("dcterms:identifier", id),
        "type": data.get("dcterms:type"),
        "shape_uri": shape_uri,
        "factory_uri": factory_uri,
        "fields": fields,
    }


async def list_folders(project: str) -> list[dict]:
    """List RM (DOORS Next) folders for a project.

    Resolves the RM service provider, reads it as rdf+xml to extract the
    oslc_config:component URI, then GETs /rm/folders?oslc.where=public_rm:parent=
    <component> as JSON and parses {folder_uri: {dcterms:title:[{value}]}}.
    System folders (root, Folder Query Results) are filtered (tenant-neutral).
    Degrades to [] when the service provider exposes no component.
    Returns [{"title","uri"}].
    """
    project_url = await _resolve_project_url("rm", project)
    sp_xml = await _get_xml_text(project_url)
    comp_m = _re.search(r"<\w*:?component\s+[^>]*rdf:resource=\"([^\"]+)\"", sp_xml)
    if comp_m is None:
        return []
    component_uri = comp_m.group(1)

    client = await get_client()
    folders_url = f"{settings.elm_url}/rm/folders"
    resp = await client.get(
        folders_url,
        params={"oslc.where": f"public_rm:parent={component_uri}"},
        headers={"Accept": "application/json", "OSLC-Core-Version": "2.0"},
    )
    resp.raise_for_status()
    data = resp.json()

    folders: list[dict] = []
    for folder_uri, props in data.items():
        title_entries = props.get("http://purl.org/dc/terms/title", [])
        title = title_entries[0].get("value") if title_entries else None
        if title in _SYSTEM_FOLDER_TITLES:
            continue
        folders.append({"title": title, "uri": folder_uri})
    return folders
