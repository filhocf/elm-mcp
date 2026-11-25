"""G3 (RED) tests for SPEC-wi-custom-types (wave W2, release v0.7.0 target).

create_workitem must accept ANY work item type the project area exposes through its typed
creation factories (the same data list_types returns): by id, by title (case-insensitive) or by
the task/story aliases, and must refuse an unknown type listing the available ones. The code must
carry no hardcoded type map and no organization-specific type id.

Observed through respx on a private router (an unrouted request raises). Tenant-neutral data:
the custom type is com.example.workitem.debt (see tests/f1_fixtures.py).
"""

import re
from pathlib import Path

import httpx
import pytest
from mcp.server.mcpserver.exceptions import ToolError

from elm_mcp import server
from elm_mcp.auth import close_client

from . import f1_fixtures as f
from .w2_fixtures import BASE, PA, PROJECT, mock_ccm, mock_creation

SRC = Path(__file__).resolve().parent.parent / "src" / "elm_mcp"
RISK_ID = "com.ibm.team.workitem.workItemType.risk"
IMPEDIMENT_ID = "com.ibm.team.workitem.workItemType.impediment"


@pytest.fixture(autouse=True)
async def _cfg(monkeypatch):
    monkeypatch.setattr("elm_mcp.config.settings.elm_url", BASE)
    yield
    await close_client()


def _only_post_to(posts: dict, type_id: str) -> None:
    """The work item went to the factory of ``type_id`` and to no other (generic factory included)."""
    counts = {key or "<generic>": route.call_count for key, route in posts.items()}
    assert counts[type_id] == 1, counts
    assert sum(counts.values()) == 1, counts


def _posted_body(posts: dict, type_id: str) -> str:
    return posts[type_id].calls[0].request.content.decode()


# ======================================================================
# EARS-1: a type present in the project's discovery creates a work item of that type
# ======================================================================
@pytest.mark.parametrize(
    ("type_arg", "type_id"),
    [
        ("defect", "defect"),
        ("Defect", "defect"),
        ("Technical Debt", f.DEBT_ID),
        ("technical debt", f.DEBT_ID),
        (f.DEBT_ID, f.DEBT_ID),
        ("risk", RISK_ID),
        (RISK_ID, RISK_ID),
        (f.STORY_ID, f.STORY_ID),
        ("Impediment factory", IMPEDIMENT_ID),
        (IMPEDIMENT_ID, IMPEDIMENT_ID),
    ],
)
async def test_create_workitem_accepts_any_type_the_project_exposes(w2_router, type_arg, type_id):
    """By id, by title (any case) or by a title that does not follow the factory naming pattern."""
    mock_ccm(w2_router)
    posts = mock_creation(w2_router)
    result = await server.create_workitem(project=PROJECT, title="T", type=type_arg, if_exists="create")
    assert result["id"] == "42"
    _only_post_to(posts, type_id)
    assert f"/types/{PA}/{type_id}" in _posted_body(posts, type_id)


@pytest.mark.parametrize("type_id", [i for i, _ in f.TYPED_FACTORIES if i != "task"])
async def test_every_type_id_listed_by_list_types_is_accepted_by_create_workitem(w2_router, type_id):
    """The contract of the SPEC: whatever list_types shows as a type id, create_workitem takes it."""
    mock_ccm(w2_router)
    posts = mock_creation(w2_router)
    listed = {t["id"] for t in await server.list_types("ccm", PROJECT)}
    assert type_id in listed
    await server.create_workitem(project=PROJECT, title="T", type=type_id, if_exists="create")
    _only_post_to(posts, type_id)


async def test_story_alias_follows_the_project_not_a_hardcoded_id(w2_router):
    """A project whose Story type has an organization-specific id: 'story' must reach THAT factory."""
    custom_story = "com.example.workitem.userStory"
    factories = [
        ("task", "Location for creation of Task change requests "),
        (custom_story, "Location for creation of Story change requests "),
    ]
    mock_ccm(w2_router, factories)
    posts = mock_creation(w2_router, factories)
    await server.create_workitem(project=PROJECT, title="T", type="story", if_exists="create")
    _only_post_to(posts, custom_story)
    assert f"/types/{PA}/{custom_story}" in _posted_body(posts, custom_story)


async def test_a_type_missing_from_a_small_project_is_refused_and_a_custom_one_is_created(w2_router):
    """Per-project discovery: a project with only a custom type accepts it and refuses 'task'."""
    factories = [("com.example.workitem.change", "Location for creation of Change Request change requests ")]
    mock_ccm(w2_router, factories)
    posts = mock_creation(w2_router, factories)
    with pytest.raises(ToolError, match="Change Request"):
        await server.create_workitem(project=PROJECT, title="T", type="task", if_exists="create")
    assert sum(r.call_count for r in posts.values()) == 0
    await server.create_workitem(project=PROJECT, title="T", type="Change Request", if_exists="create")
    _only_post_to(posts, "com.example.workitem.change")


# ======================================================================
# EARS-2: unknown type -> ToolError listing the available types, nothing is created
# ======================================================================
async def test_unknown_type_is_refused_listing_the_types_of_the_project(w2_router):
    mock_ccm(w2_router)
    posts = mock_creation(w2_router)
    with pytest.raises(ToolError) as exc:
        await server.create_workitem(project=PROJECT, title="T", type="epic", if_exists="create")
    message = str(exc.value)
    assert "epic" in message
    for type_id, _ in f.TYPED_FACTORIES:
        assert type_id in message, f"available type missing from the error: {type_id}"
    for title in ("Task", "Story", "Defect", "Risk", "Technical Debt"):
        assert title in message
    assert "decoy" not in message, "a factory of another resource type must not be offered"
    assert sum(r.call_count for r in posts.values()) == 0


async def test_type_discovery_failure_names_the_gate_and_creates_nothing(w2_router):
    """The service provider document is unreadable: the HTTP diagnosis reaches the caller."""
    mock_ccm(w2_router)
    posts = mock_creation(w2_router)
    w2_router.get(f"{BASE}/ccm/oslc/contexts/{PA}/services").mock(return_value=httpx.Response(404))
    w2_router.get(f"{BASE}/ccm/oslc/contexts/{PA}").mock(return_value=httpx.Response(404))
    with pytest.raises(ToolError, match=r"\[recurso-inexistente-ou-invisivel\]"):
        await server.create_workitem(project=PROJECT, title="T", type="defect", if_exists="create")
    assert sum(r.call_count for r in posts.values()) == 0


# ======================================================================
# EARS-3: no hardcoded type map, no organization-specific type id in the code
# ======================================================================
def test_source_has_no_hardcoded_work_item_type_map_nor_organization_type_ids():
    """The TYPE_IDS / CCM_TYPE_IDS maps are retired (types come from discovery) and src/ carries no
    com.<org>.* custom type literal (only IBM product ids may appear)."""
    org_literal = re.compile(r"""["']com\.(?!ibm\.|example\.)[\w.]*workitem[\w.]*["']""")
    type_map = re.compile(r"\bCCM_TYPE_IDS\b|\bTYPE_IDS\b")
    offenders = []
    for path in sorted(SRC.glob("*.py")):
        text = path.read_text(encoding="utf-8")
        if type_map.search(text) or org_literal.search(text):
            offenders.append(path.name)
    assert offenders == []


# ======================================================================
# The tool documentation no longer says only task/story
# ======================================================================
def test_create_workitem_docstring_points_to_list_types_instead_of_task_and_story_only():
    doc = server.create_workitem.__doc__ or ""
    assert "not accepted yet" not in doc
    assert '"task" or "story"' not in doc
    assert "list_types" in doc


def test_list_types_docstring_no_longer_says_create_workitem_accepts_only_task_or_story():
    doc = server.list_types.__doc__ or ""
    assert "accepts only" not in doc
    assert "planned in SPEC-wi-custom-types" not in doc
