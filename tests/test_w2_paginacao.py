"""G3 (RED) tests for SPEC-paginacao-sessao (wave W2, release v0.6.0).

The work item query endpoint is a fake paged server (tests/w2_fixtures.py::FakePages) on a
private respx router. Contract pinned here (the developer validates it, see the G3 report):

- ``oslc.query_resources(domain, project, where=None, select=None, limit=None)`` still returns a
  list (so existing callers keep working); the returned list carries a ``truncated`` bool
  attribute.
- ``list_workitems`` returns ``{"items": [...], "truncated": bool}``.
- Expired session while paging: re-authenticate once and repeat ONLY the failed page; if that
  fails, ToolError with the gate AND the number of items already collected.

New symbols are reached by attribute / keyword INSIDE each test so each test fails by itself.
"""

import re

import httpx
import pytest
import respx
from mcp.server.mcpserver.exceptions import ToolError

from elm_mcp import oslc, server
from elm_mcp.auth import close_client

from .w2_fixtures import AUTH_FAIL_HEADER, BASE, PROJECT, FakePages, ids_of, mock_ccm, paginate, wi

DEFAULT_LIMIT = 50
ABSOLUTE_CAP = 1000


@pytest.fixture(autouse=True)
async def _cfg(monkeypatch):
    monkeypatch.setattr("elm_mcp.config.settings.elm_url", BASE)
    yield
    await close_client()


def _paged(router, sizes: list[int], **kwargs) -> tuple[FakePages, respx.Route]:
    """Discovery + a paged query endpoint. Returns (pages fake, j_security_check route)."""
    auth = mock_ccm(router)
    return FakePages(router, paginate(1, sizes), **kwargs), auth


def _numbers(*ranges: range) -> list[str]:
    return [str(n) for r in ranges for n in r]


# ======================================================================
# EARS-1: follow oslc:nextPage until `limit` items or the pages run out
# ======================================================================
@pytest.mark.parametrize("form", ["info", "top", "member"])
async def test_query_follows_next_page_until_the_pages_run_out(w2_router, form):
    """Three pages, limit above the total: every item, in order, no truncation. All nextPage shapes."""
    pages, _ = _paged(w2_router, [3, 3, 3], form=form)
    result = await oslc.query_resources("ccm", PROJECT, None, limit=100)
    assert ids_of(result) == _numbers(range(1, 10))
    assert result.truncated is False
    assert pages.requested_pages() == [1, 2, 3]


async def test_query_stops_in_the_middle_of_a_page_when_the_limit_is_reached(w2_router):
    """EARS-3: never more than `limit` items; the surplus of the page is dropped; later pages are not fetched."""
    pages, _ = _paged(w2_router, [3, 3, 3])
    result = await oslc.query_resources("ccm", PROJECT, None, limit=4)
    assert ids_of(result) == _numbers(range(1, 5))
    assert result.truncated is True
    assert pages.requested_pages() == [1, 2]


async def test_query_with_the_limit_at_a_page_boundary_does_not_fetch_the_next_page(w2_router):
    pages, _ = _paged(w2_router, [3, 3, 3])
    result = await oslc.query_resources("ccm", PROJECT, None, limit=3)
    assert ids_of(result) == _numbers(range(1, 4))
    assert result.truncated is True
    assert pages.requested_pages() == [1]


async def test_query_with_the_limit_equal_to_the_total_is_not_truncated(w2_router):
    _paged(w2_router, [3, 3, 3])
    result = await oslc.query_resources("ccm", PROJECT, None, limit=9)
    assert ids_of(result) == _numbers(range(1, 10))
    assert result.truncated is False


async def test_query_without_next_page_returns_the_single_page_untruncated(w2_router):
    pages, _ = _paged(w2_router, [2])
    result = await oslc.query_resources("ccm", PROJECT, None, limit=100)
    assert ids_of(result) == ["1", "2"]
    assert result.truncated is False
    assert pages.requested_pages() == [1]


# ======================================================================
# EARS-2: default limit 50, truncated flag when a next page exists
# ======================================================================
async def test_query_without_limit_applies_the_default_of_50_and_flags_truncation(w2_router):
    pages, _ = _paged(w2_router, [30, 30, 30])
    result = await oslc.query_resources("ccm", PROJECT, None)
    assert len(result) == DEFAULT_LIMIT
    assert ids_of(result) == _numbers(range(1, DEFAULT_LIMIT + 1))
    assert result.truncated is True
    assert pages.requested_pages() == [1, 2]


async def test_query_without_limit_and_a_small_result_is_not_truncated(w2_router):
    _paged(w2_router, [20, 20])
    result = await oslc.query_resources("ccm", PROJECT, None)
    assert len(result) == 40
    assert result.truncated is False


# ======================================================================
# EARS-3: no item twice
# ======================================================================
async def test_query_does_not_return_an_item_that_repeats_across_pages(w2_router):
    """Item 3 closes page 1 and opens page 2 (the collection shifted between requests)."""
    mock_ccm(w2_router)
    FakePages(w2_router, [[wi(1), wi(2), wi(3)], [wi(3), wi(4), wi(5)]])
    result = await oslc.query_resources("ccm", PROJECT, None, limit=100)
    assert ids_of(result) == _numbers(range(1, 6))


async def test_query_counts_unique_items_against_the_limit(w2_router):
    """limit=5 with a duplicate between pages: five DISTINCT items, so the third page is needed."""
    mock_ccm(w2_router)
    pages = FakePages(w2_router, [[wi(1), wi(2), wi(3)], [wi(3), wi(4)], [wi(5), wi(6)]])
    result = await oslc.query_resources("ccm", PROJECT, None, limit=5)
    assert ids_of(result) == _numbers(range(1, 6))
    assert result.truncated is True
    assert pages.requested_pages() == [1, 2, 3]


# ======================================================================
# EARS-6: absolute cap of 1000 items per call
# ======================================================================
async def test_query_caps_the_result_at_1000_items_whatever_the_limit(w2_router):
    pages, _ = _paged(w2_router, [400, 400, 400, 400])
    result = await oslc.query_resources("ccm", PROJECT, None, limit=5000)
    assert len(result) == ABSOLUTE_CAP
    assert ids_of(result) == _numbers(range(1, ABSOLUTE_CAP + 1))
    assert result.truncated is True
    assert pages.requested_pages() == [1, 2, 3]


async def test_query_with_exactly_1000_items_and_limit_5000_is_not_truncated(w2_router):
    _paged(w2_router, [500, 500])
    result = await oslc.query_resources("ccm", PROJECT, None, limit=5000)
    assert len(result) == ABSOLUTE_CAP
    assert result.truncated is False


# ======================================================================
# EARS-4: expired session on page 2 -> re-authenticate once, repeat only that page
# ======================================================================
async def test_expired_session_on_page_two_reauthenticates_once_and_repeats_only_that_page(w2_router):
    pages, auth = _paged(w2_router, [3, 3, 3], failures={2: [httpx.Response(401)]})
    result = await oslc.query_resources("ccm", PROJECT, None, limit=100)
    assert ids_of(result) == _numbers(range(1, 10)), "no item lost or duplicated by the retry"
    assert pages.requested_pages() == [1, 2, 2, 3], "page 1 must not be fetched again"
    assert auth.call_count == 2, "initial login + exactly one re-authentication"


# ======================================================================
# EARS-5: a page that keeps failing -> ToolError with the gate and the items already collected
# ======================================================================
async def test_session_that_stays_expired_raises_the_session_gate_with_the_collected_count(w2_router):
    """Page 2 answers 401 even after re-authenticating: [sessao], 4 items were already collected."""
    pages, auth = _paged(w2_router, [4, 4], failures={2: [httpx.Response(401)]}, sticky=True)
    with pytest.raises(ToolError, match=r"\[sessao\]") as exc:
        await server.list_workitems(PROJECT, limit=100)
    assert pages.requested_pages() == [1, 2, 2], "the failed page is repeated exactly once"
    assert auth.call_count == 2
    assert _mentions_count(str(exc.value), 4)


async def test_page_that_fails_with_a_server_error_names_the_gate_and_the_collected_count(w2_router):
    _paged(w2_router, [4, 4], failures={2: [httpx.Response(500)]}, sticky=True)
    with pytest.raises(ToolError, match=r"\[http-500\]") as exc:
        await server.list_workitems(PROJECT, limit=100)
    assert _mentions_count(str(exc.value), 4)


async def test_failed_reauthentication_raises_the_credential_gate_with_the_collected_count(w2_router):
    _pages, auth = _paged(w2_router, [4, 4], failures={2: [httpx.Response(401)]}, sticky=True)
    auth.mock(
        side_effect=[
            httpx.Response(200),
            httpx.Response(401, headers={AUTH_FAIL_HEADER: "authentication failed"}),
        ]
    )
    with pytest.raises(ToolError, match=r"\[credencial\]") as exc:
        await server.list_workitems(PROJECT, limit=100)
    assert _mentions_count(str(exc.value), 4)


def _mentions_count(message: str, count: int) -> bool:
    """The message tells how many items had been collected (the digit as a whole word)."""
    return re.search(rf"\b{count}\b", message) is not None


# ======================================================================
# The tool: list_workitems forwards `limit` and reports truncation
# ======================================================================
async def test_list_workitems_limit_returns_items_and_the_truncated_flag(w2_router):
    pages, _ = _paged(w2_router, [3, 3, 3])
    result = await server.list_workitems(PROJECT, limit=4)
    assert result["truncated"] is True
    assert [i["id"] for i in result["items"]] == ["1", "2", "3", "4"]
    assert result["items"][0]["title"] == "Item 1"
    assert pages.requested_pages() == [1, 2]


async def test_list_workitems_without_limit_returns_50_and_flags_truncation(w2_router):
    _paged(w2_router, [30, 30, 30])
    result = await server.list_workitems(PROJECT)
    assert len(result["items"]) == DEFAULT_LIMIT
    assert result["truncated"] is True


async def test_list_workitems_small_result_is_not_truncated(w2_router):
    _paged(w2_router, [3])
    result = await server.list_workitems(PROJECT)
    assert [i["id"] for i in result["items"]] == ["1", "2", "3"]
    assert result["truncated"] is False
