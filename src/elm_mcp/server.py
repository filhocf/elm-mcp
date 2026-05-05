"""
MCP Server para IBM ELM - ALM Dataprev
Expõe work items, requisitos e projetos do ELM como tools MCP.
"""
import os
import json
import base64
from mcp.server.fastmcp import FastMCP

import elmclient.server as elmserver
import elmclient.rdfxml as rdfxml

mcp = FastMCP("elm", instructions="IBM ELM (ALM Dataprev)")

JAZZ_HOST = os.environ.get("ELM_HOST", "https://alm.dataprev.gov.br")
CRED_FILE = os.path.expanduser(os.environ.get("ELM_CREDS_FILE", "~/.elm_creds.json"))

PREFIXES = {
    "http://purl.org/dc/terms/": "dcterms",
    "http://open-services.net/ns/cm#": "oslc_cm",
}


def _safe_str(val) -> str:
    """Converte valor para string UTF-8 segura, tratando bytes Latin1/CP1252."""
    if isinstance(val, bytes):
        try:
            return val.decode('utf-8')
        except UnicodeDecodeError:
            return val.decode('cp1252', errors='replace')
    if isinstance(val, str):
        try:
            val.encode('utf-8')
            return val
        except UnicodeEncodeError:
            return val.encode('cp1252', errors='replace').decode('utf-8', errors='replace')
    return str(val)

# --- Conexão lazy (singleton) ---
_connections: dict = {}


def _load_creds() -> tuple[str, str]:
    # Prefer env vars over file (both must be set)
    username = os.environ.get("ELM_USERNAME")
    password = os.environ.get("ELM_PASSWORD")
    if username and password:
        return username, password
    if username or password:
        raise RuntimeError(
            "Both ELM_USERNAME and ELM_PASSWORD must be set together. "
            "Only one was provided."
        )

    if not os.path.exists(CRED_FILE):
        raise RuntimeError(
            f"Credenciais não encontradas. Configure ELM_USERNAME/ELM_PASSWORD "
            f"ou crie {CRED_FILE}"
        )
    with open(CRED_FILE) as f:
        data = json.load(f)
    return data["username"], base64.b64decode(data["password"]).decode()


def _get_server():
    if "server" not in _connections:
        username, password = _load_creds()
        _connections["server"] = elmserver.JazzTeamServer(
            JAZZ_HOST, username, password,
            verifysslcerts=True,
            jtsappstring="jts",
            appstring="ccm",
            cachingcontrol=0,
        )
    return _connections["server"]


def _get_app(domain: str = "ccm"):
    key = f"app_{domain}"
    if key not in _connections:
        server = _get_server()
        _connections[key] = server.find_app(domain, ok_to_create=True)
    return _connections[key]


# --- Tools ---

@mcp.tool()
def list_projects(domain: str = "ccm") -> str:
    """Lista projetos acessíveis no ELM.
    domain: 'ccm' (EWM/work items), 'rm' (DOORS Next/requisitos), 'qm' (ETM/testes)
    """
    app = _get_app(domain)
    app._load_projects()
    projects = []
    for uri, info in app._projects.items():
        if isinstance(info, dict):
            projects.append({"name": _safe_str(info.get("name", "?")), "uri": _safe_str(uri)})
    return json.dumps(projects, ensure_ascii=False, indent=2)


@mcp.tool()
def list_workitems(
    project_name: str = "MEU IMOVEL RURAL (MIR)",
    pagesize: int = 30,
    query: str = "",
) -> str:
    """Lista work items de um projeto EWM.
    project_name: nome do projeto
    pagesize: quantidade de resultados (default 30)
    query: filtro OSLC opcional (ex: dcterms:type="task")
    """
    app = _get_app("ccm")
    project = app.find_project(project_name)
    if not project:
        return json.dumps({"error": f"Projeto '{project_name}' não encontrado"})

    services_xml = project.get_services_xml()
    qcaps = rdfxml.xml_find_elements(
        services_xml,
        ".//{http://open-services.net/ns/core#}QueryCapability"
    )
    query_base = None
    for qc in qcaps:
        qb = rdfxml.xmlrdf_get_resource_uri(qc, "oslc:queryBase")
        if qb:
            query_base = qb
            break

    if not query_base:
        return json.dumps({"error": "Query capability não encontrada"})

    kwargs = dict(
        select=["dcterms:identifier", "dcterms:title"],
        orderbys=["-dcterms:modified"],
        prefixes=PREFIXES,
        pagesize=pagesize,
    )
    if query:
        kwargs["whereterms"] = query

    results = project.execute_oslc_query(query_base, **kwargs)

    items = []
    for uri, attrs in results.items():
        items.append({
            "id": _safe_str(attrs.get("dcterms:identifier", "?")),
            "title": _safe_str(attrs.get("dcterms:title", "?")),
            "uri": _safe_str(uri),
        })
    return json.dumps(items, ensure_ascii=False, indent=2)


@mcp.tool()
def get_workitem(
    project_name: str = "MEU IMOVEL RURAL (MIR)",
    workitem_id: str = "",
) -> str:
    """Busca detalhes de um work item específico pelo ID.
    project_name: nome do projeto
    workitem_id: identificador do work item (ex: '615472')
    """
    app = _get_app("ccm")
    project = app.find_project(project_name)
    if not project:
        return json.dumps({"error": f"Projeto '{project_name}' não encontrado"})

    services_xml = project.get_services_xml()
    qcaps = rdfxml.xml_find_elements(
        services_xml,
        ".//{http://open-services.net/ns/core#}QueryCapability"
    )
    query_base = None
    for qc in qcaps:
        qb = rdfxml.xmlrdf_get_resource_uri(qc, "oslc:queryBase")
        if qb:
            query_base = qb
            break

    if not query_base:
        return json.dumps({"error": "Query capability não encontrada"})

    results = project.execute_oslc_query(
        query_base,
        whereterms=[["dcterms:identifier", "=", f'"{workitem_id}"']],
        select=["*"],
        prefixes=PREFIXES,
        pagesize=1,
    )

    if not results:
        return json.dumps({"error": f"Work item {workitem_id} não encontrado"})

    uri, attrs = next(iter(results.items()))
    # Converter valores para strings UTF-8 seguras
    clean = {"uri": _safe_str(uri)}
    for k, v in attrs.items():
        clean[_safe_str(k)] = _safe_str(v)
    return json.dumps(clean, ensure_ascii=False, indent=2)


if __name__ == "__main__":
    mcp.run()


def main():
    """Entry point for elm-mcp CLI."""
    mcp.run()

if __name__ == "__main__":
    main()

