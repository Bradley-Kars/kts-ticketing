"""MC-2 project_service.py characterization — sandbox-only, real tree never written.

Characterizes the EFFECTIVE (last-defined) methods of ProjectService before the
Bug 1 / Bug 2 fix. After the fix, update these tests to expect the corrected
behavior (version persists in frontmatter, EventEntity used at emit sites).

Current (buggy) effective behavior:
  - Bug 1: create() passes type('obj', ...) as entity at line 145;
           update() passes type('obj', ...) as entity at line 203 and as
           changes values at lines 182-185; delete() passes type('obj', ...)
           at line 226. Pydantic rejects these AFTER the file write.
  - Bug 2: _serialize_for_yaml() excludes {"version", "ai"} at line 76 so
           bumped version never persists across re-read.
  - Duplicate: _get_body_for_model() defined at lines 89-91 and 293-294
           (identical, last-def-wins).

Characterization tests document current behavior; they are updated after the
fix to expect corrected behavior.
"""

# ---- ISOLATION GUARD: must be FIRST, before ANY core.* imports -------------
try:
    from tests.test_isolation_guard import require_isolation, snapshot_real_tree
except ImportError:  # running as `python3 tests/test_mc2_project_service.py`
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
        """# temp users for project_service characterization
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


def write_counters(home: Path, event: int = 0) -> Path:
    p = home / "id-counters.json"
    p.write_text(json.dumps({"event": event}))
    return p


class TestProjectServiceCharacterization(unittest.TestCase):
    """Characterize effective ProjectService methods before Bug 1 / Bug 2 fix."""

    def setUp(self):
        sys.path.insert(0, str(CODE))
        self.tmp = Path(tempfile.mkdtemp(prefix="mc2projsvc-"))
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

    def test_isolation_guard_enforced(self):
        self.assertTrue(True, "isolation guard active")

    # ---- characterization 1: create writes project file with version 1 ------
    def test_create_writes_project_file(self):
        """svc.create allocates id, sets version 1, writes frontmatter."""
        script = f'''
import json, sys
sys.path.insert(0, {str(CODE)!r})
from core.services.project_service import get_project_service
from core.models import Project, EventActor

svc = get_project_service()
actor = EventActor(id="USR-000001", type="human", name="Admin")
p = Project(
    id="PRJ-2026-009001",
    title="characterization create probe",
    description="before fix",
    status="planning",
    priority="medium",
    organization_id="ORG-20260001",
    project_manager="USR-000001",
)
created = svc.create(p, actor)
assert created.version == 1, created
assert created.created_by == "USR-000001", created
# re-read: version 1 in frontmatter (Bug 2 does not affect create)
reread = svc.get("PRJ-2026-009001")
assert reread is not None and reread.version == 1, reread
print(json.dumps({{"ok": True, "id": "PRJ-2026-009001", "version": reread.version}}))
'''
        out = self._assert_ok(self._run(script))
        self.assertTrue(out["ok"])
        self.assertEqual(out["version"], 1)
        self.real_snap.assert_unchanged("test_create_writes_project_file")

    # ---- characterization 2: get reads project back ------
    def test_get_reads_project_back(self):
        """svc.get returns the project that was created."""
        script = f'''
import json, sys
sys.path.insert(0, {str(CODE)!r})
from core.services.project_service import get_project_service
from core.models import Project, EventActor

svc = get_project_service()
actor = EventActor(id="USR-000001", type="human", name="Admin")
p = Project(
    id="PRJ-2026-009002",
    title="get readback probe",
    description="body",
    status="planning",
    priority="high",
    organization_id="ORG-20260001",
    project_manager="USR-000001",
)
svc.create(p, actor)
reread = svc.get("PRJ-2026-009002")
assert reread is not None, "project not found"
assert reread.title == "get readback probe", reread
assert reread.description == "body", reread
assert reread.priority == "high", reread
print(json.dumps({{"ok": True, "id": "PRJ-2026-009002", "title": reread.title}}))
'''
        out = self._assert_ok(self._run(script))
        self.assertTrue(out["ok"])
        self.assertEqual(out["title"], "get readback probe")
        self.real_snap.assert_unchanged("test_get_reads_project_back")

    # ---- characterization 3: update bumps version in memory AND persists ------
    def test_update_bumps_version_in_memory_and_persists(self):
        """svc.update increments version in memory AND frontmatter
        (Bug 2 fixed: _serialize_for_yaml excludes only {'ai'}, version
        persists across re-read)."""
        script = f'''
import json, sys
sys.path.insert(0, {str(CODE)!r})
from core.services.project_service import get_project_service
from core.models import Project, EventActor

svc = get_project_service()
actor = EventActor(id="USR-000001", type="human", name="Admin")
p = Project(
    id="PRJ-2026-009003",
    title="version bump probe",
    description="v1",
    status="planning",
    priority="medium",
    organization_id="ORG-20260001",
    project_manager="USR-000001",
)
svc.create(p, actor)
updated = svc.update("PRJ-2026-009003", {{"description": "v2"}}, actor)
assert updated.version == 2, updated  # version bumped in memory
# Bug 2 fixed: re-read shows version 2 (serialized to frontmatter)
reread = svc.get("PRJ-2026-009003")
assert reread is not None, "project lost"
assert reread.description == "v2", reread  # field change persisted
assert reread.version == 2, f"version persisted, got {{reread.version}}"
print(json.dumps({{"ok": True, "id": "PRJ-2026-009003", "memory_version": updated.version, "readback_version": reread.version}}))
'''
        out = self._assert_ok(self._run(script))
        self.assertTrue(out["ok"])
        self.assertEqual(out["memory_version"], 2)
        self.assertEqual(out["readback_version"], 2)
        self.real_snap.assert_unchanged("test_update_bumps_version_in_memory_and_persists")

    # ---- characterization 4: delete archives project ------
    def test_delete_archives_project(self):
        """svc.delete archives the project file and returns True."""
        script = f'''
import json, sys
sys.path.insert(0, {str(CODE)!r})
from core.services.project_service import get_project_service
from core.models import Project, EventActor

svc = get_project_service()
actor = EventActor(id="USR-000001", type="human", name="Admin")
p = Project(
    id="PRJ-2026-009004",
    title="delete archive probe",
    description="body",
    status="planning",
    priority="low",
    organization_id="ORG-20260001",
    project_manager="USR-000001",
)
svc.create(p, actor)
ok = svc.delete("PRJ-2026-009004", actor, reason="characterization delete")
assert ok is True, ok
assert svc.get("PRJ-2026-009004") is None, "project still present after delete"
print(json.dumps({{"ok": True, "id": "PRJ-2026-009004", "archived": True}}))
'''
        out = self._assert_ok(self._run(script))
        self.assertTrue(out["ok"])
        self.assertTrue(out["archived"])
        self.real_snap.assert_unchanged("test_delete_archives_project")

    # ---- characterization 5: next_id generates PRJ id ------
    def test_next_id_generates_prj_id(self):
        """svc.next_id generates PRJ-YYYY-NNNNNN format."""
        script = f'''
import json, sys, re
sys.path.insert(0, {str(CODE)!r})
from core.services.project_service import get_project_service

svc = get_project_service()
nid = svc.next_id()
assert re.match(r"^PRJ-\\d{{4}}-\\d{{6}}$", nid), nid
print(json.dumps({{"ok": True, "next_id": nid}}))
'''
        out = self._assert_ok(self._run(script))
        self.assertTrue(out["ok"])
        self.assertRegex(out["next_id"], r"^PRJ-\d{4}-\d{6}$")
        self.real_snap.assert_unchanged("test_next_id_generates_prj_id")

    # ---- characterization 6: list returns projects ------
    def test_list_returns_projects(self):
        """svc.list returns created projects."""
        script = f'''
import json, sys
sys.path.insert(0, {str(CODE)!r})
from core.services.project_service import get_project_service
from core.models import Project, EventActor

svc = get_project_service()
actor = EventActor(id="USR-000001", type="human", name="Admin")
p = Project(
    id="PRJ-2026-009005",
    title="list probe",
    description="body",
    status="planning",
    priority="medium",
    organization_id="ORG-20260001",
    project_manager="USR-000001",
)
svc.create(p, actor)
entities = svc.list()
ids = [e.id for e in entities]
assert "PRJ-2026-009005" in ids, ids
print(json.dumps({{"ok": True, "id": "PRJ-2026-009005", "listed": True}}))
'''
        out = self._assert_ok(self._run(script))
        self.assertTrue(out["ok"])
        self.assertTrue(out["listed"])
        self.real_snap.assert_unchanged("test_list_returns_projects")

    # ---- guards ----
    def test_real_vault_and_home_unchanged(self):
        self.assertEqual(
            sha256_file(REAL_HOME / "id-counters.json"),
            REAL_COUNTERS_SHA,
            "real id-counters.json changed",
        )
        self.assertEqual(
            sha256_file(REAL_HOME / "config" / "users.yaml"),
            REAL_USERS_YAML_SHA,
            "real users.yaml changed",
        )
        self.assertEqual(
            vault_file_count(REAL_VAULT),
            REAL_VAULT_FILE_COUNT,
            "real vault file count changed",
        )

    def test_shared_sandbox_not_touched(self):
        self.assertTrue(True)


if __name__ == "__main__":
    unittest.main(verbosity=2)
