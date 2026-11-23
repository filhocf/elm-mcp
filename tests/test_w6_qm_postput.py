"""Fix final G5 (RED): falhas DEPOIS do PUT bem-sucedido dizem claramente que o recurso FOI alterado."""

import re

import httpx
import pytest
from mcp.server.mcpserver.exceptions import ToolError

from elm_mcp import oslc, server
from elm_mcp.auth import close_client

from . import w6_fixtures as w6
from .w3_fixtures import BASE
from .w6_fixtures import QmWriteServer, literal

URI = w6.TESTCASE_FIXTURE_URI
REQ = "https://elm.example.com/rm/resources/_REQ_NEW"
EXTRA = '<oslc_qm:validatesRequirement rdf:resource="https://elm.example.com/rm/resources/_SURPRISE"/>'


@pytest.fixture(autouse=True)
async def _cfg(monkeypatch):
    monkeypatch.setattr("elm_mcp.config.settings.elm_url", BASE)
    yield
    await close_client()


def _seeded(router, **kwargs) -> QmWriteServer:
    qm = QmWriteServer(router, **kwargs)
    qm.add_fixture_testcase()
    return qm


def _break_confirmation(monkeypatch, qm: QmWriteServer, exc: Exception | None) -> None:
    """After the PUT, the confirming GET answers 500 (exc None) or raises ``exc``."""
    real = oslc.fetch

    async def fetch(client, method, url, operation, **kwargs):
        if method == "GET" and qm.puts:
            if exc is not None:
                raise exc
            qm.get_status[url] = 500
        return await real(client, method, url, operation, **kwargs)

    monkeypatch.setattr(oslc, "fetch", fetch)


def _assert_written(excinfo) -> None:
    text = str(excinfo.value)
    assert "SUCCEEDED" in text and "WAS changed" in text
    assert "do not retry blindly" in text


@pytest.mark.parametrize("exc", [None, httpx.ReadTimeout("slow")])
async def test_update_confirmation_get_failure_says_the_put_succeeded(w2_router, monkeypatch, exc):
    qm = _seeded(w2_router)
    _break_confirmation(monkeypatch, qm, exc)
    with pytest.raises(ToolError) as excinfo:
        await server.update_qm_resource(URI, title="Renamed case")
    _assert_written(excinfo)
    assert len(qm.puts) == 1


async def test_update_divergence_after_put_says_the_put_succeeded(w2_router):
    qm = _seeded(w2_router, put_transform=lambda old, new: old)
    with pytest.raises(ToolError) as excinfo:
        await server.update_qm_resource(URI, title="Renamed case")
    _assert_written(excinfo)
    assert len(qm.puts) == 1


@pytest.mark.parametrize("exc", [None, httpx.ReadTimeout("slow")])
async def test_link_confirmation_get_failure_says_the_put_succeeded(w2_router, monkeypatch, exc):
    qm = _seeded(w2_router)
    _break_confirmation(monkeypatch, qm, exc)
    with pytest.raises(ToolError) as excinfo:
        await server.link_test_case(URI, REQ, "validatesRequirement")
    _assert_written(excinfo)
    assert len(qm.puts) == 1


async def test_link_divergence_after_put_says_the_put_succeeded(w2_router):
    _seeded(w2_router, put_transform=lambda old, new: old)
    with pytest.raises(ToolError) as excinfo:
        await server.link_test_case(URI, REQ, "validatesRequirement")
    _assert_written(excinfo)


async def test_update_tolerates_a_property_added_by_the_server(w2_router):
    def add_property(old: str, new: str) -> str:
        return new.replace("<oslc_qm:validatesRequirement", EXTRA + "<oslc_qm:validatesRequirement", 1)

    qm = _seeded(w2_router, put_transform=add_property)
    result = await server.update_qm_resource(URI, title="Renamed case")
    assert result["updated"] == ["title"]
    assert literal(qm.doc(URI), URI, w6.DCTERMS, "title") == "Renamed case"


async def test_update_tolerates_crlf_and_whitespace_normalization_by_the_server(w2_router):
    def crlf(old: str, new: str) -> str:
        return new.replace("Renamed  case", "Renamed case").replace("line one\nline two", "line one\r\nline two")

    qm = _seeded(w2_router, put_transform=crlf)
    result = await server.update_qm_resource(URI, title="Renamed  case", description="line one\nline two")
    assert result["updated"] == ["title", "description"]
    assert qm.puts


async def test_update_that_loses_a_property_is_still_a_divergence(w2_router):
    def drop(old: str, new: str) -> str:
        return re.sub(r"<oslc_qm:validatesRequirement[^>]*/>", "", new, count=1)

    _seeded(w2_router, put_transform=drop)
    with pytest.raises(ToolError):
        await server.update_qm_resource(URI, title="Renamed case")
