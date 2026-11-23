"""Work item attachments, read only (SPEC-ccm-interno, W5): list the metadata and download the content.

Both go through OSLC. The attachment URI answers metadata to ``Accept: application/json`` and the bytes to
``Accept: */*`` only (any other Accept is a 406). The download writes a LOCAL file, never the ALM. The file
name comes from the server, so only its base name is used and the write never leaves ``dest``.
"""

import contextlib
import os
import re
import tempfile
from pathlib import Path
from urllib.parse import unquote

import httpx

from . import oslc

ATTACHMENT_LINK = "rtc_cm:com.ibm.team.workitem.linktype.attachment.attachment"
DEFAULT_MAX_DOWNLOAD_MB = 50.0
_CHUNK = 64 * 1024
_FILENAME_STAR = re.compile(r"filename\*\s*=\s*([^';]*)'[^';]*'([^;]*)", re.IGNORECASE)
_FILENAME_PLAIN = re.compile(r'filename\s*=\s*(?:"([^"]*)"|([^;\s]*))', re.IGNORECASE)


async def _metadata(client: httpx.AsyncClient, ref) -> dict:
    uri = oslc._resource_uri(ref)
    if not uri:
        raise ValueError("an attachment link has no URI")
    data = (await oslc.fetch(client, "GET", uri, "list_attachments", headers=oslc._JSON_HEADERS)).json()
    if not isinstance(data, dict):
        raise ValueError(f"the attachment '{uri}' did not answer metadata")  # noqa: TRY004 - ValueError becomes a ToolError
    identifier = data.get("dcterms:identifier")
    listed = ref.get("dcterms:title") if isinstance(ref, dict) else None
    title = data.get("dcterms:title") or listed or ""
    prefix = f"{identifier}: "
    name = title[len(prefix) :] if identifier is not None and title.startswith(prefix) else title
    entry = {
        "uri": uri,
        "name": name,
        "identifier": identifier,
        "format": data.get("dcterms:format"),
        "creator": oslc._resource_uri(data.get("dcterms:creator")),
        "created": data.get("dcterms:created"),
    }
    if data.get("dcterms:extent") is not None:
        entry["size"] = data["dcterms:extent"]
    return entry


async def list_attachments(id: str) -> list[dict]:
    """Metadata of each attachment of the work item, in the item's order."""
    client = await oslc.get_client()
    item, _ = await oslc._read_item(client, oslc.workitem_uri(id), "list_attachments")
    return [await _metadata(client, ref) for ref in oslc._as_list(item.get(ATTACHMENT_LINK))]


def max_download_bytes() -> int:
    """Download ceiling from ELM_MAX_DOWNLOAD_MB (default 50); an invalid or non-positive value means the default."""
    try:
        mb = float(os.environ.get("ELM_MAX_DOWNLOAD_MB", ""))
    except ValueError:
        mb = DEFAULT_MAX_DOWNLOAD_MB
    if not mb > 0 or mb == float("inf"):
        mb = DEFAULT_MAX_DOWNLOAD_MB
    return int(mb * 1024 * 1024)


def safe_base_name(raw: str | None) -> str | None:
    """Last path component of a server-supplied file name; None when nothing safe is left."""
    if not raw or "\x00" in raw:
        return None
    name = raw.replace("\\", "/").rsplit("/", 1)[-1].strip()
    return None if name in ("", ".", "..") else name


def filename_from_disposition(header: str | None) -> str | None:
    """Base name from a Content-Disposition (RFC 5987 ``filename*`` first, then plain ``filename``)."""
    if not header:
        return None
    m = _FILENAME_STAR.search(header)
    if m:
        charset = m.group(1).strip() or "utf-8"
        try:
            decoded = unquote(m.group(2).strip(), encoding=charset, errors="strict")
        except (LookupError, UnicodeDecodeError):
            decoded = unquote(m.group(2).strip(), encoding="utf-8", errors="replace")
        name = safe_base_name(decoded)
        if name:
            return name
    m = _FILENAME_PLAIN.search(header)
    return safe_base_name(m.group(1) if m and m.group(1) is not None else (m.group(2) if m else None))


def _select(attachments: list[dict], selector: str) -> dict:
    wanted = str(selector).strip()
    for a in attachments:
        if wanted in (str(a["identifier"]), a["name"], a["uri"]):
            return a
    known = ", ".join(f"{a['identifier']} ({a['name']})" for a in attachments) or "none"
    raise ValueError(f"Attachment '{selector}' not found on this work item. Available: {known}.")


def _target(dest: Path, header_name: str | None, fallback_name: str, overwrite: bool) -> Path:
    """Where the bytes go; refuses an existing file without overwrite and any path outside ``dest``."""
    if dest.is_dir():
        name = header_name or safe_base_name(fallback_name)
        if not name:
            raise ValueError("The server gave no usable file name for this attachment; pass a file path as dest.")
        target = dest / name
        if target.parent.resolve() != dest.resolve():
            raise ValueError(f"Refusing to write '{name}' outside the destination directory.")
    else:
        if not dest.parent.is_dir():
            raise ValueError(f"Destination directory '{dest.parent}' does not exist.")
        target = dest
    if os.path.lexists(target) and not overwrite:
        raise ValueError(f"'{target}' already exists. Pass overwrite=True to replace it.")
    return target


async def _save(resp: httpx.Response, uri: str, dest: Path, fallback_name: str, overwrite: bool) -> dict:
    if not resp.is_success:
        await resp.aread()
    oslc.check_response(resp, "download_attachment", uri)
    limit = max_download_bytes()
    too_big = (
        f"The attachment is larger than ELM_MAX_DOWNLOAD_MB ({limit // (1024 * 1024)} MB for the current setting). "
        "Raise ELM_MAX_DOWNLOAD_MB to download it."
    )
    declared = resp.headers.get("content-length", "")
    if declared.isdigit() and int(declared) > limit:
        raise ValueError(too_big)
    target = _target(dest, filename_from_disposition(resp.headers.get("content-disposition")), fallback_name, overwrite)
    fd, tmp_name = tempfile.mkstemp(dir=target.parent, prefix=".elm-download-")
    tmp = Path(tmp_name)
    try:
        size = 0
        with os.fdopen(fd, "wb") as out:
            async for chunk in resp.aiter_bytes(_CHUNK):
                size += len(chunk)
                if size > limit:
                    raise ValueError(too_big)
                out.write(chunk)
        if overwrite:
            os.replace(tmp, target)
        else:
            try:
                os.link(tmp, target)  # fails when the file appeared meanwhile: never clobber
            except FileExistsError:
                raise ValueError(f"'{target}' already exists. Pass overwrite=True to replace it.") from None
            tmp.unlink()
    finally:
        with contextlib.suppress(FileNotFoundError):
            tmp.unlink()
    return {"path": str(target), "size": size}


async def download_attachment(id: str, attachment: str, dest: str, overwrite: bool = False) -> dict:
    """Save one attachment of the work item to a LOCAL path: {"path", "size"}."""
    client = await oslc.get_client()
    item, _ = await oslc._read_item(client, oslc.workitem_uri(id), "download_attachment")
    refs = oslc._as_list(item.get(ATTACHMENT_LINK))
    wanted = str(attachment).strip()
    # An attachment is found by identifier, name or URI; the metadata of each one is read (JSON) to match.
    chosen = _select([await _metadata(client, ref) for ref in refs], wanted)
    uri = oslc.assert_same_origin(chosen["uri"])
    target = Path(dest).expanduser().absolute()
    headers = {"Accept": "*/*"}  # any other Accept is a 406 on this resource
    for attempt in (1, 2):
        async with client.stream("GET", uri, headers=headers) as resp:
            if attempt == 1 and oslc._needs_reauth(resp):
                await resp.aread()
            else:
                return await _save(resp, uri, target, chosen["name"], overwrite)
        await oslc._authenticate(client)
    raise RuntimeError("unreachable")  # pragma: no cover
