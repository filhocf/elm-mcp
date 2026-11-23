"""RED tests (G5 final, wave W1): the Literal serializer must use the stdlib XML writer."""

import xml.etree.ElementTree as ET

import pytest

from elm_mcp import oslc

from .w1_fixtures import BASE

RM_URI = f"{BASE}/rm/resources/_req1"
HEAD = (
    '<?xml version="1.0"?>\n'
    '<rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#"'
    ' xmlns:jazz_rm="http://jazz.net/ns/rm#">\n'
)
XHTML = "http://www.w3.org/1999/xhtml"


def _literal(inner: str) -> str:
    xml = (
        HEAD
        + f'<rdf:Description rdf:about="{RM_URI}">'
        + f'<jazz_rm:primaryText rdf:parseType="Literal">{inner}</jazz_rm:primaryText>'
        + "</rdf:Description></rdf:RDF>"
    )
    return oslc._rdfxml_to_flat_dict(xml, RM_URI)["jazz_rm:primaryText"]


def _reparse(value: str) -> ET.Element:
    return ET.fromstring(f"<root>{value}</root>")


def test_empty_br_stays_self_closing():
    value = _literal(f'<p xmlns="{XHTML}">a<br/>b</p>')
    assert "<br />" in value or "<br/>" in value
    assert "</br>" not in value


def test_empty_img_stays_self_closing():
    value = _literal(f'<div xmlns="{XHTML}"><img src="x.png"/></div>')
    assert "</img>" not in value
    assert 'src="x.png"' in value


def test_foreign_prefixed_child_keeps_its_namespace():
    value = _literal(f'<div xmlns="{XHTML}"><foo:bar xmlns:foo="urn:foo">x</foo:bar></div>')
    div = _reparse(value)[0]
    assert div.tag == f"{{{XHTML}}}div"
    assert div[0].tag == "{urn:foo}bar"
    assert div[0].text == "x"


def test_svg_inside_xhtml_keeps_both_namespaces():
    value = _literal(f'<div xmlns="{XHTML}"><svg xmlns="http://www.w3.org/2000/svg"><circle r="1"/></svg></div>')
    div = _reparse(value)[0]
    assert div.tag == f"{{{XHTML}}}div"
    assert div[0].tag == "{http://www.w3.org/2000/svg}svg"
    assert div[0][0].tag == "{http://www.w3.org/2000/svg}circle"


def test_xml_lang_is_preserved():
    value = _literal(f'<p xmlns="{XHTML}" xml:lang="pt">oi</p>')
    assert 'xml:lang="pt"' in value
    assert ' lang="pt"' not in value.replace('xml:lang="pt"', "")


def test_pure_xhtml_keeps_default_namespace():
    value = _literal(f'<div xmlns="{XHTML}"><p>a &amp; b</p></div>')
    assert value == f'<div xmlns="{XHTML}"><p>a &amp; b</p></div>'


def test_text_before_and_after_children_is_kept_and_escaped():
    value = _literal(f'a &lt; <b xmlns="{XHTML}">x</b> &amp; c')
    assert value.startswith("a &lt; <b")
    assert value.endswith("&amp; c")


def test_deep_nesting_raises_value_error():
    depth = 3000
    inner = f'<div xmlns="{XHTML}">' + "<div>" * depth + "</div>" * depth + "</div>"
    with pytest.raises(ValueError, match="nesting too deep"):
        _literal(inner)
