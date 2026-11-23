"""G5 loop 2 (RED), wave W5: download_attachment is not read only, ELM_DOWNLOAD_DIR, serialized re-login,
local write errors as ValueError, get_hours ceiling, and the LOW findings. Neutral fixtures, no network."""

import asyncio
import errno
import os
import stat
from pathlib import Path

import httpx
import pytest
from mcp.server.mcpserver.exceptions import ToolError

from elm_mcp import attachments, oslc, server
from elm_mcp.auth import close_client, get_client

from .w5_fixtures import BASE, SEVEN, Alm, Att, E, entry_uri, hours_ms

PNG = b"\x89PNG\r\n\x1a\n" + bytes(range(256))


@pytest.fixture(autouse=True)
async def _cfg(monkeypatch):
    monkeypatch.setattr("elm_mcp.config.settings.elm_url", BASE)
    for name in ("ELM_MAX_DOWNLOAD_MB", "ELM_DOWNLOAD_DIR", "ELM_DOWNLOAD_TIMEOUT"):
        monkeypatch.delenv(name, raising=False)
    yield
    await close_client()


def _alm(router, *atts: Att, **kwargs) -> Alm:
    alm = Alm(router, **kwargs)
    alm.add_item("100", time_spent=None, attachments=list(atts))
    return alm


def _png(name: str = "diagram.png") -> Att:
    return Att("_ATT001", 1001, name, data=PNG)


# ---------------------------------------------------------------- 1. not read only + ELM_DOWNLOAD_DIR
async def test_download_attachment_is_not_read_only_and_is_destructive():
    tools = {t.name: t for t in await server.mcp.list_tools()}
    ann = tools["download_attachment"].annotations
    assert ann.read_only_hint is False
    assert ann.destructive_hint is True
    assert tools["list_attachments"].annotations.read_only_hint is True


async def test_snapshot_marks_download_attachment_as_a_write_tool():
    import json

    snap = json.loads((Path(__file__).parent / "contract" / "tools.snapshot.json").read_text(encoding="utf-8"))
    by_name = {t["name"]: t for t in snap["tools"]}
    assert by_name["download_attachment"]["read_only"] is False
    assert sum(t["read_only"] for t in snap["tools"]) == 21
    assert sum(not t["read_only"] for t in snap["tools"]) == 14


async def test_download_dir_allows_a_destination_inside_the_root(w2_router, tmp_path, monkeypatch):
    _alm(w2_router, _png())
    root = tmp_path / "root"
    (root / "sub").mkdir(parents=True)
    monkeypatch.setenv("ELM_DOWNLOAD_DIR", str(root))
    result = await server.download_attachment("100", "1001", str(root / "sub"))
    assert Path(result["path"]).read_bytes() == PNG


@pytest.mark.parametrize("where", ["outside", "dotdot"])
async def test_download_dir_refuses_a_destination_outside_the_root(w2_router, tmp_path, monkeypatch, where):
    _alm(w2_router, _png())
    root = tmp_path / "root"
    root.mkdir()
    (tmp_path / "other").mkdir()
    monkeypatch.setenv("ELM_DOWNLOAD_DIR", str(root))
    dest = tmp_path / "other" if where == "outside" else root / ".." / "other"
    with pytest.raises(ToolError, match="ELM_DOWNLOAD_DIR"):
        await server.download_attachment("100", "1001", str(dest))
    assert list((tmp_path / "other").iterdir()) == []


async def test_download_dir_refuses_a_symlink_that_leaves_the_root(w2_router, tmp_path, monkeypatch):
    _alm(w2_router, _png())
    root = tmp_path / "root"
    root.mkdir()
    (tmp_path / "other").mkdir()
    (root / "link").symlink_to(tmp_path / "other", target_is_directory=True)
    monkeypatch.setenv("ELM_DOWNLOAD_DIR", str(root))
    with pytest.raises(ToolError, match="ELM_DOWNLOAD_DIR"):
        await server.download_attachment("100", "1001", str(root / "link" / "x.png"))
    assert list((tmp_path / "other").iterdir()) == []


async def test_download_without_download_dir_keeps_working_anywhere(w2_router, tmp_path):
    _alm(w2_router, _png())
    result = await server.download_attachment("100", "1001", str(tmp_path))
    assert Path(result["path"]).parent == tmp_path


# ---------------------------------------------------------------- 2. serialized re-login
async def _expiring_alm(router, delay: float = 0.02) -> tuple[Alm, dict]:
    """Session expiry as the real server does it: every request is a 401 until ONE login happens."""
    alm = Alm(router, delay=delay)
    state = {"expired": False}
    original = alm._dispatch

    async def dispatch(request):
        if state["expired"] and ".TimeSheetEntry/" in request.url.path:
            await asyncio.sleep(delay)
            return httpx.Response(401, text="expired")
        return await original(request)

    router.routes[1].side_effect = dispatch  # the GET route of the fake

    async def login(request):
        await asyncio.sleep(delay)
        state["expired"] = False
        return httpx.Response(200)

    alm.login.side_effect = login
    return alm, state


async def test_twenty_simultaneous_401s_cause_at_most_one_extra_login(w2_router):
    alm, state = await _expiring_alm(w2_router)
    entries = [E(f"_Q{i:02d}", hours_ms(1)) for i in range(20)]
    alm.add_item("1", time_spent=None, entries=entries)
    client = await get_client()
    state["expired"] = True
    resps = await asyncio.gather(*(oslc.fetch(client, "GET", entry_uri(e.eid), "t") for e in entries))
    assert [r.status_code for r in resps] == [200] * 20
    assert alm.login.call_count <= 2


async def test_get_hours_with_an_expired_session_logs_in_at_most_twice_and_counts_everything(w2_router):
    alm, state = await _expiring_alm(w2_router)
    alm.add_item("1", time_spent=None, entries=[E(f"_Q{i:02d}", hours_ms(1)) for i in range(20)])
    await get_client()
    state["expired"] = True
    result = await server.get_hours(item_ids=["1"])
    assert result["errors"] == []
    assert result["entries_read"] == 20
    assert result["total_hours"] == 20.0
    assert alm.login.call_count <= 2


# ---------------------------------------------------------------- 3. _save: OSError, no hardlink, long names
async def test_save_without_hardlink_support_still_never_overwrites(w2_router, tmp_path, monkeypatch):
    _alm(w2_router, _png())

    def no_link(*args, **kwargs):
        raise OSError(errno.ENOTSUP, "Operation not supported")

    monkeypatch.setattr(os, "link", no_link)
    result = await server.download_attachment("100", "1001", str(tmp_path))
    assert Path(result["path"]).read_bytes() == PNG
    assert {p.name for p in tmp_path.iterdir()} == {"diagram.png"}  # no temporary left
    with pytest.raises(ToolError, match="already exists"):
        await server.download_attachment("100", "1001", str(tmp_path))
    assert (tmp_path / "diagram.png").read_bytes() == PNG


async def test_a_400_character_server_name_is_cut_to_255_bytes_keeping_the_extension(w2_router, tmp_path):
    _alm(w2_router, _png("a" * 400 + ".png"))
    result = await server.download_attachment("100", "1001", str(tmp_path))
    name = Path(result["path"]).name
    assert len(name.encode()) <= 255
    assert name.endswith(".png")
    assert Path(result["path"]).read_bytes() == PNG


async def test_a_long_multibyte_name_is_cut_on_a_character_boundary(w2_router, tmp_path):
    _alm(w2_router, _png("é" * 300 + ".png"))
    result = await server.download_attachment("100", "1001", str(tmp_path))
    name = Path(result["path"]).name
    assert len(name.encode()) <= 255
    assert name.endswith(".png")


async def test_an_oserror_while_writing_becomes_a_value_error_and_leaves_nothing(w2_router, tmp_path):
    _alm(w2_router, _png())
    long_file = tmp_path / ("b" * 300)  # dest as a file path: ENAMETOOLONG from the filesystem
    with pytest.raises(ToolError, match="Could not (use|write)"):
        await server.download_attachment("100", "1001", str(long_file))
    assert list(tmp_path.iterdir()) == []


async def test_an_oserror_on_the_final_move_becomes_a_value_error(w2_router, tmp_path, monkeypatch):
    _alm(w2_router, _png())

    def boom(*args, **kwargs):
        raise OSError(errno.ENOSPC, "No space left on device")

    monkeypatch.setattr(os, "replace", boom)
    with pytest.raises(ToolError, match="Could not write"):
        await server.download_attachment("100", "1001", str(tmp_path), overwrite=True)
    assert list(tmp_path.iterdir()) == []


async def test_a_directory_as_the_target_with_overwrite_is_a_clear_error(w2_router, tmp_path):
    _alm(w2_router, _png())
    (tmp_path / "diagram.png").mkdir()
    with pytest.raises(ToolError):
        await server.download_attachment("100", "1001", str(tmp_path), overwrite=True)


# ---------------------------------------------------------------- 4. get_hours ceiling
async def test_get_hours_refuses_max_entries_above_5000(w2_router):
    alm = Alm(w2_router)
    alm.add_item("1", time_spent=None, entries=SEVEN)
    with pytest.raises(ToolError, match="5000"):
        await server.get_hours(item_ids=["1"], max_entries=5001)
    result = await server.get_hours(item_ids=["1"], max_entries=5000)
    assert result["entries_read"] == 7


def test_get_hours_documents_its_cost():
    doc = (server.get_hours.__doc__ or "").lower()
    assert "2n+1" in doc
    assert "5000" in doc


# ---------------------------------------------------------------- 5. LOW
async def test_partial_is_true_when_an_entry_failed_to_read(w2_router):
    alm = Alm(w2_router)
    alm.add_item("1", time_spent=None, entries=SEVEN)
    alm.entries["_ID003"] = httpx.Response(500, text="boom")
    result = await server.get_hours(item_ids=["1"])
    assert len(result["errors"]) == 1
    assert result["unread_entries"] == 0
    assert result["partial"] is True


async def test_an_entry_listed_by_two_items_is_counted_once(w2_router):
    alm = Alm(w2_router)
    alm.add_item("1", time_spent=None, children=("2",), entries=[E("_S1", hours_ms(2)), E("_S2", hours_ms(3))])
    alm.add_item("2", time_spent=None, entries=[E("_S2", hours_ms(3)), E("_S3", hours_ms(1))])
    result = await server.get_hours(item_ids=["1"], group_by="item")
    assert result["total_hours"] == 6.0
    assert result["entries_read"] == 3
    assert alm.entry_hits.count("_S2") == 1


async def test_download_has_a_global_deadline_and_cleans_the_temporary(w2_router, tmp_path, monkeypatch):
    alm = _alm(w2_router, _png())

    class Stalled(httpx.AsyncByteStream):
        async def __aiter__(self):
            yield b"abc"
            await asyncio.sleep(5)
            yield b"def"

    original = alm._attachment

    def attachment(request, aid):
        resp = original(request, aid)
        if request.headers.get("accept") == "*/*":
            return httpx.Response(200, headers=resp.headers, stream=Stalled())
        return resp

    w2_router.routes[1].side_effect = lambda request: (
        attachment(request, request.url.path.rsplit("/", 1)[1])
        if ".workitem.Attachment/" in request.url.path
        else alm._dispatch(request)
    )
    monkeypatch.setenv("ELM_DOWNLOAD_TIMEOUT", "0.2")
    with pytest.raises(ToolError, match="ELM_DOWNLOAD_TIMEOUT"):
        await server.download_attachment("100", "1001", str(tmp_path))
    assert list(tmp_path.iterdir()) == []


def test_download_timeout_defaults_to_300_and_ignores_garbage(monkeypatch):
    assert attachments.download_timeout() == 300.0
    for bad in ("x", "0", "-1", "nan", "inf"):
        monkeypatch.setenv("ELM_DOWNLOAD_TIMEOUT", bad)
        assert attachments.download_timeout() == 300.0
    monkeypatch.setenv("ELM_DOWNLOAD_TIMEOUT", "12.5")
    assert attachments.download_timeout() == 12.5


async def test_the_final_file_gets_the_mode_of_the_umask_not_0600(w2_router, tmp_path):
    _alm(w2_router, _png())
    old = os.umask(0o022)
    try:
        result = await server.download_attachment("100", "1001", str(tmp_path))
    finally:
        os.umask(old)
    assert stat.S_IMODE(os.stat(result["path"]).st_mode) == 0o644


@pytest.mark.parametrize("raw", ["a\x01b\x1fc.png", "a\x7fb.png", "a\nb\tc.png"])
def test_control_characters_are_removed_from_the_base_name(raw):
    name = attachments.safe_base_name(raw)
    assert name is not None
    assert not any(ord(c) < 32 or ord(c) == 127 for c in name)
    assert name.endswith(".png")


def test_person_is_documented_as_the_user_uri():
    text = (Path(__file__).resolve().parent.parent / "docs" / "TOOLS.md").read_text(encoding="utf-8")
    assert "ELM_DOWNLOAD_DIR" in text
    assert "ELM_DOWNLOAD_TIMEOUT" in text
    assert "`person` is the URI of the user" in text
