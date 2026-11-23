"""Shared tenant-neutral fakes for the W5 tests (hours and attachments, read only).

Neutral values only: https://elm.example.com, project area _PA1, users jdoe/asmith. Every route lives on a
private respx router (a request without a route raises); nothing here reads ~/.elm_creds.json or reaches a
real server, and the fake NEVER accepts a write (only GET and the form login exist).

``Alm`` is a small stateful CCM server built from the sanitized real fixtures in tests/fixtures
(ccm_wi.json, ccm_sheet.json, ccm_entry.json, ccm_attachment.json):
  * work item JSON with ``rtc_cm:timeSpent``/``rtc_cm:estimate`` in milliseconds and a ``rtc_cm:timeSheet`` link;
  * the sheet lists ONLY references (``rtc_cm:entry`` = ``rdf:resource``); every entry needs its own GET;
  * entry JSON (TimeSheetEntry: ``rtc_cm:timeSpent`` ms, ``startDate``, ``timeCode``, ``creator``);
  * attachments reproduce the live G0 findings: the SAME URI answers metadata to ``Accept: application/json``,
    the bytes (with ``Content-Disposition: attachment; filename*=utf-8''<percent-encoded>``) to
    ``Accept: */*`` only, and HTTP 406 to any other Accept (``image/png``, ``application/octet-stream``...).
The fake counts requests in flight to the entry URIs (``peak``) so a test can measure concurrency.
"""

import asyncio
import copy
import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import quote, unquote

import httpx
import respx

from . import w2_fixtures as w2

BASE = w2.BASE
PROJECT = w2.PROJECT
FIXTURES = Path(__file__).resolve().parent / "fixtures"
RTC_CM = "http://jazz.net/xmlns/prod/jazz/rtc/cm/1.0/"
CHILDREN = "rtc_cm:com.ibm.team.workitem.linktype.parentworkitem.children"
ATTACHMENTS = "rtc_cm:com.ibm.team.workitem.linktype.attachment.attachment"
MISSING = object()  # an entry without the rtc_cm:timeSpent key at all (null is None)

FOREIGN = "https://evil.example.org"


def load(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def item_uri(wid: str | int) -> str:
    return w2.item_uri(wid)


def sheet_uri(wid: str | int) -> str:
    return f"{BASE}/ccm/oslc/workitems/{wid}/rtc_cm:timeSheet"


def entry_uri(eid: str) -> str:
    return f"{BASE}/ccm/resource/itemOid/com.ibm.team.workitem.TimeSheetEntry/{eid}"


def att_uri(aid: str) -> str:
    return f"{BASE}/ccm/resource/itemOid/com.ibm.team.workitem.Attachment/{aid}"


def user(name: str) -> str:
    return w2.user_uri(name)


def hours_ms(hours: float) -> int:
    return round(hours * 3_600_000)


@dataclass
class E:
    """One TimeSheetEntry of the fake: id, time spent in ms (None = null, MISSING = no key), creator, start, code."""

    eid: str
    ms: object
    who: str = "jdoe"
    date: str = "2025-06-18T12:00:00.000Z"
    code: str = "Direct hours"


# 7 entries of the ccm_sheet.json fixture: jdoe 4+6+10 = 20 h, asmith 2+8+3+3 = 16 h, total 36 h (= ccm_wi timeSpent)
SEVEN = [
    E("_ID001", 14400000, "jdoe"),
    E("_ID002", 7200000, "asmith"),
    E("_ID003", 21600000, "jdoe"),
    E("_ID004", 28800000, "asmith"),
    E("_ID005", 36000000, "jdoe"),
    E("_ID006", 10800000, "asmith"),
    E("_ID007", 10800000, "asmith"),
]


@dataclass
class Att:
    """An attachment of the fake. ``disposition`` None = no Content-Disposition header."""

    aid: str
    identifier: int
    name: str
    data: bytes = b"\x89PNG\r\n\x1a\n"
    fmt: str = "image/png"
    creator: str = "jdoe"
    created: str = "2025-07-21T18:52:06.372Z"
    disposition: str | None = MISSING  # type: ignore[assignment]  # MISSING = derive from ``name``
    content_status: int | None = None  # force this status for the content request (e.g. 406 even for */*)
    title: str | None = None  # dcterms:title of the METADATA (default: the plain name)
    uri: str | None = None

    def __post_init__(self) -> None:
        self.uri = self.uri or att_uri(self.aid)


def disposition_for(name: str) -> str:
    return f"attachment; filename*=utf-8''{quote(name, safe='')}"


@dataclass
class Alm:
    router: respx.MockRouter
    delay: float = 0.0
    items: dict = field(default_factory=dict)
    sheets: dict = field(default_factory=dict)
    entries: dict = field(default_factory=dict)
    atts: dict = field(default_factory=dict)
    entry_hits: list = field(default_factory=list)
    sheet_hits: list = field(default_factory=list)
    att_requests: list = field(default_factory=list)  # (aid, Accept header)
    once_401: set = field(default_factory=set)
    inflight: int = 0
    peak: int = 0

    def __post_init__(self) -> None:
        self.login = self.router.post(f"{BASE}/jts/j_security_check").mock(return_value=httpx.Response(200))
        self.router.route(method="GET", url__regex=rf"^{re.escape(BASE)}/").mock(side_effect=self._dispatch)
        self.foreign = self.router.route(host="evil.example.org").mock(
            side_effect=lambda request: httpx.Response(200, json=load("ccm_entry.json"))
        )

    # --- building ---------------------------------------------------------------------------
    def add_item(
        self,
        wid: str,
        *,
        time_spent: object = None,
        estimate: object = None,
        children: tuple = (),
        entries: list | None = None,
        type_: str = "task",
        planned_for: str | None = None,
        sheet_link: bool = True,
        single_dict: bool = False,
        attachments: list | None = None,
    ) -> dict:
        data = copy.deepcopy(load("ccm_wi.json"))
        data["dcterms:identifier"] = str(wid)
        data["dcterms:title"] = f"Item {wid}"
        data["dcterms:type"] = type_
        data["rtc_cm:timeSpent"] = time_spent
        data["rtc_cm:estimate"] = estimate
        data["rtc_cm:timeSheet"]["rdf:resource"] = sheet_uri(wid)
        if not sheet_link:
            del data["rtc_cm:timeSheet"]
        if children:
            data[CHILDREN] = [{"rdf:resource": item_uri(c), "dcterms:title": f"{c}: Child {c}"} for c in children]
        if planned_for:
            data["rtc_cm:plannedFor"] = {"rdf:resource": planned_for}
        if attachments is not None:
            refs = []
            for att in attachments:
                self.atts[att.aid] = att
                refs.append(
                    {
                        "rdf:resource": att.uri,
                        "dcterms:title": f"{att.identifier}: {att.name}",
                        "rdf:type": [{"rdf:resource": f"{RTC_CM}Attachment"}],
                    }
                )
            data[ATTACHMENTS] = refs
        self.items[str(wid)] = data
        self.set_entries(wid, entries or [], single_dict=single_dict)
        return data

    def set_entries(self, wid: str, entries: list, *, single_dict: bool = False) -> None:
        sheet = copy.deepcopy(load("ccm_sheet.json"))
        sheet["rdf:about"] = sheet_uri(wid)
        refs = [{"rdf:resource": entry_uri(e.eid), "rdf:type": sheet["rtc_cm:entry"][0]["rdf:type"]} for e in entries]
        if not refs:
            del sheet["rtc_cm:entry"]
        else:
            sheet["rtc_cm:entry"] = refs[0] if single_dict else refs
        self.sheets[str(wid)] = sheet
        for e in entries:
            data = copy.deepcopy(load("ccm_entry.json"))
            data["rdf:about"] = entry_uri(e.eid)
            data["rtc_cm:workItem"]["rdf:resource"] = item_uri(wid)
            data["rtc_cm:startDate"] = e.date
            data["rtc_cm:timeCode"] = e.code
            data["dcterms:creator"]["rdf:resource"] = user(e.who)
            if e.ms is MISSING:
                del data["rtc_cm:timeSpent"]
            else:
                data["rtc_cm:timeSpent"] = e.ms
            self.entries[e.eid] = data

    # --- serving ----------------------------------------------------------------------------
    async def _dispatch(self, request: httpx.Request) -> httpx.Response:
        path = unquote(request.url.path)
        if ".TimeSheetEntry/" in path:
            return await self._entry(path.rsplit("/", 1)[1])
        if path.endswith("/rtc_cm:timeSheet"):
            wid = path.split("/oslc/workitems/")[1].split("/")[0]
            self.sheet_hits.append(wid)
            spec = self.sheets.get(wid)
            if isinstance(spec, httpx.Response):
                return spec
            return httpx.Response(200, json=spec) if spec is not None else httpx.Response(404, text="no sheet")
        if ".workitem.Attachment/" in path:
            return self._attachment(request, path.rsplit("/", 1)[1])
        for marker in ("/com.ibm.team.workitem.WorkItem/", "/oslc/workitems/"):
            if marker in path:
                wid = path.split(marker)[1].split("/")[0]
                if wid in self.items:
                    return httpx.Response(200, json=self.items[wid], headers={"ETag": '"etag1"'})
        return httpx.Response(404, text="unknown")

    async def _entry(self, eid: str) -> httpx.Response:
        self.entry_hits.append(eid)
        self.inflight += 1
        self.peak = max(self.peak, self.inflight)
        try:
            if self.delay:
                await asyncio.sleep(self.delay)
        finally:
            self.inflight -= 1
        if eid in self.once_401:
            self.once_401.discard(eid)
            return httpx.Response(401, text="expired")
        spec = self.entries.get(eid)
        if isinstance(spec, httpx.Response):
            return spec
        return httpx.Response(200, json=spec) if spec is not None else httpx.Response(404, text="no entry")

    def _attachment(self, request: httpx.Request, aid: str) -> httpx.Response:
        accept = request.headers.get("accept", "")
        self.att_requests.append((aid, accept))
        att = self.atts.get(aid)
        if att is None:
            return httpx.Response(404, text="no attachment")
        if "json" in accept:
            meta = copy.deepcopy(load("ccm_attachment.json"))
            meta["rdf:about"] = att.uri
            meta["dcterms:identifier"] = att.identifier
            meta["dcterms:title"] = att.title if att.title is not None else att.name
            meta["dcterms:format"] = att.fmt
            meta["dcterms:created"] = att.created
            meta["dcterms:creator"]["rdf:resource"] = user(att.creator)
            return httpx.Response(200, json=meta)
        if accept.strip() == "*/*":
            if att.content_status:
                return httpx.Response(att.content_status, text="not acceptable")
            headers = {"Content-Type": att.fmt}
            disp = disposition_for(att.name) if att.disposition is MISSING else att.disposition
            if disp is not None:
                headers["Content-Disposition"] = disp
            return httpx.Response(200, content=att.data, headers=headers)
        return httpx.Response(406, text="not acceptable")

    # --- assertions helpers -----------------------------------------------------------------
    def only_reads(self) -> bool:
        """True when every request sent was a GET or the form login (no write ever reaches the fake)."""
        return all(
            c.request.method == "GET" or c.request.url.path.endswith("/j_security_check") for c in self.router.calls
        )
