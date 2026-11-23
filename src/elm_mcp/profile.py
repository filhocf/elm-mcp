"""Local profile (SPEC-perfil-template, ADR-005): values the API does not give, kept OUTSIDE the package.

Plain JSON under ``$ELM_MCP_PROFILE_DIR`` (default ``~/.elm-mcp/profiles/``)::

    templates/<name>.json
    project-areas/<project area id>.json      # may name a template: {"template": "<name>"}

Recognized keys (unknown keys, such as the ``_review`` block of a draft, are ignored)::

    "defaults":       {"<type id>": {"<prefixed property>": <value, as in custom_fields>}}
    "action_aliases": {"<alias>": "<workflow action id>"}
    "hidden_states":  ["<state id>"]

The template is applied first and the project area file over it: dicts are merged key by key, lists and
scalars of the project area replace the template's. Without a file the profile is empty (discovery only).
The files are read on every call (they are small), so an edit takes effect without restarting the server.
"""

import json
import os
import re
from pathlib import Path

MAX_PROFILE_BYTES = 1024 * 1024  # a profile is a small hand-written file
_SAFE_NAME = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9._-]*$")


def profile_dir() -> Path:
    """``$ELM_MCP_PROFILE_DIR`` when set, else ``~/.elm-mcp/profiles``."""
    raw = os.environ.get("ELM_MCP_PROFILE_DIR", "").strip()
    return Path(raw).expanduser() if raw else Path.home() / ".elm-mcp" / "profiles"


def _read(path: Path) -> dict | None:
    """The JSON object in ``path``; None when the file does not exist; ValueError when it is unreadable."""
    if not path.is_file():
        return None
    try:
        if path.stat().st_size > MAX_PROFILE_BYTES:
            raise ValueError(f"file too large ({path.stat().st_size} bytes, limit {MAX_PROFILE_BYTES})")
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ValueError(f"Profile file {path} cannot be read as JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise ValueError(f"Profile file {path} must contain a JSON object.")  # noqa: TRY004 (ValueError is what the tool boundary translates)
    return data


def _safe(kind: str, name: str) -> str:
    if not isinstance(name, str) or not _SAFE_NAME.match(name):
        raise ValueError(f"Invalid {kind} name {name!r} in the profile (letters, digits, '.', '_' and '-' only).")
    return name


def merge(base: dict, override: dict) -> dict:
    """``override`` over ``base``: dicts key by key (deep); lists and scalars of ``override`` replace."""
    merged = dict(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = merge(merged[key], value)
        else:
            merged[key] = value
    return merged


def load(project_area: str) -> dict:
    """The effective profile of a project area: its template (if it names one) with the area file over it."""
    root = profile_dir()
    area = _read(root / "project-areas" / f"{_safe('project area', project_area)}.json")
    if area is None:
        return {}
    name = area.get("template")
    if name is None:
        return area
    path = root / "templates" / f"{_safe('template', name)}.json"
    template = _read(path)
    if template is None:  # a named template without a file is not an error: the area file stands alone
        return area
    return merge(template, area)


def type_defaults(profile: dict, type_id: str) -> dict:
    """The default property values of a work item type (``null`` entries are drafts and are skipped)."""
    defaults = profile.get("defaults")
    values = defaults.get(type_id) if isinstance(defaults, dict) else None
    return {k: v for k, v in values.items() if v is not None} if isinstance(values, dict) else {}


def action_aliases(profile: dict) -> dict[str, str]:
    aliases = profile.get("action_aliases")
    return dict(aliases) if isinstance(aliases, dict) else {}


def hidden_states(profile: dict) -> set[str]:
    states = profile.get("hidden_states")
    return {s for s in states if isinstance(s, str)} if isinstance(states, list) else set()
