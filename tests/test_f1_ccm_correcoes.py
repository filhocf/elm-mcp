"""G3 (RED) tests for F1 / SPEC-ccm-correcoes: defects D1, D2, D6, D9, D10, D11.

Behavior is observed through respx (bodies, query params, headers, call counts),
never through new internal helper names. Every test asserts the TARGET behavior and
is expected to FAIL against the pre-F1 src (see .gates/G3-rok.md).

Tenant-neutral: host https://elm.example.com, project area _PA1, user jdoe,
custom type com.example.workitem.debt.
"""

import json
from xml.etree import ElementTree as ET

import httpx
import pytest
import respx
from mcp.server.mcpserver.exceptions import ToolError

from elm_mcp import oslc
from elm_mcp.auth import close_client
from elm_mcp.server import (
    add_child_workitem,
    create_workitem,
    list_fields,
    list_types,
    transition_workitem,
    update_workitem,
)

from . import f1_fixtures as f
from .f1_fixtures import BASE, DEBT_ID, PROJECT, STORY_ID

CREATED = {
    "dcterms:title": "New",
    "dcterms:identifier": "42",
    "rdf:about": f"{BASE}/ccm/resource/itemName/com.ibm.team.workitem.WorkItem/42",
}
NS_DCTERMS = "{http://purl.org/dc/terms/}"
NS_RDF = "{http://www.w3.org/1999/02/22-rdf-syntax-ns#}"
SERVER_MESSAGE = "'Save Work Item' failed. Preconditions have not been met: attribute Foo is required"


@pytest.fixture(autouse=True)
async def _cfg(monkeypatch):
    monkeypatch.setattr("elm_mcp.config.settings.elm_url", BASE)
    yield
    await close_client()


# ======================================================================
# D1 — list_types("ccm"): one item per typed CreationFactory
# ======================================================================
def _mock_property_only_shapes() -> None:
    """Every shape GET returns only property titles ('Children', 'Verified'): the pre-F1 symptom."""
    body = """<rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#"
        xmlns:dcterms="http://purl.org/dc/terms/" xmlns:oslc="http://open-services.net/ns/core#">
        <oslc:ResourceShape>
          <oslc:property><oslc:Property><oslc:name>children</oslc:name><dcterms:title>Children</dcterms:title>
          </oslc:Property></oslc:property>
          <oslc:property><oslc:Property><oslc:name>verified</oslc:name><dcterms:title>Verified</dcterms:title>
          </oslc:Property></oslc:property>
        </oslc:ResourceShape></rdf:RDF>"""
    respx.get(url__regex=rf"{BASE}/ccm/oslc/context/_PA1/shapes/workitems/.*").mock(
        return_value=httpx.Response(200, text=body)
    )


@pytest.mark.parametrize("form", ["services_xml", "contexts"])
@respx.mock
async def test_d1_list_types_ccm_returns_one_item_per_typed_factory(form):
    """D1 EARS-1: one item per typed factory with title, type id, factory URI and shape URI."""
    f.mock_ccm_discovery(form)
    _mock_property_only_shapes()
    result = await list_types("ccm", PROJECT)
    by_id = {r["id"]: r for r in result}
    assert set(by_id) == {i for i, _ in f.TYPED_FACTORIES}
    assert len(result) == len(f.TYPED_FACTORIES)
    for type_id, _raw in f.TYPED_FACTORIES:
        item = by_id[type_id]
        assert item["factory_uri"] == f.factory_uri(type_id)
        assert item["shape_uri"] == f.shape_uri(type_id)
        assert item["resource_type"] == f.CR_TYPE


@respx.mock
async def test_d1_list_types_ccm_title_extracted_from_factory_title():
    """D1 decision 3: title = {Nome} from 'Location for creation of {Nome} change requests '; factory_title = raw."""
    f.mock_ccm_discovery()
    _mock_property_only_shapes()
    by_id = {r["id"]: r for r in await list_types("ccm", PROJECT)}
    assert by_id["task"]["title"] == "Task"
    assert by_id[STORY_ID]["title"] == "Story"
    assert by_id[DEBT_ID]["title"] == "Technical Debt"
    assert by_id["task"]["factory_title"] == "Location for creation of Task change requests "


@respx.mock
async def test_d1_list_types_ccm_title_fallback_is_stripped_raw_title():
    """D1 decision 3: when the pattern does not match, title = raw title stripped; factory_title = raw."""
    f.mock_ccm_discovery()
    _mock_property_only_shapes()
    by_id = {r["id"]: r for r in await list_types("ccm", PROJECT)}
    impediment = by_id["com.ibm.team.workitem.workItemType.impediment"]
    assert impediment["title"] == "Impediment factory"
    assert impediment["factory_title"] == "Impediment factory "


@respx.mock
async def test_d1_list_types_ccm_never_returns_shape_property_titles():
    """D1 EARS-1 negative: property titles of the shape ('Children', 'Verified') must not appear."""
    f.mock_ccm_discovery()
    _mock_property_only_shapes()
    result = await list_types("ccm", PROJECT)
    titles = {r["title"] for r in result}
    assert result  # guard: a vacuous empty list must not pass
    assert not titles & {"Children", "Verified", None}


@respx.mock
async def test_d1_list_types_ccm_excludes_generic_and_decoy_factories():
    """D1 decision 2: generic factories (…/workitems, incl. drafts) and non-exact resourceType are not listed."""
    f.mock_ccm_discovery()
    _mock_property_only_shapes()
    result = await list_types("ccm", PROJECT)
    assert result  # guard
    uris = [r["factory_uri"] for r in result]
    assert f.GENERIC_FACTORY not in uris
    assert f.factory_uri("decoy") not in uris
    assert "decoy" not in {r["id"] for r in result}


# ======================================================================
# D2 — list_fields("ccm", project, type)
# ======================================================================
ALLOWED_URI = f"{BASE}/ccm/oslc/enumerations/_PA1/priority"


def _prop(name: str, title: str, occurs: str, read_only: bool, allowed: str | None = None) -> str:
    """LEGACY format (<oslc:Property> element) - kept only for the backward-compatibility tests."""
    allowed_el = f'<oslc:allowedValues rdf:resource="{allowed}"/>' if allowed else ""
    return f"""<oslc:property><oslc:Property>
        <oslc:name>{name}</oslc:name>
        <dcterms:title>{title}</dcterms:title>
        <oslc:occurs rdf:resource="http://open-services.net/ns/core#{occurs}"/>
        <oslc:readOnly rdf:datatype="http://www.w3.org/2001/XMLSchema#boolean">{str(read_only).lower()}</oslc:readOnly>
        <oslc:propertyDefinition rdf:resource="{BASE}/ccm/oslc/properties/{name}"/>
        {allowed_el}
        </oslc:Property></oslc:property>"""


def _legacy_type_shape(marker: str) -> str:
    props = "".join(
        [
            _prop("summary", "Summary", "Exactly-one", False),
            _prop("priority", "Priority", "One-or-many", False, ALLOWED_URI),
            _prop("estimate", "Estimate", "Zero-or-one", True),
            _prop("tags", "Tags", "Zero-or-many", False),
            _prop(marker, marker, "Zero-or-one", False),
        ]
    )
    return f"""<rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#"
        xmlns:dcterms="http://purl.org/dc/terms/" xmlns:oslc="http://open-services.net/ns/core#">
        <oslc:ResourceShape>{props}</oslc:ResourceShape></rdf:RDF>"""


def _type_shape(marker: str, type_id: str = "task", *, rotate: bool = True) -> str:
    """REAL server format: one typed rdf:Description (rdf:type core#Property) per property."""
    return f.real_shape_xml(
        type_id,
        [
            f.prop("summary", "Summary", "Exactly-one"),
            f.prop("priority", "Priority", "One-or-many", allowed=ALLOWED_URI),
            f.prop("estimate", "Estimate", "Zero-or-one", True),
            f.prop("tags", "Tags", "Zero-or-many"),
            f.prop(marker, marker, "Zero-or-one", about_id="marker"),
        ],
        rotate=rotate,
    )


def _mock_typed_shapes() -> None:
    for type_id, _ in f.TYPED_FACTORIES:
        marker = "marker_" + type_id.rsplit(".", 1)[-1]
        respx.get(f.shape_uri(type_id)).mock(return_value=httpx.Response(200, text=_type_shape(marker, type_id)))
    respx.get(ALLOWED_URI).mock(
        return_value=httpx.Response(
            200,
            text=f"""<rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#"
            xmlns:oslc="http://open-services.net/ns/core#">
            <oslc:AllowedValues rdf:about="{ALLOWED_URI}">
              <oslc:allowedValue rdf:resource="{BASE}/ccm/oslc/enumerations/_PA1/priority/low"/>
              <oslc:allowedValue rdf:resource="{BASE}/ccm/oslc/enumerations/_PA1/priority/high"/>
            </oslc:AllowedValues></rdf:RDF>""",
        )
    )


def _names(section: list[dict]) -> set[str]:
    return {p["name"] for p in section}


@pytest.mark.parametrize(
    ("type_arg", "type_id"),
    [
        ("task", "task"),  # id
        (STORY_ID, STORY_ID),  # full id
        ("story", STORY_ID),  # alias
        ("Story", STORY_ID),  # alias / title, case-insensitive
        ("Technical Debt", DEBT_ID),  # title of a custom type (no alias)
        (DEBT_ID, DEBT_ID),  # id of a custom type
    ],
)
@respx.mock
async def test_d2_list_fields_ccm_resolves_type_by_id_title_or_alias(type_arg, type_id):
    """D2 EARS-2: id, title or alias of a type listed by list_types resolves to that type's shape."""
    f.mock_ccm_discovery()
    _mock_typed_shapes()
    result = await list_fields("ccm", PROJECT, type_arg)
    assert result["shape_uri"] == f.shape_uri(type_id)
    marker = "marker_" + type_id.rsplit(".", 1)[-1]
    assert marker in _names(result["optional"])


@respx.mock
async def test_d2_list_fields_ccm_exposes_occurs_read_only_and_allowed_values():
    """D2 EARS-2 + decision 13: occurs (last segment), read_only (bool) and allowed_values per field."""
    f.mock_ccm_discovery()
    _mock_typed_shapes()
    result = await list_fields("ccm", PROJECT, "task")
    fields = {p["name"]: p for p in result["required"] + result["optional"]}
    assert fields["summary"]["occurs"] == "Exactly-one"
    assert fields["priority"]["occurs"] == "One-or-many"
    assert fields["estimate"]["occurs"] == "Zero-or-one"
    assert fields["tags"]["occurs"] == "Zero-or-many"
    assert fields["summary"]["read_only"] is False
    assert fields["estimate"]["read_only"] is True
    assert [v["value_uri"] for v in fields["priority"]["allowed_values"]] == [
        f"{BASE}/ccm/oslc/enumerations/_PA1/priority/low",
        f"{BASE}/ccm/oslc/enumerations/_PA1/priority/high",
    ]
    # Required/optional partition is kept; read-only fields stay in their partition.
    assert _names(result["required"]) == {"summary", "priority"}
    assert {"estimate", "tags"} <= _names(result["optional"])


@respx.mock
async def test_d2_list_fields_ccm_unknown_type_raises_toolerror_listing_types():
    """D2: unresolved type -> ToolError listing the available types (ids)."""
    f.mock_ccm_discovery()
    _mock_typed_shapes()
    with pytest.raises(ToolError, match="nonexistent") as exc:
        await list_fields("ccm", PROJECT, "nonexistent")
    assert "task" in str(exc.value)
    assert DEBT_ID in str(exc.value)


# ----------------------------------------------------------------------
# D2 (loop 1) — real shape format: typed rdf:Description (rdf:type core#Property)
# ----------------------------------------------------------------------
REAL_TASK_PROPS = [
    f.filed_against_prop(default=f.CAT_DEFAULT, allowed=f.allowed_values_url("task")),
    f.prop("shortTitle", "Short Name", "Zero-or-one", True, propdef="http://open-services.net/ns/core#shortTitle"),
    f.prop("summary", "Summary", "Exactly-one"),
]


def _by_name(props: list[dict]) -> dict[str, dict]:
    return {p["name"]: p for p in props}


@pytest.mark.parametrize("rotate", [True, False], ids=["children-rotated", "children-fixed-order"])
@respx.mock
async def test_d2_list_shape_properties_extracts_rdf_description_properties(rotate):
    """D2 loop 1: every typed rdf:Description yields name/title/occurs/read_only/property_uri/allowed_values_uri."""
    f.mock_auth()
    respx.get(f.shape_uri("task")).mock(
        return_value=httpx.Response(200, text=f.real_shape_xml("task", REAL_TASK_PROPS, rotate=rotate))
    )
    props = _by_name(await oslc.list_shape_properties(f.shape_uri("task")))
    assert set(props) == {"filedAgainst", "shortTitle", "summary"}
    fa = props["filedAgainst"]
    assert fa["title"] == "Filed Against"  # dcterms:title with rdf:parseType="Literal"
    assert fa["occurs"] == "Exactly-one"
    assert fa["required"] is True
    assert fa["read_only"] is False
    assert fa["property_uri"] == f"{f.RTC_CM}filedAgainst"
    assert fa["allowed_values_uri"] == f.allowed_values_url("task")
    assert fa["range_uri"] == f"{f.RTC_CM}Category"
    short = props["shortTitle"]
    assert short["title"] == "Short Name"
    assert short["read_only"] is True
    assert short["occurs"] == "Zero-or-one"
    assert short["property_uri"] == "http://open-services.net/ns/core#shortTitle"
    assert short["allowed_values_uri"] is None


@respx.mock
async def test_d2_list_shape_properties_ignores_the_resource_shape_description():
    """D2 loop 1: the rdf:Description of the ResourceShape (no rdf:type Property) is not a property."""
    f.mock_auth()
    respx.get(f.shape_uri("task")).mock(
        return_value=httpx.Response(200, text=f.real_shape_xml("task", REAL_TASK_PROPS))
    )
    props = await oslc.list_shape_properties(f.shape_uri("task"))
    assert len(props) == len(REAL_TASK_PROPS)
    assert all(p["name"] for p in props)
    assert all(p["title"] != "Shape of task" for p in props)


@respx.mock
async def test_d2_list_fields_ccm_real_format_partitions_required_and_optional():
    """D2 loop 1: list_fields on the real format is not empty and keeps the required/optional partition."""
    f.mock_ccm_discovery()
    respx.get(f.shape_uri("task")).mock(
        return_value=httpx.Response(200, text=f.real_shape_xml("task", REAL_TASK_PROPS))
    )
    respx.get(f.allowed_values_url("task")).mock(
        return_value=httpx.Response(200, text=f.allowed_values_xml("task", [f.CAT_DEFAULT, f.CAT_2]))
    )
    result = await list_fields("ccm", PROJECT, "task")
    assert _names(result["required"]) == {"filedAgainst", "summary"}
    assert _names(result["optional"]) == {"shortTitle"}
    fa = {p["name"]: p for p in result["required"]}["filedAgainst"]
    assert [v["value_uri"] for v in fa["allowed_values"]] == [f.CAT_DEFAULT, f.CAT_2]


@respx.mock
async def test_d2_list_shape_properties_legacy_oslc_property_format_still_works():
    """D2 loop 1 (no regression RM/QM): the old <oslc:Property> element format is still parsed."""
    f.mock_auth()
    respx.get(f.shape_uri("task")).mock(return_value=httpx.Response(200, text=_legacy_type_shape("marker_task")))
    props = _by_name(await oslc.list_shape_properties(f.shape_uri("task")))
    assert set(props) == {"summary", "priority", "estimate", "tags", "marker_task"}
    assert props["priority"]["occurs"] == "One-or-many"
    assert props["priority"]["allowed_values_uri"] == ALLOWED_URI
    assert props["estimate"]["read_only"] is True


@respx.mock
async def test_d2_list_fields_ccm_shape_with_properties_but_none_extracted_raises_toolerror():
    """D2 loop 1 EARS: shape lists properties (oslc:property refs) but none is extracted -> ToolError, not []."""
    f.mock_ccm_discovery()
    refs = "".join(f'<oslc:property rdf:resource="{f.shape_uri("task")}/property/p{i}"/>' for i in range(3))
    unparseable = f"""<rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#"
        xmlns:dcterms="http://purl.org/dc/terms/" xmlns:oslc="http://open-services.net/ns/core#">
        <rdf:Description rdf:about="{f.shape_uri("task")}">
          <rdf:type rdf:resource="http://open-services.net/ns/core#ResourceShape"/>{refs}
        </rdf:Description></rdf:RDF>"""
    respx.get(f.shape_uri("task")).mock(return_value=httpx.Response(200, text=unparseable))
    with pytest.raises(ToolError):
        await list_fields("ccm", PROJECT, "task")


# ======================================================================
# D6 — owner in create_workitem -> dcterms:contributor (never rtc_cm:ownedBy)
# ======================================================================
def _setup_create(type_id: str = "task"):
    auth = f.mock_ccm_discovery()
    f.mock_creation_context()
    typed = respx.post(f.factory_uri(type_id)).mock(return_value=httpx.Response(201, json=CREATED))
    generic = respx.post(f.GENERIC_FACTORY).mock(return_value=httpx.Response(201, json=CREATED))
    return auth, typed, generic


def _contributor_uris(body: str) -> list[str]:
    root = ET.fromstring(body)
    return [e.get(f"{NS_RDF}resource") for e in root.iter(f"{NS_DCTERMS}contributor")]


def _posted_body() -> str:
    """Body of the single work item POST, whichever factory received it (keeps D6 independent of D9)."""
    posts = [
        c.request for c in respx.calls if c.request.method == "POST" and "j_security_check" not in str(c.request.url)
    ]
    assert len(posts) == 1
    return posts[0].content.decode()


@respx.mock
async def test_d6_create_workitem_owner_goes_to_dcterms_contributor():
    """D6 EARS-3: owner in create -> <dcterms:contributor rdf:resource=…/users/jdoe/>; no ownedBy anywhere."""
    _setup_create()
    await create_workitem(project=PROJECT, title="T", type="task", owner="jdoe", if_exists="create")
    body = _posted_body()
    assert _contributor_uris(body) == [f"{BASE}/jts/users/jdoe"]
    assert "ownedBy" not in body


@respx.mock
async def test_d6_create_workitem_owner_is_url_quoted():
    """D6: owner is quoted like update_workitem does (space -> %20), keeping the XML attribute valid."""
    _setup_create()
    await create_workitem(project=PROJECT, title="T", type="task", owner="j doe", if_exists="create")
    assert _contributor_uris(_posted_body()) == [f"{BASE}/jts/users/j%20doe"]


@respx.mock
async def test_d6_create_workitem_without_owner_has_no_contributor_nor_ownedby():
    """D6 negative: no owner -> neither dcterms:contributor nor rtc_cm:ownedBy in the body."""
    _setup_create()
    await create_workitem(project=PROJECT, title="T", type="task", if_exists="create")
    body = _posted_body()
    assert _contributor_uris(body) == []
    assert "ownedBy" not in body


@respx.mock
async def test_d6_add_child_workitem_owner_goes_to_dcterms_contributor():
    """D6: add_child_workitem delegates to create_workitem -> same owner mapping, parent link kept."""
    _setup_create()
    await add_child_workitem(parent_id="10", title="Child", project=PROJECT, owner="jdoe", if_exists="create")
    body = _posted_body()
    assert _contributor_uris(body) == [f"{BASE}/jts/users/jdoe"]
    assert "ownedBy" not in body
    assert "WorkItem/10" in body


# ======================================================================
# D9 — create_workitem posts to the factory OF THE TYPE (RDF/XML)
# ======================================================================
@pytest.mark.parametrize("type_arg", ["task", "story"])
@respx.mock
async def test_d9_create_workitem_posts_to_the_type_factory(type_arg):
    """D9 EARS-4: POST goes to …/workitems/{type id}, as RDF/XML; the generic factory is untouched."""
    type_id = {"task": "task", "story": STORY_ID}[type_arg]
    _, typed, generic = _setup_create(type_id)
    result = await create_workitem(project=PROJECT, title="T", type=type_arg, if_exists="create")
    assert result["dcterms:identifier"] == "42"
    assert typed.call_count == 1
    assert generic.call_count == 0
    request = typed.calls[0].request
    assert request.headers["content-type"] == "application/rdf+xml"
    root = ET.fromstring(request.content.decode())  # well-formed RDF/XML
    assert root.tag == f"{NS_RDF}RDF"
    assert f"/types/_PA1/{type_id}" in request.content.decode()


@respx.mock
async def test_d9_create_resource_posts_to_custom_type_factory_by_id():
    """D9: a custom type id known from list_types (com.example.workitem.debt) resolves to its own factory."""
    _, typed, generic = _setup_create(DEBT_ID)
    await oslc.create_resource("ccm", PROJECT, {"dcterms:title": "Debt"}, wi_type=DEBT_ID)
    assert typed.call_count == 1
    assert generic.call_count == 0


@respx.mock
async def test_d9_create_resource_unknown_wi_type_raises_without_posting():
    """D9: type without factory -> ValueError listing available types; no silent fallback to the generic factory."""
    _, typed, generic = _setup_create()
    with pytest.raises(ValueError, match="nonexistent") as exc:
        await oslc.create_resource("ccm", PROJECT, {"dcterms:title": "X"}, wi_type="nonexistent")
    assert "task" in str(exc.value)
    assert not generic.called
    assert not typed.called


@respx.mock
async def test_d9_server_403_with_oslc_message_becomes_toolerror_without_reauth_or_retry():
    """D9 EARS-5 + decision 'Extra': 403 + oslc:message -> ToolError carrying the message; ONE POST; no re-auth."""
    auth, typed, generic = _setup_create()
    typed.mock(return_value=httpx.Response(403, json={"oslc:message": SERVER_MESSAGE}))
    with pytest.raises(ToolError) as exc:
        await create_workitem(project=PROJECT, title="T", type="task", if_exists="create")
    assert SERVER_MESSAGE in str(exc.value)
    assert typed.call_count == 1
    assert generic.call_count == 0
    assert auth.call_count == 1  # only the initial login, no re-authentication


@respx.mock
async def test_d9_server_409_with_oslc_message_becomes_toolerror():
    """D9 EARS-5: any refused write with oslc:message (e.g. 409) surfaces the server message."""
    _, typed, _ = _setup_create()
    typed.mock(return_value=httpx.Response(409, json={"oslc:message": "Unknown attribute foo"}))
    with pytest.raises(ToolError, match="Unknown attribute foo"):
        await create_workitem(project=PROJECT, title="T", type="task", if_exists="create")


@respx.mock
async def test_d9_401_reauthenticates_once_and_resends_to_the_type_factory():
    """D9 / decision 'Retry': 401 -> re-authenticate exactly once and re-POST to the type factory."""
    auth, typed, generic = _setup_create()
    typed.mock(side_effect=[httpx.Response(401, text="Unauthorized"), httpx.Response(201, json=CREATED)])
    result = await create_workitem(project=PROJECT, title="T", type="task", if_exists="create")
    assert result["dcterms:identifier"] == "42"
    assert typed.call_count == 2
    assert generic.call_count == 0
    assert auth.call_count == 2  # initial login + one re-authentication


@respx.mock
async def test_d9_persistent_401_retries_only_once():
    """D9: a second 401 after re-authentication is an error (no retry loop)."""
    auth, typed, _ = _setup_create()
    typed.mock(return_value=httpx.Response(401, text="Unauthorized"))
    with pytest.raises(ValueError, match="sessao"):
        await oslc.create_resource("ccm", PROJECT, {"dcterms:title": "X"}, wi_type="task")
    assert typed.call_count == 2
    assert auth.call_count == 2


# ======================================================================
# D10 — transition_workitem
# ======================================================================
def _put_request(routes: dict) -> httpx.Request:
    assert routes["put"].call_count == 1
    return routes["put"].calls[0].request


@respx.mock
async def test_d10_transition_executes_via_action_param_with_if_match_and_rereads():
    """D10 EARS-6: PUT {item}?_action=<id>&oslc.properties=dcterms:title, If-Match, body only the title; re-read."""
    routes = f.mock_workflow()
    result = await transition_workitem(id=f.WI_ID, action="startWorking")
    request = _put_request(routes)
    full_id = f.ACTION_PREFIX + "startWorking"
    assert request.url.params.get("_action") == full_id
    assert request.url.params.get("oslc.properties") == "dcterms:title"
    assert request.headers["if-match"] == '"etag1"'
    assert request.headers["content-type"].startswith("application/json")
    body = json.loads(request.content)
    assert set(body) - {"prefixes"} == {"dcterms:title"}
    assert body["dcterms:title"] == f.ITEM_TITLE
    assert routes["item"].call_count == 2  # before + re-read after
    assert routes["actions"].called
    assert "Idea" in result
    assert "In Progress" in result
    assert f.WI_ID in result
    assert full_id in result


@respx.mock
async def test_d10_transition_never_puts_rtc_cm_state_nor_uses_workflowactions_url():
    """D10 EARS-8: state is never written via rtc_cm:state; the bogus {item}/rtc_cm:workflowActions is not used."""
    routes = f.mock_workflow()
    await transition_workitem(id=f.WI_ID, action="startWorking")
    request = _put_request(routes)
    assert "rtc_cm:state" not in request.content.decode()
    assert "rtc_cm:state" not in str(request.url)
    requested = [str(c.request.url) for c in respx.calls]
    assert not any("workflowActions" in u for u in requested)


@pytest.mark.parametrize(
    ("action", "expected_suffix"),
    [
        (f.ACTION_PREFIX + "a12", "a12"),  # rule 1: full id
        ("a12", "a12"),  # rule 2: suffix .a12
        ("a2", "a2"),  # rule 2 wins over substring (a2 is also inside a27)
        ("confirm", "confirm"),  # rule 2
        ("start", "startWorking"),  # rule 3: unique substring
        ("STARTWORKING", "startWorking"),  # rule 3 is case-insensitive
        ("A12", "a12"),  # rule 3 case-insensitive, unique
    ],
)
@respx.mock
async def test_d10_transition_resolves_action_by_id_suffix_or_unique_substring(action, expected_suffix):
    """D10 EARS-6 order: exact id, then `.{action}` suffix, then a unique case-insensitive substring."""
    routes = f.mock_workflow()
    await transition_workitem(id=f.WI_ID, action=action)
    request = _put_request(routes)
    assert request.url.params.get("_action") == f.ACTION_PREFIX + expected_suffix


@respx.mock
async def test_d10_transition_ambiguous_action_raises_toolerror_listing_ids_without_writing():
    """D10 EARS-7: 'close' matches closeStory and closeOld -> ToolError with the ids; no PUT."""
    routes = f.mock_workflow()
    with pytest.raises(ToolError) as exc:
        await transition_workitem(id=f.WI_ID, action="close")
    message = str(exc.value)
    assert f.ACTION_PREFIX + "closeStory" in message
    assert f.ACTION_PREFIX + "closeOld" in message
    assert routes["put"].call_count == 0


@respx.mock
async def test_d10_transition_unknown_action_raises_toolerror_listing_available_ids_without_writing():
    """D10 EARS-7: no candidate -> ToolError listing the workflow's action ids (original case); no PUT."""
    routes = f.mock_workflow()
    with pytest.raises(ToolError) as exc:
        await transition_workitem(id=f.WI_ID, action="invalid")
    message = str(exc.value)
    for action_id in f.ACTION_IDS:
        assert action_id in message
    assert routes["put"].call_count == 0


@respx.mock
async def test_d10_transition_200_without_state_change_raises_toolerror():
    """D10 EARS-9: server answers 200 but the re-read state is unchanged -> 'does not apply' ToolError."""
    routes = f.mock_workflow(after_state=f.STATE_IDLE)
    with pytest.raises(ToolError, match=r"(?i)não se aplica|nao se aplica|does not apply|not applicable"):
        await transition_workitem(id=f.WI_ID, action="startWorking")
    assert routes["put"].call_count == 1
    assert routes["item"].call_count == 2


@respx.mock
async def test_d10_transition_403_precondition_raises_toolerror_with_server_message_no_retry():
    """D10: 403 + oslc:message -> ToolError carrying the message; one PUT; no re-authentication."""
    routes = f.mock_workflow(put_response=httpx.Response(403, json={"oslc:message": SERVER_MESSAGE}))
    with pytest.raises(ToolError) as exc:
        await transition_workitem(id=f.WI_ID, action="startWorking")
    assert SERVER_MESSAGE in str(exc.value)
    assert routes["put"].call_count == 1
    assert routes["auth"].call_count == 1


@respx.mock
async def test_d10_transition_state_names_fall_back_to_last_uri_segment():
    """D10 decision 7: without the states listing, state names are the last URI segment."""
    f.mock_workflow(states=False)
    result = await transition_workitem(id=f.WI_ID, action="startWorking")
    assert f.STATE_IDLE in result
    assert f.STATE_RUNNING in result


# ======================================================================
# D11 — partial update via ?oslc.properties
# ======================================================================
U_ID = "42"
U_URI = f"{BASE}/ccm/resource/itemName/com.ibm.team.workitem.WorkItem/{U_ID}"
FULL_OLD = {
    "rdf:about": U_URI,
    "dcterms:identifier": U_ID,
    "dcterms:title": "Old",
    "rtc_cm:estimate": 1,
    **{f"rtc_cm:field{i}": i for i in range(130)},
}


def _mock_update(put_response: httpx.Response | None = None, new_item: dict | None = None):
    f.mock_auth()
    get = respx.get(U_URI).mock(
        side_effect=[
            httpx.Response(200, json=FULL_OLD, headers={"ETag": '"e1"'}),
            httpx.Response(200, json=new_item or FULL_OLD, headers={"ETag": '"e2"'}),
        ]
    )
    put = respx.put(U_URI).mock(
        return_value=put_response or httpx.Response(200, json={"rdf:about": U_URI}, headers={"ETag": '"e2"'})
    )
    return get, put


@respx.mock
async def test_d11_update_resource_sends_only_changed_properties_with_if_match():
    """D11 EARS-10: PUT ?oslc.properties=<changed>, If-Match, body without the 130+ fields of the item."""
    _, put = _mock_update()
    await oslc.update_resource(U_URI, {"rtc_cm:estimate": 28800000})
    assert put.call_count == 1
    request = put.calls[0].request
    assert request.url.params.get("oslc.properties") == "rtc_cm:estimate"
    assert request.headers["if-match"] == '"e1"'
    assert request.headers["content-type"].startswith("application/json")
    sent = json.loads(request.content)
    assert set(sent) - {"prefixes"} == {"rtc_cm:estimate"}
    assert sent["rtc_cm:estimate"] == 28800000


@respx.mock
async def test_d11_update_workitem_lists_every_changed_property_and_only_those():
    """D11 EARS-10: update_workitem(title, owner, estimate) -> oslc.properties = exactly those three."""
    _, put = _mock_update()
    await update_workitem(id=U_ID, title="New", owner="jdoe", estimate_hours=8)
    request = put.calls[0].request
    declared = set(request.url.params.get("oslc.properties", "").split(","))
    assert declared == {"dcterms:title", "dcterms:contributor", "rtc_cm:estimate"}
    sent = json.loads(request.content)
    assert set(sent) - {"prefixes"} == declared
    assert sent["dcterms:contributor"] == {"rdf:resource": f"{BASE}/jts/users/jdoe"}
    assert sent["rtc_cm:estimate"] == 28800000
    assert "dcterms:identifier" not in sent
    assert "rdf:about" not in sent
    assert request.headers["if-match"] == '"e1"'


@respx.mock
async def test_d11_update_resource_returns_the_reread_resource():
    """D11 decision 9: the return value is the item re-read by GET (not the partial PUT response)."""
    new_item = {**FULL_OLD, "rtc_cm:estimate": 28800000}
    get, _ = _mock_update(new_item=new_item)
    result = await oslc.update_resource(U_URI, {"rtc_cm:estimate": 28800000})
    assert get.call_count == 2
    assert result["rtc_cm:estimate"] == 28800000
    assert result["dcterms:identifier"] == U_ID  # fields not in the partial PUT response
    assert result["rtc_cm:field129"] == 129


@respx.mock
async def test_d11_update_resource_412_raises_conflict_valueerror():
    """D11 EARS-10: 412 (stale ETag, CRJAZ0007I) -> ValueError that says it is a conflict."""
    _mock_update(put_response=httpx.Response(412, json={"oslc:message": "CRJAZ0007I: stale"}))
    with pytest.raises(ValueError, match=r"(?i)conflit|conflict"):
        await oslc.update_resource(U_URI, {"dcterms:title": "X"})


@respx.mock
async def test_d11_update_workitem_412_raises_toolerror_conflict():
    """D11 EARS-10: at the tool boundary the 412 is a ToolError of conflict."""
    _mock_update(put_response=httpx.Response(412, json={"oslc:message": "CRJAZ0007I: stale"}))
    with pytest.raises(ToolError, match=r"(?i)conflit|conflict"):
        await update_workitem(id=U_ID, title="X")


@respx.mock
async def test_d11_update_workitem_403_with_oslc_message_raises_toolerror_with_message_no_retry():
    """EARS-5 on update: refused write with oslc:message -> ToolError containing it; one PUT."""
    _, put = _mock_update(put_response=httpx.Response(403, json={"oslc:message": SERVER_MESSAGE}))
    with pytest.raises(ToolError) as exc:
        await update_workitem(id=U_ID, title="X")
    assert SERVER_MESSAGE in str(exc.value)
    assert put.call_count == 1


# ======================================================================
# D13 (revised) — no automatic category: filed_against is sent only when the caller gives it
# ======================================================================
FILED_AGAINST_TAG = "}filedAgainst"
CATEGORIES_PATH = "/ccm/oslc/categories"
CATEGORY_ERROR_MESSAGE = "Attribute Filed Against needs to be set"


def _filed_against_uris(body: str) -> list[str]:
    root = ET.fromstring(body)
    return [e.get(f"{NS_RDF}resource") for e in root.iter() if e.tag.endswith(FILED_AGAINST_TAG)]


def _categories_calls() -> list:
    return [c for c in respx.calls if CATEGORIES_PATH in str(c.request.url)]


def _allowed_values_calls(type_id: str) -> list:
    """GETs on the allowedValues of the type's category property (what the removed default lookup read)."""
    return [c for c in respx.calls if c.request.method == "GET" and str(c.request.url) == f.allowed_values_url(type_id)]


@pytest.mark.parametrize("type_arg", ["task", "story"])
@respx.mock
async def test_d13_create_workitem_without_filed_against_omits_the_property(type_arg):
    """D13: no filed_against -> the POST body has no rtc_cm:filedAgainst (server default applies),
    even though the type shape carries a defaultValue."""
    type_id = {"task": "task", "story": STORY_ID}[type_arg]
    _setup_create(type_id)
    await create_workitem(project=PROJECT, title="T", type=type_arg, if_exists="create")
    body = _posted_body()
    assert "filedAgainst" not in body
    assert _filed_against_uris(body) == []


@pytest.mark.parametrize("type_arg", ["task", "story"])
@respx.mock
async def test_d13_create_workitem_without_filed_against_does_not_look_up_a_category(type_arg):
    """D13: no categories route call and no default category taken from the shape (W4: the shape is now read only
    to list missing required fields, never to fill filedAgainst)."""
    type_id = {"task": "task", "story": STORY_ID}[type_arg]
    _setup_create(type_id)
    await create_workitem(project=PROJECT, title="T", type=type_arg, if_exists="create")
    assert _categories_calls() == []
    assert _allowed_values_calls(type_id) == []
    assert "filedAgainst" not in _posted_body()
    assert "_OTHERPA" not in _posted_body()


@respx.mock
async def test_d13_create_workitem_explicit_filed_against_is_sent_as_is():
    """D13: explicit filed_against goes out unchanged (not the shape default), no category lookup."""
    _setup_create("task")
    await create_workitem(project=PROJECT, title="T", type="task", filed_against=f.CAT_2, if_exists="create")
    assert _filed_against_uris(_posted_body()) == [f.CAT_2]
    assert _categories_calls() == []
    assert _allowed_values_calls("task") == []


@respx.mock
async def test_d13_category_refusal_toolerror_has_server_message_and_list_fields_hint():
    """D13: 403 with an oslc:message about the category -> ToolError carries the server text
    and points to list_fields (filedAgainst); single POST, no retry."""
    _auth, typed, _generic = _setup_create("task")
    typed.mock(return_value=httpx.Response(403, json={"oslc:message": CATEGORY_ERROR_MESSAGE}))
    with pytest.raises(ToolError) as exc:
        await create_workitem(project=PROJECT, title="T", type="task", if_exists="create")
    text = str(exc.value)
    assert CATEGORY_ERROR_MESSAGE in text
    assert "list_fields" in text
    assert typed.call_count == 1


@respx.mock
async def test_d13_non_category_refusal_has_no_list_fields_hint():
    """D13: the hint is only for category refusals; an unrelated 403 message stays untouched."""
    _, typed, _ = _setup_create("task")
    typed.mock(return_value=httpx.Response(403, json={"oslc:message": SERVER_MESSAGE}))
    with pytest.raises(ToolError) as exc:
        await create_workitem(project=PROJECT, title="T", type="task", if_exists="create")
    assert SERVER_MESSAGE in str(exc.value)
    assert "list_fields" not in str(exc.value)


# ======================================================================
# Loop 2 do G5
# ======================================================================
@pytest.mark.parametrize("action", ["", " ", "  \t", "s"])
@respx.mock
async def test_g5l2_transition_blank_or_one_char_action_raises_toolerror_without_writing(action):
    """G5 L2 item 1: action vazio/só espaços/1 caractere não casa por substring; nenhum PUT/POST."""
    routes = f.mock_workflow()
    only_one = {"oslc:results": [{"rdf:about": f"{f.ACTIONS_URL}/{f.ACTION_PREFIX}startWorking"}]}
    routes["actions"].mock(return_value=httpx.Response(200, json=only_one))
    with pytest.raises(ToolError):
        await transition_workitem(id=f.WI_ID, action=action)
    assert routes["put"].call_count == 0
    assert not [
        c for c in respx.calls if c.request.method in ("PUT", "POST") and "j_security_check" not in str(c.request.url)
    ]


@respx.mock
async def test_g5l2_transition_exact_id_still_works_and_action_is_stripped():
    """G5 L2 item 1: id exato continua valendo; espaços ao redor são removidos."""
    routes = f.mock_workflow()
    await transition_workitem(id=f.WI_ID, action="  startWorking ")
    assert routes["put"].calls[0].request.url.params.get("_action") == f.ACTION_PREFIX + "startWorking"


@respx.mock
async def test_g5l2_create_403_without_oslc_message_reauthenticates_and_resends_once():
    """G5 L2 item 3: 403 sem oslc:message (CSRF/sessão obsoleta) -> reautentica e reenvia UMA vez."""
    auth, typed, _ = _setup_create()
    typed.mock(side_effect=[httpx.Response(403, text="Forbidden"), httpx.Response(201, json=CREATED)])
    result = await create_workitem(project=PROJECT, title="T", type="task", if_exists="create")
    assert result["dcterms:identifier"] == "42"
    assert typed.call_count == 2
    assert auth.call_count == 2


@respx.mock
async def test_g5l2_create_persistent_403_without_message_retries_only_once():
    """G5 L2 item 3: segundo 403 sem mensagem é erro (sem laço)."""
    auth, typed, _ = _setup_create()
    typed.mock(return_value=httpx.Response(403, text="Forbidden"))
    with pytest.raises(ValueError, match="permissao"):
        await oslc.create_resource("ccm", PROJECT, {"dcterms:title": "X"}, wi_type="task")
    assert typed.call_count == 2
    assert auth.call_count == 2


@respx.mock
async def test_g5l2_create_403_with_oslc_message_does_not_reauthenticate():
    """G5 L2 item 3: 403 com oslc:message continua indo direto ao ToolError (um POST, um login)."""
    auth, typed, _ = _setup_create()
    typed.mock(return_value=httpx.Response(403, json={"oslc:message": SERVER_MESSAGE}))
    with pytest.raises(ToolError, match="Preconditions"):
        await create_workitem(project=PROJECT, title="T", type="task", if_exists="create")
    assert typed.call_count == 1
    assert auth.call_count == 1


@respx.mock
async def test_g5l2_create_owner_path_traversal_is_fully_quoted():
    """G5 L2 item 4: owner 'jdoe/../admin' -> %2F no URI (sem separador de caminho)."""
    _setup_create()
    await create_workitem(project=PROJECT, title="T", type="task", owner="jdoe/../admin", if_exists="create")
    assert _contributor_uris(_posted_body()) == [f"{BASE}/jts/users/jdoe%2F..%2Fadmin"]


@respx.mock
async def test_g5l2_update_owner_path_traversal_is_fully_quoted():
    """G5 L2 item 4: o mesmo em update_workitem."""
    _, put = _mock_update()
    await update_workitem(id=U_ID, owner="jdoe/../admin")
    sent = json.loads(put.calls[0].request.content)
    assert sent["dcterms:contributor"] == {"rdf:resource": f"{BASE}/jts/users/jdoe%2F..%2Fadmin"}


@respx.mock
async def test_g5l2_list_types_unescapes_html_entities_in_factory_title(monkeypatch):
    """G5 L2 item 6: título da fábrica com entidade XML ('R&amp;D') sai como 'R&D'."""
    original = f.services_xml()
    monkeypatch.setattr(f, "services_xml", lambda: original.replace("Technical Debt", "R&amp;D"))
    f.mock_ccm_discovery()
    _mock_property_only_shapes()
    by_id = {r["id"]: r for r in await list_types("ccm", PROJECT)}
    assert by_id[DEBT_ID]["title"] == "R&D"


# ======================================================================
# G5 review: rdf:resource attribute escaping + rtc_cm:teamArea in the POST
# ======================================================================
def test_payload_to_rdfxml_escapes_rdf_resource_values():
    """A `"`, `&` or `<` in rdf:resource (single or list) neither injects an attribute nor breaks the XML."""
    evil = 'x" a="1&<b>'
    xml = oslc._payload_to_rdfxml(
        {"rtc_cm:teamArea": {"rdf:resource": evil}, "rtc_cm:related": [{"rdf:resource": evil}]}
    )
    root = ET.fromstring(xml)
    for el in root.iter():
        if el.tag.endswith("teamArea") or el.tag.endswith("related"):
            assert el.attrib == {f"{NS_RDF}resource": evil}


@respx.mock
async def test_create_workitem_posts_rtc_cm_team_area_as_rdf_resource():
    _setup_create()
    uri = f"{BASE}/ccm/oslc/teamareas/_TEAM00000000000000000A"
    await create_workitem(
        project=PROJECT,
        title="T",
        type="task",
        custom_fields={"rtc_cm:teamArea": {"rdf:resource": uri}},
        if_exists="create",
    )
    body = _posted_body()
    assert f'<rtc_cm:teamArea rdf:resource="{uri}"/>' in body
    elements = list(ET.fromstring(body).iter("{http://jazz.net/xmlns/prod/jazz/rtc/cm/1.0/}teamArea"))
    assert [e.get(f"{NS_RDF}resource") for e in elements] == [uri]
