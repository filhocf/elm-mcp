"""In-memory discovery cache (SPEC-cache-shape): per process, per server, per project area, per resource.

TTL in seconds from ``ELM_CACHE_TTL`` (read on every call; default 300; 0 turns the cache off; a negative,
``nan`` or infinite value is unusable and means the default).
The clock is ``time.monotonic()``. Only discovered documents are stored (never credentials or cookies).
A loader that raises stores nothing. Callers get deep copies, so mutating a result cannot corrupt the cache.
"""

import contextlib
import contextvars
import copy
import math
import os
import time
from collections.abc import Awaitable, Callable, Iterator
from typing import Any, TypeVar

from .config import settings

DEFAULT_TTL = 300.0

_T = TypeVar("_T")

# (server, project area, resource) -> (expires_at, value)
_store: dict[tuple[str, str, str], tuple[float, Any]] = {}

# Keys already reloaded during the current ``refresh=True`` call (None outside a refresh).
_refreshed: contextvars.ContextVar[set | None] = contextvars.ContextVar("elm_cache_refreshed", default=None)


def ttl() -> float:
    """The TTL in seconds from ELM_CACHE_TTL (default 300; unreadable, negative, nan or infinite -> default)."""
    raw = os.environ.get("ELM_CACHE_TTL")
    if raw is None or not raw.strip():
        return DEFAULT_TTL
    try:
        value = float(raw)
    except ValueError:
        return DEFAULT_TTL
    return value if math.isfinite(value) and value >= 0 else DEFAULT_TTL


def clear() -> None:
    """Drop every entry."""
    _store.clear()


def invalidate(project_area: str) -> None:
    """Drop the entries of a project area on the current server, plus its area-less ones (providers, catalog)."""
    server = settings.elm_url
    for key in [k for k in _store if k[0] == server and k[1] in (project_area, "")]:
        del _store[key]


@contextlib.contextmanager
def refresh_scope(refresh: bool) -> Iterator[None]:
    """While active (and ``refresh`` is true) the first access to each key reloads it and repopulates the cache."""
    if not refresh:
        yield
        return
    token = _refreshed.set(set())
    try:
        yield
    finally:
        _refreshed.reset(token)


async def get_or_fetch(
    project_area: str,
    resource: str,
    loader: Callable[[], Awaitable[_T]],
    *,
    fallback: Callable[[Exception], Awaitable[_T]] | None = None,
) -> _T:
    """The cached value of (server, project area, resource), loading it with ``loader`` on a miss.

    ``fallback`` is the extension point for the local profile (SPEC-perfil-template): when the loader
    fails, it receives the exception and may return a substitute (not cached) or re-raise.
    """
    key = (settings.elm_url, project_area, resource)
    seconds = ttl()
    refreshed = _refreshed.get()
    forced = refreshed is not None and key not in refreshed
    if seconds > 0 and not forced:
        hit = _store.get(key)
        if hit is not None and time.monotonic() < hit[0]:
            return copy.deepcopy(hit[1])
    try:
        value = await loader()
    except Exception as exc:
        if fallback is None:
            raise
        return await fallback(exc)
    if refreshed is not None:
        refreshed.add(key)
    if seconds > 0:
        _store[key] = (time.monotonic() + seconds, copy.deepcopy(value))
    return value
