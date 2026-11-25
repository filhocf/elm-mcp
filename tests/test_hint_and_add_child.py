"""RED tests for issues #79 (403 hint names the field the server asks for) and #80 (add_child_workitem
takes filed_against and custom_fields).

#79: ``The 'X' attribute needs to be set`` -> the hint names the shape property(ies) whose TITLE is X
(exact first, then casefold) and how many allowed values each has; no match -> the generic list_fields hint.
#80: add_child_workitem forwards filed_against / custom_fields to create_workitem.
"""

import json
from xml.etree import ElementTree as ET

import httpx
import pytest
from mcp.server.mcpserver.exceptions import ToolError

from elm_mcp import server

from . import f1_fixtures as f
from . import w2_fixtures as w2
from . import w4_fixtures as w4
from .test_w1_contract import SNAPSHOT, build_contract
from .w4_fixtures import BASE, PROJECT

NS_RDF = "{http://www.w3.org/1999/02/22-rdf-syntax-ns#}"
CAT_PROP = "com.dataprev.team.workitem.attribute.categoriatarefa"
EXT = "http://jazz.net/xmlns/prod/jazz/rtc/ext/1.0/"
N_ALLOWED = 7  # distinct from any status code / count that could appear by accident


def _allowed_url(about_id: str) -> str:
    return f"{f.shape_uri('task')}/property/{about_id}/allowedValues"


def _allowed_xml(about_id: str, n: int) -> str:
    vals = "".join(f'<oslc:allowedValue rdf:resource="{BASE}/ccm/val/{about_id}/{i}"/>' for i in range(n))
    return (
        '<rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#" '
        'xmlns:oslc="http://open-services.net/ns/core#">'
        f'<rdf:Description rdf:about="{_allowed_url(about_id)}">{vals}</rdf:Description></rdf:RDF>'
    )


def _fa(title: str) -> dict:
    """filedAgainst with an arbitrary title, optional (so the POST is reached)."""
    return f.prop(
        "filedAgainst",
        title,
        about_id="category",
        propdef=f"{f.RTC_CM}filedAgainst",
        allowed=_allowed_url("category"),
    )


def _custom(name: str, title: str, about_id: str) -> dict:
    return f.prop(name, title, about_id=about_id, propdef=f"{EXT}{name}", allowed=_allowed_url(about_id))


def _env(router, props: list[dict], rows=None, *, refusal: str | None = None, extra_allowed=("category",)):
    posts = w4.mock_create_env(router, props=props)
    w4.FakeCcmQuery(router, rows or [])
    for about_id in {p["about_id"] for p in props if p["allowed"]} | set(extra_allowed):
        router.get(_allowed_url(about_id)).mock(
            return_value=httpx.Response(200, text=_allowed_xml(about_id, N_ALLOWED))
        )
    if refusal is not None:
        posts["task"].mock(return_value=httpx.Response(403, json={"oslc:message": refusal}))
    return posts


def _refusal(title: str, extra: str = "") -> str:
    return f"Preconditions have not been met: The '{title}' attribute needs to be set{extra}"


async def _refused_message(**kwargs) -> tuple[str, str]:
    """(whole ToolError text, the part after 'Hint' or '' when there is no hint)."""
    with pytest.raises(ToolError) as exc:
        await server.create_workitem(project=PROJECT, title="T", type="task", if_exists="create", **kwargs)
    text = str(exc.value)
    return text, (text.split("Hint", 1)[1] if "Hint" in text else "")


# ======================================================================
# #79 (1) Dataprev: 'Categoria' is the custom attribute, filedAgainst is 'Atendido por'
# ======================================================================
async def test_hint_names_the_custom_attribute_whose_title_is_quoted(w2_router):
    _env(
        w2_router,
        [_fa("Atendido por"), _custom(CAT_PROP, "Categoria", "categoriatarefa")],
        refusal=_refusal("Categoria"),
    )
    text, hint = await _refused_message()
    assert "categoriatarefa" in hint
    assert "filedAgainst" not in hint
    assert str(N_ALLOWED) in hint
    assert _refusal("Categoria") in text


# ======================================================================
# #79 (2) standard IBM install: the quoted title belongs to filedAgainst
# ======================================================================
async def test_hint_names_filed_against_when_it_carries_the_quoted_title(w2_router):
    _env(w2_router, [_fa("Categoria")], refusal=_refusal("Categoria"))
    _, hint = await _refused_message()
    assert "filedAgainst" in hint
    assert str(N_ALLOWED) in hint


async def test_hint_matches_a_title_that_differs_only_by_case(w2_router):
    _env(
        w2_router,
        [_fa("Atendido por"), _custom(CAT_PROP, "CATEGORIA", "categoriatarefa")],
        refusal=_refusal("categoria"),
    )
    _, hint = await _refused_message()
    assert "categoriatarefa" in hint
    assert "filedAgainst" not in hint


async def test_hint_for_a_quoted_title_that_is_not_about_categories_names_that_field(w2_router):
    props = [_fa("Atendido por"), f.prop("severity", "Severidade", propdef=f"{f.RTC_CM}severity")]
    _env(w2_router, props, refusal=_refusal("Severidade"))
    _, hint = await _refused_message()
    assert "severity" in hint
    assert "filedAgainst" not in hint


# ======================================================================
# #79 (3) quoted title without a field in the shape -> the old generic hint
# ======================================================================
async def test_quoted_title_without_a_matching_field_keeps_the_generic_hint(w2_router):
    """Guard (green today, must stay green): no shape field is titled 'Categoria'."""
    _env(w2_router, [_fa("Atendido por")], refusal=_refusal("Categoria"))
    text, _ = await _refused_message()
    assert "Hint: use list_fields to see the valid values (property filedAgainst, allowedValues)." in text


# ======================================================================
# #79 (4) no quotes and no 'categor' -> no hint (current behavior)
# ======================================================================
@pytest.mark.parametrize(
    "message",
    [
        "Preconditions have not been met: attribute Severidade is required",
        "Preconditions have not been met: The Severidade attribute needs to be set",
        "Something else failed",
    ],
)
async def test_refusal_without_quotes_or_category_has_no_hint(w2_router, message):
    """Guard (green today, must stay green)."""
    _env(
        w2_router,
        [_fa("Atendido por"), f.prop("severity", "Severidade", propdef=f"{f.RTC_CM}severity")],
        refusal=message,
    )
    text, hint = await _refused_message()
    assert message in text
    assert hint == ""
    assert "list_fields" not in text


# ======================================================================
# #79 (5) two fields with the same title -> both
# ======================================================================
async def test_hint_lists_every_field_that_has_the_quoted_title(w2_router):
    props = [
        _fa("Atendido por"),
        _custom(CAT_PROP, "Categoria", "categoriatarefa"),
        _custom("outra", "Categoria", "outra"),
    ]
    _env(w2_router, props, refusal=_refusal("Categoria"))
    _, hint = await _refused_message()
    assert "categoriatarefa" in hint
    assert "outra" in hint
    assert "filedAgainst" not in hint


# ======================================================================
# #79 (6) hostile server text: relayed as today, only shape names enter the hint
# ======================================================================
async def test_hostile_server_text_is_relayed_and_never_shapes_the_hint(w2_router):
    nasty = " <script>alert(1)</script> $(rm -rf /) {0} %s \x1b[31m 'injected_field'"
    _env(
        w2_router,
        [_fa("Atendido por"), _custom(CAT_PROP, "Categoria", "categoriatarefa")],
        refusal=_refusal("Categoria", nasty),
    )
    text, hint = await _refused_message()
    assert nasty.strip() in text  # same verbatim relay as today
    assert "categoriatarefa" in hint
    assert "<script>" not in hint
    assert "injected_field" not in hint
    assert "rm -rf" not in hint


async def test_a_title_in_the_server_text_cannot_select_a_field_by_regex_or_markup(w2_router):
    """The quoted text is data: '.*' must not match every field."""
    _env(w2_router, [_fa("Atendido por"), _custom(CAT_PROP, "Categoria", "categoriatarefa")], refusal=_refusal(".*"))
    _, hint = await _refused_message()
    assert "categoriatarefa" not in hint


# ======================================================================
# #80 add_child_workitem forwards filed_against / custom_fields
# ======================================================================
def _by_local(body: str) -> dict[str, list[ET.Element]]:
    out: dict[str, list[ET.Element]] = {}
    for el in ET.fromstring(body).iter():
        out.setdefault(el.tag.rsplit("}", 1)[-1], []).append(el)
    return out


async def test_add_child_sends_the_parent_custom_fields_and_filed_against(w2_router):
    posts = _env(w2_router, [_fa("Atendido por"), _custom(CAT_PROP, "Categoria", "categoriatarefa")])
    cat = f"{BASE}/ccm/resource/itemOid/com.ibm.team.workitem.Category/_CHOSEN"
    result = await server.add_child_workitem(
        parent_id="10",
        title="Child",
        project=PROJECT,
        filed_against=cat,
        custom_fields={f"rtc_ext:{CAT_PROP}": "Infra"},
        if_exists="create",
    )
    assert result["dcterms:identifier"] == "42"
    assert posts["task"].call_count == 1
    body = w4.posted(posts["task"])
    fields = _by_local(body)
    assert [e.get(f"{NS_RDF}resource") for e in fields["filedAgainst"]] == [cat]
    assert [e.text for e in fields[CAT_PROP]] == ["Infra"]
    parents = [e.get(f"{NS_RDF}resource") for e in fields["com.ibm.team.workitem.linktype.parentworkitem.parent"]]
    assert parents == [w2.item_uri(10)]


async def test_add_child_with_if_exists_return_and_a_profile_with_defaults_still_works(
    w2_router, tmp_path, monkeypatch
):
    """Regression: the defaults of the profile fill what the call omits; an existing child is returned, nothing POSTed."""
    root = tmp_path / "profiles"
    monkeypatch.setenv("ELM_MCP_PROFILE_DIR", str(root))
    cat = f"{BASE}/ccm/resource/itemOid/com.ibm.team.workitem.Category/_FROMPROFILE"
    w4.write_profile(root, area={"defaults": {"task": {"rtc_cm:filedAgainst": {"rdf:resource": cat}}}})
    posts = _env(w2_router, [_fa("Atendido por")], rows=[w4.ccm_row(731, "Child", parent=10)])
    result = await server.add_child_workitem(parent_id="10", title="Child", project=PROJECT, if_exists="return")
    assert result["created"] is False
    assert w2.item_uri(731) in json.dumps(result)
    assert posts["task"].call_count == 0


async def test_add_child_creates_with_profile_defaults_and_the_new_parameters_together(
    w2_router, tmp_path, monkeypatch
):
    """custom_fields win over profile defaults, as in create_workitem; the unlisted default still applies."""
    root = tmp_path / "profiles"
    monkeypatch.setenv("ELM_MCP_PROFILE_DIR", str(root))
    cat = f"{BASE}/ccm/resource/itemOid/com.ibm.team.workitem.Category/_FROMPROFILE"
    w4.write_profile(
        root,
        area={"defaults": {"task": {"rtc_cm:filedAgainst": {"rdf:resource": cat}, f"rtc_ext:{CAT_PROP}": "perfil"}}},
    )
    posts = _env(w2_router, [_fa("Atendido por"), _custom(CAT_PROP, "Categoria", "categoriatarefa")])
    await server.add_child_workitem(
        parent_id="10",
        title="Child",
        project=PROJECT,
        custom_fields={f"rtc_ext:{CAT_PROP}": "chamada"},
        if_exists="create",
    )
    fields = _by_local(w4.posted(posts["task"]))
    assert [e.text for e in fields[CAT_PROP]] == ["chamada"]
    assert [e.get(f"{NS_RDF}resource") for e in fields["filedAgainst"]] == [cat]


# ======================================================================
# Contract: add_child_workitem gains exactly filed_against and custom_fields
# ======================================================================
OLD_ADD_CHILD = ["parent_id", "title", "project", "type", "description", "owner", "if_exists"]


async def test_add_child_workitem_contract_gains_only_filed_against_and_custom_fields():
    """Appended after if_exists (positional callers keep working), typed like create_workitem's; no other tool changes."""
    actual = {t["name"]: t for t in (await build_contract())["tools"]}
    params = actual["add_child_workitem"]["parameters"]
    names = [p["name"] for p in params]
    assert names[: len(OLD_ADD_CHILD)] == OLD_ADD_CHILD
    assert sorted(names[len(OLD_ADD_CHILD) :]) == ["custom_fields", "filed_against"]
    create = {p["name"]: p for p in actual["create_workitem"]["parameters"]}
    new = {p["name"]: p for p in params}
    for name in ("filed_against", "custom_fields"):
        assert new[name] == create[name]
        assert new[name]["required"] is False
    snapshot = {t["name"]: t for t in json.loads(SNAPSHOT.read_text(encoding="utf-8"))["tools"]}
    assert snapshot["add_child_workitem"]["parameters"] == params  # snapshot regenerated
    assert {n: t for n, t in snapshot.items() if n != "add_child_workitem"} == {
        n: t for n, t in actual.items() if n != "add_child_workitem"
    }
