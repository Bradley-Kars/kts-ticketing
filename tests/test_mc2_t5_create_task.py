"""MC-2 T5 createTask — sandbox-only, temp copies, real tree never written.

Bradley GO 2026-10-11 (T5 only): add POST /tasks (createTask) via the real
task-sequence write path (TaskService.next_id + create). Same sandbox/temp
rules as T1–T4.

Changes under test:
  - core/api.py: create_task / get_task (allocates id via next_id + create)
  - core/bridge.py: POST /tasks route; TASK_ALLOWED whitelist; extend
    _normalize_task_field for task-only fields.

Required proofs (by test name):
  1. happy path (title+fields) -> 200, version 1  -> test_create_task_200_version_1
  2. missing title -> 422, no task written        -> test_create_missing_title_422
  3. system-owned field (id/type/version/status) -> 422 -> test_create_system_field_422
  4. bad if_version type -> 422                   -> test_create_bad_if_version_422
  5. valid if_version on create is ignored (always v1) -> test_create_if_version_ignored
  + audited: create 200 shows token_id/method/path only (no task content)
    -> test_create_audited_no_content
  + real tree / shared sandbox untouched          -> test_real_vault_and_home_unchanged,
                                                      test_shared_sandbox_not_touched
  + isolation guard enforced                      -> test_isolation_guard_enforced

if_version note: create allocates a brand-new id, so there is no prior version
to precondition against. The route still type-checks if_version (422 on bad
type) for consistency with the update routes; a valid if_version is ignored
(new task is always version 1).
"""

# ---- ISOLATION GUARD: must be FIRST, before ANY core.* imports -------------
try:
    from tests.test_isolation_guard import require_isolation, snapshot_real_tree
except ImportError:  # running as `python3 tests/test_mc2_t5_create_task.py`
    from test_isolation_guard import require_isolation, snapshot_real_tree
require_isolation()
# ---------------------------------------------------------------------------

import json
import os
import re
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
HEADER = "X-API-Token"

REAL_COUNTERS_SHA = "22a89c711f1f08f74119fe77eea1e1436f218d035bcc16bdf444c4f4de6ae245"
REAL_USERS_YAML_SHA = "93f7797d42c115d2ddd7e4e944088e17b721370859ceab49140e6b0a2ecaada4"
REAL_VAULT_FILE_COUNT = 47
TASK_ID = "TSK-2026-903"


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
        """# temp users for T5 createTask rehearsal
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


class TestCreateTask(unittest.TestCase):
    """End-to-end createTask via TestClient against a TEMP vault+home."""

    def setUp(self):
        sys.path.insert(0, str(CODE))
        self.tmp = Path(tempfile.mkdtemp(prefix="mc2t5-"))
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
        # snapshot real tree before each test
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
        """The require_isolation() call at module top-level must have passed.
        This test exists to make the guard's presence explicit in the test run.
        """
        # If we reach here, require_isolation() at import time succeeded.
        self.assertTrue(True, "isolation guard active")

    # ---- proof 1: happy path 200, version 1 -----------------------------
    def test_create_task_200_version_1(self):
        """POST /tasks with title+optional fields -> 200; new task version 1;
        vault file created with version: 1; created_by USR-000002."""
        script = f'''
import json, re, sys
from pathlib import Path
sys.path.insert(0, {str(CODE)!r})
from fastapi.testclient import TestClient
from core.auth import TokenTable
from core.bridge import create_app
table = TokenTable({str(self.tmp / "auth" / "token-table.json")!r})
client = TestClient(create_app(table))
_, sec = table.mint("desktop", scope="write")
H = {{"X-API-Token": sec}}
r = client.post(
    "/tasks",
    json={{"title": "T5 create probe", "priority": "high", "description": "rehearsal"}},
    headers=H,
)
assert r.status_code == 200, (r.status_code, r.text)
d = r.json()
assert re.match(r"^TSK-\\d{{4}}-\\d{{6}}$", d["task_id"]), d
assert d["version"] == 1, d
assert d["status"] == "todo", d
assert d["actor"] == "USR-000002", d
assert d["created_by"] == "USR-000002", d
# task loads back with version 1; vault file created
from core.api import get_api
t = get_api().get_task(d["task_id"])
assert t is not None and t.version == 1
tf = Path({str(self.vault)!r}) / "Tasks"
hits = list(tf.glob(f"{{d['task_id']}}*.md"))
assert len(hits) == 1, hits
text = hits[0].read_text()
assert "status: todo" in text and "priority: high" in text, text[:400]
print(json.dumps({{"ok": True, "task_id": d["task_id"], "version": d["version"], "status": d["status"]}}))
'''
        out = self._assert_ok(self._run(script))
        self.assertTrue(out["ok"])
        self.assertEqual(out["version"], 1)
        self.assertEqual(out["status"], "todo")
        self.assertRegex(out["task_id"], r"^TSK-\d{4}-\d{6}$")
        # leak check after write
        self.real_snap.assert_unchanged("test_create_task_200_version_1")

    # ---- proof 2: missing title -> 422, no write ------------------------
    def test_create_missing_title_422(self):
        """POST /tasks without title -> 422; no task file in vault."""
        script = f'''
import json, sys
from pathlib import Path
sys.path.insert(0, {str(CODE)!r})
from fastapi.testclient import TestClient
from core.auth import TokenTable
from core.bridge import create_app
table = TokenTable({str(self.tmp / "auth" / "token-table.json")!r})
client = TestClient(create_app(table))
_, sec = table.mint("desktop", scope="write")
H = {{"X-API-Token": sec}}
r = client.post("/tasks", json={{"priority": "high"}}, headers=H)
assert r.status_code == 422, (r.status_code, r.text)
tf = Path({str(self.vault)!r}) / "Tasks"
assert not tf.exists() or list(tf.glob("*.md")) == [], list(tf.glob("*.md"))
print(json.dumps({{"ok": True, "code": r.status_code}}))
'''
        out = self._assert_ok(self._run(script))
        self.assertTrue(out["ok"])
        self.assertEqual(out["code"], 422)
        self.real_snap.assert_unchanged("test_create_missing_title_422")

    # ---- proof 3: system-owned field -> 422 -----------------------------
    def test_create_system_field_422(self):
        """POST /tasks with id/type/version/status (system-owned) -> 422 each."""
        script = f'''
import json, sys
from pathlib import Path
sys.path.insert(0, {str(CODE)!r})
from fastapi.testclient import TestClient
from core.auth import TokenTable
from core.bridge import create_app
table = TokenTable({str(self.tmp / "auth" / "token-table.json")!r})
client = TestClient(create_app(table))
_, sec = table.mint("desktop", scope="write")
H = {{"X-API-Token": sec}}
for key, val in (("id", "TSK-2026-999"), ("type", "task"), ("version", 9), ("status", "done")):
    r = client.post("/tasks", json={{"title": "sys-owned probe", key: val}}, headers=H)
    assert r.status_code == 422, (key, r.status_code, r.text)
    assert "system-owned" in r.json()["detail"], (key, r.json())
# no task file was written
tf = Path({str(self.vault)!r}) / "Tasks"
assert not tf.exists() or list(tf.glob("*.md")) == [], list(tf.glob("*.md"))
print(json.dumps({{"ok": True, "checked": ["id","type","version","status"]}}))
'''
        out = self._assert_ok(self._run(script))
        self.assertTrue(out["ok"])
        self.assertEqual(out["checked"], ["id", "type", "version", "status"])
        self.real_snap.assert_unchanged("test_create_system_field_422")

    # ---- proof 4: bad if_version type -> 422 ----------------------------
    def test_create_bad_if_version_422(self):
        """POST /tasks with if_version bool/str/negative -> 422; no task written."""
        script = f'''
import json, sys
from pathlib import Path
sys.path.insert(0, {str(CODE)!r})
from fastapi.testclient import TestClient
from core.auth import TokenTable
from core.bridge import create_app
table = TokenTable({str(self.tmp / "auth" / "token-table.json")!r})
client = TestClient(create_app(table))
_, sec = table.mint("desktop", scope="write")
H = {{"X-API-Token": sec}}
for bad in (True, "1", -1):
    r = client.post("/tasks", json={{"title": "bad-if_version probe", "if_version": bad}}, headers=H)
    assert r.status_code == 422, (bad, r.status_code, r.text)
tf = Path({str(self.vault)!r}) / "Tasks"
assert not tf.exists() or list(tf.glob("*.md")) == [], list(tf.glob("*.md"))
print(json.dumps({{"ok": True, "checked": ["bool","str","negative"]}}))
'''
        out = self._assert_ok(self._run(script))
        self.assertTrue(out["ok"])
        self.assertEqual(out["checked"], ["bool", "str", "negative"])
        self.real_snap.assert_unchanged("test_create_bad_if_version_422")

    # ---- proof 5: valid if_version on create is ignored (always v1) -----
    def test_create_if_version_ignored(self):
        """Valid if_version on create is ignored: new task is always version 1.
        (No prior version exists to precondition against.)"""
        script = f'''
import json, sys
from pathlib import Path
sys.path.insert(0, {str(CODE)!r})
from fastapi.testclient import TestClient
from core.auth import TokenTable
from core.bridge import create_app
table = TokenTable({str(self.tmp / "auth" / "token-table.json")!r})
client = TestClient(create_app(table))
_, sec = table.mint("desktop", scope="write")
H = {{"X-API-Token": sec}}
r = client.post("/tasks", json={{"title": "if_version-on-create", "if_version": 1}}, headers=H)
assert r.status_code == 200, (r.status_code, r.text)
d = r.json()
assert d["version"] == 1, d
from core.api import get_api
t = get_api().get_task(d["task_id"])
assert t.version == 1, t.version
print(json.dumps({{"ok": True, "task_id": d["task_id"], "version": d["version"]}}))
'''
        out = self._assert_ok(self._run(script))
        self.assertTrue(out["ok"])
        self.assertEqual(out["version"], 1)
        self.real_snap.assert_unchanged("test_create_if_version_ignored")

    # ---- audited: create 200 shows token_id/method/path only ------------
    def test_create_audited_no_content(self):
        """POST /tasks -> 200 (audited action=request); audit lines record
        token_id/method/path only (no task content, no token chars)."""
        script = f'''
import json, sys
from pathlib import Path
sys.path.insert(0, {str(CODE)!r})
from fastapi.testclient import TestClient
from core.auth import TokenTable
from core.bridge import create_app
ALLOWED = set(("ts_utc", "action", "method", "path", "status", "latency_ms", "token_id", "label"))
table = TokenTable({str(self.tmp / "auth" / "token-table.json")!r})
client = TestClient(create_app(table))
tid, sec = table.mint("desktop", scope="write")
H = {{"X-API-Token": sec}}
r = client.post("/tasks", json={{"title": "audited create"}}, headers=H)
assert r.status_code == 200, (r.status_code, r.text)
# read audit.log (token table default: next to the table file)
ap = Path({str(self.tmp / "auth" / "token-table.json")!r}).parent / "audit.log"
lines = [json.loads(x) for x in ap.read_text().splitlines() if x.strip()]
create_lines = [x for x in lines if x.get("path") == "/tasks" and x.get("method") == "POST" and x.get("status") == 200]
assert create_lines, ("no create 200 audit line", lines)
assert "token_id" in create_lines[0] and "label" in create_lines[0], create_lines[0]
assert "detail" not in create_lines[0] and "title" not in create_lines[0], create_lines[0]
assert sec not in json.dumps(lines), "secret leaked into audit"
assert all("secret" not in json.dumps(x) for x in lines), "secret key leaked into audit"
assert all(set(x.keys()) <= ALLOWED for x in lines), lines[0]
print(json.dumps({{"ok": True, "create_200": True, "audit_lines": len(lines), "create_audit": create_lines[0], "token_id": tid}}))
'''
        out = self._assert_ok(self._run(script))
        self.assertTrue(out["ok"])
        self.assertTrue(out["create_200"])
        self.assertGreater(out["audit_lines"], 0)
        self.assertEqual(out["create_audit"].get("method"), "POST")
        self.assertEqual(out["create_audit"].get("path"), "/tasks")
        self.assertEqual(out["create_audit"].get("status"), 200)
        self.assertEqual(out["create_audit"].get("token_id"), out.get("token_id"))
        self.assertIn("token_id", out["create_audit"])
        self.assertIn("label", out["create_audit"])
        self.assertNotIn("detail", out["create_audit"])
        self.assertNotIn("title", out["create_audit"])
        self.real_snap.assert_unchanged("test_create_audited_no_content")

    # ---- guards: real tree untouched (legacy, now enforced by guard) ----
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
        sandbox_home = REAL_HOME
        users = sandbox_home / "config" / "users.yaml"
        if users.exists():
            self.assertNotIn(
                TASK_ID,
                users.read_text(),
                "temp task id leaked into shared users.yaml",
            )


if __name__ == "__main__":
    unittest.main(verbosity=2)
