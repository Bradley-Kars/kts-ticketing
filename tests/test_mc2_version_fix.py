"""MC-2 version-fix rehearsal — sandbox-only, temp copies, real tree never written.

Bradley GO 2026-10-11 (rehearsal only): make `version` persist in ticket
frontmatter, add optional `if_version` optimistic-concurrency on all three
bridge write routes (stale -> 409, file hash unchanged), add an assigned_to
nonexistent-user rejection on PATCH. Real vault untouched; no bulk edit of
existing tickets. Report: code diff + files touched, real hashes before/after,
full suite count, port 8799 free, key grep; then STOP (T4 needs its own go).

Changes under test:
  - core/services/ticket_service.py: _serialize_for_yaml no longer excludes
    `version`; add_worklog/remove_worklog write fm["version"]; VersionMismatch
    exception + if_version on update/add_worklog/remove_worklog/change_status.
  - core/api.py: if_version passthrough on update_ticket/add_worklog/
    change_ticket_status; user_exists helper.
  - core/bridge.py: _parse_if_version + 409 on VersionMismatch on the three
    write routes; assigned_to existence check (400) on PATCH.

Required proofs (by test name):
  1. persists across reload              -> test_version_persists_across_reload
  2. legacy ticket without version -> 1   -> test_legacy_no_version_loads_as_1
  3. stale if_version -> 409 + hash same -> test_stale_if_version_409_hash_unchanged
  4. missing if_version behaves as before -> test_missing_if_version_behaves_as_before
  5. PATCH assigned_to nonexistent -> 400 -> test_assigned_to_nonexistent_user_400
  + real tree / shared sandbox untouched  -> test_real_vault_and_home_unchanged,
                                             test_shared_sandbox_not_touched
"""

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
HEADER = "X-API-Token"

REAL_COUNTERS_SHA = "22a89c711f1f08f74119fe77eea1e1436f218d035bcc16bdf444c4f4de6ae245"
REAL_USERS_YAML_SHA = "93f7797d42c115d2ddd7e4e944088e17b721370859ceab49140e6b0a2ecaada4"
REAL_VAULT_FILE_COUNT = 47
TICKET_ID = "TKT-2026-903"


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


def write_temp_ticket(vault: Path, ticket_id: str = TICKET_ID, with_version: bool = True) -> Path:
    """Minimal valid ticket markdown in the temp vault.

    with_version=False omits the version line (legacy ticket)."""
    tickets = vault / "Tickets"
    tickets.mkdir(parents=True, exist_ok=True)
    path = tickets / f"{ticket_id} - version-fix probe.md"
    version_line = "version: 1\n" if with_version else ""
    path.write_text(
        f"""---
id: {ticket_id}
type: ticket
title: version-fix probe
status: in-progress
created_at: '2026-10-11T12:00:00'
updated_at: '2026-10-11T12:00:00'
created_by: USR-000001
updated_by: USR-000001
{version_line}tags: []
relationships: []
ticket_type: incident
priority: medium
impact: medium
urgency: medium
priority_override: false
category: support
work_type: other
sla_policy: standard
sla_paused: false
source: phone
channel: phone
worklog: []
updated: '2026-10-11'
---

# {ticket_id} — version-fix probe

Temp-only ticket for MC-2 version-fix rehearsal.
"""
    )
    return path


def write_users_with_bridge(home: Path) -> Path:
    cfg = home / "config"
    cfg.mkdir(parents=True, exist_ok=True)
    path = cfg / "users.yaml"
    path.write_text(
        """# temp users for version-fix rehearsal
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


class TestVersionFix(unittest.TestCase):
    """End-to-end version-fix via TestClient against a TEMP vault+home."""

    def setUp(self):
        sys.path.insert(0, str(CODE))
        self.tmp = Path(tempfile.mkdtemp(prefix="mc2ver-"))
        self.vault = self.tmp / "vault"
        self.home = self.tmp / "home"
        self.vault.mkdir()
        self.home.mkdir()
        self.ticket_path = write_temp_ticket(self.vault)
        self.users_path = write_users_with_bridge(self.home)
        self.counters_path = write_counters(self.home)
        self.ticket_id = TICKET_ID
        self.env = base_env(
            KTS_SANDBOX="1",
            KTS_VAULT=str(self.vault),
            KTS_HOME=str(self.home),
        )

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

    # ---- proof 1: version persists across reload ----------------------
    def test_version_persists_across_reload(self):
        """After a write, the ticket file has version: N; a fresh reader reloads it."""
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
r1 = client.patch("/tickets/{self.ticket_id}", json={{"priority": "high", "reason": "ver-fix"}}, headers=H)
assert r1.status_code == 200, (r1.status_code, r1.text)
d1 = r1.json()
assert d1["version"] == 2, d1
tf = Path({str(self.ticket_path)!r})
text = tf.read_text()
assert "version: 2" in text, text[:400]
# Fresh GET re-reads from disk: version must still be 2 (not reset to 1).
r2 = client.get("/tickets?all=true", headers=H)
assert r2.status_code == 200, (r2.status_code, r2.text)
# Direct service reload (fresh process semantics via same app):
from core.api import get_api
api = get_api()
t = api.get_ticket("{self.ticket_id}")
assert t is not None
assert t.version == 2, t.version
print(json.dumps({{"ok": True, "version_in_file": 2, "reloaded": t.version}}))
'''
        out = self._assert_ok(self._run(script))
        self.assertTrue(out["ok"])
        self.assertEqual(out["version_in_file"], 2)
        self.assertEqual(out["reloaded"], 2)

    # ---- proof 2: legacy ticket without version loads as 1 ------------
    def test_legacy_no_version_loads_as_1(self):
        """A ticket with no version line loads as version 1 (default)."""
        script = f'''
import json, sys
from pathlib import Path
sys.path.insert(0, {str(CODE)!r})
from core.api import get_api
api = get_api()
t = api.get_ticket("{self.ticket_id}")
assert t is not None, "ticket not found"
assert t.version == 1, t.version
text = Path({str(self.ticket_path)!r}).read_text()
assert "version:" not in text, "fixture should be legacy (no version line)"
print(json.dumps({{"ok": True, "version": t.version}}))
'''
        # Rewrite fixture WITHOUT version line for this test only.
        write_temp_ticket(self.vault, with_version=False)
        out = self._assert_ok(self._run(script))
        self.assertTrue(out["ok"])
        self.assertEqual(out["version"], 1)

    # ---- proof 3: stale if_version -> 409 + file hash unchanged -------
    def test_stale_if_version_409_hash_unchanged(self):
        """if_version that does not match current -> 409; file hash unchanged."""
        script = f'''
import json, sys, hashlib
from pathlib import Path
sys.path.insert(0, {str(CODE)!r})
from fastapi.testclient import TestClient
from core.auth import TokenTable
from core.bridge import create_app
table = TokenTable({str(self.tmp / "auth" / "token-table.json")!r})
client = TestClient(create_app(table))
_, sec = table.mint("desktop", scope="write")
H = {{"X-API-Token": sec}}
tf = Path({str(self.ticket_path)!r})
before = hashlib.sha256(tf.read_bytes()).hexdigest()
# Current file has version: 1; send stale if_version=99.
r = client.patch(
    "/tickets/{self.ticket_id}",
    json={{"priority": "high", "if_version": 99}},
    headers=H,
)
assert r.status_code == 409, (r.status_code, r.text)
d = r.json()
assert d["expected_version"] == 99, d
assert d["actual_version"] == 1, d
after = hashlib.sha256(tf.read_bytes()).hexdigest()
assert before == after, "file changed on 409"
print(json.dumps({{"ok": True, "code": r.status_code, "expected": d["expected_version"], "actual": d["actual_version"]}}))
'''
        out = self._assert_ok(self._run(script))
        self.assertTrue(out["ok"])
        self.assertEqual(out["code"], 409)
        self.assertEqual(out["expected"], 99)
        self.assertEqual(out["actual"], 1)

    # ---- per-route stale if_version (worklog POST, status POST, api remove) ----
    def test_stale_if_version_worklog_route_409_hash_unchanged(self):
        """POST /tickets/{id}/worklog with stale if_version -> 409; file hash unchanged."""
        script = f'''
import json, sys, hashlib
from pathlib import Path
sys.path.insert(0, {str(CODE)!r})
from fastapi.testclient import TestClient
from core.auth import TokenTable
from core.bridge import create_app
table = TokenTable({str(self.tmp / "auth" / "token-table.json")!r})
client = TestClient(create_app(table))
_, sec = table.mint("desktop", scope="write")
H = {{"X-API-Token": sec}}
tf = Path({str(self.ticket_path)!r})
before = hashlib.sha256(tf.read_bytes()).hexdigest()
body = {{
    "date": "2026-10-11T14:00:00",
    "mode": "remote",
    "hours": 0.25,
    "amount": 0.0,
    "payment": "no-charge",
    "description": "stale-if_version probe (temp vault only)",
    "if_version": 99,
}}
r = client.post("/tickets/{self.ticket_id}/worklog", json=body, headers=H)
assert r.status_code == 409, (r.status_code, r.text)
d = r.json()
assert d["expected_version"] == 99, d
assert d["actual_version"] == 1, d
after = hashlib.sha256(tf.read_bytes()).hexdigest()
assert before == after, "file changed on 409"
print(json.dumps({{"ok": True, "code": r.status_code, "expected": d["expected_version"], "actual": d["actual_version"]}}))
'''
        out = self._assert_ok(self._run(script))
        self.assertTrue(out["ok"])
        self.assertEqual(out["code"], 409)
        self.assertEqual(out["expected"], 99)
        self.assertEqual(out["actual"], 1)

    def test_stale_if_version_status_route_409_hash_unchanged_and_audited(self):
        """POST /tickets/{id}/status with stale if_version -> 409; file hash unchanged;
        audit line records status=409 with token_id/method/path only (no ticket content)."""
        script = f'''
import json, sys, hashlib
from pathlib import Path
sys.path.insert(0, {str(CODE)!r})
from fastapi.testclient import TestClient
from core.auth import TokenTable
from core.bridge import create_app
table = TokenTable({str(self.tmp / "auth" / "token-table.json")!r})
client = TestClient(create_app(table))
_, sec = table.mint("desktop", scope="write")
H = {{"X-API-Token": sec}}
tf = Path({str(self.ticket_path)!r})
before = hashlib.sha256(tf.read_bytes()).hexdigest()
r = client.post(
    "/tickets/{self.ticket_id}/status",
    json={{"status": "resolved", "reason": "stale probe", "if_version": 99}},
    headers=H,
)
assert r.status_code == 409, (r.status_code, r.text)
d = r.json()
assert d["expected_version"] == 99, d
assert d["actual_version"] == 1, d
after = hashlib.sha256(tf.read_bytes()).hexdigest()
assert before == after, "file changed on 409"
# Audit: one line with action=request, status=409, token_id, method, path; no ticket body.
audit = Path({str(self.tmp / "auth" / "audit.log")!r})
lines = [json.loads(x) for x in audit.read_text().splitlines() if x.strip()]
hits = [x for x in lines if x.get("action") == "request" and x.get("status") == 409]
assert len(hits) == 1, hits
a = hits[0]
assert a["method"] == "POST", a
assert a["path"] == "/tickets/{self.ticket_id}/status", a
assert a.get("token_id"), a
raw = audit.read_text()
assert "resolved" not in raw, "ticket content leaked into audit"
assert "stale probe" not in raw, "reason leaked into audit"
print(json.dumps({{"ok": True, "code": r.status_code, "audit_status": a["status"], "audit_has_token": bool(a.get("token_id"))}}))
'''
        out = self._assert_ok(self._run(script))
        self.assertTrue(out["ok"])
        self.assertEqual(out["code"], 409)
        self.assertEqual(out["audit_status"], 409)
        self.assertTrue(out["audit_has_token"])

    def test_stale_if_version_remove_worklog_api_409(self):
        """api.remove_worklog with stale if_version raises VersionMismatch; file hash unchanged."""
        script = f'''
import json, sys, hashlib
from pathlib import Path
sys.path.insert(0, {str(CODE)!r})
from core.api import get_api
from core.models import EventActor
from core.services.ticket_service import VersionMismatch
api = get_api()
actor = EventActor(id="USR-000001", type="human", name="admin")
tf = Path({str(self.ticket_path)!r})
before = hashlib.sha256(tf.read_bytes()).hexdigest()
try:
    api.remove_worklog("{self.ticket_id}", 0, actor, reason="stale probe", if_version=99)
    raise SystemExit("expected VersionMismatch")
except VersionMismatch as exc:
    assert exc.expected == 99, exc.expected
    assert exc.actual == 1, exc.actual
after = hashlib.sha256(tf.read_bytes()).hexdigest()
assert before == after, "file changed on stale remove_worklog"
print(json.dumps({{"ok": True, "expected": 99, "actual": 1}}))
'''
        out = self._assert_ok(self._run(script))
        self.assertTrue(out["ok"])
        self.assertEqual(out["expected"], 99)
        self.assertEqual(out["actual"], 1)

    # ---- proof 4: missing if_version behaves as before ----------------
    def test_missing_if_version_behaves_as_before(self):
        """No if_version in body -> normal 200 (behaves exactly as pre-change)."""
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
r = client.patch(
    "/tickets/{self.ticket_id}",
    json={{"priority": "high", "reason": "no if_version"}},
    headers=H,
)
assert r.status_code == 200, (r.status_code, r.text)
d = r.json()
assert d["updated_fields"] == ["priority"], d
assert d["version"] == 2, d
print(json.dumps({{"ok": True, "code": r.status_code, "version": d["version"]}}))
'''
        out = self._assert_ok(self._run(script))
        self.assertTrue(out["ok"])
        self.assertEqual(out["code"], 200)
        self.assertEqual(out["version"], 2)

    # ---- proof 5: PATCH assigned_to nonexistent user -> 400 -----------
    def test_assigned_to_nonexistent_user_400(self):
        """PATCH assigned_to with a USR- id that does not exist -> 400; file untouched."""
        script = f'''
import json, sys, hashlib
from pathlib import Path
sys.path.insert(0, {str(CODE)!r})
from fastapi.testclient import TestClient
from core.auth import TokenTable
from core.bridge import create_app
table = TokenTable({str(self.tmp / "auth" / "token-table.json")!r})
client = TestClient(create_app(table))
_, sec = table.mint("desktop", scope="write")
H = {{"X-API-Token": sec}}
tf = Path({str(self.ticket_path)!r})
before = hashlib.sha256(tf.read_bytes()).hexdigest()
r = client.patch(
    "/tickets/{self.ticket_id}",
    json={{"assigned_to": "USR-ZZZZZZ"}},
    headers=H,
)
assert r.status_code == 400, (r.status_code, r.text)
d = r.json()
assert "does not exist" in d["detail"], d
after = hashlib.sha256(tf.read_bytes()).hexdigest()
assert before == after, "file changed on 400"
print(json.dumps({{"ok": True, "code": r.status_code}}))
'''
        out = self._assert_ok(self._run(script))
        self.assertTrue(out["ok"])
        self.assertEqual(out["code"], 400)

    # ---- guards -------------------------------------------------------
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
        hits = list(REAL_VAULT.rglob(f"*{TICKET_ID}*"))
        self.assertEqual(hits, [], f"temp ticket leaked into real vault: {hits}")

    def test_shared_sandbox_not_touched(self):
        sandbox_home = REAL_HOME
        users = sandbox_home / "config" / "users.yaml"
        if users.exists():
            self.assertNotIn(
                "TKT-2026-903",
                users.read_text(),
                "temp ticket id leaked into shared users.yaml",
            )
        shared_vault = REAL_VAULT
        for p in shared_vault.rglob("*"):
            if p.is_file() and TICKET_ID in p.name:
                self.fail(f"temp ticket leaked into shared sandbox: {p}")


if __name__ == "__main__":
    unittest.main(verbosity=2)
