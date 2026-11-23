"""G3 (RED) tests for SPEC-ccm-interno, read part (wave W5, release v0.6.0): ``list_attachments`` and
``download_attachment`` (OSLC only, no flag; the internal-API writes are DEFERRED and not tested here).

The ALM is the stateful respx fake in tests/w5_fixtures.py. It reproduces the live G0 findings: the work item
lists ``{rdf:resource, dcterms:title: "<id>: <name>"}``; the attachment URI answers metadata to
``Accept: application/json``, the bytes (+ ``Content-Disposition: attachment; filename*=utf-8''<encoded>``) to
``Accept: */*`` ONLY, and 406 to ``image/png`` / ``application/octet-stream`` / anything else.

Contract pinned here (the developer validates it, see the G3 report):
- ``list_attachments(id)`` -> list of ``{uri, name, identifier, format, creator, created}`` (``size`` when known);
  ``name`` has no ``"<id>: "`` prefix (only that one leading prefix is removed); ``creator`` contains the user name;
- ``download_attachment(id, attachment, dest, overwrite=False)`` -> ``{"path", "size"}``; ``attachment`` is the
  attachment identifier ("1001"), its exact name, or its URI; ``dest`` is an existing directory (the file is
  named after the server's Content-Disposition, base name only) or a file path (written exactly there);
- an existing target is refused unless ``overwrite=True``; ``ELM_MAX_DOWNLOAD_MB`` (default 50) caps the size and
  a refused download leaves nothing on disk; errors carry the gate diagnosis ([formato] for 406).

New symbols are reached by attribute INSIDE each test so each test fails by itself.
"""

import contextlib
from pathlib import Path

import pytest
from mcp.server.mcpserver.exceptions import ToolError

from elm_mcp import server
from elm_mcp.auth import close_client

from .w5_fixtures import BASE, FOREIGN, Alm, Att, att_uri

PNG = b"\x89PNG\r\n\x1a\n" + bytes(range(256)) * 20 + b"\r\n\x00\xff"
SRC = Path(__file__).resolve().parent.parent / "src" / "elm_mcp"


@pytest.fixture(autouse=True)
async def _cfg(monkeypatch):
    monkeypatch.setattr("elm_mcp.config.settings.elm_url", BASE)
    monkeypatch.delenv("ELM_MAX_DOWNLOAD_MB", raising=False)
    yield
    await close_client()


def _alm(router, *atts: Att) -> Alm:
    alm = Alm(router)
    alm.add_item("100", time_spent=None, attachments=list(atts))
    return alm


def _png(**kwargs) -> Att:
    return Att("_ATT001", 1001, "diagram.png", data=PNG, **kwargs)


def _tree(root: Path) -> set[str]:
    return {str(p.relative_to(root)) for p in root.rglob("*")}


# ======================================================================
# list_attachments
# ======================================================================
async def test_list_attachments_returns_the_metadata_of_each_attachment(w2_router):
    alm = _alm(w2_router, _png())
    result = await server.list_attachments("100")
    assert len(result) == 1
    a = result[0]
    assert a["uri"] == att_uri("_ATT001")
    assert a["name"] == "diagram.png"
    assert str(a["identifier"]) == "1001"
    assert a["format"] == "image/png"
    assert "jdoe" in a["creator"]
    assert a["created"] == "2025-07-21T18:52:06.372Z"
    assert alm.only_reads()


async def test_list_attachments_reads_the_metadata_with_accept_json_never_the_content(w2_router):
    alm = _alm(w2_router, _png())
    await server.list_attachments("100")
    assert alm.att_requests
    assert all("json" in accept for _, accept in alm.att_requests)


async def test_list_attachments_name_has_no_identifier_prefix(w2_router):
    _alm(w2_router, Att("_ATT002", 1002, "report.pdf", title="1002: report.pdf", fmt="application/pdf"))
    result = await server.list_attachments("100")
    assert result[0]["name"] == "report.pdf"


async def test_list_attachments_removes_only_the_leading_identifier_prefix(w2_router):
    _alm(w2_router, Att("_ATT004", 1004, "2025: plan.txt", title="1004: 2025: plan.txt", fmt="text/plain"))
    result = await server.list_attachments("100")
    assert result[0]["name"] == "2025: plan.txt"


async def test_list_attachments_lists_every_attachment_in_the_item_order(w2_router):
    _alm(w2_router, _png(), Att("_ATT002", 1002, "b.txt", fmt="text/plain"), Att("_ATT003", 1003, "c.txt"))
    result = await server.list_attachments("100")
    assert [a["name"] for a in result] == ["diagram.png", "b.txt", "c.txt"]


async def test_list_attachments_of_an_item_without_attachments_is_empty(w2_router):
    alm = Alm(w2_router)
    alm.add_item("100", time_spent=None)
    assert await server.list_attachments("100") == []


async def test_list_attachments_accepts_a_single_attachment_given_as_an_object(w2_router):
    alm = _alm(w2_router, _png())
    alm.items["100"]["rtc_cm:com.ibm.team.workitem.linktype.attachment.attachment"] = alm.items["100"][
        "rtc_cm:com.ibm.team.workitem.linktype.attachment.attachment"
    ][0]
    result = await server.list_attachments("100")
    assert [a["name"] for a in result] == ["diagram.png"]


async def test_list_attachments_never_requests_an_attachment_on_another_origin(w2_router):
    foreign = Att("_ATT009", 1009, "x.png", uri=f"{FOREIGN}/ccm/resource/itemOid/x.Attachment/_ATT009")
    alm = _alm(w2_router, foreign)
    with contextlib.suppress(ToolError):  # refusing or skipping are both acceptable; requesting is not
        await server.list_attachments("100")
    assert alm.foreign.call_count == 0
    assert alm.att_requests == []


# ======================================================================
# download_attachment: request, bytes, destination
# ======================================================================
async def test_download_asks_for_the_content_with_accept_any_and_writes_the_exact_bytes(w2_router, tmp_path):
    alm = _alm(w2_router, _png())
    result = await server.download_attachment("100", "1001", str(tmp_path))
    target = tmp_path / "diagram.png"
    assert target.read_bytes() == PNG
    assert Path(result["path"]) == target
    assert result["size"] == len(PNG)
    content_accepts = [accept for _, accept in alm.att_requests if "json" not in accept]
    assert content_accepts == ["*/*"]  # exactly one content request, with Accept */*
    assert alm.only_reads()


async def test_download_never_sends_an_accept_the_server_answers_406_to(w2_router, tmp_path):
    alm = _alm(w2_router, _png())
    await server.download_attachment("100", "1001", str(tmp_path))
    assert all(accept == "*/*" or "json" in accept for _, accept in alm.att_requests)


@pytest.mark.parametrize("name", ["diagram final.png", "relatório 2025.png", "a+b&c.png", "çã.bin"])
async def test_download_decodes_the_percent_encoded_filename_of_the_server(w2_router, tmp_path, name):
    _alm(w2_router, Att("_ATT001", 1001, name, data=PNG))
    result = await server.download_attachment("100", "1001", str(tmp_path))
    assert (tmp_path / name).read_bytes() == PNG
    assert Path(result["path"]).name == name


async def test_download_to_a_file_path_writes_exactly_there(w2_router, tmp_path):
    _alm(w2_router, _png())
    target = tmp_path / "out.bin"
    result = await server.download_attachment("100", "1001", str(target))
    assert target.read_bytes() == PNG
    assert Path(result["path"]) == target
    assert not (tmp_path / "diagram.png").exists()


@pytest.mark.parametrize("selector", ["1001", "diagram.png", att_uri("_ATT001")])
async def test_download_finds_the_attachment_by_identifier_name_or_uri(w2_router, tmp_path, selector):
    _alm(w2_router, _png(), Att("_ATT002", 1002, "other.txt", data=b"other", fmt="text/plain"))
    await server.download_attachment("100", selector, str(tmp_path))
    assert (tmp_path / "diagram.png").read_bytes() == PNG
    assert not (tmp_path / "other.txt").exists()


async def test_download_of_an_unknown_attachment_is_refused_without_requesting_any_content(w2_router, tmp_path):
    alm = _alm(w2_router, _png())
    with pytest.raises(ToolError, match="9999"):
        await server.download_attachment("100", "9999", str(tmp_path))
    assert [a for _, a in alm.att_requests if "json" not in a] == []
    assert _tree(tmp_path) == set()


# ======================================================================
# download_attachment: overwrite
# ======================================================================
async def test_download_does_not_overwrite_an_existing_file_in_the_directory(w2_router, tmp_path):
    _alm(w2_router, _png())
    existing = tmp_path / "diagram.png"
    existing.write_bytes(b"mine")
    with pytest.raises(ToolError, match="overwrite"):
        await server.download_attachment("100", "1001", str(tmp_path))
    assert existing.read_bytes() == b"mine"


async def test_download_does_not_overwrite_an_existing_destination_file(w2_router, tmp_path):
    _alm(w2_router, _png())
    existing = tmp_path / "out.bin"
    existing.write_bytes(b"mine")
    with pytest.raises(ToolError, match="overwrite"):
        await server.download_attachment("100", "1001", str(existing), overwrite=False)
    assert existing.read_bytes() == b"mine"


@pytest.mark.parametrize("as_file", [False, True])
async def test_download_overwrites_only_with_overwrite_true(w2_router, tmp_path, as_file):
    _alm(w2_router, _png())
    target = tmp_path / ("out.bin" if as_file else "diagram.png")
    target.write_bytes(b"mine")
    dest = target if as_file else tmp_path
    await server.download_attachment("100", "1001", str(dest), overwrite=True)
    assert target.read_bytes() == PNG


# ======================================================================
# download_attachment: path traversal (the server controls the filename)
# ======================================================================
@pytest.mark.parametrize(
    ("raw", "base"),
    [
        ("../x", "x"),
        ("/etc/x", "x"),
        ("a/../../x", "x"),
        ("../../etc/passwd", "passwd"),
        ("sub/dir/file.txt", "file.txt"),
    ],
)
async def test_download_keeps_only_the_base_name_of_the_server_filename(w2_router, tmp_path, raw, base):
    dest = tmp_path / "dest"
    dest.mkdir()
    _alm(w2_router, Att("_ATT001", 1001, raw, data=PNG))
    result = await server.download_attachment("100", "1001", str(dest))
    assert Path(result["path"]).parent.resolve() == dest.resolve()
    assert (dest / base).read_bytes() == PNG
    assert _tree(tmp_path) == {"dest", f"dest/{base}"}  # nothing outside dest, no sub directories


@pytest.mark.parametrize(
    "disposition",
    [
        "attachment; filename*=utf-8''",  # empty name
        "attachment; filename*=utf-8''..",
        "attachment; filename*=utf-8''.",
        "attachment; filename*=utf-8''%2F",
        'attachment; filename="../x"',  # plain (non-RFC 5987) form
        'attachment; filename=""',
        "attachment",  # no filename at all
        None,  # no Content-Disposition header
    ],
)
async def test_download_with_a_degenerate_server_filename_never_writes_outside_dest(w2_router, tmp_path, disposition):
    dest = tmp_path / "dest"
    dest.mkdir()
    _alm(w2_router, Att("_ATT001", 1001, "diagram.png", data=PNG, disposition=disposition))
    with contextlib.suppress(ToolError):  # refusing is fine; writing anywhere but a file inside dest is not
        result = await server.download_attachment("100", "1001", str(dest))
        written = Path(result["path"]).resolve()
        assert written.parent == dest.resolve()
        assert written.is_file()
        assert written.read_bytes() == PNG
    assert dest.is_dir()
    assert all(entry == "dest" or entry.startswith("dest/") for entry in _tree(tmp_path))
    assert all("/" not in entry[len("dest/") :] for entry in _tree(tmp_path) if entry.startswith("dest/"))


async def test_download_of_a_nameless_response_falls_back_to_the_attachment_name(w2_router, tmp_path):
    _alm(w2_router, Att("_ATT001", 1001, "diagram.png", data=PNG, disposition=None))
    result = await server.download_attachment("100", "1001", str(tmp_path))
    assert Path(result["path"]) == tmp_path / "diagram.png"
    assert (tmp_path / "diagram.png").read_bytes() == PNG


# ======================================================================
# download_attachment: size limit, origin, gate diagnosis
# ======================================================================
async def test_download_refuses_a_file_above_elm_max_download_mb_and_leaves_nothing_on_disk(
    w2_router, tmp_path, monkeypatch
):
    monkeypatch.setenv("ELM_MAX_DOWNLOAD_MB", "1")
    _alm(w2_router, Att("_ATT001", 1001, "big.bin", data=b"\x00" * (2 * 1024 * 1024)))
    with pytest.raises(ToolError, match="ELM_MAX_DOWNLOAD_MB"):
        await server.download_attachment("100", "1001", str(tmp_path))
    assert _tree(tmp_path) == set()  # no file and no partial/temporary file


async def test_download_accepts_a_file_below_elm_max_download_mb(w2_router, tmp_path, monkeypatch):
    monkeypatch.setenv("ELM_MAX_DOWNLOAD_MB", "1")
    _alm(w2_router, Att("_ATT001", 1001, "ok.bin", data=b"\x01" * (512 * 1024)))
    result = await server.download_attachment("100", "1001", str(tmp_path))
    assert result["size"] == 512 * 1024
    assert (tmp_path / "ok.bin").stat().st_size == 512 * 1024


async def test_download_refuses_an_attachment_listed_on_another_origin(w2_router, tmp_path):
    foreign = Att("_ATT009", 1009, "x.png", uri=f"{FOREIGN}/ccm/resource/itemOid/x.Attachment/_ATT009")
    alm = _alm(w2_router, foreign)
    with pytest.raises(ToolError, match="outside the configured ELM server"):
        await server.download_attachment("100", "1009", str(tmp_path))
    assert alm.foreign.call_count == 0
    assert _tree(tmp_path) == set()


async def test_download_refuses_an_attachment_uri_on_another_origin_given_directly(w2_router, tmp_path):
    alm = _alm(w2_router, _png())
    with pytest.raises(ToolError):
        await server.download_attachment("100", f"{FOREIGN}/ccm/x/Attachment/_A", str(tmp_path))
    assert alm.foreign.call_count == 0
    assert _tree(tmp_path) == set()


async def test_download_maps_a_406_to_the_format_gate_and_writes_nothing(w2_router, tmp_path):
    _alm(w2_router, _png(content_status=406))
    with pytest.raises(ToolError, match=r"\[formato\]"):
        await server.download_attachment("100", "1001", str(tmp_path))
    assert _tree(tmp_path) == set()


async def test_download_maps_a_404_on_the_content_to_the_not_found_gate(w2_router, tmp_path):
    _alm(w2_router, _png(content_status=404))
    with pytest.raises(ToolError, match=r"\[recurso-inexistente-ou-invisivel\]"):
        await server.download_attachment("100", "1001", str(tmp_path))
    assert _tree(tmp_path) == set()


# ======================================================================
# Contract: registered, read only (local file, never the ALM), no flag, no internal API
# ======================================================================
async def test_the_w5_attachment_tools_are_registered_with_the_agreed_parameters():
    tools = {t.name: t for t in await server.mcp.list_tools()}
    listing, download = tools["list_attachments"], tools["download_attachment"]
    assert listing.annotations.read_only_hint is True
    assert download.annotations.read_only_hint is False  # writes a LOCAL file (G5 loop 2)
    assert list(listing.input_schema["properties"]) == ["id"]
    props = download.input_schema["properties"]
    assert list(props) == ["id", "attachment", "dest", "overwrite"]
    assert props["overwrite"]["default"] is False
    assert {"id", "attachment"} <= set(download.input_schema["required"])


def test_download_attachment_documents_that_it_writes_a_local_file():
    doc = (server.download_attachment.__doc__ or "").lower()
    assert "local" in doc
    assert "overwrite" in doc


def test_sweep_no_internal_api_flag_nor_deferred_write_tools_in_the_source():
    forbidden = ["ELM_ENABLE_INTERNAL_API", "IAttachmentRestService", "workItem2", "updateLinks"]
    for path in SRC.rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        for needle in forbidden:
            assert needle not in text, f"{needle} found in {path.name}"
    for name in ("log_time", "remove_time", "add_attachment"):
        assert not hasattr(server, name), f"deferred write tool {name} must not exist in v0.6.0"


async def test_the_attachment_and_hours_tools_need_no_flag_to_be_registered(monkeypatch):
    monkeypatch.delenv("ELM_ENABLE_INTERNAL_API", raising=False)
    names = {t.name for t in await server.mcp.list_tools()}
    assert {"list_time", "get_hours", "list_attachments", "download_attachment"} <= names
    assert not {"log_time", "remove_time", "add_attachment"} & names
