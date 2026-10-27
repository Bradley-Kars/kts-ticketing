"""A6-1: Security-regression tests for field-level permission changes.

Tests encode DESIRED behavior AFTER the A6-2 fix. They MUST FAIL against
the current sandbox code because:
 1. Permission checks happen AFTER the read (info leak)
 2. No-op updates bypass permission checks entirely
 3. All entity types get hardcoded ticket.* permissions
 4. No denial logging
 5. No entity_type-scoped automation user lookup

Sandbox-only: writes to /tmp/sandbox-vault-a5/Tickets only.
"""
import sys
import uuid
import tempfile
import os
import shutil
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.models import EventActor, Ticket, Task, Project, Knowledge
from core.services.ticket_service import TicketService
from core.services.task_service import TaskService
from core.services.project_service import ProjectService
from core.services.knowledge_service import KnowledgeService
from core.events import EventStore, EventEmitter
from core.permissions import PermissionChecker
from core.auth import TokenTable
import datetime

_id_counter = 0

def _next_id(prefix: str, width: int = 6) -> str:
    global _id_counter
    _id_counter += 1
    return f"{prefix}-{_id_counter:0{width}d}"

def _make_actor(actor_id: str, actor_type: str = "human") -> EventActor:
    return EventActor(id=actor_id, type=actor_type, name="test")


def _make_checker(allowed: set):
    class PC:
        def check(self, actor, permission):
            return permission in allowed
        def check_any(self, actor, permissions):
            return any(p in allowed for p in permissions)
        def check_all(self, actor, permissions):
            return all(p in allowed for p in permissions)
    return PC()


def _make_service(tmp_path: Path) -> TicketService:
    svc = TicketService(event_emitter=EventEmitter(EventStore()),
                        permission_checker=None)
    svc.entity_dir = Path("/tmp/sandbox-vault-a5/Tickets")
    svc.entity_dir.mkdir(parents=True, exist_ok=True)
    return svc


def _create_ticket(svc: TicketService, actor: EventActor,
                   ticket_id: str, extra: dict = None) -> None:
    data = {
        "id": ticket_id,
        "title": "Baseline ticket",
        "status": "new",
        "priority": "medium",
        "created_at": datetime.datetime.now(),
        "updated_at": datetime.datetime.now(),
        "created_by": actor.id,
        "updated_by": actor.id,
        "version": 1,
    }
    if extra:
        data.update(extra)
    ticket = Ticket(**data)
    svc.permission_checker = None
    svc.create(ticket, actor)


def _create_task(svc: TaskService, actor: EventActor,
                 task_id: str) -> None:
    from core.models import Task
    task = Task(
        id=task_id,
        title="Baseline task",
        status="todo",
        created_at=datetime.datetime.now(),
        updated_at=datetime.datetime.now(),
        created_by=actor.id,
        updated_by=actor.id,
        version=1,
    )
    svc.permission_checker = None
    svc.create(task, actor)


def _create_project(svc: ProjectService, actor: EventActor,
                    proj_id: str) -> None:
    from core.models import Project
    project = Project(
        id=proj_id,
        title="Baseline project",
        status="planning",
        organization_id="ORG-000001",
        project_manager="USR-000001",
        created_at=datetime.datetime.now(),
        updated_at=datetime.datetime.now(),
        created_by=actor.id,
        updated_by=actor.id,
        version=1,
    )
    svc.permission_checker = None
    svc.create(project, actor)


def _create_knowledge(svc: KnowledgeService, actor: EventActor,
                      kb_id: str) -> None:
    from core.models import Knowledge
    kb = Knowledge(
        id=kb_id,
        title="Baseline knowledge",
        status="draft",
        category="general",
        content="content",
        author="USR-000001",
        created_at=datetime.datetime.now(),
        updated_at=datetime.datetime.now(),
        created_by=actor.id,
        updated_by=actor.id,
        version=1,
    )
    svc.permission_checker = None
    svc.create(kb, actor)


# ---------------------------------------------------------------------------
# A6-1a: Permissionless actor calling update() with unchanged values
# CURRENTLY FAILS: required set is empty when nothing changes, so the
# update goes through. Should require ticket.update even for no-op.
# ---------------------------------------------------------------------------
def test_noop_update_requires_ticket_update(tmp_path: Path):
    svc = _make_service(tmp_path)
    actor = _make_actor("USR-000001")
    tid = _next_id("TKT-2026", width=3)
    _create_ticket(svc, actor, tid, {"title": "Original"})

    svc.permission_checker = _make_checker(set())

    try:
        svc.update(tid, {"title": "Original"}, actor)
        assert False, "BUG: no-op update allowed with no permissions"
    except PermissionError as exc:
        assert "ticket.update" in str(exc), \
            f"Expected ticket.update denial, got: {exc}"


# ---------------------------------------------------------------------------
# A6-1b: Empty changes dict requires ticket.update
# ---------------------------------------------------------------------------
def test_empty_changes_requires_ticket_update(tmp_path: Path):
    svc = _make_service(tmp_path)
    actor = _make_actor("USR-000001")
    tid = _next_id("TKT-2026", width=3)
    _create_ticket(svc, actor, tid, {"title": "Original"})

    svc.permission_checker = _make_checker(set())

    try:
        svc.update(tid, {}, actor)
        assert False, "BUG: empty changes allowed with no permissions"
    except PermissionError as exc:
        assert "ticket.update" in str(exc), \
            f"Expected ticket.update denial, got: {exc}"


# ---------------------------------------------------------------------------
# A6-1c: Unauthorized actor gets PermissionError for missing ticket id
# and wrong if_version — denial happens BEFORE any read (no info leak)
# ---------------------------------------------------------------------------
def test_unauthorized_actor_denied_before_read(tmp_path: Path):
    svc = _make_service(tmp_path)
    actor = _make_actor("USR-000001")
    svc.permission_checker = _make_checker(set())

    try:
        svc.update(f"TKT-2026-{uuid.uuid4().hex[:6]}", {"title": "X"}, actor)
        assert False, "BUG: missing ticket not denied with PermissionError"
    except PermissionError:
        pass
    except ValueError:
        assert False, "BUG: got ValueError instead of PermissionError for missing ticket"

    try:
        svc.update(f"TKT-2026-{uuid.uuid4().hex[:6]}", {"title": "X"}, actor, if_version=99999)
        assert False, "BUG: wrong if_version not denied with PermissionError"
    except PermissionError:
        pass


# ---------------------------------------------------------------------------
# A6-1d: Setting assigned_to to its current value still requires ticket.assign
# ---------------------------------------------------------------------------
def test_assigned_to_current_value_requires_ticket_assign(tmp_path: Path):
    svc = _make_service(tmp_path)
    actor = _make_actor("USR-000001")
    tid = _next_id("TKT-2026", width=3)
    _create_ticket(svc, actor, tid, {"assigned_to": "USR-000002"})

    svc.permission_checker = _make_checker({"ticket.update"})

    try:
        svc.update(tid, {"assigned_to": "USR-000002"}, actor)
        assert False, "BUG: assigned_to set to current value allowed without ticket.assign"
    except PermissionError as exc:
        assert "ticket.assign" in str(exc), \
            f"Expected ticket.assign denial, got: {exc}"


# ---------------------------------------------------------------------------
# A6-1d2: Status change requires ticket.change_status
# ---------------------------------------------------------------------------
def test_status_change_requires_ticket_change_status(tmp_path: Path):
    svc = TicketService(event_emitter=EventEmitter(EventStore()),
                        permission_checker=None)
    actor = _make_actor("USR-000001")
    tid = _next_id("TKT-2026", width=3)
    _create_ticket(svc, actor, tid, {"status": "new"})

    # Actor has ONLY ticket.update
    svc.permission_checker = _make_checker({"ticket.update"})

    try:
        svc.update(tid, {"status": "resolved"}, actor)
        assert False, "BUG: status change allowed with only ticket.update"
    except PermissionError as exc:
        assert "ticket.change_status" in str(exc), \
            f"Expected ticket.change_status denial, got: {exc}"


# ---------------------------------------------------------------------------
# A6-1e: Assign-only actor can change assigned_to alone
# ---------------------------------------------------------------------------
def test_assign_only_can_change_assigned_to(tmp_path: Path):
    svc = _make_service(tmp_path)
    actor = _make_actor("USR-000001")
    tid = _next_id("TKT-2026", width=3)
    _create_ticket(svc, actor, tid, {"assigned_to": "USR-000002"})

    # Actor has ONLY ticket.assign
    svc.permission_checker = _make_checker({"ticket.assign"})

    try:
        svc.update(tid, {"assigned_to": "USR-000003"}, actor)
        assert True, "assign-only actor can change assigned_to"
    except PermissionError as exc:
        assert False, f"BUG: assign-only actor denied assigned_to change: {exc}"


# ---------------------------------------------------------------------------
# A6-1f: Assign-only actor denied title change
# ---------------------------------------------------------------------------
def test_assign_only_denied_title(tmp_path: Path):
    svc = _make_service(tmp_path)
    actor = _make_actor("USR-000001")
    tid = _next_id("TKT-2026", width=3)
    _create_ticket(svc, actor, tid, {"title": "Original"})

    # Actor has ONLY ticket.assign
    svc.permission_checker = _make_checker({"ticket.assign"})

    try:
        svc.update(tid, {"title": "New title"}, actor)
        assert False, "BUG: assign-only actor allowed title change without ticket.update"
    except PermissionError as exc:
        assert "ticket.update" in str(exc), \
            f"Expected ticket.update denial, got: {exc}"


# ---------------------------------------------------------------------------
# A6-1g: Update-only actor denied assigned_to change
# ---------------------------------------------------------------------------
def test_update_only_denied_assigned_to(tmp_path: Path):
    svc = _make_service(tmp_path)
    actor = _make_actor("USR-000001")
    tid = _next_id("TKT-2026", width=3)
    _create_ticket(svc, actor, tid, {"assigned_to": "USR-000002"})

    # Actor has ONLY ticket.update
    svc.permission_checker = _make_checker({"ticket.update"})

    try:
        svc.update(tid, {"assigned_to": "USR-000003"}, actor)
        assert False, "BUG: update-only actor allowed assigned_to change without ticket.assign"
    except PermissionError as exc:
        assert "ticket.assign" in str(exc), \
            f"Expected ticket.assign denial, got: {exc}"


# ---------------------------------------------------------------------------
# A6-1h: Mixed change (assigned_to + title) needs both permissions
# ---------------------------------------------------------------------------
def test_mixed_needs_both(tmp_path: Path):
    svc = _make_service(tmp_path)
    actor = _make_actor("USR-000001")
    tid = _next_id("TKT-2026", width=3)
    _create_ticket(svc, actor, tid, {"assigned_to": "USR-000002", "title": "Original"})

    # Actor has ONLY ticket.update
    svc.permission_checker = _make_checker({"ticket.update"})

    try:
        svc.update(tid, {"assigned_to": "USR-000003", "title": "New title"}, actor)
        assert False, "BUG: mixed change allowed with only ticket.update"
    except PermissionError as exc:
        assert "ticket.assign" in str(exc), \
            f"Expected ticket.assign denial for mixed change, got: {exc}"

    # Actor has ONLY ticket.assign
    svc.permission_checker = _make_checker({"ticket.assign"})

    try:
        svc.update(tid, {"assigned_to": "USR-000003", "title": "New title"}, actor)
        assert False, "BUG: mixed change allowed with only ticket.assign"
    except PermissionError as exc:
        assert "ticket.update" in str(exc), \
            f"Expected ticket.update denial for mixed change, got: {exc}"


# ---------------------------------------------------------------------------
# A6-1i: Task/project/knowledge updates use their own entity_type permission
# ---------------------------------------------------------------------------
def test_task_update_requires_task_update(tmp_path: Path):
    svc = TaskService(event_emitter=EventEmitter(EventStore()),
                      permission_checker=None)
    actor = _make_actor("USR-000001")
    tid = _next_id("TSK-2026", width=6)
    _create_task(svc, actor, tid)

    svc.permission_checker = _make_checker({"ticket.update"})

    try:
        svc.update(tid, {"title": "New title"}, actor)
        assert False, "BUG: task update allowed with ticket.update instead of task.update"
    except PermissionError as exc:
        assert "task.update" in str(exc), \
            f"Expected task.update denial, got: {exc}"


def test_project_update_requires_project_update(tmp_path: Path):
    svc = ProjectService(event_emitter=EventEmitter(EventStore()),
                         permission_checker=None)
    actor = _make_actor("USR-000001")
    pid = _next_id("PRJ-2026", width=6)
    _create_project(svc, actor, pid)

    svc.permission_checker = _make_checker({"ticket.update"})

    try:
        svc.update(pid, {"title": "New title"}, actor)
        assert False, "BUG: project update allowed with ticket.update instead of project.update"
    except PermissionError as exc:
        assert "project.update" in str(exc), \
            f"Expected project.update denial, got: {exc}"


def test_knowledge_update_requires_knowledge_update(tmp_path: Path):
    svc = KnowledgeService(event_emitter=EventEmitter(EventStore()),
                           permission_checker=None)
    actor = _make_actor("USR-000001")
    kid = _next_id("KB", width=6)
    _create_knowledge(svc, actor, kid)

    svc.permission_checker = _make_checker({"ticket.update"})

    try:
        svc.update(kid, {"title": "New title"}, actor)
        assert False, "BUG: knowledge update allowed with ticket.update instead of knowledge.update"
    except PermissionError as exc:
        assert "knowledge.update" in str(exc), \
            f"Expected knowledge.update denial, got: {exc}"


# ---------------------------------------------------------------------------
# A6-1i2: automation actor listed in users.yaml gets that role
# FIXED: asserts a permission dispatcher HAS (ticket.create), not one it lacks.
# ---------------------------------------------------------------------------
def test_automation_actor_listed_gets_users_yaml_role(tmp_path: Path):
    import yaml
    users_path = Path("/tmp/sandbox-home-a5/config/users.yaml")
    with open(users_path) as f:
        users_data = yaml.safe_load(f)
    users_data["users"]["AUT-BRIDGE"] = {
        "id": "AUT-BRIDGE", "role": "dispatcher"
    }
    config_dir = Path("/tmp/sandbox-home-a5/config")
    config_dir.mkdir(parents=True, exist_ok=True)
    with open(config_dir / "users.yaml", "w") as f:
        yaml.safe_dump(users_data, f)

    checker = PermissionChecker()
    actor = _make_actor("AUT-BRIDGE", "automation")
    result = checker.check(actor, "ticket.create")
    assert result is True, \
        "BUG: automation actor listed in users.yaml doesn't get dispatcher ticket.create"


# ---------------------------------------------------------------------------
# A6-1g: automation actor not listed falls back to system role
# ---------------------------------------------------------------------------
def test_automation_actor_not_listed_falls_back_to_system(tmp_path: Path):
    checker = PermissionChecker()
    actor = _make_actor("AUT-UNKNOWN", "automation")
    result = checker.check(actor, "ticket.update")
    assert result is False, \
        "unlisted automation actor -> system role -> denied (correct)"


# ---------------------------------------------------------------------------
# A6-1h: unknown actor id denies; malformed/missing users.yaml denies
# ---------------------------------------------------------------------------
def test_unknown_actor_id_denies(tmp_path: Path):
    checker = PermissionChecker()
    actor = _make_actor("USR-ZZZZZZ", "human")
    result = checker.check(actor, "ticket.update")
    assert result is False, "unknown actor id -> denied"


# ---------------------------------------------------------------------------
# A6-1i: denial logs actor id, missing permission, entity id
# ---------------------------------------------------------------------------
def test_denial_logs_actor_and_permission(tmp_path: Path, caplog):
    svc = _make_service(tmp_path)
    actor = _make_actor("USR-000001")
    tid = _next_id("TKT-2026", width=3)
    _create_ticket(svc, actor, tid)

    svc.permission_checker = _make_checker(set())

    with caplog.at_level("WARNING"):
        try:
            svc.update(tid, {"title": "X"}, actor)
        except PermissionError:
            pass

    log_text = caplog.text
    assert "USR-000001" in log_text, "log missing actor id"
    assert "ticket.update" in log_text, "log missing missing permission"
    assert tid in log_text, "log missing entity id"


# ---------------------------------------------------------------------------
# A6-1j: Token-to-actor mapping (B3)
# minting a token with actor_id stores actor_id; verify returns actor_id;
# minting without actor_id returns None for actor_id
# ---------------------------------------------------------------------------
def test_mint_stores_actor_id(tmp_path: Path):
    table_path = str(tmp_path / "token-table.json")
    table = TokenTable(table_path)
    token_id, secret = table.mint("test-token", scope="write", actor_id="USR-000002")

    data = table._load()
    rec = data["tokens"][token_id]
    assert rec["actor_id"] == "USR-000002", f"Expected actor_id USR-000002, got {rec.get('actor_id')}"
    assert rec["label"] == "test-token"
    assert rec["scope"] == "write"


def test_verify_returns_actor_id(tmp_path: Path):
    table_path = str(tmp_path / "token-table.json")
    table = TokenTable(table_path)
    token_id, secret = table.mint("test-token", scope="write", actor_id="USR-000002")

    result = table.verify(secret)
    assert result is not None, "verify should return a result for a valid token"
    assert result["token_id"] == token_id
    assert result["actor_id"] == "USR-000002", f"Expected actor_id USR-000002, got {result.get('actor_id')}"
    assert result["label"] == "test-token"
    assert result["scope"] == "write"


def test_verify_actor_id_none_when_no_actor(tmp_path: Path):
    table_path = str(tmp_path / "token-table.json")
    table = TokenTable(table_path)
    token_id, secret = table.mint("test-token", scope="read")  # no actor_id

    result = table.verify(secret)
    assert result is not None, "verify should return a result for a valid token"
    assert result.get("actor_id") is None, f"Expected actor_id None, got {result.get('actor_id')}"


def test_verify_denies_revoked_token(tmp_path: Path):
    table_path = str(tmp_path / "token-table.json")
    table = TokenTable(table_path)
    token_id, secret = table.mint("test-token", scope="write", actor_id="USR-000002")

    table.revoke(token_id)
    result = table.verify(secret)
    assert result is None, "revoked token should be denied"


def test_verify_denies_unknown_token(tmp_path: Path):
    table_path = str(tmp_path / "token-table.json")
    table = TokenTable(table_path)

    result = table.verify("kts_nonexistent_token_value")
    assert result is None, "unknown token should be denied"


if __name__ == "__main__":
    import pytest
    sys.exit(pytest.main([__file__, "-v"]))
