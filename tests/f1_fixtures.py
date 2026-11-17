"""Shared tenant-neutral fixtures for the F1 (CCM corrections) tests.

Formats mirror real server captures (ELM 7.0.1 SR1),
with neutral values: host https://elm.example.com, project area _PA1, user jdoe,
custom type com.example.workitem.debt.
"""

import httpx
import respx

BASE = "https://elm.example.com"
PA = "_PA1"
PROJECT = "Project One"
CR_TYPE = "http://open-services.net/ns/cm#ChangeRequest"

STORY_ID = "com.ibm.team.apt.workItemType.story"
DEBT_ID = "com.example.workitem.debt"

ROOTSERVICES_XML = f"""<?xml version="1.0" encoding="UTF-8"?>
<rdf:Description xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#"
    xmlns:oslc_cm="http://open-services.net/xmlns/cm/1.0/">
    <oslc_cm:cmServiceProviders rdf:resource="{BASE}/ccm/oslc/workitems/catalog"/>
</rdf:Description>
"""


def catalog_xml(about: str) -> str:
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<oslc:ServiceProviderCatalog
    xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#"
    xmlns:dcterms="http://purl.org/dc/terms/"
    xmlns:oslc="http://open-services.net/ns/core#">
    <oslc:serviceProvider>
        <oslc:ServiceProvider rdf:about="{about}">
            <dcterms:title>{PROJECT}</dcterms:title>
        </oslc:ServiceProvider>
    </oslc:serviceProvider>
</oslc:ServiceProviderCatalog>
"""


def shape_uri(type_id: str) -> str:
    return f"{BASE}/ccm/oslc/context/{PA}/shapes/workitems/{type_id}"


def factory_uri(type_id: str) -> str:
    return f"{BASE}/ccm/oslc/contexts/{PA}/workitems/{type_id}"


GENERIC_FACTORY = f"{BASE}/ccm/oslc/contexts/{PA}/workitems"

# (type id, raw factory dcterms:title). Raw titles carry the trailing space seen on the server.
TYPED_FACTORIES = [
    ("task", "Location for creation of Task change requests "),
    (STORY_ID, "Location for creation of Story change requests "),
    ("defect", "Location for creation of Defect change requests "),
    ("com.ibm.team.workitem.workItemType.risk", "Location for creation of Risk change requests "),
    # Title that does NOT follow the "Location for creation of {Nome} change requests" pattern.
    ("com.ibm.team.workitem.workItemType.impediment", "Impediment factory "),
    (DEBT_ID, "Location for creation of Technical Debt change requests "),
]


def _factory_block(title: str, creation: str, shape: str | None, rtype: str = CR_TYPE, usage: str | None = None) -> str:
    shape_el = f'<oslc:resourceShape rdf:resource="{shape}"/>' if shape else ""
    usage_el = f'<oslc:usage rdf:resource="{usage}"/>' if usage else ""
    return f"""
        <oslc:CreationFactory>
            <dcterms:title>{title}</dcterms:title>
            <oslc:resourceType rdf:resource="{rtype}"/>
            <oslc:creation rdf:resource="{creation}"/>
            {shape_el}
            {usage_el}
        </oslc:CreationFactory>"""


def services_xml() -> str:
    """Service provider with 6 typed factories, 2 generic ones and 1 decoy (other resourceType)."""
    blocks = [_factory_block(t, factory_uri(i), shape_uri(i)) for i, t in TYPED_FACTORIES]
    # Generic factory (shape 'defect') and the drafts factory (no shape): both at /workitems.
    blocks.append(
        _factory_block("Location for creation of Defect change requests ", GENERIC_FACTORY, shape_uri("defect"))
    )
    blocks.append(
        _factory_block(
            "Location for creation of Draft change requests ",
            GENERIC_FACTORY,
            None,
            usage="http://jazz.net/xmlns/prod/jazz/rtc/cm/1.0/drafts",
        )
    )
    # Decoy: resourceType only CONTAINS the change-request type (exact match required).
    blocks.append(
        _factory_block(
            "Location for creation of Decoy change requests ",
            factory_uri("decoy"),
            shape_uri("decoy"),
            rtype=CR_TYPE + "Extra",
        )
    )
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#"
    xmlns:dcterms="http://purl.org/dc/terms/"
    xmlns:oslc="http://open-services.net/ns/core#">
    <oslc:Service>
        <oslc:creationFactory>{"".join(blocks)}
        </oslc:creationFactory>
    </oslc:Service>
</rdf:RDF>
"""


def mock_auth() -> respx.Route:
    return respx.post(f"{BASE}/jts/j_security_check").mock(return_value=httpx.Response(200))


def mock_ccm_discovery(form: str = "services_xml") -> respx.Route:
    """rootservices -> catalog -> service provider XML. Returns the j_security_check route.

    form="services_xml": catalog rdf:about = .../contexts/_PA1/workitems/services.xml
    form="contexts":     catalog rdf:about = .../contexts/_PA1 (XML served at .../services)
    """
    auth = mock_auth()
    respx.get(f"{BASE}/ccm/rootservices").mock(return_value=httpx.Response(200, text=ROOTSERVICES_XML))
    xml = services_xml()
    if form == "services_xml":
        about = f"{BASE}/ccm/oslc/contexts/{PA}/workitems/services.xml"
        respx.get(f"{BASE}/ccm/oslc/contexts/{PA}/workitems/services").mock(return_value=httpx.Response(404))
        respx.get(about).mock(return_value=httpx.Response(200, text=xml))
    else:
        about = f"{BASE}/ccm/oslc/contexts/{PA}"
        respx.get(f"{about}/services").mock(return_value=httpx.Response(200, text=xml))
        respx.get(about).mock(return_value=httpx.Response(200, text=xml))
    respx.get(f"{BASE}/ccm/oslc/workitems/catalog").mock(return_value=httpx.Response(200, text=catalog_xml(about)))
    return auth


CORE = "http://open-services.net/ns/core#"
RTC_CM = "http://jazz.net/xmlns/prod/jazz/rtc/cm/1.0/"
CAT_BASE = f"{BASE}/ccm/resource/itemOid/com.ibm.team.workitem.Category"
CAT_DEFAULT = f"{CAT_BASE}/_CATDEFAULT"
CAT_2 = f"{CAT_BASE}/_CAT2"
CAT_3 = f"{CAT_BASE}/_CAT3"


def prop(
    name: str,
    title: str,
    occurs: str = "Zero-or-one",
    read_only: bool = False,
    *,
    about_id: str | None = None,
    propdef: str | None = None,
    default: str | None = None,
    allowed: str | None = None,
    range_uri: str | None = None,
) -> dict:
    """Spec of one shape property (rendered by real_shape_xml)."""
    return {
        "name": name,
        "title": title,
        "occurs": occurs,
        "read_only": read_only,
        "about_id": about_id or name,
        "propdef": propdef or f"{BASE}/ccm/oslc/properties/{name}",
        "default": default,
        "allowed": allowed,
        "range": range_uri,
    }


def _prop_description(type_id: str, p: dict, rotation: int) -> str:
    """One REAL-format property block: typed rdf:Description, children in a rotated order."""
    xsd = "http://www.w3.org/2001/XMLSchema#"
    children = [
        f'<oslc:occurs rdf:resource="{CORE}{p["occurs"]}"/>',
        f'<oslc:propertyDefinition rdf:resource="{p["propdef"]}"/>',
        f'<oslc:valueType rdf:resource="{CORE}Resource"/>',
        f'<dcterms:title rdf:parseType="Literal">{p["title"]}</dcterms:title>',
        f'<oslc:readOnly rdf:datatype="{xsd}boolean">{str(p["read_only"]).lower()}</oslc:readOnly>',
        f'<oslc:name rdf:datatype="{xsd}string">{p["name"]}</oslc:name>',
        f'<rdf:type rdf:resource="{CORE}Property"/>',
    ]
    if p["default"]:
        children.append(f'<oslc:defaultValue rdf:resource="{p["default"]}"/>')
    if p["allowed"]:
        children.append(f'<oslc:allowedValues rdf:resource="{p["allowed"]}"/>')
    if p["range"]:
        children.append(f'<oslc:range rdf:resource="{p["range"]}"/>')
    k = rotation % len(children)
    children = children[k:] + children[:k]
    about = f"{shape_uri(type_id)}/property/{p['about_id']}"
    return f'<rdf:Description rdf:about="{about}">' + "".join(children) + "</rdf:Description>"


def real_shape_xml(type_id: str, props: list[dict], *, rotate: bool = True) -> str:
    """ResourceShape in the format the real server uses (rdf:Description typed core#Property).

    Includes the rdf:Description of the ResourceShape itself (no rdf:type Property),
    which must NOT be parsed as a property. Children order varies per block when rotate=True.
    """
    refs = "".join(f'<oslc:property rdf:resource="{shape_uri(type_id)}/property/{p["about_id"]}"/>' for p in props)
    shape_desc = (
        f'<rdf:Description rdf:about="{shape_uri(type_id)}">'
        f'<rdf:type rdf:resource="{CORE}ResourceShape"/>'
        f'<dcterms:title rdf:parseType="Literal">Shape of {type_id}</dcterms:title>'
        f"{refs}</rdf:Description>"
    )
    blocks = "".join(_prop_description(type_id, p, i if rotate else 0) for i, p in enumerate(props))
    return (
        '<rdf:RDF xmlns:dcterms="http://purl.org/dc/terms/" '
        'xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#" '
        'xmlns:oslc="http://open-services.net/ns/core#">' + shape_desc + blocks + "</rdf:RDF>"
    )


def filed_against_prop(*, default: str | None, allowed: str | None) -> dict:
    """filedAgainst as on the server: rdf:about ends in /property/category, name is filedAgainst."""
    return prop(
        "filedAgainst",
        "Filed Against",
        "Exactly-one",
        about_id="category",
        propdef=f"{RTC_CM}filedAgainst",
        default=default,
        allowed=allowed,
        range_uri=f"{RTC_CM}Category",
    )


def allowed_values_url(type_id: str) -> str:
    return f"{shape_uri(type_id)}/property/category/allowedValues"


def allowed_values_xml(type_id: str, uris: list[str]) -> str:
    vals = "".join(f'<oslc:allowedValue rdf:resource="{u}"/>' for u in uris)
    return (
        '<rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#" '
        'xmlns:oslc="http://open-services.net/ns/core#">'
        f'<rdf:Description rdf:about="{allowed_values_url(type_id)}">{vals}</rdf:Description></rdf:RDF>'
    )


def default_task_props() -> list[dict]:
    """Shape props for creation tests. 'priority' (with a defaultValue) comes BEFORE filedAgainst on purpose."""
    return [
        prop(
            "priority",
            "Priority",
            default=f"{BASE}/ccm/oslc/enumerations/{PA}/priority/medium",
            propdef=f"{RTC_CM}priority",
        ),
        filed_against_prop(default=CAT_DEFAULT, allowed=allowed_values_url("task")),
        prop("plannedFor", "Planned For", propdef=f"{RTC_CM}plannedFor"),
    ]


def mock_creation_context(*, shapes: bool = True) -> None:
    """Category (legacy decoy) + iteration discovery and session-refresh GETs used by create_workitem.

    shapes=True also mocks every typed shape in the REAL format (filedAgainst default = _CATDEFAULT).
    The legacy /categories route stays mocked with a decoy category from ANOTHER project area.
    """
    respx.get(f"{BASE}/ccm/oslc/categories").mock(
        return_value=httpx.Response(
            200,
            json={"oslc:results": [{"rdf:about": f"{CAT_BASE}/_OTHERPA"}]},
        )
    )
    respx.get(f"{BASE}/ccm/oslc/iterations").mock(return_value=httpx.Response(200, json={"oslc:results": []}))
    # Session refresh GET on generic and typed factories (create_resource does this before POST).
    respx.get(GENERIC_FACTORY).mock(return_value=httpx.Response(200, json={}))
    for type_id, _ in TYPED_FACTORIES:
        respx.get(factory_uri(type_id)).mock(return_value=httpx.Response(200, json={}))
        if shapes:
            respx.get(shape_uri(type_id)).mock(
                return_value=httpx.Response(200, text=real_shape_xml(type_id, default_task_props()))
            )
    if shapes:
        respx.get(allowed_values_url("task")).mock(
            return_value=httpx.Response(200, text=allowed_values_xml("task", [CAT_DEFAULT, CAT_2]))
        )


# --------------------------------------------------------------------------
# Workflow / transition fixtures (formats from G1-respostas-manager.md, items 5 and 9)
# --------------------------------------------------------------------------
WF = "com.ibm.team.apt.storyWorkflow"
WI_ID = "100"
WI_URI = f"{BASE}/ccm/resource/itemName/com.ibm.team.workitem.WorkItem/{WI_ID}"
ACTIONS_URL = f"{BASE}/ccm/oslc/workflows/{PA}/actions/{WF}"
STATES_URL = f"{BASE}/ccm/oslc/workflows/{PA}/states/{WF}"
ACTION_PREFIX = f"{WF}.action."
ACTION_IDS = [
    ACTION_PREFIX + s for s in ("a12", "a16", "a2", "a27", "startWorking", "closeStory", "closeOld", "confirm")
]
STATE_IDLE = "com.ibm.team.apt.story.idea"
STATE_RUNNING = "com.ibm.team.apt.story.inprogress"
ITEM_TITLE = "Story title"


def state_uri(state_id: str) -> str:
    return f"{STATES_URL}/{state_id}"


def item_json(state_id: str = STATE_IDLE) -> dict:
    return {
        "rdf:about": WI_URI,
        "dcterms:identifier": WI_ID,
        "dcterms:title": ITEM_TITLE,
        "rtc_cm:estimate": 3600000,
        "rtc_cm:state": {"rdf:resource": state_uri(state_id)},
    }


def actions_json() -> dict:
    return {
        "oslc:results": [{"rdf:about": f"{ACTIONS_URL}/{a}"} for a in ACTION_IDS],
        "prefixes": {"rdf": "http://www.w3.org/1999/02/22-rdf-syntax-ns#"},
    }


def states_json() -> dict:
    return {
        "oslc:results": [
            {"dcterms:identifier": f"{WF}.state.s1", "dcterms:title": "Suspended", "rtc_cm:group": "inprogress"},
            {"dcterms:identifier": STATE_IDLE, "dcterms:title": "Idea", "rtc_cm:group": "open"},
            {"dcterms:identifier": STATE_RUNNING, "dcterms:title": "In Progress", "rtc_cm:group": "inprogress"},
        ]
    }


def mock_workflow(
    *,
    after_state: str = STATE_RUNNING,
    put_response: httpx.Response | None = None,
    states: bool = True,
) -> dict:
    """Mock item GET (before/after), workflow actions, states and the transition PUT.

    Returns the routes {"item", "put", "actions", "states", "auth"}.
    """
    auth = mock_auth()
    item = respx.get(WI_URI).mock(
        side_effect=[
            httpx.Response(200, json=item_json(STATE_IDLE), headers={"ETag": '"etag1"'}),
            httpx.Response(200, json=item_json(after_state), headers={"ETag": '"etag2"'}),
        ]
    )
    actions = respx.get(ACTIONS_URL).mock(return_value=httpx.Response(200, json=actions_json()))
    states_route = respx.get(STATES_URL).mock(
        return_value=httpx.Response(200, json=states_json()) if states else httpx.Response(404)
    )
    put = respx.put(WI_URI).mock(
        return_value=put_response
        or httpx.Response(200, json={"rdf:about": WI_URI, "dcterms:title": ITEM_TITLE}, headers={"ETag": '"etag2"'})
    )
    return {"item": item, "put": put, "actions": actions, "states": states_route, "auth": auth}
