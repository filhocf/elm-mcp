"""ETM (QM) writing: create, update, delete and link test resources (SPEC-qm-write, release v0.6.0).

Scope by evidence: TestCase, TestPlan, TestSuite and TestScript are created by TITLE (and description) only.
TestExecutionRecord, TestResult, native script steps and every other type are DEFERRED and refused.

The ETM only speaks RDF/XML. A resource read back for a PUT is NOT rebuilt from a parsed tree: the bytes the
server sent are edited in place (title/description text, one link element added or removed), so namespaces,
prefixes, ``rdf:parseType="Literal"``, XHTML inside ``rqm_qm:content``, comments, CDATA and attributes the
code does not touch come back byte for byte. The stdlib expat parser (``xml.parsers.expat``) gives the byte
offsets and validates the document; a DTD or an entity declaration is refused.

Nothing here resends a POST after an ambiguous failure (see ``oslc.create_idempotent``) and a 412 is never
retried with a fresh ETag.
"""

import logging
import re
import unicodedata
from urllib.parse import urlparse
from xml.parsers import expat
from xml.sax.saxutils import escape, unescape

import httpx

from . import oslc, qm
from .auth import get_client

logger = logging.getLogger(__name__)

SUPPORTED_TYPES = ("TestCase", "TestPlan", "TestSuite", "TestScript")
LINK_TYPES = ("validatesRequirement", "testsChangeRequest")

_RDF = oslc.NS["rdf"]
_DCTERMS = oslc.NS["dcterms"]
_QM = oslc.NS["oslc_qm"]
_RQM = qm._RQM
_RDF_RDF = f"{{{_RDF}}}RDF"
_RDF_DESCRIPTION = f"{{{_RDF}}}Description"
_RDF_ABOUT = f"{{{_RDF}}}about"
_RDF_RESOURCE = f"{{{_RDF}}}resource"
_RDF_TYPE = f"{{{_RDF}}}type"
# Attributes that make a property something other than plain text (a literal XML value, a link, a blank node).
_NOT_PLAIN_TEXT = (f"{{{_RDF}}}parseType", _RDF_RESOURCE, f"{{{_RDF}}}nodeID")
_PREFERRED_PREFIX = {_QM: ("oslc_qm",), _DCTERMS: ("dcterms",), _RDF: ("rdf",)}

_QM_RESOURCE_PATH = re.compile(r"/qm/oslc_qm/contexts/[^/]+/resources/[^/]+/[^/?#]+/?$")
_WRITE_HEADERS = {"Content-Type": "application/rdf+xml", "Accept": "application/rdf+xml", "OSLC-Core-Version": "2.0"}
_ENCODING_RE = re.compile(rb"""^\s*<\?xml[^>]*encoding\s*=\s*["']\s*(?:utf-16|utf-32|ucs-[24])""", re.IGNORECASE)
_BOMS = (b"\xff\xfe", b"\xfe\xff", b"\x00\x00\xfe\xff")


# ---------------------------------------------------------------------------------------------
# Validation and sanitation (before any request)
# ---------------------------------------------------------------------------------------------
def _supported_type(type_arg: str) -> str:
    wanted = (type_arg or "").strip().lower()
    for name in SUPPORTED_TYPES:
        if name.lower() == wanted:
            return name
    raise ValueError(
        f"Type '{type_arg}' is not supported by create_qm_resource. Supported types: {', '.join(SUPPORTED_TYPES)}. "
        "Every other type (TestExecutionRecord, TestResult, native script steps...) is deferred (adiado): "
        "its write has not been verified yet."
    )


def _clean_text(value: str, *, multiline: bool) -> str:
    """Text without control characters (and without characters XML 1.0 cannot hold)."""
    value = value.replace("\r\n", "\n").replace("\r", "\n")
    out = []
    for ch in value:
        if ch in "\n\t":
            out.append(ch if multiline else " ")
        elif unicodedata.category(ch) in ("Cc", "Cs") or ch in "\ufffe\uffff":
            continue
        else:
            out.append(ch)
    return "".join(out)


def clean_title(title: str | None) -> str:
    text = _clean_text(title or "", multiline=False).strip()
    if not text:
        raise ValueError("The title must not be empty (after removing control characters).")
    return text


def clean_description(description: str) -> str:
    return _clean_text(description, multiline=True).strip()


def _check_qm_uri(uri: str) -> str:
    """The URI of a QM resource on the configured server, or ValueError (before any request)."""
    uri = (uri or "").strip()
    oslc.assert_same_origin(uri)
    if not _QM_RESOURCE_PATH.search(urlparse(uri).path):
        raise ValueError(
            f"{uri} is not an ETM resource (expected .../qm/oslc_qm/contexts/<project>/resources/<type>/<id>). "
            "These tools only touch QM resources."
        )
    return uri


def _check_target(target: str, link_type: str) -> None:
    path = urlparse(target).path
    if link_type == "validatesRequirement":
        if not re.search(r"/rm/", path) or "/qm/" in path or "/ccm/" in path:
            raise ValueError(f"{target} is not an RM requirement: validatesRequirement links a test case to one.")
    elif not oslc._is_workitem_uri(target):
        raise ValueError(
            f"{target} is not a CCM work item (expected .../ccm/.../com.ibm.team.workitem.WorkItem/<id>): "
            "testsChangeRequest links a test case to one."
        )


# ---------------------------------------------------------------------------------------------
# RDF/XML scanning: byte spans of the elements that matter (document -> resource -> property)
# ---------------------------------------------------------------------------------------------
class _Element:
    def __init__(self, name: str, attrs: dict[str, str], start: int, scope: dict[str, str], depth: int) -> None:
        self.name, self.attrs, self.start, self.scope, self.depth = name, attrs, start, scope, depth
        self.children: list[_Element] = []
        self.open_end = start  # index of the '>' that closes the start tag
        self.empty = False
        self.content_end = start  # index of the '<' of the end tag (non-empty elements)
        self.end = start  # index just after the element


class _Doc:
    def __init__(self, data: bytes, root: _Element) -> None:
        self.data, self.root = data, root

    def text(self, el: _Element) -> str:
        if el.empty:
            return ""
        return unescape(self.data[el.open_end + 1 : el.content_end].decode("utf-8", "replace")).strip()


def _clark(name: str) -> str:
    return "{" + name.replace(" ", "}", 1) if " " in name else name


def _start_tag_end(data: bytes, start: int) -> int:
    """Index of the '>' closing the start tag at ``start`` (quotes in attribute values may hold '>')."""
    quote = 0
    for i in range(start + 1, len(data)):
        ch = data[i]
        if quote:
            if ch == quote:
                quote = 0
        elif ch in (0x22, 0x27):
            quote = ch
        elif ch == 0x3E:
            return i
    raise ValueError("Malformed RDF/XML: unterminated start tag.")


def _scan(data: bytes) -> _Doc:
    """Parse ``data`` with expat and keep the byte spans of the elements down to the property level."""
    if data.startswith(_BOMS) or _ENCODING_RE.match(data):
        raise ValueError("The ETM answered in a UTF-16/UTF-32 encoding, which this tool does not edit safely.")
    parser = expat.ParserCreate(namespace_separator=" ")
    stack: list[_Element] = []
    pending: list[tuple[str, str]] = []
    roots: list[_Element] = []

    def refuse(*_args) -> None:
        raise ValueError("The ETM answer declares a DTD or entities, which is refused (RDF/XML has none).")

    def on_ns(prefix: str | None, uri: str | None) -> None:
        pending.append((prefix or "", uri or ""))

    def on_start(name: str, attrs: dict[str, str]) -> None:
        scope = dict(stack[-1].scope) if stack else {}
        scope.update(pending)
        pending.clear()
        el = _Element(
            _clark(name),
            {_clark(k): v for k, v in attrs.items()},
            parser.CurrentByteIndex,
            scope,
            len(stack),
        )
        el.open_end = _start_tag_end(data, el.start)
        el.empty = data[el.open_end - 1 : el.open_end] == b"/"
        if el.empty:
            el.end = el.open_end + 1
        if stack:
            if el.depth <= 2:
                stack[-1].children.append(el)
        else:
            roots.append(el)
        stack.append(el)

    def on_end(_name: str) -> None:
        el = stack.pop()
        if not el.empty:
            el.content_end = parser.CurrentByteIndex
            el.end = data.index(b">", el.content_end) + 1

    parser.StartDoctypeDeclHandler = refuse
    parser.EntityDeclHandler = refuse
    parser.StartNamespaceDeclHandler = on_ns
    parser.StartElementHandler = on_start
    parser.EndElementHandler = on_end
    try:
        parser.Parse(data, True)
    except expat.ExpatError as exc:
        raise ValueError(f"The ETM answer is not valid RDF/XML ({exc}).") from exc
    return _Doc(data, roots[0])


def _subject(doc: _Doc, uri: str) -> _Element:
    nodes = doc.root.children if doc.root.name == _RDF_RDF else [doc.root]
    found = [n for n in nodes if n.attrs.get(_RDF_ABOUT) == uri]
    if len(found) != 1:
        raise ValueError(f"The ETM answer does not describe {uri} exactly once (found {len(found)} nodes about it).")
    return found[0]


def _qm_type(node: _Element) -> str | None:
    """Local name of the QM domain type of a resource node (typed node or ``rdf:type``), None when unknown."""
    names = [] if node.name == _RDF_DESCRIPTION else [node.name]
    names += [c.attrs.get(_RDF_RESOURCE, "") for c in node.children if c.name == _RDF_TYPE]
    for name in names:
        for ns in qm._QM_TYPE_NAMESPACES:
            for form in (name, name.replace("{", "").replace("}", "")):
                if form.startswith(ns) and not form.endswith("Query") and form[len(ns) :]:
                    return form[len(ns) :]
    return None


def _link_targets(node: _Element, link_type: str) -> list[str]:
    return [
        c.attrs[_RDF_RESOURCE] for c in node.children if c.name == f"{{{_QM}}}{link_type}" and _RDF_RESOURCE in c.attrs
    ]


# ---------------------------------------------------------------------------------------------
# Byte edits
# ---------------------------------------------------------------------------------------------
def _text_bytes(text: str) -> bytes:
    return escape(text).encode("ascii", "xmlcharrefreplace")


def _attr_bytes(value: str) -> bytes:
    return escape(value, {'"': "&quot;"}).encode("ascii", "xmlcharrefreplace")


def _prefix_for(scope: dict[str, str], ns: str) -> str | None:
    candidates = sorted(p for p, u in scope.items() if u == ns and p)
    for preferred in _PREFERRED_PREFIX.get(ns, ()):
        if preferred in candidates:
            return preferred
    return candidates[0] if candidates else None


def _new_element(
    parent: _Element, ns: str, local: str, *, text: str | None = None, resource: str | None = None
) -> bytes:
    """A new child element of ``parent``, with the prefixes already in scope (or a local declaration)."""
    taken = set(parent.scope)
    declarations = b""

    def prefix(namespace: str) -> str:
        nonlocal declarations
        found = _prefix_for(parent.scope, namespace)
        if found:
            return found
        fresh = next(f"q{i}" for i in range(1, 100) if f"q{i}" not in taken)
        taken.add(fresh)
        declarations += f' xmlns:{fresh}="{namespace}"'.encode("ascii")
        return fresh

    name = f"{prefix(ns)}:{local}".encode("ascii")
    if resource is not None:
        rdf = prefix(_RDF)
        return b"<" + name + declarations + f" {rdf}:resource=".encode("ascii") + b'"' + _attr_bytes(resource) + b'"/>'
    return b"<" + name + declarations + b">" + _text_bytes(text or "") + b"</" + name + b">"


def _insert_child(doc: _Doc, parent: _Element, element: bytes) -> tuple[int, int, bytes]:
    """Edit that appends ``element`` after the last property of ``parent``, keeping the file's indentation."""
    if parent.empty or not parent.children:
        raise ValueError("The resource has no properties to extend: refusing to write a guessed layout.")
    last = parent.children[-1]
    line_start = doc.data.rfind(b"\n", 0, last.start) + 1
    indent = doc.data[line_start : last.start]
    if indent.strip():
        indent = b" "
    return (last.end, last.end, b"\n" + indent + element)


def _set_text_edit(doc: _Doc, node: _Element, ns: str, local: str, text: str) -> tuple[int, int, bytes]:
    found = [c for c in node.children if c.name == f"{{{ns}}}{local}"]
    if len(found) > 1:
        raise ValueError(f"The resource has {len(found)} '{local}' properties; refusing to guess which one to change.")
    if not found:
        return _insert_child(doc, node, _new_element(node, ns, local, text=text))
    el = found[0]
    if any(a in el.attrs for a in _NOT_PLAIN_TEXT):
        raise ValueError(f"The '{local}' property is not plain text (parseType/link); refusing to overwrite it.")
    new_text = _text_bytes(text)
    if not el.empty:
        return (el.open_end + 1, el.content_end, new_text)
    start_tag = doc.data[el.start : el.open_end + 1]
    qname = re.match(rb"<([^\s/>]+)", start_tag)
    if qname is None:
        raise ValueError("Malformed RDF/XML: cannot read the property name.")
    return (el.start, el.end, start_tag[:-2].rstrip() + b">" + new_text + b"</" + qname.group(1) + b">")


def _remove_edit(doc: _Doc, el: _Element) -> tuple[int, int, bytes]:
    """Edit that deletes ``el`` and, when it sits alone on its line, the line break and indentation before it."""
    start = el.start
    while start > 0 and doc.data[start - 1] in b" \t":
        start -= 1
    if start > 0 and doc.data[start - 1] == 0x0A:
        start -= 1
        if start > 0 and doc.data[start - 1] == 0x0D:
            start -= 1
    else:
        start = el.start
    return (start, el.end, b"")


def _apply(data: bytes, edits: list[tuple[int, int, bytes]]) -> bytes:
    for start, end, replacement in sorted(edits, reverse=True):
        data = data[:start] + replacement + data[end:]
    return data


# ---------------------------------------------------------------------------------------------
# HTTP helpers
# ---------------------------------------------------------------------------------------------
async def _read(client: httpx.AsyncClient, uri: str, operation: str) -> tuple[bytes, str]:
    resp = await oslc.fetch(client, "GET", uri, operation, headers=qm._HEADERS)
    etag = resp.headers.get("ETag", "")
    if not etag:
        raise ValueError(f"Server did not return an ETag for resource '{uri}'. Cannot safely write.")
    return resp.content, etag


async def _put(client: httpx.AsyncClient, uri: str, body: bytes, etag: str, operation: str) -> None:
    resp = await oslc._request(client, "PUT", uri, content=body, headers={**_WRITE_HEADERS, "If-Match": etag})
    if resp.status_code not in (200, 204):
        if resp.is_success:
            raise RuntimeError(f"Expected 200/204, got {resp.status_code}")
        oslc._raise_write_error(resp, operation, uri)


# ---------------------------------------------------------------------------------------------
# create_qm_resource
# ---------------------------------------------------------------------------------------------
def _creation_body(title: str, description: str) -> bytes:
    """RDF/XML with ONE resource whose only properties are ``dcterms:title`` (+ ``dcterms:description``)."""
    parts = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        f'<rdf:RDF xmlns:rdf="{_RDF}" xmlns:dcterms="{_DCTERMS}">',
        " <rdf:Description>",
        f"  <dcterms:title>{escape(title)}</dcterms:title>",
    ]
    if description:
        parts.append(f"  <dcterms:description>{escape(description)}</dcterms:description>")
    parts += [" </rdf:Description>", "</rdf:RDF>", ""]
    return "\n".join(parts).encode("utf-8")


def _short_id(doc: _Doc, uri: str) -> str | None:
    try:
        node = _subject(doc, uri)
    except ValueError:
        nodes = doc.root.children if doc.root.name == _RDF_RDF else [doc.root]
        if len(nodes) != 1:
            return None
        node = nodes[0]
    for ns, local in ((_RQM, "shortIdentifier"), (oslc.NS["oslc"], "shortId")):
        for child in node.children:
            if child.name == f"{{{ns}}}{local}" and doc.text(child):
                return doc.text(child)
    return None


async def _post(factory_url: str, body: bytes) -> dict:
    client = await get_client()
    resp = await oslc._request(client, "POST", factory_url, content=body, headers=_WRITE_HEADERS)
    if resp.status_code not in (200, 201):
        if resp.is_success:
            raise RuntimeError(f"Expected 200/201 Created, got {resp.status_code}")
        oslc._raise_write_error(resp, "create_qm_resource", factory_url)
    location = resp.headers.get("Location", "").strip()
    if not location:
        raise ValueError(
            f"The ETM accepted the creation ({resp.status_code}) but sent no Location, so the new resource is "
            "unknown. It may exist: check before creating it again. The request was NOT resent."
        )
    oslc.assert_same_origin(location)
    result: dict = {"uri": location, "short_id": None}
    # The resource ALREADY exists here: a failed re-read must never propagate (a retry would duplicate it).
    try:
        reread = await oslc.fetch(client, "GET", location, "create_qm_resource", headers=qm._HEADERS)
        result["short_id"] = _short_id(_scan(reread.content), location)
    except (ValueError, RuntimeError, httpx.HTTPError) as exc:
        logger.warning("create_qm_resource: created %s but the re-read failed (%s)", location, exc)
        result["warning"] = (
            "Resource created, but re-reading it failed; short_id may be missing. Do NOT create it again."
        )
    return result


async def create_resource(
    project: str, type_name: str, title: str, description: str | None = None, if_exists: str = "error"
) -> dict:
    """Create a QM resource of a supported type by title (+ description). Returns {uri, short_id, created}."""
    canonical = _supported_type(type_name)
    oslc.check_if_exists(if_exists)
    clean = clean_title(title)
    clean_desc = clean_description(description) if description is not None else ""
    project_url = await oslc._resolve_project_url("qm", project)
    factory_url = await qm.creation_factory(project_url, canonical)
    body = _creation_body(clean, clean_desc)
    wanted = oslc.normalize_title(clean)

    async def find() -> list[dict]:
        members, _total, truncated = await qm.query_members(project, canonical, None, oslc.MAX_QUERY_LIMIT)
        if truncated:
            raise ValueError(
                f"the {canonical} query was truncated (more than {oslc.MAX_QUERY_LIMIT} items), so an existing one "
                "cannot be ruled out"
            )
        return [
            {"uri": m["uri"], "identifier": m["short_id"], "short_id": m["short_id"]}
            for m in members
            if oslc.normalize_title(m["title"]) == wanted
        ]

    async def create() -> dict:
        return await _post(factory_url, body)

    result = await oslc.create_idempotent(if_exists, find, create, canonical)
    out = {"uri": result["uri"], "short_id": result.get("short_id"), "created": result["created"]}
    if result.get("warning"):
        out["warning"] = result["warning"]
    return out


# ---------------------------------------------------------------------------------------------
# update_qm_resource
# ---------------------------------------------------------------------------------------------
async def update_resource(uri: str, title: str | None = None, description: str | None = None) -> dict:
    """Change only the title and/or description: re-read, edit those properties, PUT everything with If-Match."""
    if title is None and description is None:
        raise ValueError("Nothing to update: give a title and/or a description.")
    new_title = clean_title(title) if title is not None else None
    new_desc = clean_description(description) if description is not None else None
    uri = _check_qm_uri(uri)
    client = await get_client()
    data, etag = await _read(client, uri, "update_qm_resource")
    doc = _scan(data)
    node = _subject(doc, uri)
    found = _qm_type(node)
    if found not in SUPPORTED_TYPES:
        raise ValueError(
            f"{uri} is a {found or 'resource of unknown type'}; update_qm_resource only changes "
            f"{', '.join(SUPPORTED_TYPES)} (the other types are deferred: adiados)."
        )
    edits, changed = [], []
    if new_title is not None:
        edits.append(_set_text_edit(doc, node, _DCTERMS, "title", new_title))
        changed.append("title")
    if new_desc is not None:
        edits.append(_set_text_edit(doc, node, _DCTERMS, "description", new_desc))
        changed.append("description")
    await _put(client, uri, _apply(data, edits), etag, "update_qm_resource")
    return {"uri": uri, "updated": changed}


# ---------------------------------------------------------------------------------------------
# delete_qm_resource
# ---------------------------------------------------------------------------------------------
async def delete_resource(uri: str) -> dict:
    """DELETE a QM resource and confirm by re-reading it (404/410); a denied confirmation read is NOT a confirmation."""
    uri = _check_qm_uri(uri)
    client = await get_client()
    resp = await oslc._request(client, "DELETE", uri, headers={"OSLC-Core-Version": "2.0"})
    if not resp.is_success:
        oslc._raise_write_error(resp, "delete_qm_resource", uri)
    check = await oslc._request(client, "GET", uri, headers=qm._HEADERS)
    if check.status_code in (404, 410):
        return {"uri": uri, "deleted": True}
    if check.is_success:
        raise ValueError(f"DELETE was accepted but the resource is still readable: {uri}")
    if check.status_code in (401, 403):
        raise ValueError(
            f"Deletion not confirmed (exclusão não confirmada): the DELETE was sent but the confirmation read of "
            f"{uri} was denied (HTTP {check.status_code}). Check whether the resource still exists."
        )
    oslc.check_response(check, "delete_qm_resource", uri)
    raise ValueError(f"Could not confirm the deletion of {uri}")


# ---------------------------------------------------------------------------------------------
# link_test_case / unlink_test_case
# ---------------------------------------------------------------------------------------------
async def change_link(test_case: str, target: str, link_type: str, *, add: bool) -> dict:
    operation = "link_test_case" if add else "unlink_test_case"
    if link_type not in LINK_TYPES:
        raise ValueError(f"Unknown link type '{link_type}'. Valid link types: {', '.join(LINK_TYPES)}")
    target = (target or "").strip()
    if not target:
        raise ValueError("Target URI must not be empty.")
    source = _check_qm_uri(test_case)
    oslc.assert_same_origin(target)
    if add:
        _check_target(target, link_type)
    client = await get_client()
    data, etag = await _read(client, source, operation)
    doc = _scan(data)
    node = _subject(doc, source)
    found = _qm_type(node)
    if found != "TestCase":
        raise ValueError(
            f"{source} is a {found or 'resource of unknown type'}, not a TestCase: only test cases carry these links."
        )
    current = _link_targets(node, link_type)
    result = {"test_case": source, "target": target, "link_type": link_type}
    if add:
        if target in current:
            return {**result, "changed": False}
        edits = [_insert_child(doc, node, _new_element(node, _QM, link_type, resource=target))]
        expected = {*current, target}
    else:
        if target not in current:
            raise ValueError(f"{source} has no '{link_type}' link to {target}; nothing to remove.")
        edits = [
            _remove_edit(doc, c)
            for c in node.children
            if c.name == f"{{{_QM}}}{link_type}" and c.attrs.get(_RDF_RESOURCE) == target
        ]
        expected = set(current) - {target}
    await _put(client, source, _apply(data, edits), etag, operation)

    reread = await oslc.fetch(client, "GET", source, operation, headers=qm._HEADERS)
    final = set(_link_targets(_subject(_scan(reread.content), source), link_type))
    if final != expected:
        lost, extra = sorted(expected - final), sorted(final - expected)
        detail = (f" Missing targets: {', '.join(lost)}." if lost else "") + (
            f" Unexpected targets: {', '.join(extra)}." if extra else ""
        )
        raise ValueError(
            f"The server accepted the write but the re-read of {source} does not match the expected "
            f"'{link_type}' targets.{detail} Not retried: check the test case."
        )
    return {**result, "changed": True}
