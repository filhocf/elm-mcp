"""G3 (RED) tests for issue #84: a custom attribute whose name has accents (``classificação``).

The shape declares ``oslc:propertyDefinition`` PERCENT-ENCODED
(``.../ext/1.0/br.gov.dataprev.attribute.tarefa.classifica%C3%A7%C3%A3o``). ``oslc._compact`` keeps the
encoded form, so set_field / create_workitem / update_workitem write the key
``rtc_ext:...classifica%C3%A7%C3%A3o`` and the server answers 409 "Unknown attribute". The server accepts
the DECODED key (``rtc_ext:...classificação``). Target: the local name is percent-decoded ONCE, and only
when the result is a plain name (unicode letters, digits, ``.``, ``_``, ``-``); anything else stays as it came.

Groups:
  1  ``_compact`` / ``canonical_property_key`` decode the local name          (RED)
  2  set_field writes the decoded key in ?oslc.properties and in the body     (RED)
  3  set_field by decoded name, decoded prefix:name, encoded prefix:name      (RED)
  4  create_workitem / update_workitem normalize custom_fields keys           (RED)
  5  security: unsafe / double-encoded names are never decoded or injected    (GUARDS: green today,
     they pin the limits a naive ``unquote`` implementation would break)
  6  regression: list_workitems filter, inspect_workitem                      (GUARDS: green today)

Behavior is observed through respx (query params, parsed JSON/XML bodies). Tenant-neutral data only.
"""

import json
from contextlib import suppress
from xml.etree import ElementTree as ET

import httpx
import pytest
import respx
from mcp.server.mcpserver.exceptions import ToolError

from elm_mcp import oslc, server
from elm_mcp.auth import close_client

from . import f1_fixtures as f
from .w2_fixtures import (
    BASE,
    PROJECT,
    FakeItem,
    FakePages,
    mock_ccm,
    mock_creation,
    mock_login,
    paginate,
)

RTC_EXT = "http://jazz.net/xmlns/prod/jazz/rtc/ext/1.0/"
LOCAL_ENC = "br.gov.dataprev.attribute.tarefa.classifica%C3%A7%C3%A3o"
LOCAL_DEC = "br.gov.dataprev.attribute.tarefa.classificação"
URI_ENC = RTC_EXT + LOCAL_ENC
KEY_ENC = "rtc_ext:" + LOCAL_ENC
KEY_DEC = "rtc_ext:" + LOCAL_DEC
PREFIXES = {"rtc_ext": RTC_EXT, "rtc_cm": f.RTC_CM, "dcterms": "http://purl.org/dc/terms/"}


@pytest.fixture(autouse=True)
async def _cfg(monkeypatch):
    monkeypatch.setattr("elm_mcp.config.settings.elm_url", BASE)
    yield
    await close_client()


# ======================================================================
# 1. _compact / canonical_property_key
# ======================================================================
def test_compact_decodes_the_percent_encoded_local_name():
    assert oslc._compact(URI_ENC, PREFIXES) == KEY_DEC


def test_compact_decodes_lowercase_hex_too():
    assert oslc._compact(RTC_EXT + "x.classifica%c3%a7%c3%a3o", PREFIXES) == "rtc_ext:x.classificação"


@pytest.mark.parametrize("local", ["storyPoints", "br.gov.dataprev.attribute.tarefa.tipo", "a-b_c.d9"])
def test_compact_leaves_ascii_names_identical(local):
    assert oslc._compact(RTC_EXT + local, PREFIXES) == f"rtc_ext:{local}"


def test_compact_still_returns_none_without_a_known_namespace():
    assert oslc._compact("http://other.example.com/ns/classifica%C3%A7%C3%A3o", PREFIXES) is None


def test_canonical_property_key_decodes_a_full_encoded_uri():
    assert oslc.canonical_property_key(URI_ENC) == KEY_DEC


def test_canonical_property_key_decodes_prefix_name_with_encoded_local_name():
    assert oslc.canonical_property_key(KEY_ENC) == KEY_DEC


def test_canonical_property_key_keeps_a_decoded_key():
    assert oslc.canonical_property_key(KEY_DEC) == KEY_DEC


# ======================================================================
# Fixtures for set_field / create / update with the accented attribute
# ======================================================================
def _accent_prop(*, required: bool = False, name: str = LOCAL_DEC, propdef: str = URI_ENC) -> dict:
    return f.prop(name, "Classificação", "Exactly-one" if required else "Zero-or-one", propdef=propdef, about_id="cls")


def _field_item(router, props: list[dict] | None = None, shape: str = "task") -> FakeItem:
    mock_login(router)
    router.get(f.shape_uri(shape)).mock(
        return_value=httpx.Response(200, text=f.real_shape_xml(shape, props or [_accent_prop()]))
    )
    item = FakeItem(router, "100", shape=shape)
    item.data["prefixes"] = {**item.data["prefixes"], "rtc_ext": RTC_EXT}
    return item


def _assert_put_uses_decoded_key(item: FakeItem, key: str = KEY_DEC, value: object = "Alta") -> None:
    assert len(item.puts) == 1
    request = item.puts[0]
    assert request.url.params.get("oslc.properties") == key
    # httpx percent-encodes the UTF-8 key ONCE in the query: %C3%A7, never %25C3
    assert "classifica%C3%A7%C3%A3o" in request.url.query.decode()
    assert "%25" not in request.url.query.decode()
    body = json.loads(request.content)
    assert set(body) - {"prefixes"} == {key}
    assert body[key] == value


# ======================================================================
# 2. set_field: decoded key in ?oslc.properties and in the JSON body
# ======================================================================
async def test_set_field_writes_the_decoded_key_when_the_shape_is_percent_encoded(w2_router):
    item = _field_item(w2_router)
    result = await server.set_field("100", LOCAL_DEC, "Alta")
    _assert_put_uses_decoded_key(item)
    assert result["field"] == KEY_DEC


# ======================================================================
# 3. set_field: the ways to name the field
# ======================================================================
async def test_set_field_by_decoded_shape_name(w2_router):
    item = _field_item(w2_router)
    await server.set_field("100", LOCAL_DEC, "Alta")
    _assert_put_uses_decoded_key(item)


async def test_set_field_by_decoded_prefix_name(w2_router):
    item = _field_item(w2_router)
    await server.set_field("100", KEY_DEC, "Alta")
    _assert_put_uses_decoded_key(item)


async def test_set_field_by_encoded_prefix_name_is_accepted_and_written_decoded(w2_router):
    item = _field_item(w2_router)
    result = await server.set_field("100", KEY_ENC, "Alta")
    _assert_put_uses_decoded_key(item)
    assert result["field"] == KEY_DEC


async def test_set_field_by_decoded_prefix_name_when_the_shape_name_is_not_the_attribute_name(w2_router):
    """The shape's oslc:name may differ (title-like); the property URI still identifies the field."""
    item = _field_item(w2_router, [_accent_prop(name="classificacao")])
    await server.set_field("100", KEY_DEC, "Alta")
    _assert_put_uses_decoded_key(item)


# ======================================================================
# 4. create_workitem / update_workitem: normalization of custom_fields keys
# ======================================================================
def _create_env(router, *, required: bool = True) -> dict:
    mock_ccm(router)
    posts = mock_creation(router)
    props = [*f.default_task_props(), _accent_prop(required=required)]
    router.get(f.shape_uri("task")).mock(return_value=httpx.Response(200, text=f.real_shape_xml("task", props)))
    return posts


def _posted_xml(posts: dict) -> ET.Element:
    sent = [c.request for route in posts.values() for c in route.calls]
    assert len(sent) == 1
    return ET.fromstring(sent[0].content)  # bytes: the declared encoding is UTF-8


async def _create(**custom) -> None:
    await server.create_workitem(project=PROJECT, title="T", type="task", custom_fields=custom, if_exists="create")


@pytest.mark.parametrize("key", [URI_ENC, KEY_ENC, KEY_DEC], ids=["full-uri-encoded", "prefix-encoded", "decoded"])
async def test_create_workitem_normalizes_the_key_before_the_required_check_and_the_write(w2_router, key):
    posts = _create_env(w2_router)
    await _create(**{key: "Alta"})
    root = _posted_xml(posts)
    element = root.find(f".//{{{RTC_EXT}}}{LOCAL_DEC}")
    assert element is not None, "the RDF/XML must carry the decoded element name"
    assert element.text == "Alta"


async def test_create_workitem_missing_required_accented_field_names_the_decoded_key(w2_router):
    _create_env(w2_router)
    with pytest.raises(ToolError, match="Required fields missing") as exc:
        await _create()
    assert KEY_DEC in str(exc.value)
    assert "%C3" not in str(exc.value)


def test_payload_to_rdfxml_emits_the_decoded_element_name_and_the_body_parses():
    xml = oslc._payload_to_rdfxml({"dcterms:title": "T", KEY_DEC: "Alta"})
    root = ET.fromstring(xml.encode("utf-8"))
    element = root.find(f".//{{{RTC_EXT}}}{LOCAL_DEC}")
    assert element is not None and element.text == "Alta"


def _update_env() -> tuple[str, respx.Route]:
    uri = f"{BASE}/ccm/resource/itemName/com.ibm.team.workitem.WorkItem/42"
    f.mock_auth()
    respx.get(uri).mock(
        return_value=httpx.Response(
            200, json={"rdf:about": uri, "prefixes": {"rtc_ext": RTC_EXT}}, headers={"ETag": '"e1"'}
        )
    )
    return uri, respx.put(uri).mock(return_value=httpx.Response(200, json={}, headers={"ETag": '"e2"'}))


@respx.mock
@pytest.mark.parametrize("key", [URI_ENC, KEY_ENC, KEY_DEC], ids=["full-uri-encoded", "prefix-encoded", "decoded"])
async def test_update_workitem_normalizes_custom_field_keys_before_the_write(key):
    _uri, put = _update_env()
    await server.update_workitem(id="42", custom_fields={key: "Alta"})
    request = put.calls[0].request
    assert request.url.params.get("oslc.properties") == KEY_DEC
    body = json.loads(request.content)
    assert set(body) - {"prefixes"} == {KEY_DEC}
    assert body[KEY_DEC] == "Alta"


# ======================================================================
# 5. Security (GUARDS: green today; a naive unquote() breaks them)
# ======================================================================
UNSAFE_LOCALS = [
    "a%2Fb",  # '/'
    "a%2fb",
    "%2E%2E%2Fetc",  # '../'
    "a%2E%2E",  # '..'
    "a%252Fb",  # '%2F' after one decode
    "a%00b",  # NUL
    "a%0Ab",  # control: LF
    "a%09b",  # control: TAB
    "a%20b",  # space
    "a%3Cb",  # <
    "a%3Eb",  # >
    "a%22b",  # "
    "a%26b",  # &
    "a%27b",  # '
    "a%3D%22x%22",  # =" (attribute injection)
    "a%E2%80%AEb",  # right-to-left override
    "a%C2%A0b",  # no-break space
    "a%E2%80%8Bb",  # zero-width space
    "a%FFb",  # invalid UTF-8
    "a%C3",  # truncated UTF-8
    "classifica%25C3%25A7%25C3%25A3o",  # double encoding: must NOT become classificação
    "classifica%25C3%A7",  # mixed: decoding twice would yield 'ç' + more
]


@pytest.mark.parametrize("local", UNSAFE_LOCALS)
def test_compact_does_not_decode_an_unsafe_local_name(local):
    assert oslc._compact(RTC_EXT + local, PREFIXES) == f"rtc_ext:{local}"


def test_compact_decodes_exactly_once():
    assert oslc._compact(RTC_EXT + "x%25C3%25A7", PREFIXES) == "rtc_ext:x%25C3%25A7"


@pytest.mark.parametrize("local", UNSAFE_LOCALS)
def test_canonical_property_key_does_not_decode_an_unsafe_local_name(local):
    assert oslc.canonical_property_key(f"rtc_ext:{local}") == f"rtc_ext:{local}"
    assert oslc.canonical_property_key(RTC_EXT + local) == f"rtc_ext:{local}"


@pytest.mark.parametrize("local", ["x%3Cinjected%2F%3E", "x%22%20evil%3D%221", "x%26amp%3B%3Cinjected%2F%3E"])
async def test_create_workitem_never_injects_an_element_or_attribute_from_an_unsafe_key(w2_router, local):
    """Either the key is refused (ValueError) or any POSTed body is well-formed XML without the injected node.

    Today the key goes to the body as is: ``%`` is not valid in an XML name, so the POST is malformed.
    """
    posts = _create_env(w2_router, required=False)
    with suppress(ValueError, ToolError):
        await _create(**{f"rtc_ext:{local}": "v"})
    for route in posts.values():
        for call in route.calls:
            root = ET.fromstring(call.request.content)  # a body that does not parse is itself a defect
            names = {e.tag.rsplit("}", 1)[-1] for e in root.iter()}
            assert "injected" not in names
            assert all("evil" not in k for e in root.iter() for k in e.attrib)


@pytest.mark.parametrize("local", ["evil%3Cx%2F%3E", "evil%2F..%2Fpath", "evil%22%20a%3D%22b"])
async def test_set_field_never_writes_a_decoded_unsafe_key(w2_router, local):
    props = [_accent_prop(name="evil", propdef=RTC_EXT + local)]
    item = _field_item(w2_router, props)
    with suppress(ValueError, ToolError):
        await server.set_field("100", "evil", "v")
    for request in item.puts:
        keys = request.url.params.get("oslc.properties", "").split(",") + list(json.loads(request.content))
        for key in keys:
            assert not any(ch in key for ch in '<>"/& \x00\n'), key


# ======================================================================
# 6. Regression (GUARDS)
# ======================================================================
async def test_list_workitems_query_is_forwarded_untouched(w2_router):
    mock_ccm(w2_router)
    pages = FakePages(w2_router, paginate(1, [1]))
    query = f'{KEY_ENC}="Alta" and rtc_cm:state="x"'
    await server.list_workitems(PROJECT, query=query)
    assert pages.where_of_first_request() == query


async def test_list_workitems_filters_are_not_touched_by_the_decoding(w2_router):
    mock_ccm(w2_router)
    pages = FakePages(w2_router, paginate(1, [1]))
    await server.list_workitems(PROJECT, planned_for=f"{BASE}/ccm/resource/iteration/%C3%A7")
    where = pages.where_of_first_request()
    assert f"{BASE}/ccm/resource/iteration/%C3%A7" in where  # filter VALUES are never decoded


@respx.mock
async def test_inspect_workitem_keeps_the_keys_as_the_server_sent_them():
    uri = f"{BASE}/ccm/resource/itemName/com.ibm.team.workitem.WorkItem/7"
    f.mock_auth()
    respx.get(uri).mock(
        return_value=httpx.Response(
            200,
            json={
                "dcterms:identifier": "7",
                "dcterms:type": "task",
                "oslc:instanceShape": {"rdf:resource": f.shape_uri("task")},
                KEY_ENC: "a",
                KEY_DEC: "b",
            },
        )
    )
    result = await server.inspect_workitem("7")
    assert result["fields"] == {KEY_ENC: "a", KEY_DEC: "b"}
