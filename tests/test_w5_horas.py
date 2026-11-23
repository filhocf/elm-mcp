"""G3 (RED) tests for SPEC-horas (wave W5, release v0.6.0): ``list_time`` and ``get_hours`` (read only).

The ALM is the stateful respx fake in tests/w5_fixtures.py, built from the sanitized real fixtures
(ccm_wi.json, ccm_sheet.json, ccm_entry.json): work item time in MILLISECONDS, a ``rtc_cm:timeSheet`` link
whose sheet lists only REFERENCES, one GET per ``TimeSheetEntry``. Nothing here can reach a real server.

Contract pinned here (the developer validates it, see the G3 report):
- ``list_time(id)`` -> ``{"id", "entries": [{entry_uri, person, date, hours, time_code}], "total_hours"}``;
  ``person`` is the ``dcterms:creator`` URI (it contains the user name); ``date`` starts with the ISO day;
- ``get_hours(item_ids, plan, group_by="person", max_entries=500, project=None)`` (``project`` is only needed
  for ``plan``, as in ``get_plan_items``) -> ``{"group_by", "groups", "total_hours", "items_counted",
  "entries_read", "partial", "unread_entries", "errors", "truncated"}``;
  person groups ``{"person", "hours"}``; item groups ``{"id", "hours", "estimate_hours", "without_entries"}``;
  ``errors`` entries ``{"entry_uri", "error"}``; the selection is the given work items PLUS their direct
  children, exactly the ids ``get_plan_items`` returns, each work item counted once;
- ``max_entries`` caps the entries READ (the sheet order is kept); an invalid ``group_by`` or a
  ``max_entries`` < 1 is a ToolError.

New symbols are reached by attribute INSIDE each test so each test fails by itself.
"""

import inspect

import pytest
from mcp.server.mcpserver.exceptions import ToolError

from elm_mcp import oslc, server
from elm_mcp.auth import close_client

from .w5_fixtures import (
    BASE,
    FOREIGN,
    MISSING,
    PROJECT,
    SEVEN,
    Alm,
    E,
    entry_uri,
    hours_ms,
)


@pytest.fixture(autouse=True)
async def _cfg(monkeypatch):
    monkeypatch.setattr("elm_mcp.config.settings.elm_url", BASE)
    yield
    await close_client()


def _people(result: dict) -> dict:
    return {g["person"]: g["hours"] for g in result["groups"]}


def _hours_of(people: dict, name: str) -> float:
    found = [v for k, v in people.items() if name in k]
    assert len(found) == 1, people
    return found[0]


def _rows(result: dict) -> dict:
    return {g["id"]: g for g in result["groups"]}


# ======================================================================
# list_time
# ======================================================================
async def test_list_time_returns_every_entry_with_person_date_hours_and_time_code(w2_router):
    alm = Alm(w2_router)
    alm.add_item("559832", time_spent=hours_ms(36), estimate=hours_ms(40), entries=SEVEN)
    result = await server.list_time("559832")
    assert len(result["entries"]) == 7
    first = next(e for e in result["entries"] if e["entry_uri"] == entry_uri("_ID001"))
    assert "jdoe" in first["person"]
    assert first["date"].startswith("2025-06-18")
    assert first["hours"] == 4.0
    assert first["time_code"] == "Direct hours"
    assert result["total_hours"] == 36.0
    assert alm.only_reads()


@pytest.mark.parametrize(
    ("ms", "expected"),
    [(5400000, 1.5), (1000000, 0.28), (3600000, 1.0), (0, 0.0), (None, 0.0), (MISSING, 0.0), (1, 0.0)],
)
async def test_list_time_converts_ms_to_hours_with_two_decimals_and_null_time_is_zero(w2_router, ms, expected):
    alm = Alm(w2_router)
    alm.add_item("559832", time_spent=None, entries=[E("_ID001", ms)])
    result = await server.list_time("559832")
    assert result["entries"][0]["hours"] == expected
    assert result["total_hours"] == expected


async def test_list_time_of_an_item_without_a_time_sheet_link_is_empty_not_an_error(w2_router):
    alm = Alm(w2_router)
    alm.add_item("559832", time_spent=None, sheet_link=False)
    result = await server.list_time("559832")
    assert result["entries"] == []
    assert result["total_hours"] == 0.0


async def test_list_time_of_a_sheet_without_entries_is_empty(w2_router):
    alm = Alm(w2_router)
    alm.add_item("559832", time_spent=None, entries=[])
    result = await server.list_time("559832")
    assert result["entries"] == []
    assert result["total_hours"] == 0.0


async def test_list_time_accepts_a_single_entry_given_as_an_object_not_a_list(w2_router):
    alm = Alm(w2_router)
    alm.add_item("559832", time_spent=hours_ms(2), entries=[E("_ID001", hours_ms(2))], single_dict=True)
    result = await server.list_time("559832")
    assert [e["hours"] for e in result["entries"]] == [2.0]


async def test_list_time_refuses_a_sheet_link_on_another_origin_without_requesting_it(w2_router):
    alm = Alm(w2_router)
    data = alm.add_item("559832", time_spent=None)
    data["rtc_cm:timeSheet"]["rdf:resource"] = f"{FOREIGN}/ccm/oslc/workitems/559832/rtc_cm:timeSheet"
    with pytest.raises(ToolError, match="outside the configured ELM server"):
        await server.list_time("559832")
    assert alm.foreign.call_count == 0


async def test_list_time_reports_the_gate_diagnosis_when_the_sheet_is_not_found(w2_router):
    alm = Alm(w2_router)
    alm.add_item("559832", time_spent=None)
    del alm.sheets["559832"]
    with pytest.raises(ToolError, match=r"\[recurso-inexistente-ou-invisivel\]"):
        await server.list_time("559832")


# ======================================================================
# get_hours: grouping, conversion, counters
# ======================================================================
async def test_get_hours_by_person_sums_the_creator_hours_and_counts_items_and_entries(w2_router):
    alm = Alm(w2_router)
    alm.add_item("559832", time_spent=hours_ms(36), estimate=hours_ms(40), entries=SEVEN)
    result = await server.get_hours(item_ids=["559832"], group_by="person")
    people = _people(result)
    assert _hours_of(people, "jdoe") == 20.0
    assert _hours_of(people, "asmith") == 16.0
    assert len(people) == 2
    assert result["total_hours"] == 36.0
    assert result["items_counted"] == 1
    assert result["entries_read"] == 7
    assert result["partial"] is False
    assert result["errors"] == []
    assert alm.only_reads()


async def test_get_hours_group_by_defaults_to_person_and_max_entries_to_500(w2_router):
    params = inspect.signature(server.get_hours).parameters
    assert params["group_by"].default == "person"
    assert params["max_entries"].default == 500


async def test_get_hours_by_item_sums_per_work_item_with_the_estimate_in_hours(w2_router):
    alm = Alm(w2_router)
    alm.add_item("100", time_spent=hours_ms(36), estimate=hours_ms(40), children=("101",), entries=SEVEN)
    alm.add_item("101", time_spent=hours_ms(1.5), estimate=hours_ms(2), entries=[E("_ID101", hours_ms(1.5), "asmith")])
    result = await server.get_hours(item_ids=["100"], group_by="item")
    rows = _rows(result)
    assert set(rows) == {"100", "101"}
    assert rows["100"]["hours"] == 36.0
    assert rows["100"]["estimate_hours"] == 40.0
    assert rows["101"]["hours"] == 1.5
    assert rows["101"]["estimate_hours"] == 2.0
    assert result["items_counted"] == 2
    assert result["entries_read"] == 8
    assert not rows["100"].get("without_entries")


async def test_get_hours_by_person_rounds_each_group_to_two_decimals(w2_router):
    alm = Alm(w2_router)
    entries = [E("_ID001", 1000000, "jdoe"), E("_ID002", 1000000, "jdoe"), E("_ID003", 1000000, "jdoe")]
    alm.add_item("559832", time_spent=3000000, entries=entries)
    result = await server.get_hours(item_ids=["559832"])
    assert _hours_of(_people(result), "jdoe") == 0.83  # 3_000_000 ms = 0.8333 h


async def test_get_hours_null_time_spent_without_entries_is_zero_and_raises_nothing(w2_router):
    alm = Alm(w2_router)
    alm.add_item("100", time_spent=None, estimate=None, children=("101",), entries=[])
    alm.add_item("101", time_spent=None, entries=[E("_ID101", None, "jdoe")])
    result = await server.get_hours(item_ids=["100"], group_by="item")
    rows = _rows(result)
    assert rows["100"]["hours"] == 0.0
    assert rows["101"]["hours"] == 0.0
    assert not rows["100"].get("without_entries")  # no timeSpent: nothing to flag
    assert result["total_hours"] == 0.0
    assert result["errors"] == []


async def test_get_hours_item_with_time_spent_but_no_entries_uses_its_time_spent_and_is_flagged(w2_router):
    alm = Alm(w2_router)
    alm.add_item("100", time_spent=hours_ms(36), children=("101", "102"), entries=SEVEN)
    alm.add_item("101", time_spent=hours_ms(5), entries=[])
    alm.add_item("102", time_spent=hours_ms(1), entries=[E("_ID102", hours_ms(1), "asmith")])
    rows = _rows(await server.get_hours(item_ids=["100"], group_by="item"))
    assert rows["101"]["hours"] == 5.0
    assert rows["101"]["without_entries"] is True
    assert not rows["102"].get("without_entries")
    assert not rows["100"].get("without_entries")


async def test_get_hours_without_entries_flag_works_when_the_sheet_link_is_absent(w2_router):
    alm = Alm(w2_router)
    alm.add_item("100", time_spent=hours_ms(3), sheet_link=False)
    rows = _rows(await server.get_hours(item_ids=["100"], group_by="item"))
    assert rows["100"]["hours"] == 3.0
    assert rows["100"]["without_entries"] is True


# ======================================================================
# get_hours: max_entries, errors, concurrency
# ======================================================================
async def test_get_hours_stops_at_max_entries_and_returns_the_partial_without_raising(w2_router):
    alm = Alm(w2_router)
    alm.add_item("559832", time_spent=hours_ms(36), entries=SEVEN)
    result = await server.get_hours(item_ids=["559832"], max_entries=3)
    assert result["partial"] is True
    assert result["entries_read"] == 3
    assert result["unread_entries"] == 4
    assert result["total_hours"] == 12.0  # the first three of the sheet: 4 + 2 + 6 h
    assert sorted(alm.entry_hits) == ["_ID001", "_ID002", "_ID003"]  # it STOPPED reading, no extra GET


async def test_get_hours_max_entries_equal_to_the_total_is_not_partial(w2_router):
    alm = Alm(w2_router)
    alm.add_item("559832", time_spent=hours_ms(36), entries=SEVEN)
    result = await server.get_hours(item_ids=["559832"], max_entries=7)
    assert result["partial"] is False
    assert result["unread_entries"] == 0
    assert result["entries_read"] == 7


async def test_get_hours_max_entries_counts_across_all_selected_items(w2_router):
    alm = Alm(w2_router)
    alm.add_item("100", time_spent=None, children=("101", "102"), entries=[E(f"_A{i}", hours_ms(1)) for i in range(3)])
    alm.add_item("101", time_spent=None, entries=[E(f"_B{i}", hours_ms(1)) for i in range(3)])
    alm.add_item("102", time_spent=None, entries=[E(f"_C{i}", hours_ms(1)) for i in range(3)])
    result = await server.get_hours(item_ids=["100"], max_entries=5)
    assert result["entries_read"] == 5
    assert result["unread_entries"] == 4
    assert result["partial"] is True
    assert len(alm.entry_hits) == 5
    assert result["total_hours"] == 5.0


@pytest.mark.parametrize("bad", [0, -1])
async def test_get_hours_refuses_a_max_entries_below_one(w2_router, bad):
    alm = Alm(w2_router)
    alm.add_item("100", time_spent=None, entries=[E("_ID001", hours_ms(1))])
    with pytest.raises(ToolError, match="max_entries"):
        await server.get_hours(item_ids=["100"], max_entries=bad)
    assert alm.entry_hits == []


async def test_get_hours_refuses_an_unknown_group_by_before_any_request(w2_router):
    alm = Alm(w2_router)
    alm.add_item("100", time_spent=None)
    with pytest.raises(ToolError, match="group_by"):
        await server.get_hours(item_ids=["100"], group_by="team")
    assert alm.sheet_hits == []


@pytest.mark.parametrize("failure", ["500", "404", "not-json"])
async def test_get_hours_one_failed_entry_goes_to_errors_and_the_rest_is_summed(w2_router, failure):
    import httpx

    alm = Alm(w2_router)
    alm.add_item("559832", time_spent=hours_ms(36), entries=SEVEN)
    alm.entries["_ID003"] = {
        "500": httpx.Response(500, text="boom"),
        "404": httpx.Response(404, text="gone"),
        "not-json": httpx.Response(200, text="<html>not json</html>"),
    }[failure]
    result = await server.get_hours(item_ids=["559832"])
    assert len(result["errors"]) == 1
    assert result["errors"][0]["entry_uri"] == entry_uri("_ID003")
    assert result["errors"][0]["error"]
    assert result["entries_read"] == 6
    assert result["total_hours"] == 30.0  # 36 h minus the unreadable 6 h entry
    assert _hours_of(_people(result), "jdoe") == 14.0
    assert _hours_of(_people(result), "asmith") == 16.0
    assert result["partial"] is False


async def test_get_hours_reauthenticates_once_and_counts_the_entry_after_an_expired_session(w2_router):
    alm = Alm(w2_router)
    alm.add_item("559832", time_spent=hours_ms(36), entries=SEVEN)
    alm.once_401.add("_ID001")
    result = await server.get_hours(item_ids=["559832"])
    assert alm.login.call_count >= 2  # first login + the re-authentication
    assert result["errors"] == []
    assert result["total_hours"] == 36.0


async def test_get_hours_reads_entries_with_at_most_8_requests_in_flight(w2_router):
    alm = Alm(w2_router, delay=0.02)
    kids = ("2", "3", "4")
    alm.add_item("1", time_spent=None, children=kids, entries=[E(f"_P{i}", hours_ms(1)) for i in range(10)])
    for kid in kids:
        alm.add_item(kid, time_spent=None, entries=[E(f"_K{kid}_{i}", hours_ms(1)) for i in range(10)])
    result = await server.get_hours(item_ids=["1"])
    assert result["entries_read"] == 40
    assert len(alm.entry_hits) == 40
    assert alm.peak <= 8


async def test_get_hours_does_read_entries_concurrently_not_one_by_one(w2_router):
    alm = Alm(w2_router, delay=0.02)
    alm.add_item("1", time_spent=None, entries=[E(f"_P{i}", hours_ms(1)) for i in range(20)])
    await server.get_hours(item_ids=["1"])
    assert alm.peak >= 2  # SPEC: limited concurrency, not a serial loop of 130 calls


async def test_get_hours_never_requests_an_entry_on_another_origin(w2_router):
    alm = Alm(w2_router)
    alm.add_item("559832", time_spent=hours_ms(36), entries=SEVEN)
    sheet = alm.sheets["559832"]
    sheet["rtc_cm:entry"][1]["rdf:resource"] = f"{FOREIGN}/ccm/resource/itemOid/x.TimeSheetEntry/_ID002"
    result = await server.get_hours(item_ids=["559832"])
    assert alm.foreign.call_count == 0
    assert len(result["errors"]) == 1
    assert result["entries_read"] == 6
    assert result["total_hours"] == 34.0


# ======================================================================
# get_hours: selection equal to get_plan_items
# ======================================================================
def _plan_ids(plan: dict) -> set[str]:
    ids: set[str] = set()
    for ib in plan["items"]:
        ids.add(ib["id"])
        ids.update(child["id"] for child in ib["children"])
    return ids


def _three_level_alm(router) -> Alm:
    alm = Alm(router)
    alm.add_item(
        "100", time_spent=hours_ms(1), children=("101", "102"), entries=[E("_E100", hours_ms(1))], type_="story"
    )
    alm.add_item("101", time_spent=hours_ms(2), entries=[E("_E101", hours_ms(2), "asmith")])
    alm.add_item("102", time_spent=hours_ms(3), entries=[E("_E102", hours_ms(3))])
    alm.add_item("200", time_spent=hours_ms(4), children=("201",), entries=[E("_E200", hours_ms(4))], type_="story")
    alm.add_item("201", time_spent=hours_ms(5), entries=[E("_E201", hours_ms(5))])
    alm.add_item("999", time_spent=hours_ms(50), entries=[E("_E999", hours_ms(50))])  # not selected
    return alm


async def test_get_hours_by_item_covers_exactly_the_work_items_of_get_plan_items(w2_router):
    _three_level_alm(w2_router)
    plan = await server.get_plan_items(PROJECT, item_ids=["100", "200"])
    result = await server.get_hours(item_ids=["100", "200"], group_by="item")
    assert set(_rows(result)) == _plan_ids(plan) == {"100", "101", "102", "200", "201"}
    assert result["items_counted"] == 5
    assert result["total_hours"] == 15.0


async def test_get_hours_counts_a_work_item_once_even_when_it_is_also_a_child_of_a_selected_item(w2_router):
    alm = _three_level_alm(w2_router)
    result = await server.get_hours(item_ids=["100", "101"], group_by="item")
    assert result["items_counted"] == 3
    assert result["total_hours"] == 6.0
    assert result["entries_read"] == 3
    assert alm.entry_hits.count("_E101") == 1


async def test_get_hours_plan_mode_uses_the_get_plan_items_selection_and_limit_1000(w2_router, monkeypatch):
    alm = _three_level_alm(w2_router)
    iteration = f"{BASE}/ccm/oslc/iterations/_IT1"
    alm.items["100"]["rtc_cm:plannedFor"] = {"rdf:resource": iteration}
    alm.items["200"]["rtc_cm:plannedFor"] = {"rdf:resource": f"{BASE}/ccm/oslc/iterations/_IT2"}
    seen: list = []

    async def iterations(project):
        return [{"title": "Sprint 1", "uri": iteration, "start_date": "2026-01-01", "end_date": None}]

    async def query(domain, project, where=None, select=None, limit=None):
        seen.append(limit)
        found = oslc.QueryResults(
            [
                {"dcterms:identifier": "100", "dcterms:type": "story"},
                {"dcterms:identifier": "200", "dcterms:type": "story"},
                {"dcterms:identifier": "999", "dcterms:type": "task"},
            ]
        )
        found.truncated = True
        return found

    monkeypatch.setattr(server, "list_iterations", iterations)
    monkeypatch.setattr(oslc, "query_resources", query)
    plan = await server.get_plan_items(PROJECT, plan="Sprint 1")
    seen.clear()
    result = await server.get_hours(plan="Sprint 1", project=PROJECT, group_by="item")
    assert set(_rows(result)) == _plan_ids(plan) == {"100", "101", "102"}
    assert seen == [oslc.MAX_QUERY_LIMIT] == [1000]
    assert result["truncated"] is True  # the 1000-item ceiling of the selection is reported, as get_plan_items does


# ======================================================================
# Contract: registered, read only, no internal API
# ======================================================================
async def test_the_w5_hours_tools_are_registered_as_read_only_with_the_agreed_parameters():
    tools = {t.name: t for t in await server.mcp.list_tools()}
    list_time = tools["list_time"]
    get_hours = tools["get_hours"]
    assert list_time.annotations.read_only_hint is True
    assert get_hours.annotations.read_only_hint is True
    assert list(list_time.input_schema["properties"]) == ["id"]
    assert list_time.input_schema["required"] == ["id"]
    props = get_hours.input_schema["properties"]
    assert {"item_ids", "plan", "group_by", "max_entries"} <= set(props)
    assert props["group_by"]["default"] == "person"
    assert props["max_entries"]["default"] == 500
    assert "project" in props  # needed by ``plan`` (same as get_plan_items); optional
    assert not get_hours.input_schema.get("required")


def test_the_hours_tools_have_no_internal_api_dependency():
    import elm_mcp.server as srv

    assert hasattr(srv, "list_time")
    assert hasattr(srv, "get_hours")
    source = inspect.getsource(srv.get_hours) + inspect.getsource(srv.list_time)
    assert "ELM_ENABLE_INTERNAL_API" not in source
