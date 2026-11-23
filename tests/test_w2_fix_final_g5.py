"""Final G5 fix (RED) for wave W2: add_comment text matching, redirects, integer ranges.

Private respx routers only: an unrouted request raises, so nothing reaches a real server.
Tenant-neutral data only (https://elm.example.com).
"""

import json

import httpx
import pytest
from mcp.server.mcpserver.exceptions import ToolError

from elm_mcp import oslc, server
from elm_mcp.auth import close_client, get_client

from . import f1_fixtures as f
from .test_w2_loop2_g5 import XSD, _typed_item
from .w2_fixtures import BASE, FakeComments, mock_login

EVIL = "https://evil.example.org"


@pytest.fixture(autouse=True)
async def _cfg(monkeypatch):
    monkeypatch.setattr("elm_mcp.config.settings.elm_url", BASE)
    yield
    await close_client()


class _ConcurrentComments(FakeComments):
    """Someone else comments at the same time; the POST answer carries no id of the created comment."""

    def _post(self, request: httpx.Request) -> httpx.Response:
        self.posts.append(request)
        text = json.loads(request.content)["dc:description"]
        n = len(self.comments)
        self.comments.append((str(n + 1), "concurrent comment by someone else"))
        if self.persist:
            self.comments.append((str(n + 2), text))
        return httpx.Response(201, json={})


# ======================================================================
# 1. add_comment: the same normalisation on both sides of the text comparison
# ======================================================================
async def test_add_comment_finds_the_comment_by_text_with_markup_characters_and_a_concurrent_one(w2_router):
    mock_login(w2_router)
    thread = _ConcurrentComments(w2_router, "100")
    result = await server.add_comment("100", "use <b> tag & more")
    assert len(thread.posts) == 1
    assert result["id"] == "3"


async def test_add_comment_without_created_id_and_without_the_comment_raises_without_a_second_post(w2_router):
    mock_login(w2_router)
    thread = _ConcurrentComments(w2_router, "100", persist=False)
    with pytest.raises(ToolError, match="NOT resent"):
        await server.add_comment("100", "use <b> tag & more")
    assert len(thread.posts) == 1


# ======================================================================
# 2. redirects must not leave the configured origin
# ======================================================================
async def test_a_redirect_to_another_host_is_refused_and_never_requested(w2_router):
    mock_login(w2_router)
    w2_router.get(f"{BASE}/ccm/oslc/redirected").mock(
        return_value=httpx.Response(302, headers={"Location": f"{EVIL}/steal"})
    )
    evil = w2_router.get(url__startswith=EVIL).mock(return_value=httpx.Response(200, json={}))
    client = await get_client()
    with pytest.raises(ValueError, match="ELM server"):
        await oslc._request(client, "GET", f"{BASE}/ccm/oslc/redirected")
    assert evil.call_count == 0


async def test_a_redirect_inside_the_origin_is_followed(w2_router):
    mock_login(w2_router)
    w2_router.get(f"{BASE}/ccm/oslc/old").mock(return_value=httpx.Response(302, headers={"Location": "/ccm/oslc/new"}))
    w2_router.get(f"{BASE}/ccm/oslc/new").mock(return_value=httpx.Response(200, json={"ok": True}))
    client = await get_client()
    resp = await oslc._request(client, "GET", f"{BASE}/ccm/oslc/old")
    assert resp.json() == {"ok": True}


# ======================================================================
# 3. _convert_to_range: strict ASCII integers, wider integer family
# ======================================================================
@pytest.mark.parametrize("range_name", ["integer", "int", "long", "short", "byte", "negativeInteger"])
@pytest.mark.parametrize("raw", ["1_000", "٣", "１２", "1.0", "0x10", "", "+-1"])
def test_convert_to_range_refuses_non_ascii_or_underscored_integers(range_name, raw):
    with pytest.raises(ValueError, match="not a valid"):
        oslc._convert_to_range("custom", raw, f"{XSD}{range_name}")


@pytest.mark.parametrize(
    ("range_name", "raw", "expected"),
    [
        ("integer", "-5", -5),
        ("integer", "+7", 7),
        ("negativeInteger", "-1", -1),
        ("nonNegativeInteger", "0", 0),
        ("positiveInteger", "3", 3),
        ("unsignedInt", "4", 4),
        ("unsignedLong", "4", 4),
        ("unsignedShort", "4", 4),
        ("unsignedByte", "4", 4),
        ("nonPositiveInteger", "-2", -2),
        ("long", " 9 ", 9),
    ],
)
def test_convert_to_range_accepts_the_integer_family(range_name, raw, expected):
    got = oslc._convert_to_range("custom", raw, f"{XSD}{range_name}")
    assert got == expected
    assert type(got) is int


@pytest.mark.parametrize(
    ("range_name", "raw"),
    [("negativeInteger", "1"), ("nonNegativeInteger", "-1"), ("positiveInteger", "0"), ("unsignedInt", "-3")],
)
def test_convert_to_range_respects_the_sign_of_the_range(range_name, raw):
    with pytest.raises(ValueError, match="not a valid"):
        oslc._convert_to_range("custom", raw, f"{XSD}{range_name}")


@pytest.mark.parametrize("raw", ["1_5", "١٫٥", "nan", "inf", "1e999"])
def test_convert_to_range_refuses_odd_decimals(raw):
    with pytest.raises(ValueError, match="not a valid"):
        oslc._convert_to_range("custom", raw, f"{XSD}decimal")


async def test_set_field_refuses_an_underscored_integer_before_writing(w2_router):
    prop = f.prop("custom", "Custom", propdef=f"{f.RTC_CM}custom", range_uri=f"{XSD}integer")
    item = _typed_item(w2_router, [prop])
    with pytest.raises(ToolError, match="custom"):
        await server.set_field("100", "rtc_cm:custom", "1_000")
    assert item.puts == []
