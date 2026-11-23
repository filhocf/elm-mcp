"""RED loop 2 do G5 (W3): links (M1, M2), leitura QM (M3, M4, L1, L2, L3) e _find_query_base (D12)."""

import pytest
from mcp.server.mcpserver.exceptions import ToolError

from elm_mcp import oslc, qm, server
from elm_mcp.auth import close_client

from .w2_fixtures import BASE, FakeItem, item_uri, mock_login
from .w3_fixtures import (
    CORE,
    PROJECT,
    QM_NS,
    RDF,
    QmServer,
    member_uri,
    page_xml,
    simple_member,
)

SRC = item_uri("100")
TESTED_BY = "oslc_cm:testedByTestCase"
CM_NS = "http://open-services.net/ns/cm#"


@pytest.fixture(autouse=True)
async def _cfg(monkeypatch):
    monkeypatch.setattr("elm_mcp.config.settings.elm_url", BASE)
    yield
    await close_client()


def _case(n: str) -> str:
    return member_uri("TestCase", n)


def _entry(uri: str) -> dict:
    return {"rdf:resource": uri}


def _targets(body: dict, prop: str) -> list[str]:
    return [e["rdf:resource"] for e in body[prop]]


class LossyItem(FakeItem):
    """Server that, after a PUT, silently drops one target of the property."""

    def __init__(self, *args, drop: str, prop: str = TESTED_BY, **kwargs):
        self.drop, self.prop = drop, prop
        super().__init__(*args, **kwargs)

    def _put(self, request):
        resp = super()._put(request)
        self.data[self.prop] = [e for e in self.data[self.prop] if e["rdf:resource"] != self.drop]
        return resp


# ----------------------------------------------------------------------- M1
async def test_m1_server_that_loses_another_target_fails_naming_it(w2_router):
    mock_login(w2_router)
    LossyItem(w2_router, "100", drop=_case("_C1"), data={TESTED_BY: [_entry(_case("_C1")), _entry(_case("_C2"))]})
    with pytest.raises(ToolError, match="_C1"):
        await server.link_artifacts(SRC, _case("_C3"), "testedByTestCase")


async def test_m1_unlink_that_loses_a_remaining_target_fails_naming_it(w2_router):
    mock_login(w2_router)
    LossyItem(w2_router, "100", drop=_case("_C2"), data={TESTED_BY: [_entry(_case("_C1")), _entry(_case("_C2"))]})
    with pytest.raises(ToolError, match="_C2"):
        await server.unlink_artifacts(SRC, _case("_C1"), "testedByTestCase")


async def test_m1_missing_key_without_declared_namespace_is_refused_without_put(w2_router):
    mock_login(w2_router)
    item = FakeItem(w2_router, "100", data={"other:testedByTestCase": [_entry(_case("_C1"))]})
    item.data["prefixes"] = {"oslc": CORE}
    with pytest.raises(ToolError, match="oslc_cm"):
        await server.link_artifacts(SRC, _case("_C2"), "testedByTestCase")
    assert item.puts == []


async def test_m1_alternative_prefix_declared_in_prefixes_is_resolved(w2_router):
    mock_login(w2_router)
    item = FakeItem(w2_router, "100", data={"cm:testedByTestCase": [_entry(_case("_C1"))]})
    item.data["prefixes"] = {"cm": CM_NS, "oslc": CORE}
    result = await server.link_artifacts(SRC, _case("_C2"), "testedByTestCase")
    assert item.put_properties() == "cm:testedByTestCase"
    assert set(_targets(item.put_body(), "cm:testedByTestCase")) == {_case("_C1"), _case("_C2")}
    assert result["property"] == "cm:testedByTestCase"


# ----------------------------------------------------------------------- M2
@pytest.mark.parametrize(
    "bad",
    [
        f"{BASE}/qm/resources/com.example.qm.TestCase/_C1",
        f"{BASE}/rm/resources/_REQ1",
        f"{BASE}/ccm/resource/itemName/com.ibm.team.workitem.Comment/100",
        f"{BASE}/ccm/oslc/contexts/_p/workitems/services.xml",
    ],
)
async def test_m2_source_must_be_a_ccm_work_item(w2_router, bad):
    mock_login(w2_router)
    for tool in (server.link_artifacts, server.unlink_artifacts):
        with pytest.raises(ToolError, match="work item"):
            await tool(bad, _case("_X"), "testedByTestCase")
    assert [r for r in w2_router.calls if r.request.method != "POST"] == []


# ----------------------------------------------------------------------- M3
def _info_page(total: str) -> str:
    return (
        f'<rdf:RDF xmlns:rdf="{RDF}" xmlns:oslc="{CORE}"><rdf:Description rdf:about="{BASE}/q">'
        f'<rdf:type rdf:resource="{CORE}ResponseInfo"/><oslc:totalCount>{total}</oslc:totalCount>'
        "</rdf:Description></rdf:RDF>"
    )


@pytest.mark.parametrize("total", ["²", "--5", "1.5", ""])
def test_m3_page_ignores_a_non_ascii_or_malformed_total(total):
    assert qm._page(_info_page(total))[1] is None


def test_m3_page_reads_ascii_totals():
    assert qm._page(_info_page("12"))[1] == 12
    assert qm._page(_info_page("-1"))[1] == -1


@pytest.mark.parametrize("value", ["--5", "²", "1.5"])
def test_m3_points_ignores_a_malformed_value(value):
    assert qm._points({"_literals": {"pointsPassed": value}}, "pointsPassed") is None
    assert qm._points({"_literals": {"pointsPassed": "-3"}}, "pointsPassed") == -3


# ----------------------------------------------------------------------- M4
async def test_m4_query_members_keeps_only_members_of_the_requested_type(w2_router):
    case_uri, case_xml = simple_member("TestCase", 1)
    script_uri, script_xml = simple_member("TestScript", 2)
    raw = page_xml("TestCase", [case_xml, script_xml], [case_uri, script_uri], total=2, next_page=None)
    QmServer(w2_router, {}, raw_pages={"TestCase": raw})
    result = await server.list_qm_resources(PROJECT, "TestCase")
    assert [i["uri"] for i in result["items"]] == [case_uri]
    assert {i["type"] for i in result["items"]} == {"TestCase"}


# ----------------------------------------------------------------------- L1
def test_l1_verdict_name_without_fragment_uses_the_last_path_segment():
    assert qm._verdict_name("https://h/qm/verdicts/passed") == "passed"
    assert qm._verdict_name("https://h/qm/ExecutionResult#com.ibm.state.failed") == "failed"


# ----------------------------------------------------------------------- L2
def _typed_page(total: int = 1) -> str:
    return (
        f'<rdf:RDF xmlns:rdf="{RDF}" xmlns:oslc="{CORE}" xmlns:oslc_qm="{QM_NS}" '
        'xmlns:dcterms="http://purl.org/dc/terms/">'
        f'<oslc_qm:TestCase rdf:about="{_case("_T1")}"><dcterms:title>Typed</dcterms:title></oslc_qm:TestCase>'
        f'<rdf:Description rdf:about="{BASE}/q"><rdf:type rdf:resource="{CORE}ResponseInfo"/>'
        f"<oslc:totalCount>{total}</oslc:totalCount></rdf:Description></rdf:RDF>"
    )


def test_l2_typed_rdf_xml_nodes_are_members():
    members, total, _ = qm._page(_typed_page())
    assert [m["uri"] for m in members] == [_case("_T1")]
    assert [m["type"] for m in members] == ["TestCase"]
    assert total == 1


async def test_l2_total_without_readable_members_warns_and_truncates(w2_router, caplog):
    raw = _info_page("5")
    QmServer(w2_router, {}, raw_pages={"TestCase": raw})
    with caplog.at_level("WARNING"):
        result = await server.list_qm_resources(PROJECT, "TestCase")
    assert result["items"] == []
    assert result["truncated"] is True
    assert any("0 members" in r.getMessage() or "no member" in r.getMessage() for r in caplog.records)


# ----------------------------------------------------------------------- L3
async def test_l3_duplicate_capabilities_with_the_same_query_base_are_not_ambiguous(w2_router):
    from .w3_fixtures import qm_services_xml

    xml = qm_services_xml(["TestCase"])
    block = xml[
        xml.index("<oslc:queryCapability") : xml.rindex("</oslc:queryCapability>") + len("</oslc:queryCapability>")
    ]
    QmServer(w2_router, {"TestCase": [simple_member("TestCase", 1)]}, services_xml=xml.replace(block, block + block, 1))
    result = await server.list_qm_resources(PROJECT, "TestCase")
    assert len(result["items"]) == 1


# ----------------------------------------------------------------------- D12
async def test_d12_find_query_base_refuses_qm_pointing_to_the_right_module():
    with pytest.raises(ValueError, match="qm.query_members"):
        await oslc._find_query_base("qm", f"{BASE}/qm/oslc_qm/contexts/_PA/services.xml")
