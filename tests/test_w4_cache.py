"""G3 (RED) tests for SPEC-cache-shape (wave W4, release v0.6.0).

An in-memory discovery cache (per process, per server, per project area, per resource) with a TTL:
``ELM_CACHE_TTL`` seconds, default 300, ``0`` turns it off. Observed through respx request counts only.

Assumptions the developer must validate (see the report):
  * the module is ``elm_mcp.cache`` and exposes ``clear()`` (used by the fixture below to isolate tests);
  * ``ELM_CACHE_TTL`` is read from the environment at call time;
  * the cache reads the clock through ``time.monotonic()`` (module attribute), so the tests can fake it;
  * a "stale shape or value" write error is an HTTP 400 or 409 on the creation POST.

Tenant-neutral data only (https://elm.example.com, _PA1, jdoe).
"""

import importlib
import importlib.util
import time
import types
from collections.abc import Mapping

import httpx
import pytest
from mcp.server.mcpserver.exceptions import ToolError

from elm_mcp import server
from elm_mcp.auth import close_client
from elm_mcp.config import settings

from . import f1_fixtures as f
from . import w2_fixtures as w2
from . import w4_fixtures as w4
from .w4_fixtures import BASE, PROJECT, calls_to

SERVICES = f"{w2.CTX}/services"
OTHER_BASE = "https://elm2.example.com"
PASSWORD = "not-a-real-password"
SESSION_SECRET = "SECRETSESSIONVALUE"
WORKFLOW = "com.example.workflow"
STATES_DOC = f"{BASE}/ccm/oslc/workflows/{f.PA}/states/{WORKFLOW}"
ACTIONS_DOC = f"{BASE}/ccm/oslc/workflows/{f.PA}/actions/{WORKFLOW}"


def _clear_cache() -> None:
    """Empty the cache between tests (a no-op while the module does not exist)."""
    if importlib.util.find_spec("elm_mcp.cache") is not None:
        importlib.import_module("elm_mcp.cache").clear()


class Clock:
    """time.monotonic() replacement: the real clock plus a manual offset."""

    def __init__(self) -> None:
        self._real = time.monotonic
        self.offset = 0.0

    def __call__(self) -> float:
        return self._real() + self.offset

    def advance(self, seconds: float) -> None:
        self.offset += seconds


@pytest.fixture(autouse=True)
async def _isolated(monkeypatch):
    monkeypatch.setattr("elm_mcp.config.settings.elm_url", BASE)
    monkeypatch.delenv("ELM_CACHE_TTL", raising=False)
    _clear_cache()
    yield
    await close_client()
    _clear_cache()


@pytest.fixture
def clock(monkeypatch) -> Clock:
    fake = Clock()
    monkeypatch.setattr(time, "monotonic", fake)
    return fake


class Srv:
    """A CCM server whose discovery documents can be changed between calls (factories, shape, workflow)."""

    def __init__(self, router, base: str = BASE, factories: list[tuple[str, str]] | None = None) -> None:
        self.base = base
        self.factories = list(factories if factories is not None else f.TYPED_FACTORIES[:2])
        self.extra_props: list[dict] = []
        self.state_ids = [f"{WORKFLOW}.state.s1", f"{WORKFLOW}.state.s3"]
        self.router = router
        self.fail_services = False
        swap = self._swap
        cookie = {"Set-Cookie": f"JSESSIONID={SESSION_SECRET}; Path=/"}
        router.post(f"{base}/jts/j_security_check").mock(return_value=httpx.Response(200, headers=cookie))
        router.get(f"{base}/ccm/rootservices").mock(return_value=httpx.Response(200, text=swap(f.ROOTSERVICES_XML)))
        router.get(f"{base}/ccm/oslc/workitems/catalog").mock(
            return_value=httpx.Response(200, text=swap(w2._catalog_xml()))
        )
        ctx = f"{base}/ccm/oslc/contexts/{f.PA}"
        router.get(f"{ctx}/services").mock(side_effect=self._services)
        router.get(ctx).mock(side_effect=self._services)
        for type_id in {t for t, _ in f.TYPED_FACTORIES}:
            router.get(swap(f.shape_uri(type_id))).mock(side_effect=self._shape(type_id))
        self.allowed_url = swap(f"{BASE}/ccm/oslc/shapes/workitems/task/property/internalState/allowedValues")
        router.get(self.allowed_url).mock(side_effect=self._allowed)
        router.get(swap(STATES_DOC)).mock(side_effect=self._states)
        router.get(swap(ACTIONS_DOC)).mock(side_effect=self._actions)

    def _swap(self, text: str) -> str:
        return text.replace(BASE, self.base)

    def _services(self, request: httpx.Request) -> httpx.Response:
        if self.fail_services:
            return httpx.Response(500, text="boom")
        return httpx.Response(200, text=self._swap(w2.services_xml(self.factories)))

    def _shape(self, type_id: str):
        def handler(request: httpx.Request) -> httpx.Response:
            state = f.prop("state", "State", "Exactly-one", propdef=f"{f.RTC_CM}state", allowed=self.allowed_url)
            props = [state, f.prop("alpha", "Alpha", propdef=f"{f.RTC_CM}alpha"), *self.extra_props]
            return httpx.Response(200, text=self._swap(f.real_shape_xml(type_id, props)))

        return handler

    def _allowed(self, request: httpx.Request) -> httpx.Response:
        refs = "".join(
            f'<oslc:allowedValue rdf:resource="{self.base}/ccm/oslc/workflows/{f.PA}/states/{WORKFLOW}/{s}"/>'
            for s in self.state_ids
        )
        xml = (
            '<rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#" '
            'xmlns:oslc="http://open-services.net/ns/core#">'
            f'<rdf:Description rdf:about="{self.allowed_url}">{refs}</rdf:Description></rdf:RDF>'
        )
        return httpx.Response(200, text=xml)

    def _states(self, request: httpx.Request) -> httpx.Response:
        results = [
            {"dcterms:identifier": s, "dcterms:title": s.rsplit(".", 1)[-1], "rtc_cm:group": "open"}
            for s in self.state_ids
        ]
        return httpx.Response(200, json={"oslc:results": results})

    def _actions(self, request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"oslc:results": [{"rdf:about": f"{ACTIONS_DOC}/{WORKFLOW}.action.a1"}]})


async def _type_ids() -> list[str]:
    return [t["id"] for t in await server.list_types("ccm", PROJECT)]


# ======================================================================
# Second discovery inside the TTL costs no request
# ======================================================================
async def test_second_list_types_inside_the_ttl_sends_no_request(w2_router):
    Srv(w2_router)
    first = await server.list_types("ccm", PROJECT)
    sent = len(w2_router.calls)
    second = await server.list_types("ccm", PROJECT)
    assert second == first
    assert len(w2_router.calls) == sent, "the cached discovery must not touch the server"
    assert calls_to(w2_router, SERVICES) == 1


async def test_second_list_fields_inside_the_ttl_sends_no_request(w2_router):
    Srv(w2_router)
    first = await server.list_fields("ccm", PROJECT, "task")
    sent = len(w2_router.calls)
    second = await server.list_fields("ccm", PROJECT, "task")
    assert second == first
    assert len(w2_router.calls) == sent
    assert calls_to(w2_router, f.shape_uri("task")) == 1


async def test_second_list_workflow_inside_the_ttl_sends_no_request(w2_router):
    Srv(w2_router)
    first = await server.list_workflow(PROJECT, "task")
    sent = len(w2_router.calls)
    second = await server.list_workflow(PROJECT, "task")
    assert second == first
    assert len(w2_router.calls) == sent
    assert calls_to(w2_router, STATES_DOC) == 1


async def test_another_type_reuses_the_provider_but_discovers_its_own_shape(w2_router):
    """The cache key includes the resource: a different type is a miss for its shape, a hit for the provider."""
    Srv(w2_router)
    await server.list_fields("ccm", PROJECT, "task")
    await server.list_fields("ccm", PROJECT, "story")
    assert calls_to(w2_router, SERVICES) == 1
    assert calls_to(w2_router, f.shape_uri("task")) == 1
    assert calls_to(w2_router, f.shape_uri(f.STORY_ID)) == 1


async def test_discovery_is_shared_between_tools(w2_router):
    """list_types warms the provider that list_fields then needs."""
    Srv(w2_router)
    await server.list_types("ccm", PROJECT)
    await server.list_fields("ccm", PROJECT, "task")
    assert calls_to(w2_router, SERVICES) == 1


async def test_a_failed_discovery_is_not_cached(w2_router):
    srv = Srv(w2_router)
    srv.fail_services = True
    with pytest.raises(ToolError, match=r"\[http-500\]"):
        await _type_ids()
    srv.fail_services = False
    assert await _type_ids(), "the failure must not have been stored"
    sent = len(w2_router.calls)
    assert await _type_ids()
    assert len(w2_router.calls) == sent, "the successful discovery is cached"


# ======================================================================
# TTL: ELM_CACHE_TTL (default 300 s, 0 = off)
# ======================================================================
async def test_default_ttl_is_300_seconds(w2_router, clock):
    Srv(w2_router)
    await _type_ids()
    clock.advance(299)
    await _type_ids()
    assert calls_to(w2_router, SERVICES) == 1, "299 s < 300 s: still cached"
    clock.advance(2)
    await _type_ids()
    assert calls_to(w2_router, SERVICES) == 2, "301 s > 300 s: discovered again"


async def test_expired_entry_is_repopulated_and_serves_the_next_call(w2_router, clock):
    Srv(w2_router)
    await _type_ids()
    clock.advance(301)
    await _type_ids()
    await _type_ids()
    assert calls_to(w2_router, SERVICES) == 2


async def test_ttl_comes_from_elm_cache_ttl(w2_router, clock, monkeypatch):
    monkeypatch.setenv("ELM_CACHE_TTL", "10")
    Srv(w2_router)
    await _type_ids()
    clock.advance(9)
    await _type_ids()
    assert calls_to(w2_router, SERVICES) == 1
    clock.advance(2)
    await _type_ids()
    assert calls_to(w2_router, SERVICES) == 2


async def test_ttl_zero_turns_the_cache_off(w2_router, monkeypatch):
    monkeypatch.setenv("ELM_CACHE_TTL", "0")
    Srv(w2_router)
    await _type_ids()
    await _type_ids()
    await _type_ids()
    assert calls_to(w2_router, SERVICES) == 3
    monkeypatch.delenv("ELM_CACHE_TTL")
    await _type_ids()
    await _type_ids()
    assert calls_to(w2_router, SERVICES) == 4, "without the variable the default TTL applies again"


# ======================================================================
# refresh=True ignores the cache and repopulates it
# ======================================================================
async def test_list_types_refresh_rediscovers_and_repopulates(w2_router):
    srv = Srv(w2_router)
    assert "com.example.workitem.extra" not in await _type_ids()
    srv.factories.append(("com.example.workitem.extra", "Location for creation of Extra change requests "))
    assert "com.example.workitem.extra" not in await _type_ids(), "still inside the TTL: the old answer"
    refreshed = await server.list_types("ccm", PROJECT, refresh=True)
    assert "com.example.workitem.extra" in [t["id"] for t in refreshed]
    sent = len(w2_router.calls)
    assert "com.example.workitem.extra" in await _type_ids(), "the refresh repopulated the cache"
    assert len(w2_router.calls) == sent


async def test_list_fields_refresh_rediscovers_and_repopulates(w2_router):
    srv = Srv(w2_router)
    before = await server.list_fields("ccm", PROJECT, "task")
    srv.extra_props.append(f.prop("beta", "Beta", "Exactly-one", propdef=f"{f.RTC_CM}beta"))
    assert await server.list_fields("ccm", PROJECT, "task") == before
    refreshed = await server.list_fields("ccm", PROJECT, "task", refresh=True)
    assert "beta" in [p["name"] for p in refreshed["required"]]
    sent = len(w2_router.calls)
    assert await server.list_fields("ccm", PROJECT, "task") == refreshed
    assert len(w2_router.calls) == sent


async def test_list_workflow_refresh_rediscovers_and_repopulates(w2_router):
    srv = Srv(w2_router)
    before = await server.list_workflow(PROJECT, "task")
    srv.state_ids.append(f"{WORKFLOW}.state.s4")
    assert await server.list_workflow(PROJECT, "task") == before
    refreshed = await server.list_workflow(PROJECT, "task", refresh=True)
    assert f"{WORKFLOW}.state.s4" in [s["id"] for s in refreshed["states"]]
    sent = len(w2_router.calls)
    assert await server.list_workflow(PROJECT, "task") == refreshed
    assert len(w2_router.calls) == sent


@pytest.mark.parametrize("tool_name", ["list_types", "list_fields", "list_workflow"])
async def test_discovery_tools_are_registered_with_a_refresh_parameter(tool_name):
    tool = next(t for t in await server.mcp.list_tools() if t.name == tool_name)
    assert "refresh" in tool.input_schema["properties"]
    assert "refresh" not in tool.input_schema.get("required", [])


# ======================================================================
# Isolation by server (ELM_URL) and no credentials in the cache
# ======================================================================
async def test_cache_is_not_shared_between_servers(w2_router, monkeypatch):
    Srv(w2_router, BASE, factories=f.TYPED_FACTORIES[:1])
    assert await _type_ids() == ["task"]
    await close_client()
    monkeypatch.setattr("elm_mcp.config.settings.elm_url", OTHER_BASE)
    Srv(w2_router, OTHER_BASE, factories=f.TYPED_FACTORIES[1:3])
    other = await _type_ids()
    assert other == [f.STORY_ID, "defect"], "the second server must not be answered from the first one's cache"
    other_services = f"{OTHER_BASE}/ccm/oslc/contexts/{f.PA}/services"
    assert await _type_ids() == other
    assert calls_to(w2_router, other_services) == 1, "each server is cached on its own"
    await close_client()
    monkeypatch.setattr("elm_mcp.config.settings.elm_url", BASE)
    assert await _type_ids() == ["task"]
    assert calls_to(w2_router, SERVICES) == 1, "the first server's entry survived the visit to the second"


def _strings(obj, seen: set[int], depth: int = 0):
    """Every str/bytes reachable from ``obj`` (containers and plain objects), bounded in depth."""
    if id(obj) in seen or depth > 8 or obj is settings:
        return
    seen.add(id(obj))
    if isinstance(obj, (str, bytes)):
        yield obj if isinstance(obj, str) else obj.decode("latin-1")
    elif isinstance(obj, (types.ModuleType, types.FunctionType, types.BuiltinFunctionType, type)):
        return
    elif isinstance(obj, Mapping):
        for key, value in obj.items():
            yield from _strings(key, seen, depth + 1)
            yield from _strings(value, seen, depth + 1)
    elif isinstance(obj, (list, tuple, set, frozenset)):
        for item in obj:
            yield from _strings(item, seen, depth + 1)
    elif hasattr(obj, "__dict__"):
        yield from _strings(vars(obj), seen, depth + 1)


async def test_the_cache_holds_no_credentials_or_cookies(w2_router):
    Srv(w2_router)
    await server.list_types("ccm", PROJECT)
    await server.list_fields("ccm", PROJECT, "task")
    cache = importlib.import_module("elm_mcp.cache")
    held = list(_strings({k: v for k, v in vars(cache).items() if not k.startswith("__")}, set()))
    assert held, "the cache module must hold the discovered data in memory"
    blob = "\n".join(held)
    assert PASSWORD not in blob
    assert SESSION_SECRET not in blob


# ======================================================================
# Stale shape/value: 400/409 on the creation POST -> invalidate + rediscover ONCE + retry ONCE
# ======================================================================
def _validation_error(status: int) -> httpx.Response:
    return httpx.Response(status, json={"oslc:message": "Validation failed: the value is not allowed"})


async def _primed_create_env(router):
    posts = w4.mock_create_env(router, props=f.default_task_props())
    w4.FakeCcmQuery(router, [])
    await server.list_types("ccm", PROJECT)  # warms the cache
    return posts, calls_to(router, SERVICES)


@pytest.mark.parametrize("status", [400, 409])
async def test_validation_error_invalidates_rediscovers_once_and_retries_once(w2_router, status):
    posts, services_before = await _primed_create_env(w2_router)
    posts["task"].side_effect = [_validation_error(status), httpx.Response(201, json=w2.CREATED)]
    result = await server.create_workitem(project=PROJECT, title="T", type="task")
    assert result["id"] == "42"
    assert posts["task"].call_count == 2
    assert calls_to(w2_router, SERVICES) == services_before + 1, "exactly one rediscovery of the project area"


@pytest.mark.parametrize("status", [400, 409])
async def test_a_persistent_validation_error_is_reported_after_a_single_retry(w2_router, status):
    posts, services_before = await _primed_create_env(w2_router)
    posts["task"].side_effect = lambda request: _validation_error(status)
    with pytest.raises(ToolError, match="Validation failed"):
        await server.create_workitem(project=PROJECT, title="T", type="task")
    assert posts["task"].call_count == 2
    assert calls_to(w2_router, SERVICES) == services_before + 1


@pytest.mark.parametrize(
    "response",
    [
        httpx.Response(500, text="boom"),
        httpx.Response(403, json={"oslc:message": "process rule"}),
        httpx.Response(404, text="gone"),
    ],
)
async def test_other_errors_do_not_invalidate_or_retry(w2_router, response):
    posts, services_before = await _primed_create_env(w2_router)
    posts["task"].side_effect = lambda request: response
    with pytest.raises(ToolError):
        await server.create_workitem(project=PROJECT, title="T", type="task")
    assert posts["task"].call_count == 1
    assert calls_to(w2_router, SERVICES) == services_before


# ======================================================================
# Profile as fallback when the discovery fails
# ======================================================================
async def test_profile_defaults_are_the_fallback_when_the_shape_cannot_be_discovered(w2_router, tmp_path, monkeypatch):
    root = w4.write_profile(
        tmp_path / "profiles",
        area={"defaults": {"task": {"rtc_cm:filedAgainst": {"rdf:resource": f.CAT_2}}}},
    )
    monkeypatch.setenv("ELM_MCP_PROFILE_DIR", str(root))
    posts = w4.mock_create_env(w2_router, shape_status=500)
    w4.FakeCcmQuery(w2_router, [])
    result = await server.create_workitem(project=PROJECT, title="T", type="task")
    assert result["id"] == "42"
    assert f.CAT_2 in w4.posted(posts["task"])
