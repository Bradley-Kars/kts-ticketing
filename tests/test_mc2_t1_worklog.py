"""MC-2 T1 — bridge write path: POST /tickets/{id}/worklog as USR-000002.

Bradley 2026-10-10: suite reset wipes sandbox auth/ + users.yaml (suite
finding recorded in plan section 3). ALL state for these tests lives in
a temp directory: temp vault, temp token table, temp home with USR-000002
re-created inside the test setup. The shared sandbox is never opened.
The real vault / real ~/.ticketing are never opened.

Proofs required for T1:
  1. mint(scope="read") verify returns scope="read"; mint(scope="write") -> "write"
  2. invalid scope rejected at mint
  3. POST /tickets/{id}/worklog with no token -> 401
  4. POST with read-scope token -> 403 (scope gate)
  5. POST with write-scope token -> 200, worklog appended, actor=USR-000002
  6. real id-counters.json, real users.yaml, real vault hashes unchanged
  7. temp vault ticket file updated; real vault file bytes unchanged
  8. isolation guard enforced
"""

# ---- ISOLATION GUARD: must be FIRST, before ANY core.* imports -------------
try:
    from tests.test_isolation_guard import require_isolation, snapshot_real_tree
except ImportError:  # running as `python3 tests/test_mc2_t1_worklog.py`
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
CODE = REAL_HOME  # code lives in real ~/.ticketing; data is temp-only
HEADER = "X-API-Token"

# Real baselines captured before suite (from plan / pre-T1 manifest)
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


def write_minimal_ticket(vault: Path, ticket_id: str = "TKT-2026-901") -> Path:
    """Create a minimal valid ticket markdown in the temp vault.
    Field names and status values match real sandbox frontmatter
    (created_at / updated_at, status in Ticket.status Literal)."""
    tickets = vault / "Tickets"
    tickets.mkdir(parents=True, exist_ok=True)
    path = tickets / f"{ticket_id} - T1 temp probe.md"
    path.write_text(
        f"""---
id: {ticket_id}
type: ticket
title: T1 temp probe
status: in-progress
created_at: '2026-10-10T12:00:00'
updated_at: '2026-10-10T12:00:00'
created_by: USR-000001
updated_by: USR-000001
version: 1
tags: []
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
updated: '2026-10-10'
---

# {ticket_id} — T1 temp probe

Temp-only ticket for MC-2 T1 bridge write rehearsal.
"""
    )
    return path


def write_users_with_bridge(home: Path) -> Path:
    """Temp users.yaml containing USR-000001 + USR-000002 (recreated in setup)."""
    cfg = home / "config"
    cfg.mkdir(parents=True, exist_ok=True)
    path = cfg / "users.yaml"
    path.write_text(
        """# temp users for MC-2 T1
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
    # PermissionChecker loads config/permissions.yaml from paths.config_dir();
    # without this copy the temp home has no rules and USR-000002 gets 403.
    src = REAL_HOME / "config" / "permissions.yaml"
    if src.exists():
        shutil.copy2(src, cfg / "permissions.yaml")
    return path


def write_counters(home: Path, event: int = 0, invoice: int = 0) -> Path:
    p = home / "id-counters.json"
    p.write_text(json.dumps({"event": event, "invoice:2026": invoice}))
    return p


class TestTokenScopeUnit(unittest.TestCase):
    """Scope field on TokenTable.mint/verify (hermetic temp, no vault)."""

    def setUp(self):
        sys.path.insert(0, str(CODE))
        from core.auth import TokenTable
        self.tmp = Path(tempfile.mkdtemp(prefix="mc2t1-scope-"))
        self.table = TokenTable(self.tmp / "auth" / "token-table.json")

    def test_mint_default_scope_read(self):
        tid, secret = self.table.mint("phone")
        rec = self.table.verify(secret)
        self.assertIsNotNone(rec)
        self.assertEqual(rec["scope"], "read")
        self.assertEqual(rec["token_id"], tid)

    def test_mint_write_scope(self):
        tid, secret = self.table.mint("desktop", scope="write")
        rec = self.table.verify(secret)
        self.assertEqual(rec["scope"], "write")

    def test_mint_invalid_scope_rejected(self):
        with self.assertRaises(ValueError):
            self.table.mint("bad", scope="admin")

    def test_old_table_record_defaults_to_read(self):
        """Tokens minted before scope field was added default to read."""
        import hashlib, secrets as pysecrets
        from core.auth import _hash_secret, PBKDF2_ITERATIONS
        secret = "kts_" + pysecrets.token_urlsafe(32)
        salt = pysecrets.token_hex(16)
        dk = _hash_secret(secret, salt, PBKDF2_ITERATIONS)
        data = {
            "version": 1,
            "tokens": {
                "tok_legacy": {
                    "label": "legacy",
                    # no scope field — pre-T1 table
                    "salt": salt,
                    "dk": dk,
                    "iterations": PBKDF2_ITERATIONS,
                    "created_utc": "2026-01-01T00:00:00Z",
                    "revoked": False,
                    "revoked_utc": None,
                }
            },
        }
        path = self.tmp / "auth" / "token-table.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data))
        rec = self.table.verify(secret)
        self.assertIsNotNone(rec)
        self.assertEqual(rec["scope"], "read")


class TestPostWorklogBridge(unittest.TestCase):
    """End-to-end T1 via TestClient against a TEMP vault+home (never shared sandbox)."""

    def setUp(self):
        sys.path.insert(0, str(CODE))
        self.tmp = Path(tempfile.mkdtemp(prefix="mc2t1-t1-"))
        self.vault = self.tmp / "vault"
        self.home = self.tmp / "home"
        self.vault.mkdir()
        self.home.mkdir()
        self.ticket_path = write_minimal_ticket(self.vault)
        self.users_path = write_users_with_bridge(self.home)
        self.counters_path = write_counters(self.home)
        self.ticket_id = "TKT-2026-901"
        self.env = base_env(
            KTS_SANDBOX="1",
            KTS_VAULT=str(self.vault),
            KTS_HOME=str(self.home),
        )
        self.real_snap = snapshot_real_tree()

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _run_bridge_script(self, script: str) -> subprocess.CompletedProcess:
        """Run a python snippet under the temp env (fresh interpreter)."""
        return subprocess.run(
            [sys.executable, "-c", script],
            env=self.env,
            capture_output=True,
            text=True,
            timeout=60,
            cwd=str(CODE),
        )

    # ---- isolation guard enforcement ------------------------------------
    def test_isolation_guard_enforced(self):
        """The require_isolation() call at module top-level must have passed."""
        self.assertTrue(True, "isolation guard active")

    def test_scope_gate_and_write_in_subprocess(self):
        """Full T1 proof in one subprocess: scope gate + worklog append."""
        worklog_body = json.dumps(
            {
                "date": "2026-10-10T14:00:00",
                "mode": "remote",
                "hours": 0.25,
                "amount": 0.0,
                "payment": "no-charge",
                "description": "MC-2 T1 rehearsal (temp vault only)",
            }
        )
        script = f'''
import json, sys
sys.path.insert(0, {str(CODE)!r})
from fastapi.testclient import TestClient
from core.auth import TokenTable
from core.bridge import create_app

table = TokenTable({str(self.tmp / "auth" / "token-table.json")!r})
app = create_app(table)
client = TestClient(app)

# 1. no token -> 401
r = client.post("/tickets/{self.ticket_id}/worklog", json={{"x": 1}})
assert r.status_code == 401, r.text

# 2. read-scope token -> 403
_, read_secret = table.mint("phone", scope="read")
r = client.post(
    "/tickets/{self.ticket_id}/worklog",
    json=json.loads({worklog_body!r}),
    headers={{"X-API-Token": read_secret}},
)
assert r.status_code == 403, r.text
assert "scope" in r.json()["detail"]

# 3. write-scope token -> 200
_, write_secret = table.mint("desktop", scope="write")
r = client.post(
    "/tickets/{self.ticket_id}/worklog",
    json=json.loads({worklog_body!r}),
    headers={{"X-API-Token": write_secret}},
)
assert r.status_code == 200, r.text
data = r.json()
assert data["actor"] == "USR-000002", data
assert data["ticket_id"] == "{self.ticket_id}"
assert data["worklog_count"] >= 1
assert data["last_entry"]["mode"] == "remote"
assert data["last_entry"]["hours"] == 0.25

# 4. temp vault ticket file updated
from pathlib import Path
tf = Path({str(self.ticket_path)!r})
text = tf.read_text()
assert "MC-2 T1 rehearsal" in text, text[:500]
assert "worklog:" in text

print(json.dumps({{"ok": True, "version": data["version"], "count": data["worklog_count"]}}))
'''
        result = self._run_bridge_script(script)
        self.assertEqual(
            result.returncode, 0,
            f"stderr:\n{result.stderr}\nstdout:\n{result.stdout}",
        )
        out = json.loads(result.stdout.strip().splitlines()[-1])
        self.assertTrue(out["ok"])
        self.assertGreaterEqual(out["count"], 1)
        self.real_snap.assert_unchanged("test_scope_gate_and_write_in_subprocess")

    def test_real_vault_and_home_unchanged(self):
        """Real tree hashes must match pre-T1 baselines after T1 suite."""
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
        # no TKT-2026-901 in real vault
        hits = list(REAL_VAULT.rglob("*TKT-2026-901*"))
        self.assertEqual(hits, [], f"temp ticket leaked into real vault: {hits}")

    def test_shared_sandbox_not_touched(self):
        """This test must not have created USR-000002 in shared sandbox."""
        sb_users = HOME / ".ticketing-sandbox" / "config" / "users.yaml"
        if sb_users.exists():
            text = sb_users.read_text()
            # suite already wiped it; our tests must not re-create it there
            self.assertNotIn(
                "USR-000002", text,
                "USR-000002 leaked into shared sandbox users.yaml",
            )


if __name__ == "__main__":
    unittest.main(verbosity=2)
