"""Byte-level edits of the re-read RDF/XML (G4): everything that is not changed comes back untouched."""

import difflib
from pathlib import Path

import pytest
from mcp.server.mcpserver.exceptions import ToolError

from elm_mcp import qm_write, server
from elm_mcp.auth import close_client

from . import w6_fixtures as w6
from .w3_fixtures import BASE
from .w6_fixtures import QmWriteServer

URI = w6.TESTCASE_FIXTURE_URI
FIXTURE = Path(__file__).parent / "fixtures" / "qm_testcase_one.xml"
RM = "https://elm.example.com/rm/resources/_NEW"


@pytest.fixture(autouse=True)
async def _cfg(monkeypatch):
    monkeypatch.setattr("elm_mcp.config.settings.elm_url", BASE)
    yield
    await close_client()


def _diff(before: bytes, after: bytes) -> list[str]:
    lines = difflib.unified_diff(before.decode().splitlines(), after.decode().splitlines(), lineterm="", n=0)
    return [line for line in lines if line[:1] in "+-" and line[:3] not in ("+++", "---")]


async def test_real_fixture_update_changes_only_the_title_line(w2_router):
    qm = QmWriteServer(w2_router)
    qm.add_fixture_testcase()
    before = FIXTURE.read_bytes()
    await server.update_qm_resource(URI, title="Renamed")
    changed = _diff(before, qm.doc(URI).encode())
    assert len(changed) == 2
    assert "Test case 1" in changed[0] and "Renamed" in changed[1]
    assert 'rdf:datatype="http://www.w3.org/2001/XMLSchema#string"' in changed[1]


async def test_real_fixture_link_adds_one_line_with_the_prefix_of_the_document(w2_router):
    qm = QmWriteServer(w2_router)
    qm.add_fixture_testcase()
    before = FIXTURE.read_bytes()
    await server.link_test_case(URI, RM, "validatesRequirement")
    assert _diff(before, qm.doc(URI).encode()) == [f'+  <oslc_qm:validatesRequirement rdf:resource="{RM}"/>']


async def test_real_fixture_unlink_removes_exactly_that_line(w2_router):
    qm = QmWriteServer(w2_router)
    qm.add_fixture_testcase()
    before = FIXTURE.read_bytes()
    await server.unlink_test_case(URI, w6.FIXTURE_WORKITEM, "testsChangeRequest")
    removed = _diff(before, qm.doc(URI).encode())
    assert removed == [f'-  <oslc_qm:testsChangeRequest rdf:resource="{w6.FIXTURE_WORKITEM}"/>']


TRICKY = """<?xml version="1.0" encoding="UTF-8"?>
<rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#" xmlns:dcterms="http://purl.org/dc/terms/"
    xmlns:q="http://open-services.net/ns/qm#" xmlns:rqm="http://jazz.net/ns/qm/rqm#">
 <rdf:Description rdf:about="{uri}" note="a &gt; b" other='x>y'>
  <rdf:type rdf:resource="http://open-services.net/ns/qm#TestCase"/>
  <dcterms:title/>
  <rqm:shortIdentifier>12</rqm:shortIdentifier>
  <rqm:content rdf:parseType="Literal"><div xmlns="http://www.w3.org/1999/xhtml"><p>a &amp; b</p></div></rqm:content>
  <rqm:note><![CDATA[<keep> & this]]></rqm:note>
  <!-- a comment -->
  <q:validatesRequirement rdf:resource="https://elm.example.com/rm/resources/_A"/>
 </rdf:Description>
</rdf:RDF>
"""


async def test_tricky_document_parse_type_cdata_comment_empty_title_and_foreign_prefix(w2_router):
    qm = QmWriteServer(w2_router)
    uri = f"{w6.creation_url('TestCase')}/_T01"
    qm.add_raw(uri, TRICKY.format(uri=uri))
    before = qm.doc(uri).encode()
    await server.link_test_case(uri, RM, "validatesRequirement")
    await server.update_qm_resource(uri, title="Filled in")
    after = qm.doc(uri)
    for keep in (
        'rdf:parseType="Literal"',
        "<![CDATA[<keep> & this]]>",
        "<!-- a comment -->",
        'note="a &gt; b"',
        "other='x>y'",
    ):
        assert keep in after
    assert "<dcterms:title>Filled in</dcterms:title>" in after
    assert f'<q:validatesRequirement rdf:resource="{RM}"/>' in after
    assert w6.link_targets(after, uri, "validatesRequirement") == ["https://elm.example.com/rm/resources/_A", RM]
    assert len(_diff(before, after.encode())) == 3  # empty title replaced (2 lines), one link added


async def test_namespace_missing_in_the_document_is_declared_on_the_new_element(w2_router):
    qm = QmWriteServer(w2_router)
    uri = f"{w6.creation_url('TestCase')}/_T02"
    doc = (
        '<rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#" xmlns:dcterms="http://purl.org/dc/terms/">\n'
        f' <rdf:Description rdf:about="{uri}">\n'
        '  <rdf:type rdf:resource="http://open-services.net/ns/qm#TestCase"/>\n'
        "  <dcterms:title>x</dcterms:title>\n"
        " </rdf:Description>\n</rdf:RDF>\n"
    )
    qm.add_raw(uri, doc)
    await server.link_test_case(uri, RM, "validatesRequirement")
    assert w6.link_targets(qm.doc(uri), uri, "validatesRequirement") == [RM]
    assert 'xmlns:q1="http://open-services.net/ns/qm#"' in qm.doc(uri)


@pytest.mark.parametrize(
    "doctype",
    ['<!DOCTYPE rdf:RDF [<!ENTITY x "boom">]>', "<!DOCTYPE rdf:RDF>"],
)
async def test_dtd_and_entities_in_the_re_read_document_are_refused_without_a_put(w2_router, doctype):
    qm = QmWriteServer(w2_router)
    uri = qm.add("TestCase", "_E01", "Entity")
    qm.store[uri]["doc"] = qm.doc(uri).replace("<rdf:RDF", doctype + "\n<rdf:RDF", 1)
    with pytest.raises(ToolError):
        await server.update_qm_resource(uri, title="New")
    assert qm.puts == []


def test_scan_refuses_utf16_documents():
    with pytest.raises(ValueError):
        qm_write._scan("<a/>".encode("utf-16"))
