"""G3 (RED) tests for SPEC-perfil-template + ADR-005 (wave W4, release v0.6.0).

The profile is plain JSON OUTSIDE the package: ``$ELM_MCP_PROFILE_DIR`` or ``~/.elm-mcp/profiles/``, with
``templates/<name>.json`` and ``project-areas/<key>.json`` (``"template": "<name>"`` inside). Tests only write
under ``tmp_path`` (conftest.py also points HOME at it): the real HOME is never read.

The ADR does not fix the JSON keys, so THESE TESTS FIX THEM (the developer must validate, see the report):

    {
      "template": "<template name>",                         # project-areas/<key>.json only
      "defaults": {"<type id>": {"<payload property>": <value as in custom_fields>}},
      "action_aliases": {"<alias>": "<workflow action id>"},
      "hidden_states": ["<state id>"]
    }

  * ``<key>`` is the project area id taken from the service provider URL (``_PA1``): it is also what
    ``transition_workitem`` can derive from the item's workflow URI (it has no ``project`` argument);
  * ``defaults`` keys are payload property names (``rtc_cm:filedAgainst``), values exactly as ``custom_fields``;
  * template and project area are merged: dicts key by key (deep), lists and scalars replaced by the project area.

Tenant-neutral data only (https://elm.example.com, _PA1, jdoe).
"""

import json
import re
import sys
from pathlib import Path

import httpx
import pytest
import respx
from mcp.server.mcpserver.exceptions import ToolError

from elm_mcp import cli, server
from elm_mcp.auth import close_client

from . import f1_fixtures as f
from . import w2_fixtures as w2
from . import w4_fixtures as w4
from .w4_fixtures import BASE, PROJECT

SRC = Path(__file__).resolve().parent.parent / "src" / "elm_mcp"
CAT_A = f"{f.CAT_BASE}/_CAT_A"
CAT_B = f"{f.CAT_BASE}/_CAT_B"
SEV = w4.SEVERITY_URI
SEV_OTHER = f"{BASE}/ccm/oslc/enumerations/{f.PA}/severity/sev9"


def _res(uri: str) -> dict:
    return {"rdf:resource": uri}


@pytest.fixture(autouse=True)
async def _cfg(monkeypatch):
    monkeypatch.setattr("elm_mcp.config.settings.elm_url", BASE)
    monkeypatch.delenv("ELM_MCP_PROFILE_DIR", raising=False)
    monkeypatch.delenv("ELM_CACHE_TTL", raising=False)
    yield
    await close_client()


@pytest.fixture
def profile_dir(tmp_path, monkeypatch) -> Path:
    root = tmp_path / "profiles"
    monkeypatch.setenv("ELM_MCP_PROFILE_DIR", str(root))
    return root


def _env(router, rows=None):
    posts = w4.mock_create_env(router)  # filedAgainst + severity REQUIRED, no default in the shape
    w4.FakeCcmQuery(router, rows or [])
    return posts


async def _create(**kwargs) -> dict:
    return await server.create_workitem(project=PROJECT, title="T", type="task", **kwargs)


# ======================================================================
# No profile: discovery only; missing required fields are listed from the shape
# ======================================================================
async def test_without_a_profile_missing_required_fields_are_refused_listing_them(w2_router):
    posts = _env(w2_router)
    with pytest.raises(ToolError) as exc:
        await _create()
    message = str(exc.value)
    assert "filedAgainst" in message
    assert "severity" in message
    assert w4.post_total(posts) == 0


async def test_without_a_profile_a_filled_required_field_is_not_reported(w2_router):
    posts = _env(w2_router)
    with pytest.raises(ToolError) as exc:
        await _create(filed_against=CAT_A)
    assert "severity" in str(exc.value)
    assert "filedAgainst" not in str(exc.value)
    assert w4.post_total(posts) == 0


async def test_without_a_profile_custom_fields_can_fill_the_required_ones(w2_router):
    posts = _env(w2_router)
    result = await _create(filed_against=CAT_A, custom_fields={"rtc_cm:severity": _res(SEV)})
    assert result["id"] == "42"
    assert w4.post_total(posts) == 1


# ======================================================================
# Profile: where it is read from
# ======================================================================
async def test_profile_dir_comes_from_elm_mcp_profile_dir(w2_router, profile_dir):
    w4.write_profile(
        profile_dir,
        area={"defaults": {"task": {"rtc_cm:filedAgainst": _res(CAT_A), "rtc_cm:severity": _res(SEV)}}},
    )
    posts = _env(w2_router)
    await _create()
    body = w4.posted(posts["task"])
    assert CAT_A in body
    assert SEV in body


async def test_without_the_variable_the_profile_is_read_from_the_home_directory(w2_router, tmp_path):
    """conftest.py sets HOME to tmp_path: ~/.elm-mcp/profiles/ is tmp_path/.elm-mcp/profiles."""
    w4.write_profile(
        tmp_path / ".elm-mcp" / "profiles",
        area={"defaults": {"task": {"rtc_cm:filedAgainst": _res(CAT_A), "rtc_cm:severity": _res(SEV)}}},
    )
    posts = _env(w2_router)
    await _create()
    assert CAT_A in w4.posted(posts["task"])


async def test_the_environment_directory_wins_over_the_home_directory(w2_router, tmp_path, profile_dir):
    both = {"rtc_cm:severity": _res(SEV)}
    w4.write_profile(
        tmp_path / ".elm-mcp" / "profiles", area={"defaults": {"task": {"rtc_cm:filedAgainst": _res(CAT_B), **both}}}
    )
    w4.write_profile(profile_dir, area={"defaults": {"task": {"rtc_cm:filedAgainst": _res(CAT_A), **both}}})
    posts = _env(w2_router)
    await _create()
    body = w4.posted(posts["task"])
    assert CAT_A in body
    assert CAT_B not in body


# ======================================================================
# Profile: template, then project area; merge semantics
# ======================================================================
async def test_template_values_apply_to_a_project_area_that_names_the_template(w2_router, profile_dir):
    w4.write_profile(
        profile_dir,
        template=("demo", {"defaults": {"task": {"rtc_cm:filedAgainst": _res(CAT_A), "rtc_cm:severity": _res(SEV)}}}),
        area={"template": "demo"},
    )
    posts = _env(w2_router)
    await _create()
    body = w4.posted(posts["task"])
    assert CAT_A in body
    assert SEV in body


async def test_project_area_overrides_the_template_key_by_key(w2_router, profile_dir):
    w4.write_profile(
        profile_dir,
        template=("demo", {"defaults": {"task": {"rtc_cm:filedAgainst": _res(CAT_A), "rtc_cm:severity": _res(SEV)}}}),
        area={"template": "demo", "defaults": {"task": {"rtc_cm:filedAgainst": _res(CAT_B)}}},
    )
    posts = _env(w2_router)
    await _create()
    body = w4.posted(posts["task"])
    assert CAT_B in body, "the project area overrides the template"
    assert CAT_A not in body
    assert SEV in body, "what the project area does not override still comes from the template"


async def test_a_project_area_file_without_template_applies_by_itself(w2_router, profile_dir):
    w4.write_profile(
        profile_dir,
        template=("demo", {"defaults": {"task": {"rtc_cm:filedAgainst": _res(CAT_A)}}}),
        area={"defaults": {"task": {"rtc_cm:filedAgainst": _res(CAT_B), "rtc_cm:severity": _res(SEV)}}},
    )
    posts = _env(w2_router)
    await _create()
    body = w4.posted(posts["task"])
    assert CAT_B in body
    assert CAT_A not in body


async def test_a_template_not_named_by_the_project_area_is_not_applied(w2_router, profile_dir):
    w4.write_profile(
        profile_dir,
        template=("other", {"defaults": {"task": {"rtc_cm:filedAgainst": _res(CAT_A), "rtc_cm:severity": _res(SEV)}}}),
        area={"template": "demo"},
    )
    posts = _env(w2_router)
    with pytest.raises(ToolError, match="filedAgainst"):
        await _create()
    assert w4.post_total(posts) == 0


async def test_the_profile_of_another_project_area_is_not_applied(w2_router, profile_dir):
    w4.write_profile(
        profile_dir,
        area={"defaults": {"task": {"rtc_cm:filedAgainst": _res(CAT_A), "rtc_cm:severity": _res(SEV)}}},
        key="_OTHER",
    )
    posts = _env(w2_router)
    with pytest.raises(ToolError, match="filedAgainst"):
        await _create()
    assert w4.post_total(posts) == 0


# ======================================================================
# Profile: defaults fill the missing required fields of create_workitem
# ======================================================================
async def test_defaults_are_chosen_by_the_type_of_the_work_item(w2_router, profile_dir):
    w4.write_profile(
        profile_dir,
        area={"defaults": {f.STORY_ID: {"rtc_cm:filedAgainst": _res(CAT_A), "rtc_cm:severity": _res(SEV)}}},
    )
    posts = _env(w2_router)
    with pytest.raises(ToolError, match="filedAgainst"):
        await _create()  # a task: the story defaults do not apply
    assert w4.post_total(posts) == 0


async def test_the_profile_fills_only_what_is_missing_and_the_rest_is_refused(w2_router, profile_dir):
    w4.write_profile(profile_dir, area={"defaults": {"task": {"rtc_cm:filedAgainst": _res(CAT_A)}}})
    posts = _env(w2_router)
    with pytest.raises(ToolError) as exc:
        await _create()
    assert "severity" in str(exc.value)
    assert "filedAgainst" not in str(exc.value)
    assert w4.post_total(posts) == 0


async def test_an_explicit_argument_wins_over_the_profile(w2_router, profile_dir):
    w4.write_profile(
        profile_dir,
        area={"defaults": {"task": {"rtc_cm:filedAgainst": _res(CAT_A), "rtc_cm:severity": _res(SEV)}}},
    )
    posts = _env(w2_router)
    await _create(filed_against=CAT_B)
    body = w4.posted(posts["task"])
    assert CAT_B in body
    assert CAT_A not in body
    assert SEV in body, "the severity the caller did not give still comes from the profile"


async def test_custom_fields_win_over_the_profile(w2_router, profile_dir):
    w4.write_profile(
        profile_dir,
        area={"defaults": {"task": {"rtc_cm:filedAgainst": _res(CAT_A), "rtc_cm:severity": _res(SEV)}}},
    )
    posts = _env(w2_router)
    await _create(custom_fields={"rtc_cm:severity": _res(SEV_OTHER)})
    body = w4.posted(posts["task"])
    assert SEV_OTHER in body
    assert SEV not in body
    assert CAT_A in body


# ======================================================================
# Workflow: aliases and hidden states
# ======================================================================
@respx.mock
async def test_an_action_alias_of_the_profile_is_translated_to_the_action_id(profile_dir):
    full_id = f.ACTION_PREFIX + "startWorking"
    w4.write_profile(profile_dir, area={"action_aliases": {"iniciar": full_id}})
    routes = f.mock_workflow()
    await server.transition_workitem(id=f.WI_ID, action="iniciar")
    assert routes["put"].calls[0].request.url.params["_action"] == full_id


@respx.mock
async def test_without_the_alias_in_a_profile_the_action_is_not_found(profile_dir):
    w4.write_profile(profile_dir, area={"action_aliases": {"outro": f.ACTION_PREFIX + "startWorking"}})
    routes = f.mock_workflow()
    with pytest.raises(ToolError, match="not found"):
        await server.transition_workitem(id=f.WI_ID, action="iniciar")
    assert routes["put"].call_count == 0


@respx.mock
async def test_action_ids_still_resolve_normally_when_a_profile_exists(profile_dir):
    w4.write_profile(profile_dir, area={"action_aliases": {"iniciar": f.ACTION_PREFIX + "closeStory"}})
    routes = f.mock_workflow()
    await server.transition_workitem(id=f.WI_ID, action="startWorking")
    assert routes["put"].calls[0].request.url.params["_action"] == f.ACTION_PREFIX + "startWorking"


def _states_rows() -> list[dict]:
    return [
        w4.ccm_row(1, "Open one", state=w4.OPEN_STATE),
        w4.ccm_row(2, "Closed one", state=w4.CLOSED_STATE),
        w4.ccm_row(3, "Open two", state=w4.OPEN_STATE),
    ]


async def test_hidden_states_are_left_out_of_list_workitems_by_default(w2_router, profile_dir):
    w4.write_profile(profile_dir, area={"hidden_states": [w4.CLOSED_STATE]})
    w2.mock_ccm(w2_router)
    w4.FakeCcmQuery(w2_router, _states_rows())
    result = await server.list_workitems(PROJECT)
    assert [i["id"] for i in result["items"]] == ["1", "3"]


async def test_hidden_states_can_be_shown_on_request(w2_router, profile_dir):
    w4.write_profile(profile_dir, area={"hidden_states": [w4.CLOSED_STATE]})
    w2.mock_ccm(w2_router)
    w4.FakeCcmQuery(w2_router, _states_rows())
    result = await server.list_workitems(PROJECT, include_hidden=True)
    assert [i["id"] for i in result["items"]] == ["1", "2", "3"]


async def test_without_a_profile_no_state_is_hidden(w2_router):
    w2.mock_ccm(w2_router)
    w4.FakeCcmQuery(w2_router, _states_rows())
    result = await server.list_workitems(PROJECT)
    assert [i["id"] for i in result["items"]] == ["1", "2", "3"]


async def test_hidden_states_come_from_the_template_when_the_area_names_it(w2_router, profile_dir):
    w4.write_profile(profile_dir, template=("demo", {"hidden_states": [w4.CLOSED_STATE]}), area={"template": "demo"})
    w2.mock_ccm(w2_router)
    w4.FakeCcmQuery(w2_router, _states_rows())
    result = await server.list_workitems(PROJECT)
    assert [i["id"] for i in result["items"]] == ["1", "3"]


async def test_list_workitems_registers_include_hidden_defaulting_to_false():
    tool = next(t for t in await server.mcp.list_tools() if t.name == "list_workitems")
    assert tool.input_schema["properties"]["include_hidden"].get("default") is False


# ======================================================================
# elm-mcp profile draft <project>: a draft of the profile from the discovery (stdout, JSON)
# ======================================================================
def test_profile_draft_prints_a_json_draft_built_from_the_discovery(monkeypatch, capsys):
    monkeypatch.setattr("elm_mcp.auth._client", None)
    monkeypatch.setattr(sys, "argv", ["elm-mcp", "profile", "draft", PROJECT])
    with respx.MockRouter(assert_all_called=False) as router:
        w2.mock_ccm(router, f.TYPED_FACTORIES[:2])
        for type_id, _title in f.TYPED_FACTORIES[:2]:
            props = [f.filed_against_prop(default=None, allowed=f.allowed_values_url(type_id))]
            router.get(f.shape_uri(type_id)).mock(
                return_value=httpx.Response(200, text=f.real_shape_xml(type_id, props))
            )
            router.get(f.allowed_values_url(type_id)).mock(
                return_value=httpx.Response(200, text=f.allowed_values_xml(type_id, [f.CAT_DEFAULT, f.CAT_2]))
            )
        cli.main()
        methods = {call.request.method for call in router.calls}
    assert methods <= {"GET", "POST"}, "the draft only reads (POST is the form login)"
    out = capsys.readouterr().out
    draft = json.loads(out)
    assert set(draft["defaults"]) == {"task", f.STORY_ID}, "one entry per type of the project"
    assert "rtc_cm:filedAgainst" in draft["defaults"]["task"], "the required fields of each type are listed"
    assert f.CAT_DEFAULT in out
    assert f.CAT_2 in out, "the allowed values are listed for the human review"
    assert "not-a-real-password" not in out


def test_profile_without_a_subcommand_is_a_usage_error(monkeypatch, capsys):
    monkeypatch.setattr(sys, "argv", ["elm-mcp", "profile"])
    with pytest.raises(SystemExit) as exc:
        cli.main()
    assert exc.value.code not in (None, 0)
    captured = capsys.readouterr()
    assert "draft" in captured.out + captured.err


# ======================================================================
# Nothing of an installation inside the package
# ======================================================================
def test_the_package_ships_no_profile_data_and_no_installation_values():
    """Guard (green by design, must stay green): no data files and only public, neutral hosts in src/."""
    data_files = [p.name for p in SRC.rglob("*") if p.suffix in {".json", ".yaml", ".yml", ".toml"}]
    assert data_files == []
    allowed = ("open-services.net", "purl.org", "w3.org", "jazz.net", "example.com", "example.org", "localhost")
    offenders = []
    for path in SRC.rglob("*.py"):
        for host in re.findall(r"https?://([A-Za-z0-9.-]+)", path.read_text(encoding="utf-8")):
            if not host.endswith(allowed):
                offenders.append((path.name, host))
    assert offenders == []
    lowered = "\n".join(p.read_text(encoding="utf-8").lower() for p in SRC.rglob("*.py"))
    for marker in ("dataprev", "prevnet", ".gov.br"):
        assert marker not in lowered
