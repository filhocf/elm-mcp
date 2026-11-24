"""RED tests (G3) for SPEC-ccm-times (release v0.7.0): the read-only tool ``list_teams``.

``server.list_teams(project, include_archived=False, refresh=False)`` lists the team areas of a CCM project
area with ONE call to the Process REST (``GET {ELM_URL}/ccm/process/project-areas/{paId}/team-areas``,
``Accept: text/xml``). The ``paId`` comes from the service provider URL of the project area
(``/ccm/oslc/contexts/{paId}/workitems/services.xml``), never from the title.
Result: ``{"items": [{name, id, uri, process_uri, archived}], "total": n}``, sorted by ``name`` (casefold),
archived teams only with ``include_archived=True``.

RED expectation: ``server.list_teams`` and the contract entry do not exist yet, so every behavior test fails
with ``AttributeError: module 'elm_mcp.server' has no attribute 'list_teams'`` (the call itself) and the
registration/contract tests fail on their assertions. No ``except`` wraps a call under test.

Decisions pinned here (the SPEC leaves them open):
  * a block whose URL is on ANOTHER origin (host, scheme or port) is dropped from the result (the rest is
    still returned) and the URL is never requested;
  * a well-formed ``team-areas`` document with no team is a success: ``{"items": [], "total": 0}``;
    an HTTP error, malformed XML or another root element is an error (``ToolError``), never an empty list.

Tenant-neutral data only (https://elm.example.com, jdoe). Fixture: tests/fixtures/ccm_process_team_areas.xml.
"""

import json
import time
from pathlib import Path

import httpx
import pytest
from mcp.server.mcpserver.exceptions import ToolError

from elm_mcp import server
from elm_mcp.auth import close_client

from . import f1_fixtures as f
from .w4_fixtures import calls_to

BASE = f.BASE
PROJECT = f.PROJECT
PA = "_PA0000000000000000000A"
OTHER_PA = "_PA0000000000000000000B"
TEAM_A = "_TEAM00000000000000000A"
TEAM_B = "_TEAM00000000000000000B"
TEAM_C = "_TEAM00000000000000000C"
SP_URL = f"{BASE}/ccm/oslc/contexts/{PA}/workitems/services.xml"
TEAMS_URL = f"{BASE}/ccm/process/project-areas/{PA}/team-areas"
OTHER_TEAMS_URL = f"{BASE}/ccm/process/project-areas/{OTHER_PA}/team-areas"
FIXTURE = Path(__file__).parent / "fixtures" / "ccm_process_team_areas.xml"
NS = "http://jazz.net/xmlns/prod/jazz/process/0.6/"
PASSWORD = "not-a-real-password"


def _catalog(providers: list[tuple[str, str]]) -> str:
    blocks = "".join(
        f'<oslc:serviceProvider><oslc:ServiceProvider rdf:about="{url}"><dcterms:title>{title}</dcterms:title>'
        "</oslc:ServiceProvider></oslc:serviceProvider>"
        for title, url in providers
    )
    return (
        '<?xml version="1.0" encoding="UTF-8"?><oslc:ServiceProviderCatalog '
        'xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#" xmlns:dcterms="http://purl.org/dc/terms/" '
        f'xmlns:oslc="http://open-services.net/ns/core#">{blocks}</oslc:ServiceProviderCatalog>'
    )


def team_xml(name: str | None, url: str | None, archived: str | None = "false") -> str:
    """One ``jp06:team-area`` block shaped like the real Process REST answer (name/url/archived optional)."""
    attr = f' jp06:name="{name}"' if name is not None else ""
    url_el = f"<jp06:url>{url}</jp06:url>" if url is not None else ""
    arch_el = f"<jp06:archived>{archived}</jp06:archived>" if archived is not None else ""
    return f"<jp06:team-area{attr}><jp06:summary/>{url_el}{arch_el}</jp06:team-area>"


def team_url(team_id: str, pa: str = PA, base: str = BASE) -> str:
    return f"{base}/ccm/process/project-areas/{pa}/team-areas/{team_id}"


def doc(*blocks: str, prefix: str = "") -> str:
    return f'{prefix}<jp06:team-areas xmlns:jp06="{NS}">{"".join(blocks)}</jp06:team-areas>'


def uri_of(team_id: str) -> str:
    return f"{BASE}/ccm/oslc/teamareas/{team_id}"


def process_uri_of(team_id: str) -> str:
    return f"{BASE}/ccm/process/team-areas/{team_id}"


@pytest.fixture(autouse=True)
async def _isolated(monkeypatch):
    monkeypatch.setattr("elm_mcp.config.settings.elm_url", BASE)
    monkeypatch.delenv("ELM_CACHE_TTL", raising=False)
    yield
    await close_client()


def mock_env(router, *, providers: list[tuple[str, str]] | None = None) -> None:
    """Login + rootservices + a catalog with the given (title, service provider URL) providers."""
    router.post(f"{BASE}/jts/j_security_check").mock(return_value=httpx.Response(200))
    router.get(f"{BASE}/ccm/rootservices").mock(return_value=httpx.Response(200, text=f.ROOTSERVICES_XML))
    catalog = _catalog(providers if providers is not None else [(PROJECT, SP_URL)])
    router.get(f"{BASE}/ccm/oslc/workitems/catalog").mock(return_value=httpx.Response(200, text=catalog))


def mock_teams(router, body: str | None = None, *, url: str = TEAMS_URL, status: int = 200, **kw):
    text = FIXTURE.read_text(encoding="utf-8") if body is None else body
    return router.get(url).mock(return_value=httpx.Response(status, text=text, **kw))


@pytest.fixture
def env(w2_router):
    mock_env(w2_router)
    return w2_router


# ======================================================================
# 1. Happy path
# ======================================================================
async def test_lists_active_teams_from_the_process_rest(env):
    route = mock_teams(env)
    result = await server.list_teams(PROJECT)
    assert result == {
        "items": [
            {
                "name": "Team Alpha",
                "id": TEAM_A,
                "uri": uri_of(TEAM_A),
                "process_uri": process_uri_of(TEAM_A),
                "archived": False,
            },
            {
                "name": "Team Beta",
                "id": TEAM_B,
                "uri": uri_of(TEAM_B),
                "process_uri": process_uri_of(TEAM_B),
                "archived": False,
            },
        ],
        "total": 2,
    }
    assert route.call_count == 1


async def test_request_is_a_get_with_accept_text_xml_on_the_pa_id_from_the_service_provider(env):
    route = mock_teams(env)
    await server.list_teams(PROJECT)
    request = route.calls.last.request
    assert request.method == "GET"
    assert str(request.url) == TEAMS_URL
    assert request.headers["Accept"] == "text/xml"


async def test_items_are_sorted_by_name_casefold(w2_router):
    mock_env(w2_router)
    blocks = [team_xml(n, team_url(f"_T{i}")) for i, n in enumerate(["zeta", "Alpha", "beta", "Éclair", "alpha2"])]
    mock_teams(w2_router, doc(*blocks))
    result = await server.list_teams(PROJECT)
    assert [i["name"] for i in result["items"]] == sorted(
        ["zeta", "Alpha", "beta", "Éclair", "alpha2"], key=str.casefold
    )
    assert result["total"] == 5


async def test_a_document_with_no_team_is_an_empty_success(w2_router):
    mock_env(w2_router)
    mock_teams(w2_router, doc())
    assert await server.list_teams(PROJECT) == {"items": [], "total": 0}


# ======================================================================
# 2. include_archived
# ======================================================================
async def test_include_archived_returns_all_three_with_a_boolean_flag(env):
    mock_teams(env)
    result = await server.list_teams(PROJECT, include_archived=True)
    assert result["total"] == 3
    assert {i["name"]: i["archived"] for i in result["items"]} == {
        "Old Team": True,
        "Team Alpha": False,
        "Team Beta": False,
    }
    assert [i["name"] for i in result["items"]] == ["Old Team", "Team Alpha", "Team Beta"]
    old = next(i for i in result["items"] if i["name"] == "Old Team")
    assert old["id"] == TEAM_C
    assert old["uri"] == uri_of(TEAM_C)
    assert old["process_uri"] == process_uri_of(TEAM_C)
    assert all(type(i["archived"]) is bool for i in result["items"])


async def test_default_excludes_the_archived_team(env):
    mock_teams(env)
    result = await server.list_teams(PROJECT)
    assert "Old Team" not in [i["name"] for i in result["items"]]
    assert result["total"] == len(result["items"]) == 2


# ======================================================================
# 3. Cache (SPEC-cache-shape)
# ======================================================================
async def test_second_call_inside_the_ttl_sends_no_request(env):
    mock_teams(env)
    first = await server.list_teams(PROJECT)
    sent = len(env.calls)
    second = await server.list_teams(PROJECT)
    assert second == first
    assert len(env.calls) == sent, "the cached team list must not touch the server"
    assert calls_to(env, TEAMS_URL) == 1


async def test_refresh_true_reloads_the_teams(env):
    mock_teams(env)
    await server.list_teams(PROJECT)
    await server.list_teams(PROJECT, refresh=True)
    assert calls_to(env, TEAMS_URL) == 2
    await server.list_teams(PROJECT)
    assert calls_to(env, TEAMS_URL) == 2, "the refresh repopulates the cache"


async def test_ttl_zero_does_not_cache(env, monkeypatch):
    monkeypatch.setenv("ELM_CACHE_TTL", "0")
    mock_teams(env)
    await server.list_teams(PROJECT)
    await server.list_teams(PROJECT)
    assert calls_to(env, TEAMS_URL) == 2


async def test_the_cache_expires_after_the_ttl(env, monkeypatch):
    monkeypatch.setenv("ELM_CACHE_TTL", "10")
    real = time.monotonic
    offset = {"s": 0.0}
    monkeypatch.setattr(time, "monotonic", lambda: real() + offset["s"])
    mock_teams(env)
    await server.list_teams(PROJECT)
    offset["s"] = 11.0
    await server.list_teams(PROJECT)
    assert calls_to(env, TEAMS_URL) == 2


async def test_a_failed_load_is_not_cached(env):
    route = env.get(TEAMS_URL).mock(
        side_effect=[httpx.Response(500, text="boom"), httpx.Response(200, text=FIXTURE.read_text(encoding="utf-8"))]
    )
    with pytest.raises(ToolError):
        await server.list_teams(PROJECT)
    assert (await server.list_teams(PROJECT))["total"] == 2
    assert route.call_count == 2


async def test_mutating_a_result_does_not_corrupt_the_cache(env):
    mock_teams(env)
    first = await server.list_teams(PROJECT)
    first["items"].clear()
    first["total"] = 99
    again = await server.list_teams(PROJECT)
    assert again["total"] == 2
    assert [i["name"] for i in again["items"]] == ["Team Alpha", "Team Beta"]


# ======================================================================
# 4. Errors name the gate (SPEC-diagnostico-gate); never an empty list
# ======================================================================
async def test_401_names_the_session_gate(env):
    mock_teams(env, "denied", status=401)
    with pytest.raises(ToolError, match=r"\[sessao\]"):
        await server.list_teams(PROJECT)


async def test_401_with_the_auth_failure_header_names_the_credential_gate(env):
    mock_teams(env, "denied", status=401, headers={"X-com-ibm-team-repository-web-auth-msg": "authfailed"})
    with pytest.raises(ToolError, match=r"\[credencial\]"):
        await server.list_teams(PROJECT)


async def test_403_names_the_permission_gate(env):
    mock_teams(env, "forbidden", status=403)
    with pytest.raises(ToolError, match=r"\[permissao\]"):
        await server.list_teams(PROJECT)


async def test_404_names_the_missing_or_invisible_resource_gate(env):
    mock_teams(env, "nope", status=404)
    with pytest.raises(ToolError, match=r"\[recurso-inexistente-ou-invisivel\]"):
        await server.list_teams(PROJECT)


async def test_500_names_the_http_gate(env):
    mock_teams(env, "boom", status=500)
    with pytest.raises(ToolError, match=r"\[http-500\]"):
        await server.list_teams(PROJECT)


@pytest.mark.parametrize(
    "body",
    [
        "<html><body>Please log in</body></html>",
        '<other xmlns="urn:x"><team-area name="X"/></other>',
        "this is not xml at all",
        '<jp06:team-areas xmlns:jp06="http://jazz.net/xmlns/prod/jazz/process/0.6/"><jp06:team-area',
        "",
    ],
    ids=["html-page", "other-root", "plain-text", "truncated-xml", "empty-body"],
)
async def test_a_200_that_is_not_a_team_areas_document_is_an_error(env, body):
    mock_teams(env, body)
    with pytest.raises(ToolError, match=r"(?i)team"):
        await server.list_teams(PROJECT)


# ======================================================================
# 5. Security
# ======================================================================
@pytest.mark.parametrize(
    "prefix",
    [
        '<!DOCTYPE jp06:team-areas [<!ENTITY xxe "boom">]>',
        '<!DOCTYPE jp06:team-areas SYSTEM "http://evil.example.org/x.dtd">',
        '<!ENTITY a "b">',
    ],
    ids=["doctype-entity", "doctype-system", "bare-entity"],
)
async def test_dtd_and_entity_declarations_are_refused(env, prefix):
    mock_teams(env, doc(team_xml("&xxe;", team_url(TEAM_A)), prefix=prefix))
    with pytest.raises(ToolError, match=r"(?i)dtd|entity"):
        await server.list_teams(PROJECT)


async def test_blocks_without_name_or_url_are_ignored(w2_router):
    mock_env(w2_router)
    body = doc(
        team_xml("Good", team_url(TEAM_A)),
        team_xml(None, team_url(TEAM_B)),
        team_xml("", team_url(TEAM_B)),
        team_xml("   ", team_url(TEAM_B)),
        team_xml("No Url", None),
        team_xml("Empty Url", ""),
        team_xml("Blank Url", "   "),
    )
    mock_teams(w2_router, body)
    result = await server.list_teams(PROJECT)
    assert [i["name"] for i in result["items"]] == ["Good"]
    assert result["total"] == 1


@pytest.mark.parametrize(
    "url",
    [
        "https://evil.example.org/ccm/process/project-areas/_PA/team-areas/_EVIL",
        "http://elm.example.com/ccm/process/project-areas/_PA/team-areas/_EVIL",
        "https://elm.example.com:9443/ccm/process/project-areas/_PA/team-areas/_EVIL",
        "https://elm.example.com.evil.example.org/ccm/process/project-areas/_PA/team-areas/_EVIL",
    ],
    ids=["other-host", "other-scheme", "other-port", "suffix-host"],
)
async def test_a_team_url_on_another_origin_is_dropped_and_never_followed(env, url):
    mock_teams(env, doc(team_xml("Good", team_url(TEAM_A)), team_xml("Hostile", url)))
    result = await server.list_teams(PROJECT)
    assert [i["name"] for i in result["items"]] == ["Good"]
    assert result["total"] == 1
    assert "_EVIL" not in json.dumps(result)
    assert all(str(c.request.url).startswith(BASE) for c in env.calls), "no request may leave the ELM origin"


@pytest.mark.parametrize(
    "url",
    [
        f"{BASE}/ccm/process/project-areas/{PA}/team-areas/",
        f"{BASE}/ccm/process/project-areas/{PA}/members",
        f"{BASE}/ccm/process/project-areas/{PA}/team-areas/_T/members",
    ],
    ids=["no-id", "not-a-team-area-url", "sub-resource"],
)
async def test_a_url_that_does_not_end_in_team_areas_id_is_ignored(env, url):
    mock_teams(env, doc(team_xml("Good", team_url(TEAM_A)), team_xml("Odd", url)))
    result = await server.list_teams(PROJECT)
    assert [i["name"] for i in result["items"]] == ["Good"]


async def test_the_uris_are_built_on_the_configured_origin_not_taken_from_the_response(env):
    mock_teams(env)
    result = await server.list_teams(PROJECT, include_archived=True)
    for item in result["items"]:
        assert item["uri"] == f"{BASE}/ccm/oslc/teamareas/{item['id']}"
        assert item["process_uri"] == f"{BASE}/ccm/process/team-areas/{item['id']}"


async def test_the_process_url_uses_the_configured_origin_even_if_the_provider_url_is_on_another(w2_router):
    evil_sp = f"https://evil.example.org/ccm/oslc/contexts/{PA}/workitems/services.xml"
    mock_env(w2_router, providers=[(PROJECT, evil_sp)])
    route = mock_teams(w2_router)
    result = await server.list_teams(PROJECT)
    assert result["total"] == 2
    assert route.call_count == 1
    assert all(str(c.request.url).startswith(BASE) for c in w2_router.calls)


async def test_xml_escaped_names_come_back_decoded(w2_router):
    mock_env(w2_router)
    mock_teams(w2_router, doc(team_xml("R&amp;D &lt;Core&gt; &quot;Q&quot;", team_url(TEAM_A))))
    result = await server.list_teams(PROJECT)
    assert result["items"][0]["name"] == 'R&D <Core> "Q"'


async def test_a_huge_name_is_returned_intact(w2_router):
    mock_env(w2_router)
    name = "N" * 200_000
    mock_teams(w2_router, doc(team_xml(name, team_url(TEAM_A))))
    result = await server.list_teams(PROJECT)
    assert result["items"][0]["name"] == name


async def test_a_thousand_teams_are_listed_in_under_a_second(w2_router):
    mock_env(w2_router)
    blocks = [team_xml(f"Team {i:04d}", team_url(f"_T{i:04d}")) for i in range(1000)]
    mock_teams(w2_router, doc(*blocks))
    start = time.perf_counter()
    result = await server.list_teams(PROJECT)
    assert time.perf_counter() - start < 1.0
    assert result["total"] == 1000
    assert [i["name"] for i in result["items"]] == [f"Team {i:04d}" for i in range(1000)]


# ======================================================================
# 6. Project area resolution
# ======================================================================
async def test_an_unknown_project_lists_the_valid_ones(w2_router):
    mock_env(w2_router, providers=[(PROJECT, SP_URL), ("Other Project", f"{BASE}/ccm/oslc/contexts/{OTHER_PA}")])
    route = mock_teams(w2_router)
    with pytest.raises(ToolError, match="not found") as exc:
        await server.list_teams("No Such Project")
    assert PROJECT in str(exc.value)
    assert "Other Project" in str(exc.value)
    assert route.call_count == 0


async def test_the_pa_id_comes_from_the_provider_url_not_from_the_title(w2_router):
    providers = [
        (PA, f"{BASE}/ccm/oslc/contexts/{OTHER_PA}/workitems/services.xml"),
        ("Second", SP_URL),
    ]
    mock_env(w2_router, providers=providers)
    right = mock_teams(w2_router, url=OTHER_TEAMS_URL)
    wrong = mock_teams(w2_router, url=TEAMS_URL)
    await server.list_teams(PA)  # the TITLE equals another project's id
    assert right.call_count == 1
    assert wrong.call_count == 0


async def test_a_provider_url_without_the_contexts_segment_also_gives_the_pa_id(w2_router):
    mock_env(w2_router, providers=[(PROJECT, f"{BASE}/ccm/oslc/contexts/{PA}")])
    route = mock_teams(w2_router)
    await server.list_teams(PROJECT)
    assert route.call_count == 1


async def test_the_project_name_match_ignores_case(env):
    mock_teams(env)
    assert (await server.list_teams(PROJECT.upper()))["total"] == 2


# ======================================================================
# 7. Registration and contract
# ======================================================================
async def _tools() -> dict:
    return {t.name: t for t in await server.mcp.list_tools()}


async def test_list_teams_is_registered_as_a_read_only_tool():
    tool = (await _tools())["list_teams"]
    assert tool.annotations is not None
    assert tool.annotations.read_only_hint is True


async def test_list_teams_signature_is_project_include_archived_refresh():
    schema = (await _tools())["list_teams"].input_schema
    assert list(schema["properties"]) == ["project", "include_archived", "refresh"]
    assert schema.get("required", []) == ["project"]
    assert schema["properties"]["project"]["type"] == "string"
    for name in ("include_archived", "refresh"):
        assert schema["properties"][name]["type"] == "boolean"
        assert schema["properties"][name]["default"] is False


async def test_the_server_registers_forty_one_tools():
    """40 before this tool (AGENTS.md says 40; the SPEC's '36' and TOOLS.md's 'thirty-five' are stale)."""
    names = [t.name for t in await server.mcp.list_tools()]
    assert "list_teams" in names
    assert len(names) == 41


def test_the_contract_snapshot_lists_list_teams():
    snapshot = json.loads((Path(__file__).parent / "contract" / "tools.snapshot.json").read_text(encoding="utf-8"))
    by_name = {t["name"]: t for t in snapshot["tools"]}
    assert len(by_name) == 41
    assert by_name["list_teams"] == {
        "name": "list_teams",
        "read_only": True,
        "parameters": [
            {"name": "project", "type": "string", "required": True},
            {"name": "include_archived", "type": "boolean", "required": False, "default": False},
            {"name": "refresh", "type": "boolean", "required": False, "default": False},
        ],
    }


# ======================================================================
# 6. Hardening (G5 review): safe ids, DOCTYPE case, project area id
# ======================================================================
@pytest.mark.parametrize(
    "bad_id",
    ['x" a="1', "..", ".", "%2e%2e", "a b", "é", "a%2Fb", "a%0d%0ab", "a&b", "a<b"],
    ids=["quote", "dotdot", "dot", "pct-dotdot", "space", "non-ascii", "pct-slash", "pct-crlf", "amp", "lt"],
)
async def test_team_ids_outside_the_safe_charset_are_dropped(env, bad_id):
    from xml.sax.saxutils import escape

    mock_teams(env, doc(team_xml("Good", team_url(TEAM_A)), team_xml("Bad", escape(team_url(bad_id)))))
    result = await server.list_teams(PROJECT)
    assert [i["id"] for i in result["items"]] == [TEAM_A]


@pytest.mark.parametrize("prefix", ["<!--<!DOCTYPE-->", "<!doctype x>", "<!DocType x>", "<!entity a 'b'>"])
async def test_doctype_in_any_case_or_after_a_comment_is_refused(env, prefix):
    mock_teams(env, doc(team_xml("Good", team_url(TEAM_A)), prefix=prefix))
    with pytest.raises(ToolError, match=r"(?i)dtd|entity"):
        await server.list_teams(PROJECT)


@pytest.mark.parametrize(
    "sp_url",
    [f"{BASE}/ccm/oslc/workitems/catalog/", f"{BASE}/ccm/oslc/contexts/"],
    ids=["catalog-slash", "contexts-empty"],
)
async def test_an_unextractable_project_area_id_is_a_clear_error(w2_router, sp_url):
    mock_env(w2_router, providers=[(PROJECT, sp_url)])
    with pytest.raises(ToolError, match=r"cannot extract the project area id from"):
        await server.list_teams(PROJECT)
