"""Shared tenant-neutral fixtures for the W4 tests (RM query, cache, idempotency, profile).

Neutral values only: https://elm.example.com, project area _PA1, user jdoe, project "MY PROJECT (KEY)".
Every route lives on a respx router (a request without a route raises); nothing here reads
~/.elm_creds.json or reaches a real server.

The query fakes answer from a small in-memory table and ENFORCE what the real server enforces:
  * RM: HTTP 400 without ``Configuration-Context`` (when ``require_context``), HTTP 400 when a prefix used in
    ``oslc.select``/``oslc.where`` is not declared in ``oslc.prefix``, and only the properties named in
    ``oslc.select`` come back (plus rdf:type). ``oslc.where`` is NOT evaluated: a duplicate check has to
    compare the returned properties itself.
  * CCM: only the properties named in ``oslc.select`` come back (plus rdf:about); ``oslc.where`` is ignored.
"""

import json
import re
from pathlib import Path
from urllib.parse import urlencode

import httpx
import respx

from . import f1_fixtures as f
from . import w1_fixtures as w1
from . import w2_fixtures as w2

BASE = w1.BASE
PROJECT = w1.PROJECT
PA = f.PA

FIXTURES = Path(__file__).resolve().parent / "fixtures"
RM_PAGE_FIXTURE = FIXTURES / "rm_query_page.json"

# ---------------------------------------------------------------------------------------------
# RM: configuration (stream) discovery + query
# ---------------------------------------------------------------------------------------------
CONFIGS_URL = f"{BASE}/rm/cm/component/_compA/configurations"
STREAM_1 = f"{BASE}/rm/cm/stream/_STREAM1"
STREAM_2 = f"{BASE}/rm/cm/stream/_STREAM2"
VIEWS_URL = f"{BASE}/rm/views"
FOLDER_A = f"{BASE}/rm/folders/FR_A"
FOLDER_B = f"{BASE}/rm/folders/FR_B"

RDF = "http://www.w3.org/1999/02/22-rdf-syntax-ns#"
PREFIX_URIS = {
    "dcterms": "http://purl.org/dc/terms/",
    "oslc": "http://open-services.net/ns/core#",
    "rdf": RDF,
    "nav": "http://jazz.net/ns/rm/navigation#",
    "jazz_rm": "http://jazz.net/ns/rm#",
}
RM_REQUIREMENT = "http://open-services.net/ns/rm#Requirement"
RM_RESPONSE_INFO = "http://open-services.net/ns/core#ResponseInfo"


def services_xml_without_configuration() -> str:
    """The RM service provider document with no oslc_config:component."""
    xml = re.sub(r"\s*<oslc_config:component[^>]*/>", "", w1.SERVICES_XML)
    assert "oslc_config:component" not in xml
    return xml


def mock_rm_config(streams: list[str] | None, *, services: str | None = None) -> dict:
    """RM discovery + component document + configurations document listing ``streams`` (rdfs:member).

    ``streams=None`` registers no component/configurations routes (use with services_xml_without_configuration).
    Returns the routes {"component", "configurations"}.
    """
    w1.mock_rm_discovery()
    if services is not None:
        respx.get(w1.SERVICES_URL).mock(return_value=httpx.Response(200, text=services))
    routes: dict = {}
    if streams is None:
        return routes
    component_xml = f"""<?xml version="1.0" encoding="UTF-8"?>
<rdf:RDF xmlns:rdf="{RDF}" xmlns:oslc_config="http://open-services.net/ns/config#">
    <oslc_config:Component rdf:about="{w1.COMPONENT_URI}">
        <oslc_config:configurations rdf:resource="{CONFIGS_URL}"/>
    </oslc_config:Component>
</rdf:RDF>
"""
    members = "".join(f'<rdfs:member rdf:resource="{s}"/>' for s in streams)
    configs_xml = f"""<?xml version="1.0" encoding="UTF-8"?>
<rdf:RDF xmlns:rdf="{RDF}" xmlns:rdfs="http://www.w3.org/2000/01/rdf-schema#">
    <rdf:Description rdf:about="{CONFIGS_URL}">{members}</rdf:Description>
</rdf:RDF>
"""
    routes["component"] = respx.get(w1.COMPONENT_URI).mock(return_value=httpx.Response(200, text=component_xml))
    routes["configurations"] = respx.get(CONFIGS_URL).mock(return_value=httpx.Response(200, text=configs_xml))
    return routes


def rm_req(n: int, title: str, *, shape: str = w1.SHAPE_REQ, folder: str | None = None) -> dict:
    return {
        "uri": f"{BASE}/rm/resources/TX_REQ{n}",
        "title": title,
        "identifier": str(1000 + n),
        "shape": shape,
        "folder": folder,
        "modified": "2026-01-02T03:04:05.000Z",
    }


def _parse_prefix(raw: str | None) -> dict[str, str]:
    """oslc.prefix 'a=<uri>,b=<uri>' -> {a: uri}."""
    return {m.group(1): m.group(2) for m in re.finditer(r"(\w+)=<([^>]*)>", raw or "")}


def _used_prefixes(clause: str | None) -> set[str]:
    """Prefixes used in an oslc.select/oslc.where text (angle-bracket URIs and quoted strings removed)."""
    text = re.sub(r"<[^>]*>|\"[^\"]*\"", "", clause or "")
    return set(re.findall(r"\b([A-Za-z_]\w*):[A-Za-z_]", text))


class FakeRmQuery:
    """The RM query endpoint (/rm/views): RDF/JSON pages of requirements with container and ResponseInfo."""

    def __init__(
        self,
        rows: list[dict],
        *,
        require_context: bool = True,
        page_size: int = 2,
        total_count: int | None = None,
        next_override: dict[int, str] | None = None,
        fail: httpx.Response | None = None,
    ) -> None:
        self.rows = rows
        self.require_context = require_context
        self.page_size = page_size
        self.total_count = total_count
        self.next_override = next_override or {}
        self.fail = fail
        self.requests: list[httpx.Request] = []
        self.route = respx.get(VIEWS_URL).mock(side_effect=self._serve)

    @property
    def headers_seen(self) -> list[str | None]:
        return [r.headers.get("Configuration-Context") for r in self.requests]

    def _serve(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if self.fail is not None:
            return self.fail
        params = request.url.params
        if self.require_context and not request.headers.get("Configuration-Context"):
            return httpx.Response(
                400,
                text="localConfiguration and globalConfigurationUri are null",
                headers={"Content-Type": "text/plain"},
            )
        declared = _parse_prefix(params.get("oslc.prefix"))
        select = params.get("oslc.select") or ""
        for clause in (select, params.get("oslc.where")):
            undeclared = _used_prefixes(clause) - set(declared)
            if undeclared:
                return httpx.Response(400, text=f"Undefined namespace prefix: {min(undeclared)}")
        selected: set[str] = set()
        for token in filter(None, (t.strip() for t in select.split(","))):
            prefix, _, local = token.partition(":")
            selected.add(declared[prefix] + local)
        page = int(params.get("page", "1"))
        chunk = self.rows[(page - 1) * self.page_size : page * self.page_size]
        body: dict = {}
        members = []
        for row in chunk:
            props: dict = {RDF + "type": [{"type": "uri", "value": RM_REQUIREMENT}]}
            values = {
                PREFIX_URIS["dcterms"] + "title": ("literal", row["title"]),
                PREFIX_URIS["dcterms"] + "identifier": ("literal", row["identifier"]),
                PREFIX_URIS["dcterms"] + "modified": ("literal", row["modified"]),
                PREFIX_URIS["oslc"] + "instanceShape": ("uri", row["shape"]),
                PREFIX_URIS["nav"] + "parent": ("uri", row["folder"]),
            }
            for key, (kind, value) in values.items():
                if key in selected and value is not None:
                    props[key] = [{"type": kind, "value": value}]
            body[row["uri"]] = props
            members.append({"type": "uri", "value": row["uri"]})
        body[f"{BASE}/rm/viewsnull"] = {"http://www.w3.org/2000/01/rdf-schema#member": members}
        info: dict = {
            RDF + "type": [{"type": "uri", "value": RM_RESPONSE_INFO}],
            "http://purl.org/dc/terms/title": [{"type": "literal", "value": f"Query Results: {len(self.rows)}"}],
        }
        if self.total_count is not None:
            info[PREFIX_URIS["oslc"] + "totalCount"] = [{"type": "literal", "value": str(self.total_count)}]
        if page in self.next_override:
            info[PREFIX_URIS["oslc"] + "nextPage"] = [{"type": "uri", "value": self.next_override[page]}]
        elif page * self.page_size < len(self.rows):
            nxt = {k: v for k, v in params.items() if k != "page"}
            nxt["page"] = str(page + 1)
            info[PREFIX_URIS["oslc"] + "nextPage"] = [{"type": "uri", "value": f"{VIEWS_URL}?{urlencode(nxt)}"}]
        body[f"{VIEWS_URL}?{request.url.query.decode()}"] = info
        return httpx.Response(200, json=body)


def mock_rm_write(*, post_status: int = 201) -> respx.Route:
    """Factory POST + re-read of the created requirement. Returns the POST route."""
    respx.get(w1.CREATED_URI).mock(return_value=httpx.Response(200, text=w1.CREATED_RDF))
    return respx.post(w1.FACTORY_URL).mock(
        return_value=httpx.Response(post_status, headers={"Location": w1.CREATED_URI}, text="")
    )


# ---------------------------------------------------------------------------------------------
# CCM: work item query (duplicate check / list_workitems) honoring oslc.select
# ---------------------------------------------------------------------------------------------
PARENT_LINK = "rtc_cm:com.ibm.team.workitem.linktype.parentworkitem.parent"
TASK_TYPE_URI = f"{BASE}/ccm/oslc/types/{PA}/task"
STORY_TYPE_URI = f"{BASE}/ccm/oslc/types/{PA}/{f.STORY_ID}"
CLOSED_STATE = "com.example.workflow.state.closed"
OPEN_STATE = "com.example.workflow.state.open"


def ccm_row(
    n: int,
    title: str,
    *,
    type_id: str = "task",
    parent: int | None = None,
    state: str = OPEN_STATE,
) -> dict:
    return {"n": n, "title": title, "type_id": type_id, "parent": parent, "state": state}


class FakeCcmQuery:
    """The CCM work item query (QBASE): rows come back with ONLY the properties named in oslc.select."""

    def __init__(self, router: respx.MockRouter, rows: list[dict] | None = None, *, fail: httpx.Response | None = None):
        self.rows = rows if rows is not None else []
        self.fail = fail
        self.requests: list[httpx.Request] = []
        self.route = router.get(w2.QBASE).mock(side_effect=self._serve)

    def _serve(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if self.fail is not None:
            return self.fail
        select = {t.strip() for t in (request.url.params.get("oslc.select") or "").split(",") if t.strip()}
        results = []
        for row in self.rows:
            full = {
                "dcterms:title": row["title"],
                "dcterms:identifier": str(row["n"]),
                "dcterms:type": row["type_id"],
                "rtc_cm:type": {"rdf:resource": f"{BASE}/ccm/oslc/types/{PA}/{row['type_id']}"},
                "oslc_cm:status": "Closed" if row["state"] == CLOSED_STATE else "Open",
                "rtc_cm:state": {
                    "rdf:resource": f"{BASE}/ccm/oslc/workflows/{PA}/states/com.example.workflow/{row['state']}"
                },
            }
            if row["parent"] is not None:
                full[PARENT_LINK] = [{"rdf:resource": w2.item_uri(row["parent"])}]
            entry = {"rdf:about": w2.item_uri(row["n"])}
            entry.update({k: v for k, v in full.items() if k in select})
            results.append(entry)
        return httpx.Response(200, json={"oslc:results": results})

    def add(self, row: dict) -> None:
        self.rows.append(row)


def calls_to(router, url: str) -> int:
    """Number of requests of a respx router to ``url`` (query string ignored)."""
    return sum(1 for call in router.calls if str(call.request.url).split("?")[0] == url)


# ---------------------------------------------------------------------------------------------
# CCM creation environment with a configurable task shape
# ---------------------------------------------------------------------------------------------
SEVERITY_URI = f"{BASE}/ccm/oslc/enumerations/{PA}/severity/sev2"
FACTORIES = f.TYPED_FACTORIES[:2]  # task + story


def required_props() -> list[dict]:
    """Shape of the tested types: filedAgainst and severity are REQUIRED and have no default value."""
    return [
        f.filed_against_prop(default=None, allowed=f.allowed_values_url("task")),
        f.prop("severity", "Severity", "Exactly-one", propdef=f"{f.RTC_CM}severity"),
    ]


def mock_create_env(
    router: respx.MockRouter,
    props: list[dict] | None = None,
    *,
    shape_status: int = 200,
) -> dict[str, respx.Route]:
    """CCM discovery + shapes (``props``) + session-refresh GETs + one POST route per typed factory.

    Returns {type id: POST route}. ``shape_status`` != 200 makes every shape GET fail with that status.
    """
    w2.mock_ccm(router, FACTORIES)
    router.get(f"{BASE}/ccm/oslc/iterations").mock(return_value=httpx.Response(200, json={"oslc:results": []}))
    router.get(f.GENERIC_FACTORY).mock(return_value=httpx.Response(200, json={}))
    router.get(f.allowed_values_url("task")).mock(
        return_value=httpx.Response(200, text=f.allowed_values_xml("task", [f.CAT_DEFAULT, f.CAT_2]))
    )
    posts: dict[str, respx.Route] = {}
    for type_id, _title in FACTORIES:
        router.get(f.factory_uri(type_id)).mock(return_value=httpx.Response(200, json={}))
        if shape_status == 200:
            shape = httpx.Response(200, text=f.real_shape_xml(type_id, props or required_props()))
        else:
            shape = httpx.Response(shape_status, text="boom")
        router.get(f.shape_uri(type_id)).mock(return_value=shape)
        posts[type_id] = router.post(f.factory_uri(type_id)).mock(return_value=httpx.Response(201, json=w2.CREATED))
    return posts


def posted(route: respx.Route, index: int = 0) -> str:
    return route.calls[index].request.content.decode()


def post_total(posts: dict[str, respx.Route]) -> int:
    return sum(r.call_count for r in posts.values())


def write_json(path: Path, data: dict) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


def write_profile(root: Path, *, template: tuple[str, dict] | None = None, area: dict | None = None, key: str = PA):
    """Profile tree: <root>/templates/<name>.json and <root>/project-areas/<key>.json (plain JSON)."""
    if template is not None:
        write_json(root / "templates" / f"{template[0]}.json", template[1])
    if area is not None:
        write_json(root / "project-areas" / f"{key}.json", area)
    return root
