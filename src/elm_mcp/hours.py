"""Time sheet reading (SPEC-horas, W5): entries of a work item and hours grouped by person or by item.

Read only: every call is a GET (JSON). The work item and the entry carry time in MILLISECONDS
(``rtc_cm:timeSpent``, ``rtc_cm:estimate``); the sheet (``rtc_cm:timeSheet``) lists only references and
each ``TimeSheetEntry`` needs its own GET. Sums are kept in milliseconds and converted once, so rounding
never accumulates.
"""

import asyncio
import logging

import httpx

from . import oslc

logger = logging.getLogger(__name__)

MS_PER_HOUR = 3_600_000
MAX_CONCURRENT_ENTRIES = 8
GROUP_BY = ("person", "item")
UNKNOWN_PERSON = "(unknown)"


def ms_to_hours(value) -> float:
    """Milliseconds -> hours with 2 decimals; null, missing or non-numeric time is 0."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return 0.0
    return round(value / MS_PER_HOUR, 2)


def _ms(value) -> int | float:
    return 0 if isinstance(value, bool) or not isinstance(value, (int, float)) else value


async def _sheet_entry_uris(client: httpx.AsyncClient, item: dict) -> list[str]:
    """References to the entries of the work item's sheet; [] when the item has no sheet link or no entries."""
    sheet_uri = oslc._resource_uri(item.get("rtc_cm:timeSheet"))
    if not sheet_uri:
        return []
    sheet = (await oslc.fetch(client, "GET", sheet_uri, "list_time", headers=oslc._JSON_HEADERS)).json()
    if not isinstance(sheet, dict):
        return []
    return [u for u in (oslc._resource_uri(e) for e in oslc._as_list(sheet.get("rtc_cm:entry"))) if u]


async def _read_entry(client: httpx.AsyncClient, uri: str, gate: asyncio.Semaphore) -> dict:
    """GET one TimeSheetEntry: {entry_uri, person, date, ms, time_code}. Raises on any failure."""
    async with gate:
        resp = await oslc.fetch(client, "GET", uri, "list_time", headers=oslc._JSON_HEADERS)
        data = resp.json()
    if not isinstance(data, dict):
        raise ValueError("the entry is not a JSON object")  # noqa: TRY004 - ValueError becomes a ToolError
    creator = data.get("dcterms:creator")
    return {
        "entry_uri": uri,
        "person": oslc._resource_uri(creator) or (creator if isinstance(creator, str) else None),
        "date": data.get("rtc_cm:startDate"),
        "ms": _ms(data.get("rtc_cm:timeSpent")),
        "time_code": data.get("rtc_cm:timeCode") or data.get("rtc_cm:timeCodeId"),
    }


async def _read_entries(client: httpx.AsyncClient, uris: list[str]) -> list[dict | Exception]:
    """Read the entries with at most MAX_CONCURRENT_ENTRIES requests in flight; a failure is returned in place."""
    gate = asyncio.Semaphore(MAX_CONCURRENT_ENTRIES)
    return await asyncio.gather(*(_read_entry(client, u, gate) for u in uris), return_exceptions=True)


async def list_time(id: str) -> dict:
    """Entries of the work item's time sheet and their total. A failed entry fails the call."""
    client = await oslc.get_client()
    item, _ = await oslc._read_item(client, oslc.workitem_uri(id), "list_time")
    results = await _read_entries(client, await _sheet_entry_uris(client, item))
    entries = []
    for result in results:
        if isinstance(result, BaseException):
            raise result
        entries.append(result)
    return {
        "id": str(id),
        "entries": [
            {
                "entry_uri": e["entry_uri"],
                "person": e["person"],
                "date": e["date"],
                "hours": ms_to_hours(e["ms"]),
                "time_code": e["time_code"],
            }
            for e in entries
        ],
        "total_hours": ms_to_hours(sum(e["ms"] for e in entries)),
    }


def _error(uri: str, exc: BaseException, item_id: str | None = None) -> dict:
    error = {"entry_uri": uri, "error": str(exc) or type(exc).__name__}
    if item_id is not None:
        error["item"] = item_id
    return error


async def _read_item_sheet(client: httpx.AsyncClient, wid: str, gate: asyncio.Semaphore) -> dict:
    """{id, time_spent, estimate, uris, error}: the work item and the references of its sheet."""
    info = {"id": wid, "time_spent": 0, "estimate": 0, "uris": [], "error": None}
    uri = oslc.workitem_uri(wid)
    try:
        async with gate:
            item, _ = await oslc._read_item(client, uri, "get_hours")
            info["time_spent"] = _ms(item.get("rtc_cm:timeSpent"))
            info["estimate"] = _ms(item.get("rtc_cm:estimate"))
            info["uris"] = await _sheet_entry_uris(client, item)
    except Exception as exc:  # noqa: BLE001 - one unreadable item must not stop the report
        info["error"] = _error(uri, exc, wid)
    return info


async def collect_hours(item_ids: list[str], group_by: str, max_entries: int) -> dict:
    """Hours of the given work items (already expanded and deduplicated by the caller)."""
    client = await oslc.get_client()
    gate = asyncio.Semaphore(MAX_CONCURRENT_ENTRIES)
    infos = await asyncio.gather(*(_read_item_sheet(client, wid, gate) for wid in item_ids))
    errors = [i["error"] for i in infos if i["error"]]

    pairs = [(i["id"], uri) for i in infos for uri in i["uris"]]  # item order, then sheet order
    to_read, unread = pairs[:max_entries], len(pairs) - min(len(pairs), max_entries)
    results = await _read_entries(client, [uri for _, uri in to_read])

    item_ms: dict[str, int | float] = {i["id"]: 0 for i in infos}
    person_ms: dict[str, int | float] = {}
    read = 0
    for (wid, uri), result in zip(to_read, results, strict=True):
        if isinstance(result, BaseException):
            errors.append(_error(uri, result, wid))
            continue
        read += 1
        item_ms[wid] += result["ms"]
        person = result["person"] or UNKNOWN_PERSON
        person_ms[person] = person_ms.get(person, 0) + result["ms"]

    without = [i["id"] for i in infos if not i["error"] and not i["uris"] and i["time_spent"] > 0]
    if group_by == "person":
        groups = [
            {"person": p, "hours": ms_to_hours(v)} for p, v in sorted(person_ms.items(), key=lambda kv: (-kv[1], kv[0]))
        ]
        total_ms = sum(person_ms.values())
    else:
        groups = []
        total_ms = 0
        for i in infos:
            ms = i["time_spent"] if i["id"] in without else item_ms[i["id"]]
            total_ms += ms
            row = {"id": i["id"], "hours": ms_to_hours(ms), "estimate_hours": ms_to_hours(i["estimate"])}
            if i["id"] in without:
                row["without_entries"] = True
            groups.append(row)
    return {
        "group_by": group_by,
        "groups": groups,
        "total_hours": ms_to_hours(total_ms),
        "items_counted": len(infos),
        "entries_read": read,
        "partial": unread > 0,
        "unread_entries": unread,
        "items_without_entries": without,
        "errors": errors,
    }
