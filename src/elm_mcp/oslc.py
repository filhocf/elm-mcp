"""OSLC service discovery and query builder for IBM ELM."""

import html as _html
import io
import logging
import math
import re as _re
from urllib.parse import unquote, urlparse
from xml.etree import ElementTree as ET
from xml.sax.saxutils import escape as _xml_escape

import httpx

from . import cache, profile
from .auth import _authenticate, get_client
from .config import settings

logger = logging.getLogger(__name__)

_AUTH_FAIL_HEADER = "X-com-ibm-team-repository-web-auth-msg"

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


def _server_message(resp: httpx.Response) -> str | None:
    """Extract the server's oslc:message (JSON, or XML as a fallback) from an error response."""
    try:
        data = resp.json()
    except ValueError:
        data = None
    if isinstance(data, dict) and isinstance(data.get("oslc:message"), str) and data["oslc:message"].strip():
        return data["oslc:message"].strip()
    try:
        text = resp.text or ""
    except ValueError:
        return None
    m = _re.search(r"<\w*:?message\b[^>]*>([^<]+)</\w*:?message>", text)
    return m.group(1).strip() if m else None


def diagnose_http_error(resp: httpx.Response, operation: str, uri: str) -> str:
    """Name the gate that probably blocked an HTTP call and the action for the user.

    The single diagnosis point for every read and write error (SPEC-diagnostico-gate).
    The gate comes from the status alone; a server ``oslc:message`` is repassed verbatim.
    Only the status, the operation, the URI and the server message are used: never the
    password, cookies or the Authorization header.
    """
    status = resp.status_code
    where = f"{operation} ({uri})"
    message = _server_message(resp)
    if status == 401 and (_AUTH_FAIL_HEADER in resp.headers or operation == "login"):
        return (
            f"[credencial] HTTP 401 on {where}: invalid ELM user or password. "
            "Check ELM_USER and ELM_PASSWORD (and that the account is not locked)."
        )
    if status == 401:
        return (
            f"[sessao] HTTP 401 on {where}: the session expired and re-authentication did not help. "
            "Retry the call; if it persists, check the ELM credentials."
        )
    if status == 403 and message:
        return f"[regra-de-processo] HTTP 403 on {where}: {message}"
    if status == 403:
        return (
            f"[permissao] HTTP 403 on {where}: the role, license or project area visibility is insufficient "
            "(re-authenticated once). Ask for the proper role/permission on the project area."
        )
    if status == 404:
        return (
            f"[recurso-inexistente-ou-invisivel] HTTP 404 on {where}: the resource does not exist or the "
            "project area is not visible to this user. Check the URI and the project area visibility."
        )
    if status == 412:
        return (
            f"[conflito] HTTP 412 on {where}: the resource was changed by someone else since it was read "
            "(stale ETag). Re-read it and retry."
        )
    if status == 406:
        return (
            f"[formato] HTTP 406 on {where}: the server does not offer the requested format (QM only answers RDF/XML)."
        )
    suffix = f": {message}" if message else ""
    return f"[http-{status}] HTTP {status} on {where}{suffix}"


def _needs_reauth(resp: httpx.Response) -> bool:
    """True for an expired session: 401, a login redirect, or a 403 without oslc:message (stale CSRF/session).

    A 403 with oslc:message is a server business rule and must not be resent.
    """
    return (
        resp.status_code == 401
        or "j_security_check" in str(resp.headers.get("location", ""))
        or (resp.status_code == 403 and _server_message(resp) is None)
    )


_DEFAULT_PORTS = {"http": 80, "https": 443}


def _origin(url: str) -> tuple[str, str, int | None]:
    parsed = urlparse(url)
    scheme = parsed.scheme.lower()
    try:
        port = parsed.port or _DEFAULT_PORTS.get(scheme)
    except ValueError:
        port = None
    return scheme, (parsed.hostname or "").lower(), port


def assert_same_origin(url: str) -> str:
    """Return ``url`` if it is on the configured ELM server (scheme, host and port), else raise ValueError.

    Every URL followed from a server response (nextPage, workflow states, comment references,
    iterations, shapes...) goes through here (via _request), so a hostile or misconfigured response
    cannot make the client, which carries the session cookies, call another host.
    """
    if not isinstance(url, str) or not _origin(url)[1] or _origin(url) != _origin(settings.elm_url):
        raise ValueError(
            f"Refusing to follow '{url}': it is outside the configured ELM server ({settings.elm_url}). "
            "URLs found in server responses must be on the same scheme, host and port."
        )
    return url


async def _request(client: httpx.AsyncClient, method: str, url: str, **kwargs) -> httpx.Response:
    """Send a request, re-authenticating ONCE when the session looks expired (see _needs_reauth).

    Only URLs on the configured ELM server are requested (see assert_same_origin).
    """
    assert_same_origin(url)
    send = getattr(client, method.lower())
    resp = await send(url, **kwargs)
    if _needs_reauth(resp):
        # A 403 without oslc:message is treated as an expired session/CSRF and the request is resent
        # once, writes included (decision: 403 refuses the write, covered by the F1 tests). Residual risk:
        # if that 403 was a real refusal the write is sent twice; it is bounded to one resend and the
        # second answer is the one reported.
        await _authenticate(client)
        resp = await send(url, **kwargs)
    return resp


def check_response(resp: httpx.Response, operation: str, uri: str) -> None:
    """Raise ValueError (-> ToolError at the tool boundary) with the gate diagnosis for a non-2xx response."""
    if not resp.is_success:
        raise ValueError(diagnose_http_error(resp, operation, uri))


async def fetch(client: httpx.AsyncClient, method: str, url: str, operation: str, **kwargs) -> httpx.Response:
    """_request + check_response: the standard path of every HTTP call that must succeed."""
    resp = await _request(client, method, url, **kwargs)
    check_response(resp, operation, url)
    return resp


async def _get_xml(client: httpx.AsyncClient, url: str) -> ET.Element:
    """Fetch URL and parse as XML."""
    resp = await fetch(client, "GET", url, "discovery", headers={"Accept": "application/rdf+xml, application/xml"})
    return ET.fromstring(resp.text)


async def _get_json(client: httpx.AsyncClient, url: str) -> dict:
    """Fetch URL as JSON (OSLC JSON response)."""
    resp = await fetch(client, "GET", url, "read", headers={"Accept": "application/json", "OSLC-Core-Version": "2.0"})
    return resp.json()


async def get_catalog_url(domain: str) -> str:
    """Discover the service provider catalog URL from rootservices (cached, see elm_mcp.cache)."""
    return await cache.get_or_fetch("", f"catalog-url:{domain}", lambda: _load_catalog_url(domain))


async def _load_catalog_url(domain: str) -> str:
    client = await get_client()
    root_url = f"{settings.elm_url}{DOMAIN_PATHS[domain]}"
    root = await _get_xml(client, root_url)
    for xpath in CATALOG_XPATH[domain]:
        elem = root.find(xpath, NS)
        if elem is not None:
            return elem.get(f"{{{NS['rdf']}}}resource")
    raise ValueError(f"Cannot find service provider catalog for domain '{domain}'")


async def _cached_service_providers(domain: str) -> list[dict]:
    """The providers of a domain from the discovery cache (project name resolution of every tool)."""
    return await cache.get_or_fetch("", f"providers:{domain}", lambda: list_service_providers(domain))


async def list_service_providers(domain: str) -> list[dict]:
    """List all service providers (projects) for a domain (always read from the server)."""
    import re

    client = await get_client()
    catalog_url = await get_catalog_url(domain)
    resp = await fetch(
        client,
        "GET",
        catalog_url,
        "list_projects",
        headers={"Accept": "application/rdf+xml, application/xml"},
    )
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
    """Find the OSLC query base URL for a project's service provider (domains ``ccm`` and ``rm``)."""
    if domain == "qm":
        raise ValueError(
            "QM query capabilities are chosen by resource type, not by this generic lookup: use qm.query_members."
        )
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

    # CCM/QM: the service provider document. A project URL that is not a services.xml shares the discovery cache
    # (one fetch per project area); a services.xml URL keeps its own order of candidates.
    if project_url and not project_url.endswith("services.xml"):
        urls_to_try = [None]
    else:
        urls_to_try = [project_url.replace("/services.xml", "/services"), project_url]

    last_http_error: ValueError | None = None
    for url in urls_to_try:
        try:
            if url is None:
                sp = ET.fromstring(await _fetch_service_xml(project_url))
            else:
                sp = await _get_xml(await get_client(), url)
        except (httpx.HTTPError, ValueError, ET.ParseError) as exc:
            logger.debug("OSLC discovery: skipping %s (%s)", url or project_url, exc)
            if isinstance(exc, ValueError) and not isinstance(exc, ET.ParseError):
                last_http_error = exc
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

    if last_http_error is not None:
        raise last_http_error
    raise ValueError(f"No query capability found for {domain} in project '{project_url}'")


async def _resolve_project_url(domain: str, project_name: str) -> str:
    """Resolve project name to service provider URL."""
    providers = await _cached_service_providers(domain)
    for p in providers:
        if p["title"] and p["title"].lower() == project_name.lower():
            return p["url"]
    available = [p["title"] for p in providers]
    raise ValueError(f"Project '{project_name}' not found. Available: {available}")


DEFAULT_QUERY_LIMIT = 50
MAX_QUERY_LIMIT = 1000
MAX_QUERY_PAGES = 100


class QueryResults(list):
    """List of query members; ``truncated`` is True when more items exist than were returned.

    ``total`` (RM queries) is the server's count of matching items, when it reports one;
    ``configuration`` (RM queries) is the stream sent as Configuration-Context, or None.
    """

    truncated: bool = False
    total: int | None = None
    configuration: str | None = None


# ---------------------------------------------------------------------------------------------
# RM query (SPEC-rm-consulta): configuration context, oslc.prefix/oslc.select and RDF/JSON pages
# ---------------------------------------------------------------------------------------------
_RM_QUERY_PREFIXES = {
    "dcterms": "http://purl.org/dc/terms/",
    "oslc": "http://open-services.net/ns/core#",
    "rdf": NS["rdf"],
    "nav": "http://jazz.net/ns/rm/navigation#",
    "jazz_rm": "http://jazz.net/ns/rm#",
}
_RM_QUERY_SELECT = "dcterms:title,dcterms:identifier,dcterms:modified,oslc:instanceShape,nav:parent"
_RDF_JSON_OSLC = _RM_QUERY_PREFIXES["oslc"]
_RDF_JSON_DCTERMS = _RM_QUERY_PREFIXES["dcterms"]
_RDFS_MEMBER = "http://www.w3.org/2000/01/rdf-schema#member"
_QUERY_RESULTS_RE = _re.compile(r"Query Results:\s*(\d+)")


def _rdf_values(node: dict, predicate: str) -> list[str]:
    values = node.get(predicate) if isinstance(node, dict) else None
    return [str(v["value"]) for v in values if isinstance(v, dict) and "value" in v] if isinstance(values, list) else []


def _rdf_first(node: dict, predicate: str) -> str | None:
    values = _rdf_values(node, predicate)
    return values[0] if values else None


def _rdf_requirements(data: dict) -> list[dict]:
    """One item per rm#Requirement subject of an RDF/JSON page; the container and ResponseInfo are skipped."""
    items = []
    for uri, node in data.items():
        if _RM_REQUIREMENT_TYPE not in _rdf_values(node, f"{_RDF_NS}type"):
            continue
        item = {
            "uri": uri,
            "title": _rdf_first(node, f"{_RDF_JSON_DCTERMS}title"),
            "identifier": _rdf_first(node, f"{_RDF_JSON_DCTERMS}identifier"),
            "type": _rdf_first(node, f"{_RDF_JSON_OSLC}instanceShape"),
            "modified": _rdf_first(node, f"{_RDF_JSON_DCTERMS}modified"),
        }
        folder = _rdf_first(node, f"{_RM_QUERY_PREFIXES['nav']}parent")
        if folder:
            item["folder"] = folder
        items.append(item)
    return items


def _rdf_response_info(data: dict) -> dict:
    """The ResponseInfo node (the subject typed oslc:ResponseInfo) of an RDF/JSON page, or {}."""
    for node in data.values():
        if f"{_RDF_JSON_OSLC}ResponseInfo" in _rdf_values(node, f"{_RDF_NS}type"):
            return node
    return {}


def _rdf_next_page(data: dict) -> str | None:
    return _rdf_first(_rdf_response_info(data), f"{_RDF_JSON_OSLC}nextPage")


def _rdf_total(data: dict) -> int | None:
    """oslc:totalCount of the ResponseInfo, else N of its title "Query Results: N"."""
    info = _rdf_response_info(data)
    count = _rdf_first(info, f"{_RDF_JSON_OSLC}totalCount")
    if count is not None and count.strip().isdigit():
        return int(count)
    title = _QUERY_RESULTS_RE.search(_rdf_first(info, f"{_RDF_JSON_DCTERMS}title") or "")
    return int(title.group(1)) if title else None


async def _find_rm_configuration(project_url: str) -> str | None:
    """The stream of an RM project: service provider -> oslc_config:component -> its oslc_config:configurations
    -> first rdfs:member. None when the project exposes no component or the component lists no configuration.
    Cached per project area (SPEC-cache-shape); the first stream listed is the one used.
    """

    async def load() -> str | None:
        component = _re.search(
            r"<oslc_config:component\s[^>]*rdf:resource=\"([^\"]+)\"", await _fetch_service_xml(project_url)
        )
        if component is None:
            return None
        component_xml = await _get_xml_text(_html.unescape(component.group(1)))
        configurations = _re.search(r"<(?:\w+:)?configurations\s[^>]*rdf:resource=\"([^\"]+)\"", component_xml)
        if configurations is None:
            return None
        member = _re.search(
            r"<(?:\w+:)?member\s[^>]*rdf:resource=\"([^\"]+)\"",
            await _get_xml_text(_html.unescape(configurations.group(1))),
        )
        return _html.unescape(member.group(1)) if member else None

    return await cache.get_or_fetch(_project_area_id(project_url), f"rm-configuration:{project_url}", load)


def _next_page_url(data: dict) -> str | None:
    """The oslc:nextPage URL of an OSLC JSON page (top level or inside oslc:responseInfo), or None."""
    info = data.get("oslc:responseInfo")
    for holder in (info if isinstance(info, dict) else {}, data):
        nxt = holder.get("oslc:nextPage")
        if isinstance(nxt, dict):
            nxt = nxt.get("rdf:resource")
        if isinstance(nxt, str) and nxt:
            return nxt
    return None


def _page_members(data: dict) -> list[dict]:
    members = data.get("oslc:results", data.get("rdfs:member", data.get("results", [data])))
    return members if isinstance(members, list) else [members]


async def query_resources(
    domain: str,
    project: str,
    where: str | None = None,
    select: str | None = None,
    limit: int | None = None,
) -> QueryResults:
    """Execute an OSLC query on a project, following oslc:nextPage until ``limit`` unique items.

    ``limit`` defaults to 50 and is capped at 1000; zero or a negative value is an error. The walk also
    stops (truncated) on a repeated next-page URL, on a page without any new item and after 100 pages. The
    result is a list with a ``truncated`` attribute (True when the query has more items). An expired session
    is re-authenticated once per page; a page that keeps failing raises a ValueError naming the gate and,
    after the first page, how many items had been collected.

    RM queries (``domain == "rm"``) discover the project's configuration (stream) and send it as
    ``Configuration-Context`` with ``oslc.prefix`` and a default ``oslc.select``; the RDF/JSON pages are
    interpreted into one item per requirement (uri, title, identifier, type, modified, folder) and
    ``total``/``configuration`` are set on the result.
    """
    if limit is not None and limit <= 0:
        raise ValueError(
            f"limit must be a positive number (got {limit}); omit it for the default of {DEFAULT_QUERY_LIMIT}."
        )
    cap = min(limit if limit else DEFAULT_QUERY_LIMIT, MAX_QUERY_LIMIT)
    project_url = await _resolve_project_url(domain, project)
    query_base = await _find_query_base(domain, project_url)
    client = await get_client()
    rm = domain == "rm"
    configuration = await _find_rm_configuration(project_url) if rm else None
    params = {}
    if where:
        params["oslc.where"] = where
    if rm:
        params["oslc.prefix"] = ",".join(f"{name}=<{uri}>" for name, uri in _RM_QUERY_PREFIXES.items())
        select = select or _RM_QUERY_SELECT
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

    headers = {"Accept": "application/json", "OSLC-Core-Version": "2.0"}
    if configuration:
        headers["Configuration-Context"] = configuration
    resp = await _request(client, "GET", url, params=params, headers=headers)
    # Some domains (QM) don't support JSON — fallback to XML
    if resp.status_code == 406:
        resp = await fetch(
            client,
            "GET",
            url,
            "query_resources",
            params=params,
            headers={"Accept": "application/xml", "OSLC-Core-Version": "2.0"},
        )
        # Parse XML response into list of resource URIs
        from xml.etree import ElementTree as ET

        root = ET.fromstring(resp.text)
        results = QueryResults()
        for member in root.iter(f"{{{NS['rdf']}}}Description"):
            about = member.get(f"{{{NS['rdf']}}}about")
            title_el = member.find("{http://purl.org/dc/terms/}title")
            entry = {"rdf:about": about}
            if title_el is not None:
                entry["dcterms:title"] = title_el.text
            results.append(entry)
        if not results:
            results.append({"raw_xml": resp.text[:2000]})
        return results

    results = QueryResults()
    results.configuration = configuration
    seen: set[str] = set()
    visited: set[str] = {url}
    page_url = url
    pages = 1
    while True:
        check_response(resp, "query_resources", page_url)
        data = resp.json()
        new_on_page = 0
        if rm:
            members = _rdf_requirements(data)
            if results.total is None:
                results.total = _rdf_total(data)
        else:
            members = _page_members(data)
        for item in members:
            key = (
                item.get("uri")
                if rm
                else item.get("rdf:about", item.get("rdf:resource"))
                if isinstance(item, dict)
                else None
            )
            if key is not None:
                if key in seen:
                    continue
                seen.add(key)
            if len(results) >= cap:
                results.truncated = True
                return results
            results.append(item)
            new_on_page += 1
        next_url = _rdf_next_page(data) if rm else _next_page_url(data)
        if next_url is None:
            return results
        if len(results) >= cap:
            results.truncated = True
            return results
        # Guards against a server that never ends: a repeated page URL, a page with nothing new
        # and a ceiling on the number of pages all stop the walk (the result is then truncated).
        if next_url in visited or new_on_page == 0 or pages >= MAX_QUERY_PAGES:
            logger.warning(
                "query_resources: stopped at page %d (repeated next page, no new item or page ceiling)", pages
            )
            results.truncated = True
            return results
        visited.add(next_url)
        page_url = next_url
        pages += 1
        try:
            resp = await _request(client, "GET", page_url, headers=headers)
            check_response(resp, "query_resources", page_url)
        except (ValueError, RuntimeError) as exc:
            raise ValueError(f"{exc} ({len(results)} items had been collected before this page failed.)") from exc


_XHTML_NS = "http://www.w3.org/1999/xhtml"


def _qname(tag: str, prefixes: dict[str, str]) -> str:
    """Prefixed name for an ElementTree ``{ns}local`` tag, using the prefixes seen in the document."""
    ns_uri, _, local = tag[1:].partition("}") if tag.startswith("{") else ("", "", tag)
    prefix = prefixes.get(ns_uri, "")
    return f"{prefix}:{local}" if prefix else local


def _child_to_xml(child: ET.Element) -> str:
    """Serialize one element with the stdlib writer; XHTML keeps a default ``xmlns`` (no ``ns0:`` prefixes)."""
    try:
        return ET.tostring(child, encoding="unicode", default_namespace=_XHTML_NS, short_empty_elements=True)
    except ValueError:  # content outside the XHTML namespace: use the standard prefixed output
        return ET.tostring(child, encoding="unicode", short_empty_elements=True)


def _serialize_literal(prop: ET.Element) -> str:
    """Serialize the content of a ``rdf:parseType="Literal"`` element from the already parsed tree.

    Uses ``ET.tostring`` per child; text and tails are XML-escaped, so empty elements,
    namespaces and ``xml:lang`` come out as well-formed XML. Excessive nesting raises ``ValueError``.
    """
    try:
        parts = [_xml_escape(prop.text or "")]
        for child in prop:
            parts.append(_child_to_xml(child))
            parts.append(_xml_escape(child.tail or ""))
    except RecursionError as exc:
        raise ValueError("invalid RDF/XML response: nesting too deep") from exc
    return "".join(parts).strip()


def _rdfxml_to_flat_dict(xml_text: str, uri: str) -> dict:
    """Flatten the main rdf:Description of an RDF/XML document into a plain dict.

    Keys are ``rdf:about`` plus ``prefix:name`` for every property (prefixes as declared in the document).
    Values are text (literal), the inner XML as a string (``rdf:parseType="Literal"``) or a URI
    (``rdf:resource``); a repeated property becomes a list. The main Description is the one whose
    ``rdf:about`` equals ``uri``, else the first one. Malformed XML or a DTD/entity declaration raises
    ``ValueError`` (legitimate OSLC responses carry no DTD).
    """
    if "<!DOCTYPE" in xml_text or "<!ENTITY" in xml_text:
        raise ValueError("invalid RDF/XML response: DTD/entity declarations are not accepted")
    prefixes: dict[str, str] = {}
    root = None
    try:
        for event, item in ET.iterparse(io.StringIO(xml_text), events=("start-ns", "end")):
            if event == "start-ns":
                prefix, ns_uri = item
                prefixes.setdefault(ns_uri, prefix)
            else:
                root = item
    except ET.ParseError as exc:
        raise ValueError(f"invalid RDF/XML response: {exc}") from exc
    rdf_ns = NS["rdf"]
    if root is not None and root.tag == f"{{{rdf_ns}}}Description":
        descriptions = [root]
    else:
        descriptions = list(root) if root is not None else []
    main = next((d for d in descriptions if d.get(f"{{{rdf_ns}}}about") == uri), None)
    if main is None and descriptions:
        main = descriptions[0]
    result: dict = {}
    if main is None:
        return result
    result["rdf:about"] = main.get(f"{{{rdf_ns}}}about", uri)
    for prop in main:
        key = _qname(prop.tag, prefixes)
        if prop.get(f"{{{rdf_ns}}}resource") is not None:
            value = prop.get(f"{{{rdf_ns}}}resource")
        elif prop.get(f"{{{rdf_ns}}}parseType") == "Literal":
            value = _serialize_literal(prop)
        else:
            value = prop.text or ""
        if key in result:
            if not isinstance(result[key], list):
                result[key] = [result[key]]
            result[key].append(value)
        else:
            result[key] = value
    return result


async def get_resource(uri: str) -> dict:
    """Fetch a single OSLC resource by URI.

    Asks for JSON. RM resources (URI path starting with ``/rm/``) answer HTTP 415 to JSON: in that
    case the request is repeated with ``application/rdf+xml`` and the RDF/XML is flattened to a dict
    (see _rdfxml_to_flat_dict). Other resources (e.g. CCM) never get the fallback.
    """
    client = await get_client()
    resp = await _request(client, "GET", uri, headers={"Accept": "application/json", "OSLC-Core-Version": "2.0"})
    if resp.status_code == 415 and urlparse(uri).path.startswith("/rm/"):
        resp = await fetch(
            client, "GET", uri, "read", headers={"Accept": "application/rdf+xml", "OSLC-Core-Version": "2.0"}
        )
        return _rdfxml_to_flat_dict(resp.text, uri)
    check_response(resp, "read", uri)
    return resp.json()


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
        resp = await fetch(
            client,
            "GET",
            iterations_url,
            "find_current_iteration",
            headers={"Accept": "application/json", "OSLC-Core-Version": "2.0"},
        )
        members = resp.json().get("oslc:results", [])
        now = datetime.now(timezone.utc)
        candidates = []  # (uri, start_dt, has_end)
        for m in members:
            iter_uri = m.get("rdf:resource")
            if not iter_uri:
                continue
            detail = await client.get(
                assert_same_origin(iter_uri), headers={"Accept": "application/json", "OSLC-Core-Version": "2.0"}
            )
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


class WriteRejected(ValueError):
    """A write the server refused; ``status_code`` tells a stale-shape validation error (400/409) from the rest."""

    def __init__(self, message: str, status_code: int) -> None:
        super().__init__(message)
        self.status_code = status_code


def _raise_write_error(resp: httpx.Response, operation: str, uri: str) -> None:
    """Raise a readable ValueError naming the gate for a refused write.

    The @tool boundary only translates ValueError/RuntimeError, so the diagnosis must leave
    this module as one of them. The server's oslc:message is kept verbatim by diagnose_http_error.
    """
    message = diagnose_http_error(resp, operation, uri)
    server_message = _server_message(resp)
    if server_message and _re.search(r"filed\s*against|categor", server_message, _re.IGNORECASE):
        message += " Hint: use list_fields to see the valid values (property filedAgainst, allowedValues)."
    raise WriteRejected(message, resp.status_code)


def _resolve_ccm_type(factories: list[dict], type_arg: str) -> dict:
    """Pick the typed factory for a type id, short alias or title.

    Everything comes from the project's discovery: the alias is the last segment of the type id
    (``task``, ``story``, ``risk``...), so no type map is kept in code. Precedence, first level that
    matches wins: exact id > alias (last id segment, case-insensitive) > title (case-insensitive).
    More than one match inside the winning level is ambiguous: ValueError listing the ids.
    """
    wanted = type_arg.strip()
    lowered = wanted.lower()
    levels = (
        ("id", [f for f in factories if f["id"] == wanted]),
        ("alias", [f for f in factories if f["id"].rsplit(".", 1)[-1].lower() == lowered]),
        ("title", [f for f in factories if (f["title"] or "").strip().lower() == lowered]),
    )
    for kind, matches in levels:
        if len(matches) > 1:
            ids = ", ".join(f["id"] for f in matches)
            raise ValueError(
                f"Type '{type_arg}' is ambiguous (several types share this {kind}): {ids}. Use the type id."
            )
        if matches:
            return matches[0]
    available = ", ".join(f"{f['id']} ({f['title']})" for f in factories)
    raise ValueError(f"Type '{type_arg}' not found in this project. Available types: {available}")


async def create_resource(
    domain: str,
    project: str,
    payload: dict,
    wi_type: str | None = None,
    *,
    factory: dict | None = None,
    project_url: str | None = None,
) -> dict:
    """POST a new resource (RDF/XML) to the creation factory.

    With wi_type, the POST goes to the factory of that type (id, title or alias); the
    generic /workitems factory is only used when no type is given. A caller that already
    resolved the type passes ``factory`` (and ``project_url``) to skip a second discovery.
    """
    if domain != "ccm":
        raise NotImplementedError(f"create_resource only supports 'ccm' domain, got '{domain}'")
    if project_url is None:
        project_url = await _resolve_project_url(domain, project)
    generic_url = f"{settings.elm_url}/ccm/oslc/contexts/{_project_area_id(project_url)}/workitems"
    creation_url = generic_url
    if factory is not None:
        creation_url = factory["factory_uri"]
    elif wi_type:
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
    # Re-authenticates once on an expired session (see _needs_reauth); a 403 with oslc:message
    # is the server's business rule and is not resent.
    resp = await _request(client, "POST", creation_url, content=rdfxml_body, headers=post_headers)
    if resp.status_code != 201:
        if resp.is_success:
            raise RuntimeError(f"Expected 201 Created, got {resp.status_code}")
        _raise_write_error(resp, "create_workitem", creation_url)
    return resp.json()


async def update_resource(uri: str, payload: dict) -> dict:
    """Partially update a resource: PUT only the changed properties (with If-Match).

    Sends ?oslc.properties=<changed props> and a body with just those properties, then
    returns the re-read resource.
    """
    client = await get_client()
    # GET to obtain the ETag (and the prefix map, reused so custom prefixes resolve)
    get_resp = await fetch(
        client, "GET", uri, "update_workitem", headers={"Accept": "application/json", "OSLC-Core-Version": "2.0"}
    )
    etag = get_resp.headers.get("ETag", "")
    if not etag:
        raise ValueError(f"Server did not return an ETag for resource '{uri}'. Cannot safely update.")
    body = dict(payload)
    prefixes = get_resp.json().get("prefixes")
    if isinstance(prefixes, dict):
        body["prefixes"] = prefixes
    resp = await _request(
        client,
        "PUT",
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
    _check_put(resp, "update_workitem", uri)
    return await _get_json(client, uri)


def _check_put(resp: httpx.Response, operation: str, uri: str) -> None:
    """Map a refused conditional PUT to a readable error naming the gate (412 = conflito)."""
    if resp.status_code >= 400:
        _raise_write_error(resp, operation, uri)


_STATE_URI_RE = _re.compile(r"/oslc/workflows/([^/]+)/states/([^/]+)/([^/?#]+)$")


def _last_segment(uri: str) -> str:
    """Last path (or fragment) segment of a URI, e.g. .../core#Exactly-one -> Exactly-one."""
    return _re.split(r"[/#]", uri.rstrip("/"))[-1]


_MIN_ACTION_MATCH_LEN = 2


def _resolve_workflow_action(action: str, action_ids: list[str]) -> str:
    """Resolve `action` to one workflow action id: exact, then '.{action}' suffix, then unique substring.

    Blank input is refused and suffix/substring matching needs at least 2 characters, so a
    trivial value cannot match (and execute) an arbitrary action.
    """
    action = action.strip()
    if not action:
        raise ValueError(f"Action must not be empty. Available actions: {action_ids}")
    if action in action_ids:
        return action
    if len(action) < _MIN_ACTION_MATCH_LEN:
        raise ValueError(
            f"Action '{action}' is too short to match by suffix or substring "
            f"(min {_MIN_ACTION_MATCH_LEN} characters). Available actions: {action_ids}"
        )
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
        resp = await client.get(
            assert_same_origin(states_url), headers={"Accept": "application/json", "OSLC-Core-Version": "2.0"}
        )
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
    get_resp = await fetch(client, "GET", item_uri, "transition_workitem", headers=json_headers)
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
    actions_resp = await fetch(
        client, "GET", f"{workflows}/actions/{workflow}", "transition_workitem", headers=json_headers
    )
    action = profile.action_aliases(profile.load(area)).get(action.strip(), action)
    action_id = _resolve_workflow_action(action, _action_ids(actions_resp.json()))

    resp = await _request(
        client,
        "PUT",
        item_uri,
        params={"_action": action_id, "oslc.properties": "dcterms:title"},
        json={"dcterms:title": title},
        headers={**json_headers, "Content-Type": "application/json", "If-Match": etag},
    )
    _check_put(resp, "transition_workitem", item_uri)

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
    xml_text = await _get_xml_text(project_url)

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


IF_EXISTS_MODES = ("error", "return", "create")

# Failures after which the server may already have processed a POST: never resent, the duplicate is queried again.
_AMBIGUOUS_FAILURES = (httpx.ReadTimeout, httpx.WriteTimeout, httpx.ReadError, httpx.RemoteProtocolError)


def check_if_exists(if_exists: str) -> str:
    """The ``if_exists`` mode, or ValueError (raised before any request is made)."""
    if if_exists not in IF_EXISTS_MODES:
        raise ValueError(f"Invalid if_exists '{if_exists}'. Must be one of: {', '.join(IF_EXISTS_MODES)}.")
    return if_exists


def normalize_title(title: str | None) -> str:
    """Title as compared by the duplicate check: edge and repeated whitespace removed, case-folded."""
    return " ".join((title or "").split()).casefold()


def _where_literal(value: str) -> str:
    """A string literal for oslc.where (backslash and double quote escaped)."""
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


def _entry_uri(entry: dict) -> str:
    return entry.get("uri") or entry.get("rdf:about") or ""


def _entry_identifier(entry: dict) -> str:
    return str(entry.get("identifier") or entry.get("dcterms:identifier") or "?")


def _describe(entries: list[dict]) -> str:
    return "; ".join(f"{_entry_uri(e)} (identifier {_entry_identifier(e)})" for e in entries)


async def create_idempotent(if_exists: str, find, create, kind: str) -> dict:
    """Create unless ``find()`` already lists an equivalent artifact (SPEC-idempotencia).

    ``find`` returns the existing equivalents ([{uri | rdf:about, identifier | dcterms:identifier, ...}]);
    ``create`` performs the POST and returns the created resource. ``if_exists``: ``error`` refuses naming the
    existing one, ``return`` hands it back with ``created: false``, ``create`` skips the check. A failed check
    refuses the creation (except ``create``). After an ambiguous failure (timeout once the request was sent) the
    POST is NOT resent: ``find`` runs again and only an artifact that was not there before counts as created.
    """
    check_if_exists(if_exists)
    before: list[dict] = []
    if if_exists != "create":
        try:
            before = await find()
        except (ValueError, RuntimeError, httpx.HTTPError) as exc:
            raise ValueError(
                f"The duplicate check failed ({exc}); nothing was created. Fix the cause, or pass "
                "if_exists='create' to create without the check."
            ) from exc
        if before and if_exists == "return":
            return {**before[0], "created": False}
        if before:
            raise ValueError(
                f"A {kind} with the same title already exists: {_describe(before)}. "
                "Use if_exists='return' to get it, or if_exists='create' to create another one."
            )
    try:
        return {**await create(), "created": True}
    except _AMBIGUOUS_FAILURES as exc:
        known = {_entry_uri(e) for e in before}
        try:
            found = await find()
        except (ValueError, RuntimeError, httpx.HTTPError) as requery_exc:
            raise ValueError(
                f"The creation request failed without an answer ({exc}) and the {kind} could not be queried again "
                f"({requery_exc}). It may or may not exist; the request was NOT resent. Check before retrying."
            ) from exc
        new = [e for e in found if _entry_uri(e) not in known]
        if if_exists != "create" and new:
            return {
                **new[0],
                "created": True,
                "warning": f"The server did not answer the creation request ({exc}); the {kind} was found "
                "afterwards, so it was created. The request was NOT resent.",
            }
        detail = f" Possible matches: {_describe(found)}." if found else ""
        raise ValueError(
            f"The creation request failed without an answer ({exc}) and no new {kind} was found afterwards."
            f"{detail} The request was NOT resent; check before retrying."
        ) from exc


async def _find_rm_duplicates(project: str, title: str, shape: str, folder: str | None) -> list[dict]:
    """Requirements of the project with the same normalized title, type and (when given) folder."""
    results = await query_resources("rm", project, f"dcterms:title={_where_literal(title.strip())}")
    wanted = normalize_title(title)
    return [
        {"uri": item["uri"], "identifier": item.get("identifier")}
        for item in results
        if normalize_title(item.get("title")) == wanted
        and item.get("type") == shape
        and (folder is None or item.get("folder") == folder)
    ]


async def create_requirement(
    project: str,
    title: str,
    xhtml: str,
    folder: str | None = None,
    shape: str | None = None,
    if_exists: str = "error",
) -> dict:
    """Create an RM requirement via OSLC POST to the requirementFactory.

    Resolves the project, requires and resolves ``shape`` (URI or type title, D7),
    discovers the factory, sanitizes nothing here (the caller passes already-sanitized
    XHTML), builds the RDF/XML, and POSTs it (Accept/Content-Type application/rdf+xml).
    Accepts 200 or 201. Re-authenticates once on an expired session (see _needs_reauth).
    Before the POST, a requirement with the same normalized title, type and folder is looked up (see
    create_idempotent, ``if_exists``); the result carries ``created``.
    Returns {"uri": ..., "identifier": ..., "created": ...}, the identifier re-read from the server (D8). If the
    re-read fails after the POST (the requirement exists), it never raises: it returns the POST-body identifier
    (or None) and a short ``warning`` key, and logs a warning.
    Raises ValueError if the project does not exist or the type is missing/unknown.
    """
    check_if_exists(if_exists)
    project_url = await _resolve_project_url("rm", project)
    shape = await _resolve_requirement_shape(project, shape)
    factory_url = await _find_requirement_factory(project_url)
    _assert_xhtml_wellformed(xhtml)
    rdf_body = _requirement_to_rdfxml(title, xhtml, shape=shape, folder=folder)

    async def find() -> list[dict]:
        return await _find_rm_duplicates(project, title, shape, folder)

    async def create() -> dict:
        return await _post_requirement(factory_url, rdf_body)

    return await create_idempotent(if_exists, find, create, "requirement")


async def _post_requirement(factory_url: str, rdf_body: str) -> dict:
    client = await get_client()
    post_headers = {
        "Content-Type": "application/rdf+xml",
        "Accept": "application/rdf+xml",
        "OSLC-Core-Version": "2.0",
    }
    resp = await _request(client, "POST", factory_url, content=rdf_body.encode("utf-8"), headers=post_headers)
    if resp.status_code not in (200, 201):
        if resp.is_success:
            raise RuntimeError(f"Expected 200/201 Created, got {resp.status_code}")
        _raise_write_error(resp, "create_requirement", factory_url)
    location = resp.headers.get("Location", "")
    identifier = _extract_identifier(resp.text)
    result: dict = {"uri": location, "identifier": identifier}
    if location:
        # The POST body can be empty: read the identifier back from the created artifact (D8).
        # The requirement ALREADY exists here, so a failed re-read must never propagate (the caller
        # would retry the POST and create a duplicate): fall back to the POST body and warn.
        try:
            reread = await fetch(
                client,
                "GET",
                location,
                "create_requirement",
                headers={"Accept": "application/rdf+xml, application/xml", "OSLC-Core-Version": "2.0"},
            )
            result["identifier"] = _extract_identifier(reread.text) or identifier
        except (ValueError, RuntimeError, httpx.HTTPError) as exc:
            logger.warning("create_requirement: created %s but the re-read failed (%s)", location, exc)
            result["warning"] = (
                "Requirement created, but re-reading it failed; the identifier may be missing. Do NOT create it again."
            )
    return result


async def _resolve_requirement_shape(project: str, type_arg: str | None) -> str:
    """Resolve the required RM type (D7) to a shape URI.

    ``type_arg`` is a shape URI (used as is) or a type title matched case-insensitively
    against list_types("rm"). Without a type, or with an unknown title, raises ValueError
    listing the available types (the POST is never sent).
    """
    wanted = (type_arg or "").strip()
    if wanted.lower().startswith(("http://", "https://")):
        return wanted
    shapes = await list_resource_shapes("rm", project)
    if wanted:
        match = next((sh for sh in shapes if (sh["title"] or "").lower() == wanted.lower()), None)
        if match is not None:
            return match["shape_uri"]
    available = ", ".join(f"{sh['title']} ({sh['shape_uri']})" for sh in shapes) or "none found"
    reason = f"Unknown type '{wanted}'" if wanted else "'type' is required"
    raise ValueError(f"{reason} for create_requirement. Available RM types (see list_types): {available}")


async def delete_requirement(uri: str) -> dict:
    """DELETE an RM requirement (direct mode) and confirm by re-reading it (404 or 410).

    The DELETE is unconditional (no If-Match): it removes whatever version exists. Raises ValueError
    when the DELETE is refused (gate named), when the artifact is still readable afterwards, or when
    the confirmation GET is denied (401/403: the DELETE was sent, the deletion is NOT confirmed).
    Returns {"uri", "deleted": True}.
    """
    client = await get_client()
    resp = await _request(client, "DELETE", uri, headers={"OSLC-Core-Version": "2.0"})
    if not resp.is_success:
        _raise_write_error(resp, "delete_requirement", uri)
    check = await _request(client, "GET", uri, headers={"Accept": "application/rdf+xml", "OSLC-Core-Version": "2.0"})
    if check.status_code in (404, 410):
        return {"uri": uri, "deleted": True}
    if check.is_success:
        raise ValueError(f"DELETE was accepted but the requirement is still readable: {uri}")
    if check.status_code in (401, 403):
        raise ValueError(
            f"Deletion not confirmed: the DELETE was sent but the confirmation read of {uri} was denied "
            f"(HTTP {check.status_code}). Check whether the requirement still exists."
        )
    check_response(check, "delete_requirement", uri)
    raise ValueError(f"Could not confirm the deletion of {uri}")


async def update_requirement(uri: str, title: str | None = None, xhtml: str | None = None) -> dict:
    """Update an RM requirement via OSLC PUT with If-Match (ETag).

    GETs the resource as rdf+xml for its ETag, edits the XML as a string
    preserving parseType="Literal" (mirrors publicar_elm.atualizar_requisito),
    then PUTs with If-Match. Accepts 200/204. On 412 raises ValueError signalling
    an ETag conflict (the @tool boundary converts it to a legible ToolError).
    """
    client = await get_client()
    get_resp = await fetch(
        client,
        "GET",
        uri,
        "update_requirement",
        headers={"Accept": "application/rdf+xml, application/xml", "OSLC-Core-Version": "2.0"},
    )
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

    put_resp = await _request(
        client,
        "PUT",
        uri,
        content=xml_content.encode("utf-8"),
        headers={
            "Content-Type": "application/rdf+xml",
            "Accept": "application/rdf+xml",
            "OSLC-Core-Version": "2.0",
            "If-Match": etag,
        },
    )
    if put_resp.status_code not in (200, 204):
        if put_resp.is_success:
            raise RuntimeError(f"Expected 200/204, got {put_resp.status_code}")
        _raise_write_error(put_resp, "update_requirement", uri)
    result: dict = {"uri": uri, "identifier": _extract_identifier(put_resp.text)}
    # The PUT already succeeded: a failed re-read must never propagate (same rule as create_requirement).
    try:
        reread = await fetch(
            client,
            "GET",
            uri,
            "update_requirement",
            headers={"Accept": "application/rdf+xml, application/xml", "OSLC-Core-Version": "2.0"},
        )
        result["identifier"] = _extract_identifier(reread.text) or result["identifier"]
    except (ValueError, RuntimeError, httpx.HTTPError) as exc:
        logger.warning("update_requirement: updated %s but the re-read failed (%s)", uri, exc)
        result["warning"] = "Requirement updated, but re-reading it failed; the identifier may be missing."
    return result


# =====================================================================
# Discovery tools — shapes / fields / folders / work item inspection.
# ADDITIVE, read-only (G0-discovery-tools.md). Regex parsing (not ET —
# ET fails in the MCP stdio context, memória 0c1b2aa4); JSON is parsed
# natively where the entry point returns JSON (folders, inspect_workitem).
# =====================================================================

# Config-management + RM public namespaces. Additive to NS (like jazz_rm/nav).
NS["oslc_config"] = "http://open-services.net/ns/config#"
NS["public_rm"] = "http://jazz.net/xmlns/rm/public/1.0/"

_RDF_TYPE = "http://www.w3.org/1999/02/22-rdf-syntax-ns#type"
_NAV_FOLDER_TYPE = "http://jazz.net/ns/rm/navigation#folder"
_NAV_SUBFOLDERS = "http://jazz.net/ns/rm/navigation#subfolders"


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
    resp = await fetch(
        client,
        "GET",
        url,
        "discovery",
        headers={"Accept": "application/rdf+xml, application/xml", "OSLC-Core-Version": "2.0"},
    )
    return resp.text


async def _cached_xml_text(url: str) -> str:
    """_get_xml_text through the discovery cache (shapes and allowedValues documents, keyed by URL)."""
    return await cache.get_or_fetch("", f"xml:{url}", lambda: _get_xml_text(url))


async def _fetch_service_xml(project_url: str) -> str:
    """The service provider XML of a project (cached by project area, see elm_mcp.cache)."""
    return await cache.get_or_fetch(
        _project_area_id(project_url), f"services:{project_url}", lambda: _load_service_xml(project_url)
    )


async def _load_service_xml(project_url: str) -> str:
    """GET the service provider XML. A services.xml URL is the document itself; otherwise try /services first."""
    if project_url.endswith("services.xml"):
        candidates = [project_url, project_url.replace("/services.xml", "/services")]
    else:
        candidates = [project_url.rstrip("/") + "/services", project_url]
    last_error: Exception | None = None
    for url in candidates:
        try:
            return await _get_xml_text(url)
        except (httpx.HTTPError, ValueError) as exc:
            logger.debug("OSLC discovery: skipping %s (%s)", url, exc)
            last_error = exc
    if isinstance(last_error, ValueError):
        raise last_error
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
        raw_title = _html.unescape(title_m.group(1)) if title_m else ""
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
    xml_text = await _fetch_service_xml(project_url)
    resource_type = QUERY_RESOURCE_TYPE[domain]

    shapes: list[dict] = []
    for block in _re.findall(r"<\w*:?CreationFactory\b.*?</\w*:?CreationFactory>", xml_text, _re.DOTALL):
        rt = _re.search(r"<\w*:?resourceType\s+[^>]*rdf:resource=\"([^\"]+)\"", block)
        if rt is None or rt.group(1) != resource_type:
            continue
        for sh in _re.findall(r"<\w*:?resourceShape\s+[^>]*rdf:resource=\"([^\"]+)\"", block):
            shape_xml = await _cached_xml_text(sh)
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


_CORE_NS = "http://open-services.net/ns/core#"
_RDF_NS = "http://www.w3.org/1999/02/22-rdf-syntax-ns#"


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _is_shape_property(el: ET.Element) -> bool:
    """oslc:Property element (RM/QM) or rdf:Description typed core#Property (CCM)."""
    if el.tag == f"{{{_CORE_NS}}}Property":
        return True
    if el.tag != f"{{{_RDF_NS}}}Description":
        return False
    return any(c.tag == f"{{{_RDF_NS}}}type" and c.get(f"{{{_RDF_NS}}}resource") == f"{_CORE_NS}Property" for c in el)


def _shape_property(el: ET.Element) -> dict:
    """Extract one property from its element, whatever the order of the children."""
    text: dict[str, str] = {}
    res: dict[str, str] = {}
    for child in el:
        key = _local(child.tag)
        value = child.get(f"{{{_RDF_NS}}}resource")
        if value is not None:
            res.setdefault(key, value)
        else:
            text.setdefault(key, "".join(child.itertext()).strip())
    occurs = res.get("occurs")
    return {
        "name": text.get("name") or None,
        "title": text.get("title") or None,
        "required": _occurs_required(occurs),
        "occurs": _last_segment(occurs) if occurs else None,
        "read_only": text.get("readOnly", "").lower() == "true",
        "property_uri": res.get("propertyDefinition"),
        "range_uri": res.get("range"),
        "allowed_values_uri": res.get("allowedValues"),
        "default_value": res.get("defaultValue"),
    }


async def list_shape_properties(shape_uri: str) -> list[dict]:
    """GET a resourceShape and extract its properties.

    Two formats: <oslc:Property> blocks (RM/QM) and <rdf:Description> typed core#Property
    (CCM; the ResourceShape's own Description is not a property). Each entry: name, title,
    required (from oslc:occurs), occurs (last segment), read_only, property_uri
    (propertyDefinition), range_uri, allowed_values_uri, default_value.
    Raises ValueError when the shape references properties (oslc:property) but none could be extracted.
    """
    xml_text = await _cached_xml_text(shape_uri)
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError as exc:
        raise ValueError(f"Shape '{shape_uri}' is not valid XML: {exc}") from exc
    props = [_shape_property(el) for el in root.iter() if _is_shape_property(el)]
    if not props:
        refs = sum(1 for el in root.iter(f"{{{_CORE_NS}}}property") if el.get(f"{{{_RDF_NS}}}resource"))
        if refs:
            raise ValueError(f"Shape '{shape_uri}' lists {refs} properties but none could be extracted")
    return props


async def list_allowed_values(allowed_values_uri: str) -> list[dict]:
    """GET an oslc:allowedValues resource and extract its values.

    Two formats: oslc:allowedValue rdf:resource (URI only), or
    rdf:Description with rdf:about + dcterms:title (URI + label).
    Returns [{"value_uri", "title"}] (title None when absent).
    """
    xml_text = await _cached_xml_text(allowed_values_uri)
    titles: dict[str, str | None] = {}
    # rdf:Description blocks with rdf:about carry a title.
    for block in _re.findall(r"<\w*:?Description\b[^>]*>.*?</\w*:?Description>", xml_text, _re.DOTALL):
        about_m = _re.search(r"rdf:about=\"([^\"]+)\"", block)
        title_m = _re.search(r"<\w*:?title[^>]*>([^<]+)</\w*:?title>", block)
        if about_m:
            titles[about_m.group(1)] = title_m.group(1).strip() if title_m else None
    # oslc:allowedValue rdf:resource lists the values (the real CCM container is itself a Description,
    # whose rdf:about is NOT a value).
    refs = _re.findall(r"<\w*:?allowedValue\s+[^>]*rdf:resource=\"([^\"]+)\"", xml_text)
    if refs:
        return [{"value_uri": uri, "title": titles.get(uri)} for uri in refs]
    return [{"value_uri": uri, "title": title} for uri, title in titles.items()]


async def list_fields(domain: str, project: str, type: str) -> dict:
    """List required/optional fields of a type, with allowedValues when enumerated.

    Resolves the shape by its title (case-insensitive equality) among the shapes
    of the project; for ccm the type is the id, title or short alias (task, story...) of a typed
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
    data = await get_resource(uri)  # a refused read raises ValueError naming the gate

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

    Resolves the RM service provider, reads it as rdf+xml to extract the project
    area URI (oslc:details), then GETs /rm/folders?oslc.where=public_rm:parent=
    <project area> as JSON and parses {folder_uri: {dcterms:title:[{value}]}}.
    (The component URI is rejected by the server with a 400: D3.)
    Only resources typed nav#folder are returned (query/description resources are dropped);
    the root folder is included because it is a valid parent for create_requirement(folder=...).
    Raises ValueError when the service provider announces no project area (no oslc:details).
    Returns [{"title","uri","subfolders_query"}] (subfolders_query is a URI or None; not navigable yet).
    """
    project_url = await _resolve_project_url("rm", project)
    sp_xml = await _get_xml_text(project_url)
    area_m = _re.search(r"<\w*:?details\s+[^>]*rdf:resource=\"([^\"]+)\"", sp_xml)
    if area_m is None:
        raise ValueError(
            f"project area not advertised by the service provider ({project_url}): "
            "the folders cannot be queried (no oslc:details)."
        )
    project_area_uri = area_m.group(1)

    client = await get_client()
    folders_url = f"{settings.elm_url}/rm/folders"
    resp = await fetch(
        client,
        "GET",
        folders_url,
        "list_folders",
        params={"oslc.where": f"public_rm:parent={project_area_uri}"},
        headers={"Accept": "application/json", "OSLC-Core-Version": "2.0"},
    )
    data = resp.json()

    folders: list[dict] = []
    for folder_uri, props in data.items():
        # Keep only real folders (nav#folder): the query answer also carries a ResponseInfo
        # and a nav#Description resource. The root folder is kept: it is a valid parent.
        types = [t.get("value") for t in props.get(_RDF_TYPE, []) if isinstance(t, dict)]
        if _NAV_FOLDER_TYPE not in types:
            continue
        title_entries = props.get("http://purl.org/dc/terms/title", [])
        title = title_entries[0].get("value") if title_entries else None
        sub = props.get(_NAV_SUBFOLDERS, [])
        folders.append({"title": title, "uri": folder_uri, "subfolders_query": sub[0].get("value") if sub else None})
    return folders


# =====================================================================
# CCM collaboration (SPEC-ccm-colaboracao, W2): comments, links, set_field, workflow.
# Every write is a partial PUT (?oslc.properties) with If-Match; every HTTP error goes
# through fetch/_check_put -> diagnose_http_error (named gate).
# Property names were verified read-only on the real server (7.0.1) unless marked
# "to be confirmed in live acceptance".
# =====================================================================

_JSON_HEADERS = {"Accept": "application/json", "OSLC-Core-Version": "2.0"}
_LINKTYPE_PREFIX = "rtc_cm:com.ibm.team.workitem.linktype."

# Tool link type -> work item property (values: list of {"rdf:resource", "dcterms:title"}).
# Inverses on the target side (not written by the tools): parentworkitem.children,
# duplicateworkitem.duplicates, resolvesworkitem.resolvedBy.
LINK_TYPES = {
    "parent": _LINKTYPE_PREFIX + "parentworkitem.parent",
    "blocks": _LINKTYPE_PREFIX + "blocksworkitem.blocks",
    "depends_on": _LINKTYPE_PREFIX + "blocksworkitem.dependsOn",
    "related": _LINKTYPE_PREFIX + "relatedworkitem.related",
    "duplicate_of": _LINKTYPE_PREFIX + "duplicateworkitem.duplicateOf",
    "resolves": _LINKTYPE_PREFIX + "resolvesworkitem.resolves",
}
# A work item has at most one parent: linking a new parent replaces the current one (re-parent).
_SINGLE_VALUED_LINKS = {"parent"}

_COMMENT_CONTENT_TYPE = "application/x-oslc-cm-change-request+json"
# Title of the "not assigned" literal of an enumeration. To be confirmed in live acceptance
# (only the English title was seen in the RED fixtures).
_UNASSIGNED_RE = _re.compile(r"^\s*(unassigned|n[aã]o\s+(atribu[ií]do|designado))\s*$", _re.IGNORECASE)
_WORKFLOW_STATE_RE = _re.compile(r"/oslc/workflows/([^/]+)/states/([^/]+)/([^/?#]+)/?$")
_WORKITEM_ID_RE = _re.compile(r"/(?:com\.ibm\.team\.workitem\.WorkItem|oslc/workitems)/([^/?#]+)/?$")


def workitem_uri(id: str) -> str:
    """Canonical URI of a work item (same form as get_workitem/update_workitem)."""
    return f"{settings.elm_url}/ccm/resource/itemName/com.ibm.team.workitem.WorkItem/{_quote_segment(id)}"


def _quote_segment(value: str) -> str:
    from urllib.parse import quote

    return quote(str(value).strip(), safe="")


def _workitem_key(uri: str) -> str:
    """Work item id of either URL form (itemName/... or oslc/workitems/...); the URI itself otherwise."""
    m = _WORKITEM_ID_RE.search(uri or "")
    return m.group(1) if m else uri


def _resource_uri(value) -> str | None:
    if isinstance(value, dict):
        value = value.get("rdf:resource") or value.get("rdf:about")
    return value if isinstance(value, str) and value else None


def _as_list(value) -> list:
    if value is None:
        return []
    return value if isinstance(value, list) else [value]


async def _read_item(client: httpx.AsyncClient, uri: str, operation: str) -> tuple[dict, str]:
    """GET a work item as JSON; returns (item, ETag or '')."""
    resp = await fetch(client, "GET", uri, operation, headers=_JSON_HEADERS)
    return resp.json(), resp.headers.get("ETag", "")


async def _put_partial(
    client: httpx.AsyncClient, uri: str, etag: str, item: dict, payload: dict, operation: str
) -> None:
    """Partial PUT of ``payload`` (?oslc.properties) conditioned on ``etag`` (If-Match)."""
    if not etag:
        raise ValueError(f"Server did not return an ETag for work item '{uri}'. Cannot safely write.")
    body = dict(payload)
    prefixes = item.get("prefixes")
    if isinstance(prefixes, dict):
        body["prefixes"] = prefixes
    resp = await _request(
        client,
        "PUT",
        uri,
        params={"oslc.properties": ",".join(payload)},
        json=body,
        headers={**_JSON_HEADERS, "Content-Type": "application/json", "If-Match": etag},
    )
    _check_put(resp, operation, uri)


# ---------------------------------------------------------------------------------------------
# Comments
# ---------------------------------------------------------------------------------------------
def comments_uri(id: str) -> str:
    return f"{settings.elm_url}/ccm/oslc/workitems/{_quote_segment(id)}/rtc_cm:comments"


async def _comment_entry(client: httpx.AsyncClient, member) -> dict:
    """{id, author, created, text} of one oslc:comment member (inline, or a reference that is read).

    The body of an individual comment (dcterms:identifier/creator/created/description) is to be
    confirmed in live acceptance.
    """
    if isinstance(member, str):
        member = {"rdf:resource": member}
    if not isinstance(member, dict):
        return {"id": None, "author": None, "created": None, "text": None}
    if set(member) <= {"rdf:resource"} and member.get("rdf:resource"):
        resp = await fetch(client, "GET", member["rdf:resource"], "list_comments", headers=_JSON_HEADERS)
        member = {**resp.json(), "rdf:about": member["rdf:resource"]}
    about = member.get("rdf:about") or member.get("rdf:resource") or ""
    ident = member.get("dcterms:identifier")
    creator = member.get("dcterms:creator")
    return {
        "id": str(ident) if ident is not None else (_last_segment(about) if about else None),
        "author": _resource_uri(creator) or (creator if isinstance(creator, str) else None),
        "created": member.get("dcterms:created"),
        "text": member.get("dcterms:description"),
    }


async def list_comments(id: str) -> list[dict]:
    """Comments of a work item: oslc:discussedBy -> oslc:comment, in thread order."""
    client = await get_client()
    item, _ = await _read_item(client, workitem_uri(id), "list_comments")
    discussion = _resource_uri(item.get("oslc:discussedBy"))
    if not discussion:
        return []  # an item that never had a comment may carry no discussion
    resp = await fetch(client, "GET", discussion, "list_comments", headers=_JSON_HEADERS)
    data = resp.json()
    members = _as_list(data.get("oslc:comment") if isinstance(data, dict) else None)
    return [await _comment_entry(client, m) for m in members]


def _comment_html(text: str) -> str:
    """The comment body the server stores: HTML-escaped, line breaks as <br/>."""
    return _html.escape(text).replace("\r\n", "\n").replace("\r", "\n").replace("\n", "<br/>")


def _plain(value: str | None) -> str:
    """Comparable plain text of a comment (markup stripped, entities decoded, whitespace collapsed)."""
    value = _re.sub(r"<br\s*/?>|</(?:div|p)>", " ", value or "", flags=_re.IGNORECASE)
    value = _re.sub(r"<[^>]+>", "", value)
    return " ".join(_html.unescape(value).split())


async def add_comment(id: str, text: str) -> dict:
    """POST a comment (OSLC CM 1.0) and confirm it through list_comments. The POST is never repeated.

    The text is sent HTML-escaped (the server stores comments as HTML) with line breaks as <br/>.
    Confirmation is by the NEW comment id (ids absent before the POST); when the server's answer does not
    give the id of the created comment, only a new comment with the same normalized text confirms it
    (a lone new comment of other text may be someone else's, so it is not accepted).
    """
    if not text or not text.strip():
        raise ValueError("Comment text must not be empty.")
    before_list = await list_comments(id)
    before = {c["id"] for c in before_list if c["id"] is not None}
    client = await get_client()
    url = comments_uri(id)
    # CM 1.0 on purpose: with OSLC-Core-Version 2.0 this endpoint answers 405.
    resp = await fetch(
        client,
        "POST",
        url,
        "add_comment",
        json={"dc:description": _comment_html(text)},
        headers={"Content-Type": _COMMENT_CONTENT_TYPE, "Accept": "application/json"},
    )
    try:
        created_uri = _resource_uri(resp.json())
    except ValueError:
        created_uri = None
    created_id = _last_segment(created_uri) if created_uri else None

    after = await list_comments(id)
    # Same path on both sides: the server returns the escaped text, so the sent text is compared as stored.
    wanted = _plain(_comment_html(text))
    fresh = [c for c in after if c["id"] is not None and c["id"] not in before]
    # Comments the server gives no id for cannot be told apart by id: the ones past the old count count as new.
    anonymous = [c for c in after[len(before_list) :] if c["id"] is None]
    new: list[dict] = []
    if created_id is not None:
        new = [c for c in fresh if c["id"] == created_id]
    if not new:
        new = [c for c in fresh if _plain(c["text"]) == wanted]
    if not new:
        new = [c for c in anonymous if _plain(c["text"]) == wanted]
    if not new:
        raise ValueError(
            f"The server answered HTTP {resp.status_code} to the comment POST on work item {id}, but the "
            "comment does not appear in the discussion. It was NOT resent (to avoid a duplicate): check the "
            "work item before retrying."
        )
    return new[-1]


# ---------------------------------------------------------------------------------------------
# Links
# ---------------------------------------------------------------------------------------------
def _link_property(link_type: str) -> str:
    prop = LINK_TYPES.get((link_type or "").strip().lower())
    if prop is None:
        raise ValueError(f"Unknown link type '{link_type}'. Valid link types: {', '.join(LINK_TYPES)}")
    return prop


def _linked_keys(item: dict, prop: str) -> list[str]:
    return [_workitem_key(u) for u in (_resource_uri(e) for e in _as_list(item.get(prop))) if u]


async def _change_link(source: str, target: str, link_type: str, *, add: bool) -> dict:
    prop = _link_property(link_type)
    kind = link_type.strip().lower()
    operation = "link_workitems" if add else "unlink_workitems"
    target_key = str(target).strip()
    if not target_key:
        raise ValueError("Target work item id must not be empty.")
    if target_key == str(source).strip():
        raise ValueError(f"Work item {target_key} cannot be linked to itself.")
    client = await get_client()
    uri = workitem_uri(source)
    item, etag = await _read_item(client, uri, operation)
    current = [u for u in (_resource_uri(e) for e in _as_list(item.get(prop))) if u]
    keys = [_workitem_key(u) for u in current]
    if add:
        if target_key in keys and (kind not in _SINGLE_VALUED_LINKS or len(keys) == 1):
            return {"source": source, "target": target, "link_type": kind, "property": prop, "changed": False}
        kept = [] if kind in _SINGLE_VALUED_LINKS else current
        values = [*kept, workitem_uri(target_key)]
    else:
        if target_key not in keys:
            raise ValueError(f"Work item {source} has no '{kind}' link to work item {target_key}; nothing to remove.")
        values = [u for u, k in zip(current, keys, strict=True) if k != target_key]
    await _put_partial(client, uri, etag, item, {prop: [{"rdf:resource": v} for v in values]}, operation)

    reread, _ = await _read_item(client, uri, operation)
    linked = target_key in _linked_keys(reread, prop)
    if linked is not add:
        state = "does not show" if add else "still shows"
        raise ValueError(
            f"The server accepted the write but the re-read of work item {source} {state} the '{kind}' link "
            f"to {target_key} ({prop}). Not retried."
        )
    return {"source": source, "target": target, "link_type": kind, "property": prop, "changed": True}


async def link_workitems(source: str, target: str, link_type: str) -> dict:
    """Add a link source -> target (partial PUT of the link property, existing links preserved)."""
    return await _change_link(source, target, link_type, add=True)


async def unlink_workitems(source: str, target: str, link_type: str) -> dict:
    """Remove only the link source -> target, resending the remaining links of that type."""
    return await _change_link(source, target, link_type, add=False)


# Cross-domain links (SPEC-oslc-links): closed list of oslc_cm properties of the work item shape.
# The write format is the one of link_workitems; to be confirmed in live acceptance.
ARTIFACT_LINK_TYPES = (
    "implementsRequirement",
    "tracksRequirement",
    "affectsRequirement",
    "testedByTestCase",
    "relatedTestCase",
    "relatedTestScript",
    "relatedTestPlan",
    "relatedTestExecutionRecord",
    "affectsPlanItem",
    "affectedByDefect",
    "affectsTestResult",
    "blocksTestExecutionRecord",
    "relatedChangeRequest",
)


_CM_NS = NS["oslc_cm"]


def _artifact_link_property(item: dict, link_type: str) -> str:
    """Key of the ``oslc_cm`` link property in this item, resolved through the item's own ``prefixes`` map.

    A PUT of a property under a key the item does not use would replace nothing and could drop links, so when
    the item neither declares the oslc_cm namespace nor carries the property, the write is refused.
    """
    prefixes = item.get("prefixes")
    declared = [p for p, ns in prefixes.items() if ns == _CM_NS] if isinstance(prefixes, dict) else []
    for prefix in declared:
        if f"{prefix}:{link_type}" in item:
            return f"{prefix}:{link_type}"
    if declared:
        return f"{declared[0]}:{link_type}"
    if f"oslc_cm:{link_type}" in item:
        return f"oslc_cm:{link_type}"
    raise ValueError(
        f"The work item does not declare the oslc_cm namespace ({_CM_NS}) in its prefixes and has no "
        f"'oslc_cm:{link_type}' property: the link was NOT written (a PUT could erase existing links)."
    )


def _is_workitem_uri(uri: str) -> bool:
    path = urlparse(uri).path
    return "/ccm/" in path and _WORKITEM_ID_RE.search(path) is not None


async def _change_artifact_link(source_uri: str, target_uri: str, link_type: str, *, add: bool) -> dict:
    operation = "link_artifacts" if add else "unlink_artifacts"
    if link_type not in ARTIFACT_LINK_TYPES:
        raise ValueError(f"Unknown link type '{link_type}'. Valid link types: {', '.join(ARTIFACT_LINK_TYPES)}")
    source, target = (source_uri or "").strip(), (target_uri or "").strip()
    if not target:
        raise ValueError("Target URI must not be empty.")
    assert_same_origin(source)
    assert_same_origin(target)
    if not _is_workitem_uri(source):
        raise ValueError(
            f"The source {source} is not a CCM work item (expected .../ccm/.../com.ibm.team.workitem.WorkItem/<id> "
            "or .../ccm/oslc/workitems/<id>). Only work items carry these links."
        )
    if source == target:
        raise ValueError(f"{source} cannot be linked to itself.")
    client = await get_client()
    item, etag = await _read_item(client, source, operation)
    prop = _artifact_link_property(item, link_type)
    result = {"source_uri": source, "target_uri": target, "link_type": link_type, "property": prop}
    current = [u for u in (_resource_uri(e) for e in _as_list(item.get(prop))) if u]
    if add:
        if target in current:
            return {**result, "changed": False}
        values = [*current, target]
    else:
        if target not in current:
            raise ValueError(f"{source} has no '{link_type}' link to {target}; nothing to remove.")
        values = [u for u in current if u != target]
    await _put_partial(client, source, etag, item, {prop: [{"rdf:resource": v} for v in values]}, operation)

    reread, _ = await _read_item(client, source, operation)
    final = {u for u in (_resource_uri(e) for e in _as_list(reread.get(prop))) if u}
    if (target in final) is not add:
        state = "does not show" if add else "still shows"
        raise ValueError(
            f"The server accepted the write but the re-read of {source} {state} the '{link_type}' link "
            f"to {target} ({prop}). Not retried."
        )
    expected = set(values)
    if final != expected:
        lost, extra = sorted(expected - final), sorted(final - expected)
        detail = (f" Lost targets: {', '.join(lost)}." if lost else "") + (
            f" Unexpected targets: {', '.join(extra)}." if extra else ""
        )
        raise ValueError(
            f"The server accepted the write but the re-read of {source} does not match the expected "
            f"'{link_type}' targets ({prop}).{detail} Not retried: check the work item."
        )
    return {**result, "changed": True}


async def link_artifacts(source_uri: str, target_uri: str, link_type: str) -> dict:
    """Add an oslc_cm link from a work item to an artifact (partial PUT, existing targets preserved)."""
    return await _change_artifact_link(source_uri, target_uri, link_type, add=True)


async def unlink_artifacts(source_uri: str, target_uri: str, link_type: str) -> dict:
    """Remove only the link source -> target, resending the remaining targets of that type."""
    return await _change_artifact_link(source_uri, target_uri, link_type, add=False)


# ---------------------------------------------------------------------------------------------
# set_field
# ---------------------------------------------------------------------------------------------
def _expand(name: str, prefixes: dict) -> str | None:
    prefix, sep, local = name.partition(":")
    ns = prefixes.get(prefix) if sep else None
    return f"{ns}{local}" if ns else None


def _compact(uri: str, prefixes: dict) -> str | None:
    best = max(((p, ns) for p, ns in prefixes.items() if uri.startswith(ns)), key=lambda x: len(x[1]), default=None)
    return f"{best[0]}:{uri[len(best[1]) :]}" if best else None


def _match_literal(value: str, allowed: list[dict]) -> dict | None:
    wanted = value.strip()
    for a in allowed:
        if a["value_uri"] == wanted or _last_segment(a["value_uri"]) == wanted:
            return a
    lowered = wanted.lower()
    return next((a for a in allowed if (a["title"] or "").strip().lower() == lowered), None)


_FIELD_QNAME_RE = _re.compile(r"^[A-Za-z_][\w.-]*:[\w.-]+$")
_FIELD_NAME_RE = _re.compile(r"^[A-Za-z_][\w.-]*$")
_INTEGER_RANGES = {
    "integer": (None, None),
    "int": (None, None),
    "long": (None, None),
    "short": (None, None),
    "byte": (None, None),
    "negativeinteger": (None, -1),
    "nonpositiveinteger": (None, 0),
    "nonnegativeinteger": (0, None),
    "positiveinteger": (1, None),
    "unsignedint": (0, None),
    "unsignedlong": (0, None),
    "unsignedshort": (0, None),
    "unsignedbyte": (0, None),
}
_DECIMAL_RANGES = {"decimal", "double", "float"}
_INTEGER_RE = _re.compile(r"[+-]?[0-9]+")
_DECIMAL_RE = _re.compile(r"[+-]?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)(?:[eE][+-]?[0-9]+)?")


def _convert_to_range(key: str, value: str, range_uri: str | None) -> object:
    """Convert ``value`` to the XSD range the shape declares (integer, boolean, decimal); a string otherwise.

    Integers and decimals are plain ASCII (no underscores, no Unicode digits) and integers respect the
    sign of their range (nonNegativeInteger, negativeInteger...).
    """
    kind = _last_segment(range_uri).lower() if range_uri else ""
    text = str(value).strip()
    try:
        if kind in _INTEGER_RANGES:
            if not _INTEGER_RE.fullmatch(text):
                raise ValueError(text)
            number = int(text)
            low, high = _INTEGER_RANGES[kind]
            if (low is not None and number < low) or (high is not None and number > high):
                raise ValueError(text)
            return number
        if kind == "boolean":
            if text.lower() not in ("true", "false"):
                raise ValueError(text)
            return text.lower() == "true"
        if kind in _DECIMAL_RANGES:
            if not _DECIMAL_RE.fullmatch(text):
                raise ValueError(text)
            number = float(text)
            if not math.isfinite(number):
                raise ValueError(text)
            return number
    except ValueError:
        raise ValueError(f"Value '{value}' is not a valid {kind} for {key}.") from None
    return value


async def set_field(id: str, field: str, value: str) -> dict:
    """Write one field with a partial PUT; an enumerated field takes the literal title or id.

    ``field`` is the item's prefixed property name (rtc_cm:storyPoints) or the shape's property
    name (storyPoints); anything else (commas, braces, spaces) is refused, as is a field that is not
    in the item's shape or that the shape marks read-only. The value is converted to the property's
    range (integer, boolean, decimal; a string otherwise). The value of an enumerated property is
    validated against its allowedValues (shape of the allowedValues document: to be confirmed in
    live acceptance).
    """
    field = (field or "").strip()
    if not field:
        raise ValueError("Field must not be empty.")
    if not (_FIELD_QNAME_RE.match(field) if ":" in field else _FIELD_NAME_RE.match(field)):
        raise ValueError(
            f"Invalid field name {field!r}: use prefix:name (e.g. rtc_cm:storyPoints) or a shape property name."
        )
    client = await get_client()
    uri = workitem_uri(id)
    item, etag = await _read_item(client, uri, "set_field")
    prefixes = {**NS, "rtc_cm": "http://jazz.net/xmlns/prod/jazz/rtc/cm/1.0/"}
    if isinstance(item.get("prefixes"), dict):
        prefixes.update(item["prefixes"])

    shape = _resource_uri(item.get("oslc:instanceShape"))
    if not shape:
        raise ValueError(
            f"Work item {id} has no instance shape: field '{field}' cannot be validated (use list_fields)."
        )
    props = await list_shape_properties(shape)
    expanded = _expand(field, prefixes)
    prop = next((p for p in props if expanded and p["property_uri"] == expanded), None)
    if prop is None:
        prop = next((p for p in props if p["name"] == field), None)
    if prop is None:
        names = sorted(p["name"] for p in props if p["name"])
        raise ValueError(
            f"Unknown field '{field}': it is not in this work item's shape. Use list_fields to see the valid fields "
            f"({names})."
        )
    key = field
    if ":" not in field:
        key = _compact(prop["property_uri"] or "", prefixes) or ""
        if not key:
            raise ValueError(f"Cannot map shape property '{field}' ({prop['property_uri']}) to a prefixed name.")
    if prop["read_only"]:
        raise ValueError(f"Field '{key}' is read-only in this work item's shape and cannot be written.")

    warnings: list[str] = []
    written: object = value
    if not prop["allowed_values_uri"]:
        written = _convert_to_range(key, value, prop["range_uri"])
    else:
        allowed = await list_allowed_values(prop["allowed_values_uri"])
        literal = _match_literal(value, allowed)
        if literal is None:
            titles = [a["title"] or _last_segment(a["value_uri"]) for a in allowed]
            raise ValueError(f"Value '{value}' is not allowed for {key}. Valid values: {titles}")
        written = {"rdf:resource": literal["value_uri"]}
        if prop["required"] and _UNASSIGNED_RE.match(literal["title"] or ""):
            warnings.append(
                f"'{literal['title']}' (Unassigned) was written to the required attribute {key}: the server "
                "treats this literal as empty, so the attribute still counts as not filled."
            )
    await _put_partial(client, uri, etag, item, {key: written}, "set_field")
    return {"id": id, "field": key, "value": written, "warnings": warnings}


# ---------------------------------------------------------------------------------------------
# list_workflow
# ---------------------------------------------------------------------------------------------
def _is_state_property(p: dict) -> bool:
    return p["property_uri"] == "http://jazz.net/xmlns/prod/jazz/rtc/cm/1.0/state" or p["name"] == "state"


_ID_RE = _re.compile(r"[A-Za-z0-9._:-]+")
_STATE_ID_RE = _re.compile(r"[A-Za-z0-9_:-]+(\.[A-Za-z0-9_:-]+)+")


def _checked_id(name: str, value: str) -> str:
    """Id used inside an oslc.where clause: ASCII letters, digits, '.', '_', ':' and '-' only (checked AFTER unquote)."""
    if not _ID_RE.fullmatch(value):
        raise ValueError(f"Filter '{name}' has an invalid id (only A-Z a-z 0-9 . _ : - are allowed): {value!r}")
    return value


async def _cached_json(project_area: str, url: str) -> dict:
    """GET a workflow document as JSON through the discovery cache (keyed by project area and URL)."""

    async def load() -> dict:
        client = await get_client()
        return (await fetch(client, "GET", url, "list_workflow", headers=_JSON_HEADERS)).json()

    return await cache.get_or_fetch(project_area, f"json:{url}", load)


async def _workflow_states(factory: dict) -> tuple[str, str, list[dict]]:
    """(area, workflow, states) of a type; states carry id, title and group. Does not read the actions.

    Verified live: the shape's ``state`` property points at ``.../shapes/workitems/{type}/property/
    internalState/allowedValues``, whose values are the state URIs
    ``/oslc/workflows/{pa}/states/{wf}/{state}``. Area and workflow come from those URIs; titles and
    groups from ``.../states/{wf}`` (when that document cannot be read, the state ids are still returned).
    Ids that are not a valid id after unquote are dropped (never reach an oslc.where).
    """
    if not factory["shape_uri"]:
        raise ValueError(f"Type '{factory['title']}' has no resource shape: its workflow cannot be resolved.")
    props = await list_shape_properties(factory["shape_uri"])
    allowed_url = next(
        (p["allowed_values_uri"] for p in props if _is_state_property(p) and p["allowed_values_uri"]), None
    )
    state_uris = [v["value_uri"] for v in await list_allowed_values(allowed_url)] if allowed_url else []
    matches = [m for m in (_WORKFLOW_STATE_RE.search(u) for u in state_uris) if m]
    if not matches:
        raise ValueError(
            f"Type '{factory['title']}' does not expose its workflow (no state property whose allowed values "
            "are /oslc/workflows/.../states/... URIs)."
        )
    area, workflow = matches[0].group(1), matches[0].group(2)
    state_ids = [unquote(m.group(3)) for m in matches if (m.group(1), m.group(2)) == (area, workflow)]
    state_ids = [sid for sid in state_ids if _ID_RE.fullmatch(sid)]
    states_url = f"{settings.elm_url}/ccm/oslc/workflows/{area}/states/{workflow}"
    states: list[dict] = []
    try:
        states_doc = await _cached_json(area, states_url)
        for s in _page_members(states_doc):
            if not isinstance(s, dict):
                continue
            about = s.get("rdf:about") or ""
            sid = s.get("dcterms:identifier") or (_last_segment(about) if about else None)
            states.append(
                {
                    "id": sid if isinstance(sid, str) and _ID_RE.fullmatch(sid) else None,
                    "title": s.get("dcterms:title"),
                    "group": s.get("rtc_cm:group"),
                }
            )
    except ValueError:
        states = []
    if not states:
        states = [{"id": sid, "title": None, "group": None} for sid in state_ids]
    return area, workflow, states


async def _workflow_of(factory: dict) -> dict:
    """Workflow of a type (resolved factory): states (id, title, group) and action ids."""
    area, workflow, states = await _workflow_states(factory)
    actions_url = f"{settings.elm_url}/ccm/oslc/workflows/{area}/actions/{workflow}"
    actions_doc = await _cached_json(area, actions_url)
    return {
        "type": factory["title"],
        "workflow": workflow,
        "states": states,
        "actions": _action_ids(actions_doc),
    }


async def list_workflow(project: str, type: str) -> dict:
    """States (id, title, group) and action ids of the workflow of a work item type."""
    project_url = await _resolve_project_url("ccm", project)
    factory = _resolve_ccm_type(await _list_ccm_creation_factories(project_url), type)
    return await _workflow_of(factory)


# ---------------------------------------------------------------------------------------------
# list_workitems filters (oslc.where). Verified live (ELM 7.0.1): rtc_cm:type="<type id>" and
# rtc_cm:state="<full state id>" filter; dcterms:type="<title>" matches nothing, rtc_cm:state="<title>"
# matches nothing and oslc_cm:status is an HTTP 400 in oslc.where; dcterms:contributor only as a user URI;
# rtc_cm:plannedFor=<iteration URI> is the documented form (not verified live).
# ---------------------------------------------------------------------------------------------
def _safe_filter_value(name: str, value: str) -> str:
    value = str(value).strip()
    if not value:
        raise ValueError(f"Filter '{name}' must not be empty.")
    if any(c in value for c in '"<>\\') or not value.isprintable():
        raise ValueError(
            f"Filter '{name}' has a forbidden character (quote, angle bracket, backslash or non-printable): {value!r}"
        )
    return value


def _is_uri(value: str) -> bool:
    return value.startswith(("http://", "https://")) and " " not in value


async def _state_filter_id(state: str, factory: dict | None) -> str:
    """State id for rtc_cm:state, always validated as an id after unquote (never a free string).

    A state URI gives its last segment. With ``type`` the value must be an id or a title of the type's
    workflow (exact id > exact title; ambiguous = error). Without ``type`` only an id-shaped value
    (dotted, no spaces) is accepted; anything else asks for the id (list_workflow).
    """
    if _is_uri(state):
        return _checked_id("state", unquote(_last_segment(state)))
    hint = "Pass the state id instead (use list_workflow to see the ids of the type's states)."
    if factory is None:
        if _STATE_ID_RE.fullmatch(state):
            return state
        raise ValueError(
            f"State '{state}' is not a state id, and a title can only be resolved together with `type`. {hint}"
        )
    states = (await _workflow_states(factory))[2]
    by_id = {s["id"] for s in states if s["id"] == state}
    by_title = {s["id"] for s in states if s["id"] and (s["title"] or "").strip().lower() == state.lower()}
    for found in (by_id, by_title):
        if len(found) == 1:
            return _checked_id("state", next(iter(found)))
        if len(found) > 1:
            raise ValueError(f"State '{state}' is ambiguous in the workflow of type '{factory['title']}'. {hint}")
    raise ValueError(f"State '{state}' was not found in the workflow of type '{factory['title']}'. {hint}")


async def workitem_filters(
    project: str,
    type: str | None = None,
    state: str | None = None,
    owner: str | None = None,
    planned_for: str | None = None,
) -> list[str]:
    """oslc.where clauses of the list_workitems filters (validated against clause injection)."""
    if type is not None:
        type = _safe_filter_value("type", type)
    if state is not None:
        state = _safe_filter_value("state", state)
    if owner is not None:
        owner = _safe_filter_value("owner", owner)
    if planned_for is not None:
        planned_for = _safe_filter_value("planned_for", planned_for)
        if not _is_uri(planned_for):
            raise ValueError(f"planned_for must be an iteration URI (see list_iterations), got {planned_for!r}")
    clauses: list[str] = []
    factory = None
    if type is not None:
        project_url = await _resolve_project_url("ccm", project)
        factory = _resolve_ccm_type(await _list_ccm_creation_factories(project_url), type)
        clauses.append(f'rtc_cm:type="{_checked_id("type", factory["id"])}"')
    if state is not None:
        clauses.append(f'rtc_cm:state="{await _state_filter_id(state, factory)}"')
    if owner is not None:
        user = owner if _is_uri(owner) else f"{settings.elm_url}/jts/users/{_quote_segment(owner)}"
        clauses.append(f"dcterms:contributor=<{user}>")
    if planned_for is not None:
        clauses.append(f"rtc_cm:plannedFor=<{planned_for}>")
    return clauses


# ---------------------------------------------------------------------------------------------
# Work item creation: duplicate check, required fields (SPEC-idempotencia, SPEC-perfil-template)
# ---------------------------------------------------------------------------------------------
_PARENT_LINK = "rtc_cm:com.ibm.team.workitem.linktype.parentworkitem.parent"
_WORKITEM_QUERY_SELECT = f"dcterms:title,dcterms:identifier,dcterms:type,rtc_cm:type,{_PARENT_LINK}"
_CCM_PREFIXES = {
    **{name: NS[name] for name in ("dcterms", "rdf", "oslc", "oslc_cm")},
    "rtc_cm": "http://jazz.net/xmlns/prod/jazz/rtc/cm/1.0/",
    "rtc_ext": "http://jazz.net/xmlns/prod/jazz/rtc/ext/1.0/",
}
# Properties create_workitem always sends itself: never asked from the caller or the profile.
_SELF_FILLED = frozenset({"dcterms:title", "dcterms:type", "rtc_cm:type"})


async def find_workitem_duplicates(project: str, title: str, type_id: str, parent_id: str | None) -> list[dict]:
    """Work items of the project with the same normalized title, type and parent (none when no parent is given)."""
    results = await query_resources(
        "ccm", project, f"dcterms:title={_where_literal(title.strip())}", select=_WORKITEM_QUERY_SELECT
    )
    wanted = normalize_title(title)
    found = []
    for item in results:
        if normalize_title(item.get("dcterms:title")) != wanted:
            continue
        type_uri = _resource_uri(item.get("rtc_cm:type"))
        item_type = _last_segment(type_uri) if type_uri else item.get("dcterms:type")
        if item_type != type_id:
            continue
        parents = {_workitem_key(u) for u in map(_resource_uri, _as_list(item.get(_PARENT_LINK))) if u}
        if str(parent_id).strip() not in parents if parent_id else parents:
            continue
        found.append(
            {
                "rdf:about": item.get("rdf:about", ""),
                "dcterms:identifier": item.get("dcterms:identifier"),
                "dcterms:title": item.get("dcterms:title"),
            }
        )
    return found


def _required_shape_fields(props: list[dict]) -> list[dict]:
    """Required, writable properties of a shape, each with the prefixed ``key`` used in payloads."""
    fields = []
    for p in props:
        if not p["required"] or p["read_only"] or not p["property_uri"]:
            continue
        key = _compact(p["property_uri"], _CCM_PREFIXES)
        if key is None or key in _SELF_FILLED:
            continue
        fields.append({**p, "key": key})
    return fields


async def missing_required_fields(factory: dict, payload: dict) -> list[str] | None:
    """Required shape properties without default value that ``payload`` does not carry.

    Returned as "key (title)" strings; None when the shape cannot be read (the server then validates).
    """
    if not factory.get("shape_uri"):
        return None
    try:
        props = await list_shape_properties(factory["shape_uri"])
    except (ValueError, httpx.HTTPError) as exc:
        logger.warning(
            "create_workitem: shape of %s unreadable (%s); required fields not pre-checked", factory["id"], exc
        )
        return None
    return [
        f"{f['key']} ({f['title'] or f['name']})"
        for f in _required_shape_fields(props)
        if not f["default_value"] and f["key"] not in payload
    ]


async def build_profile_draft(project: str) -> dict:
    """A profile draft from the discovery: per type, its required fields (value null) and the allowed values.

    Read-only. ``defaults`` is the part a person fills in; ``_review`` lists the allowed values to choose from
    and is ignored when the profile is loaded.
    """
    defaults: dict = {}
    review: dict = {}
    for factory in await list_resource_shapes("ccm", project):
        if not factory["shape_uri"]:
            continue
        values: dict = {}
        notes: dict = {}
        for field in _required_shape_fields(await list_shape_properties(factory["shape_uri"])):
            default = field["default_value"]
            values[field["key"]] = {"rdf:resource": default} if default else None
            allowed = await list_allowed_values(field["allowed_values_uri"]) if field["allowed_values_uri"] else []
            notes[field["key"]] = {"title": field["title"], "allowed_values": allowed}
        defaults[factory["id"]] = values
        review[factory["id"]] = notes
    return {"defaults": defaults, "action_aliases": {}, "hidden_states": [], "_review": review}
