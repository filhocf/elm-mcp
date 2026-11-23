"""RED tests (G3) for the G5 corrections, loop 3 (wave W1): RDF/XML flattening and the 415 fallback."""

import httpx
import pytest
import respx

from elm_mcp import oslc
from elm_mcp.auth import close_client

from .w1_fixtures import BASE

RM_URI = f"{BASE}/rm/resources/_req1"
OTHER_URI = f"{BASE}/rm/resources/_req2"
HEAD = (
    '<?xml version="1.0"?>\n'
    '<rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#"'
    ' xmlns:dcterms="http://purl.org/dc/terms/" xmlns:jazz_rm="http://jazz.net/ns/rm#"'
    ' xmlns:alt="http://jazz.net/ns/rm#">\n'
)
XH = 'xmlns="http://www.w3.org/1999/xhtml"'


@pytest.fixture(autouse=True)
async def cleanup(monkeypatch):
    monkeypatch.setattr("elm_mcp.config.settings.elm_url", BASE)
    yield
    await close_client()


def test_literal_comes_from_main_description_not_first_in_document():
    xml = (
        HEAD + f'<rdf:Description rdf:about="{OTHER_URI}"><jazz_rm:primaryText rdf:parseType="Literal">'
        f"<div {XH}><p>other</p></div></jazz_rm:primaryText></rdf:Description>\n"
        f'<rdf:Description rdf:about="{RM_URI}"><jazz_rm:primaryText rdf:parseType="Literal">'
        f"<div {XH}><p>mine</p></div></jazz_rm:primaryText></rdf:Description></rdf:RDF>"
    )
    data = oslc._rdfxml_to_flat_dict(xml, RM_URI)
    assert data["rdf:about"] == RM_URI
    assert data["jazz_rm:primaryText"] == f"<div {XH}><p>mine</p></div>"


def test_repeated_literal_keeps_distinct_values_in_list():
    xml = (
        HEAD + f'<rdf:Description rdf:about="{RM_URI}">'
        f'<jazz_rm:primaryText rdf:parseType="Literal"><div {XH}><p>one</p></div></jazz_rm:primaryText>'
        f'<jazz_rm:primaryText rdf:parseType="Literal"><div {XH}><p>two</p></div></jazz_rm:primaryText>'
        "</rdf:Description></rdf:RDF>"
    )
    data = oslc._rdfxml_to_flat_dict(xml, RM_URI)
    assert data["jazz_rm:primaryText"] == [f"<div {XH}><p>one</p></div>", f"<div {XH}><p>two</p></div>"]


def test_cdata_with_closing_tag_does_not_truncate():
    xml = (
        HEAD + f'<rdf:Description rdf:about="{RM_URI}"><jazz_rm:primaryText rdf:parseType="Literal">'
        f"<div {XH}><p><![CDATA[a </p> b]]></p><p>tail</p></div></jazz_rm:primaryText></rdf:Description></rdf:RDF>"
    )
    value = oslc._rdfxml_to_flat_dict(xml, RM_URI)["jazz_rm:primaryText"]
    assert "a &lt;/p&gt; b" in value
    assert value.endswith("<p>tail</p></div>")


def test_alternative_prefix_for_same_namespace_still_returns_value():
    xml = (
        '<rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#"'
        ' xmlns:a="http://jazz.net/ns/rm#" xmlns:b="http://jazz.net/ns/rm#">'
        f'<rdf:Description rdf:about="{RM_URI}"><b:primaryText rdf:parseType="Literal">'
        f"<div {XH}><p>x</p></div></b:primaryText></rdf:Description></rdf:RDF>"
    )
    data = oslc._rdfxml_to_flat_dict(xml, RM_URI)
    assert list(data.values())[-1] == f"<div {XH}><p>x</p></div>"


def test_malformed_xml_raises_value_error():
    with pytest.raises(ValueError, match="invalid RDF/XML response"):
        oslc._rdfxml_to_flat_dict("<rdf:RDF><oops", RM_URI)


@pytest.mark.parametrize("marker", ["<!DOCTYPE x>", '<!DOCTYPE x [<!ENTITY e "v">]>'])
def test_doctype_or_entity_is_refused(marker):
    xml = f'<?xml version="1.0"?>\n{marker}\n<rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#"/>'
    with pytest.raises(ValueError, match="invalid RDF/XML response"):
        oslc._rdfxml_to_flat_dict(xml, RM_URI)


@respx.mock
async def test_415_outside_rm_does_not_retry():
    respx.post(f"{BASE}/jts/j_security_check").mock(return_value=httpx.Response(200))
    uri = f"{BASE}/ccm/resource/itemName/WI1"
    route = respx.get(uri).mock(return_value=httpx.Response(415, text="[http-415]"))
    with pytest.raises(Exception):  # noqa: B017 - any mapped HTTP error, not a second call
        await oslc.get_resource(uri)
    assert route.call_count == 1
