"""#88 review M1: update_requirement must never PUT to a Description other than the one asked for."""

import asyncio

from elm_mcp import oslc

from .test_regex_timing import FakeResponse

URI = "https://h/rm/resources/TX_1"


def _desc(about, title):
    return f'<rdf:Description rdf:about="{about}"><dcterms:title>{title}</dcterms:title></rdf:Description>'


def _run(monkeypatch, uri, xml):
    calls = []

    async def request(_client, method, _url, **kwargs):
        calls.append(method)
        return FakeResponse("")

    async def fetch(_client, method, _url, *_a, **_k):
        calls.append(method)
        return FakeResponse(xml)

    async def client():
        return None

    monkeypatch.setattr(oslc, "get_client", client)
    monkeypatch.setattr(oslc, "fetch", fetch)
    monkeypatch.setattr(oslc, "_request", request)
    try:
        asyncio.run(oslc.update_requirement(uri, title="new"))
    except ValueError as exc:
        return calls, exc
    return calls, None


def test_uri_with_gt_is_refused_before_any_request(monkeypatch):
    calls, exc = _run(monkeypatch, "https://h/a>b", "<rdf:RDF>" + _desc("https://h/other", "x") + "</rdf:RDF>")
    assert exc is not None and "contém >" in str(exc)
    assert calls == []


def test_several_descriptions_without_match_never_put(monkeypatch):
    xml = "<rdf:RDF>" + _desc("https://h/one", "1") + _desc("https://h/two", "2") + "</rdf:RDF>"
    calls, exc = _run(monkeypatch, URI, xml)
    assert exc is not None
    assert "PUT" not in calls


def test_single_description_fallback_still_works(monkeypatch):
    calls, exc = _run(monkeypatch, URI, "<rdf:RDF>" + _desc("https://h/abbrev", "x") + "</rdf:RDF>")
    assert exc is None
    assert "PUT" in calls
