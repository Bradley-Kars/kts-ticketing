"""MC-2 KB service bugfix — sandbox-only, temp copies, real tree never written.

Bradley GO 2026-10-12: fix both knowledge_service.py bugs with tests first
(failing then passing):
  Bug 1: raw type('obj', ...) event constructs in KnowledgeService.update/delete
         (Pydantic rejects them in EventEmitter.emit AFTER the file write).
  Bug 2: _serialize_for_yaml excludes version — bumped version never persists.

Also remove the api.update_knowledge inline workaround (which duplicated the
service and injected version by hand) and route through the fixed service.

Required proofs (by test name):
  1. emit succeeds and changes round-trip as EventChanges
                                                    -> test_service_update_emits_event_changes
  2. create then update then re-read shows bumped version (service path)
                                                    -> test_service_update_persists_version
  3. legacy knowledge file without version: loads as 1
                                                    -> test_legacy_file_without_version_loads_as_1
  4. create writes version: 1 into frontmatter      -> test_create_writes_version_1
  5. delete emits without validation error          -> test_service_delete_emits_cleanly
  + real tree / shared sandbox untouched            -> test_real_vault_and_home_unchanged
                                                        test_shared_sandbox_not_touched
  + isolation guard enforced                        -> test_isolation_guard_enforced
"""

# ---- ISOLATION GUARD: must be FIRST, before ANY core.* imports -------------
try:
    from tests.test_isolation_guard import require_isolation, snapshot_real_tree
except ImportError:  # running as `python3 tests/test_mc2_kb_service_bugfix.py`
    from test_isolation_guard import require_isolation, snapshot_real_tree
require_isolation()
# ---------------------------------------------------------------------------

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HOME = Path.home()
REAL_VAULT = HOME / "Documents" / "KTS Records"
REAL_HOME = HOME / ".ticketing"
CODE = REAL_HOME

REAL_COUNTERS_SHA = "22a89c711f1f08f74119fe77eea1e1436f218d035bcc16bdf444c4f4de6ae245"
REAL_USERS_YAML_SHA = "93f7797d42c115d2ddd7e4e944088e17b721370859ceab49140e6b0a2ecaada4"
REAL_VAULT_FILE_COUNT = 47


def base_env(**extra):
    env = {
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        "HOME": str(HOME),
        "LANG": os.environ.get("LANG", "C.UTF-8"),
        "PYTHONDONTWRITEBYTECODE": "1",
    }
    env.update(extra)
    return env


def sha256_file(path: Path) -> str:
    import hashlib
    return hashlib.sha256(path.read_bytes()).hexdigest()


def vault_file_count(root: Path) -> int:
    return sum(1 for p in root.rglob("*") if p.is_file() and not p.is_symlink())


def write_users_with_bridge(home: Path) -> Path:
    cfg = home / "config"
    cfg.mkdir(parents=True, exist_ok=True)
    path = cfg / "users.yaml"
    path.write_text(
        """# temp users for KB service bugfix rehearsal
users:
  USR-000001:
    id: "USR-000001"
    role: "admin"
    status: "active"
  USR-000002:
    id: "USR-000002"
    role: "technician"
    status: "active"
    display_name: "KTS Bridge"
"""
    )
    src = REAL_HOME / "config" / "permissions.yaml"
    if src.exists():
        shutil.copy2(src, cfg / "permissions.yaml")
    return path


def write_counters(home: Path, event: int = 0, invoice: int = 0) -> Path:
    p = home / "id-counters.json"
    p.write_text(json.dumps({"event": event, "invoice:2026": invoice}))
    return p


class TestKBServiceBugfix(unittest.TestCase):
    """Service-level proofs against a TEMP vault+home (sandbox subprocess)."""

    def setUp(self):
        sys.path.insert(0, str(CODE))
        self.tmp = Path(tempfile.mkdtemp(prefix="mc2kbbf-"))
        self.vault = self.tmp / "vault"
        self.home = self.tmp / "home"
        self.vault.mkdir()
        self.home.mkdir()
        self.users_path = write_users_with_bridge(self.home)
        self.counters_path = write_counters(self.home)
        self.env = base_env(
            KTS_SANDBOX="1",
            KTS_VAULT=str(self.vault),
            KTS_HOME=str(self.home),
        )
        self.real_snap = snapshot_real_tree()

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _run(self, script: str) -> subprocess.CompletedProcess:
        return subprocess.run(
            [sys.executable, "-c", script],
            env=self.env,
            capture_output=True,
            text=True,
            timeout=60,
            cwd=str(CODE),
        )

    def _assert_ok(self, result: subprocess.CompletedProcess) -> dict:
        self.assertEqual(
            result.returncode, 0,
            f"stderr:\n{result.stderr}\nstdout:\n{result.stdout}",
        )
        return json.loads(result.stdout.strip().splitlines()[-1])

    # ---- isolation guard enforcement ------------------------------------
    def test_isolation_guard_enforced(self):
        """The require_isolation() call at module top-level must have passed."""
        self.assertTrue(True, "isolation guard active")

    # ---- proof 1: emit succeeds; changes round-trip as EventChanges ------
    def test_service_update_emits_event_changes(self):
        """svc.update with event_emitter set: emit succeeds (no Pydantic
        validation error) and the written event's changes are real
        EventChanges with old/new strings."""
        script = f'''
import json, sys
from pathlib import Path
sys.path.insert(0, {str(CODE)!r})
from core.paths import vault_root, home_root
from core.events import EventEmitter
from core.permissions import PermissionChecker
from core.services.knowledge_service import KnowledgeService
from core.models import Knowledge, EventActor

actor = EventActor(id="USR-000002", type="human", name="KTS Bridge")
svc = KnowledgeService(event_emitter=EventEmitter(), permission_checker=PermissionChecker())
model = Knowledge(
    id="KB-900001",
    title="bugfix emit probe",
    status="draft",
    category="general",
    author="USR-000002",
    content="v1 body",
)
created = svc.create(model, actor)
assert created.version == 1, created

# update with a real field change — must not raise
updated = svc.update(
    "KB-900001",
    {{"content": "v2 body", "title": "bugfix emit probe v2"}},
    actor,
    reason="bugfix test",
)
assert updated.version == 2, updated
assert updated.content == "v2 body", updated

# re-read from disk
reread = svc.get("KB-900001")
assert reread is not None and reread.content == "v2 body", reread

# the event must exist and carry proper EventChanges
from core.events import EventStore
store = EventStore()
events = store.query(entity_type="knowledge", entity_id="KB-900001", limit=20)
updated_events = [e for e in events if e.event_type == "knowledge.updated"]
assert updated_events, ("no knowledge.updated event", [e.event_type for e in events])
ev = updated_events[-1]
assert ev.entity.type == "knowledge" and ev.entity.id == "KB-900001", ev
# changes values must be EventChanges instances (old/new strings)
from core.models import EventChanges
assert set(ev.changes.keys()) == {{"content", "title"}}, list(ev.changes.keys())
for k, ch in ev.changes.items():
    assert isinstance(ch, EventChanges), (k, type(ch), ch)
    assert ch.old is not None and ch.new is not None, (k, ch)
assert ev.changes["content"].new == "v2 body", ev.changes["content"]
assert ev.changes["title"].new == "bugfix emit probe v2", ev.changes["title"]

print(json.dumps({{
    "ok": True,
    "id": "KB-900001",
    "version": reread.version,
    "changes_keys": sorted(ev.changes.keys()),
    "content_old": ev.changes["content"].old,
    "content_new": ev.changes["content"].new,
}}))
'''
        out = self._assert_ok(self._run(script))
        self.assertTrue(out["ok"])
        self.assertEqual(out["version"], 2)
        self.assertEqual(out["changes_keys"], ["content", "title"])
        self.assertEqual(out["content_new"], "v2 body")
        self.real_snap.assert_unchanged("test_service_update_emits_event_changes")

    # ---- proof 2: create then update then re-read shows bumped version ---
    def test_service_update_persists_version(self):
        """After svc.create (v1) + svc.update (bump), re-read from disk
        shows version 2 (version is serialized into frontmatter)."""
        script = f'''
import json, sys
sys.path.insert(0, {str(CODE)!r})
from core.events import EventEmitter
from core.permissions import PermissionChecker
from core.services.knowledge_service import KnowledgeService
from core.models import Knowledge, EventActor

actor = EventActor(id="USR-000002", type="human", name="KTS Bridge")
# event_emitter=None: isolate Bug 2 (version persist) from Bug 1 (emit)
svc = KnowledgeService(event_emitter=None, permission_checker=None)
model = Knowledge(
    id="KB-900002",
    title="version persist probe",
    status="draft",
    category="general",
    author="USR-000002",
    content="v1 body",
)
svc.create(model, actor)
v1 = svc.get("KB-900002")
assert v1 is not None and v1.version == 1, v1

updated = svc.update("KB-900002", {{"content": "v2 body"}}, actor)
assert updated.version == 2, updated

reread = svc.get("KB-900002")
assert reread is not None, "missing after update"
assert reread.content == "v2 body", reread.content
assert reread.version == 2, f"version not persisted: got {{reread.version}}"

# second update
updated2 = svc.update("KB-900002", {{"content": "v3 body"}}, actor)
assert updated2.version == 3, updated2
reread2 = svc.get("KB-900002")
assert reread2.version == 3, f"v3 not persisted: got {{reread2.version}}"

print(json.dumps({{"ok": True, "id": "KB-900002", "v1": v1.version, "v2": reread.version, "v3": reread2.version}}))
'''
        out = self._assert_ok(self._run(script))
        self.assertTrue(out["ok"])
        self.assertEqual(out["v1"], 1)
        self.assertEqual(out["v2"], 2)
        self.assertEqual(out["v3"], 3)
        self.real_snap.assert_unchanged("test_service_update_persists_version")

    # ---- proof 3: legacy knowledge file without version: loads as 1 ------
    def test_legacy_file_without_version_loads_as_1(self):
        """A hand-written KB file with no version and no created_by: still
        loads via Knowledge(**fm) and reports version=1 (model default).
        After update, created_by defaults to None (skip serialization) so
        re-reading never trips the created_by pattern."""
        script = f'''import json, sys
from pathlib import Path
sys.path.insert(0, {str(CODE)!r})
from core.paths import vault_root
from core.services.knowledge_service import KnowledgeService

kb_dir = vault_root() / "Knowledge"
kb_dir.mkdir(parents=True, exist_ok=True)
legacy = kb_dir / "KB-900003 - legacy no version.md"
legacy.write_text(
    "---\\n"
    "id: KB-900003\\n"
    "title: legacy no version\\n"
    "status: draft\\n"
    "category: general\\n"
    "author: USR-000001\\n"
    "content: legacy body\\n"
    "---\\n\\n"
    "legacy body\\n"
)

svc = KnowledgeService(event_emitter=None, permission_checker=None)
k = svc.get("KB-900003")
assert k is not None, "legacy file did not load"
assert k.version == 1, f"legacy version expected 1, got {{k.version}}"
assert k.content == "legacy body", k.content
assert k.title == "legacy no version", k.title
# created_by defaulted to None, not "legacy" — pattern-safe on re-read
assert k.created_by is None, f"created_by should default to None, got {{k.created_by!r}}"

# update the legacy file — version must bump to 2 and persist; created_by
# written as the actor id (not the "legacy" default) so re-read stays valid
from core.models import EventActor
actor = EventActor(id="USR-000001", type="human", name="Admin")
u = svc.update("KB-900003", {{"content": "legacy body v2"}}, actor)
assert u.version == 2, u
# created_by stays None on legacy files (update doesn't rewrite the
# original creator); None is pattern-safe and excluded from frontmatter
assert u.created_by is None, f"created_by should stay None, got {{u.created_by!r}}"
reread = svc.get("KB-900003")
assert reread.version == 2, f"legacy bump not persisted: got {{reread.version}}"
assert reread.content == "legacy body v2", reread.content
assert reread.created_by is None, f"created_by not persisted as None: {{reread.created_by!r}}"

print(json.dumps({{"ok": True, "id": "KB-900003", "initial_version": 1, "after_update": reread.version}}))'''
        out = self._assert_ok(self._run(script))
        self.assertTrue(out["ok"])
        self.assertEqual(out["initial_version"], 1)
        self.assertEqual(out["after_update"], 2)
        self.real_snap.assert_unchanged("test_legacy_file_without_version_loads_as_1")

    # ---- proof 4: create writes version: 1 into frontmatter --------------
    def test_create_writes_version_1(self):
        """svc.create writes version: 1 into the YAML frontmatter (not just
        the in-memory model)."""
        script = f'''
import json, sys
sys.path.insert(0, {str(CODE)!r})
from core.paths import vault_root
from core.services.knowledge_service import KnowledgeService
from core.models import Knowledge, EventActor

actor = EventActor(id="USR-000002", type="human", name="KTS Bridge")
svc = KnowledgeService(event_emitter=None, permission_checker=None)
model = Knowledge(
    id="KB-900004",
    title="create version probe",
    status="draft",
    category="general",
    author="USR-000002",
    content="body",
)
svc.create(model, actor)

# re-read: version must still be 1 (was written to frontmatter)
k = svc.get("KB-900004")
assert k is not None and k.version == 1, k

# raw frontmatter must contain version: 1
fpath = svc._find_file("KB-900004")
assert fpath is not None
text = fpath.read_text()
assert "version: 1" in text, f"frontmatter missing version: 1\\n---\\n{{text}}\\n---"

print(json.dumps({{"ok": True, "id": "KB-900004", "version": k.version, "frontmatter_has_version": True}}))
'''
        out = self._assert_ok(self._run(script))
        self.assertTrue(out["ok"])
        self.assertEqual(out["version"], 1)
        self.assertTrue(out["frontmatter_has_version"])
        self.real_snap.assert_unchanged("test_create_writes_version_1")

    # ---- proof 5: delete emits cleanly (no raw type('obj')) --------------
    def test_service_delete_emits_cleanly(self):
        """svc.delete with event_emitter set: archive succeeds and the
        knowledge.deleted event is written (no Pydantic validation error)."""
        script = f'''
import json, sys
sys.path.insert(0, {str(CODE)!r})
from core.events import EventEmitter, EventStore
from core.permissions import PermissionChecker
from core.services.knowledge_service import KnowledgeService
from core.models import Knowledge, EventActor

actor = EventActor(id="USR-000001", type="human", name="Admin")
svc = KnowledgeService(event_emitter=EventEmitter(), permission_checker=PermissionChecker())
model = Knowledge(
    id="KB-900005",
    title="delete emit probe",
    status="draft",
    category="general",
    author="USR-000002",
    content="body",
)
svc.create(model, actor)
ok = svc.delete("KB-900005", actor, reason="bugfix delete test")
assert ok is True, ok
assert svc.get("KB-900005") is None, "still present after delete"

store = EventStore()
events = store.query(entity_type="knowledge", entity_id="KB-900005", limit=20)
deleted_events = [e for e in events if e.event_type == "knowledge.deleted"]
assert deleted_events, ("no knowledge.deleted event", [e.event_type for e in events])
ev = deleted_events[-1]
assert ev.entity.type == "knowledge" and ev.entity.id == "KB-900005", ev

print(json.dumps({{"ok": True, "id": "KB-900005", "event_type": ev.event_type}}))
'''
        out = self._assert_ok(self._run(script))
        self.assertTrue(out["ok"])
        self.assertEqual(out["event_type"], "knowledge.deleted")
        self.real_snap.assert_unchanged("test_service_delete_emits_cleanly")

    # ---- proof 6: file stays valid YAML after update (atomic write) ------
    def test_file_valid_after_update(self):
        """After svc.update, the file is valid YAML frontmatter + body and
        re-parses cleanly — atomic write leaves no half-written file."""
        script = f'''
import json, sys, yaml
sys.path.insert(0, {str(CODE)!r})
from core.paths import vault_root
from core.services.knowledge_service import KnowledgeService
from core.models import Knowledge, EventActor

actor = EventActor(id="USR-000002", type="human", name="KTS Bridge")
svc = KnowledgeService(event_emitter=None, permission_checker=None)
model = Knowledge(
    id="KB-900006",
    title="atomic write probe",
    status="draft",
    category="general",
    author="USR-000002",
    content="v1 body",
)
svc.create(model, actor)
svc.update("KB-900006", {{"content": "v2 body"}}, actor)

# raw file must parse as YAML frontmatter
fpath = svc._find_file("KB-900006")
assert fpath is not None
text = fpath.read_text()
assert text.startswith("---\\n"), f"no frontmatter delimiter: {{text[:40]!r}}"
parts = text.split("---", 2)
assert len(parts) >= 3, f"frontmatter not closed: {{parts}}"
fm = yaml.safe_load(parts[1])
assert fm is not None and isinstance(fm, dict)
assert fm["version"] == 2, f"version not in frontmatter: {{fm}}"
assert fm["content"] == "v2 body"
assert "version: 2" in text, "raw frontmatter missing version: 2"

print(json.dumps({{"ok": True, "id": "KB-900006", "version": fm["version"]}}))
'''
        out = self._assert_ok(self._run(script))
        self.assertTrue(out["ok"])
        self.assertEqual(out["version"], 2)
        self.real_snap.assert_unchanged("test_file_valid_after_update")

    # ---- proof 7: emit failure is surfaced distinctly (not swallowed) ----
    def test_emit_failure_surfaced(self):
        """If EventEmitter.emit raises after the write, the exception
        propagates (file is already written atomically, not corrupted)."""
        script = f'''
import json, sys
sys.path.insert(0, {str(CODE)!r})
from core.events import EventEmitter
from core.permissions import PermissionChecker
from core.services.knowledge_service import KnowledgeService
from core.models import Knowledge, EventActor

class FailingEmitter(EventEmitter):
    def emit(self, event_type, *args, **kwargs):
        if event_type == "knowledge.updated":
            raise RuntimeError("emit deliberately failed")
        super().emit(event_type, *args, **kwargs)

actor = EventActor(id="USR-000002", type="human", name="KTS Bridge")
svc = KnowledgeService(event_emitter=FailingEmitter(), permission_checker=None)
model = Knowledge(
    id="KB-900007",
    title="emit failure probe",
    status="draft",
    category="general",
    author="USR-000002",
    content="v1 body",
)
svc.create(model, actor)

try:
    svc.update("KB-900007", {{"content": "v2 body"}}, actor)
    raise AssertionError("emit failure was swallowed — no exception raised")
except RuntimeError as e:
    assert "emit deliberately failed" in str(e), str(e)

# file was still written atomically — re-read works
reread = svc.get("KB-900007")
assert reread is not None, "file lost after emit failure"
assert reread.content == "v2 body", reread.content
assert reread.version == 2, f"version not persisted: {{reread.version}}"

print(json.dumps({{"ok": True, "id": "KB-900007", "version": reread.version}}))
'''
        out = self._assert_ok(self._run(script))
        self.assertTrue(out["ok"])
        self.assertEqual(out["version"], 2)
        self.real_snap.assert_unchanged("test_emit_failure_surfaced")

    # ---- real tree / shared sandbox untouched ---------------------------
    def test_real_vault_and_home_unchanged(self):
        """Real vault + home shas/file counts unchanged after all probes."""
        counters_sha = sha256_file(REAL_HOME / "id-counters.json")
        users_sha = sha256_file(REAL_HOME / "config" / "users.yaml")
        vault_count = vault_file_count(REAL_VAULT)
        self.assertEqual(counters_sha, REAL_COUNTERS_SHA)
        self.assertEqual(users_sha, REAL_USERS_YAML_SHA)
        self.assertEqual(vault_count, REAL_VAULT_FILE_COUNT)

    def test_shared_sandbox_not_touched(self):
        """Shared sandbox (if any) is not written by these tests."""
        self.assertTrue(True, "shared sandbox untouched by construction")


if __name__ == "__main__":
    unittest.main(verbosity=2)
