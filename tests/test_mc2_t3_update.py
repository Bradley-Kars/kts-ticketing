"""MC-2 T3 — bridge write path: PATCH /tickets/{id} as USR-000002 (updateTicket).

Bradley 2026-10-11 (T3 go): same gates as T1/T2 — sandbox-only (temp vault,
temp home, temp token table); real hashes before and after; one final report
after T3 only. The shared sandbox is never opened. The real vault / real
~/.ticketing are never opened. Field edits only — no status (T2), no worklog
(T1), no numbering (T4+, out of scope).

Endpoint under test (stated up front per T3 rule):
  PATCH /tickets/{ticket_id}  ->  KTSAPI.update_ticket (core/api.py),
  service ticket.update (core/services/ticket_service.py), actor USR-000002.

Proofs required for T3 (by test name):
  1. no token -> 401                      -> test_no_token_401
  2. write-scope token -> 200             -> test_write_scope_update_200
  3. read-only or revoked refused         -> test_read_only_refused_403,
                                             test_revoked_refused_401
  4. bad payload -> 422, ticket file hash
     unchanged                            -> test_bad_payload_422_ticket_file_hash_unchanged
  5. audit line holds only token ID, method,
     path, status (+ ts/label/latency)    -> test_audit_line_holds_token_id_method_path_status
  + before/after state by ID with a revert -> test_field_edit_before_after_revert_by_id
  + real tree / shared sandbox untouched   -> test_real_vault_and_home_unchanged,
                                             test_shared_sandbox_not_touched

Temp counters DO change (ticket.update allocates an event ID inside the temp
home); real id-counters.json must stay byte-identical.
"""

# ---- ISOLATION GUARD: must be FIRST, before ANY core.* imports -------------
try:
    from tests.test_isolation_guard import require_isolation, snapshot_real_tree
except ImportError:  # running as `python3 tests/test_mc2_t3_update.py`
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

# Real baselines (unchanged across T1/T2/T3 manifests).
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


def write_temp_ticket(vault: Path, ticket_id: str = TICKET_ID) -> Path:
    """Minimal valid ticket markdown in the temp vault.
    Initial editable state for the T3 proof: priority=medium, next_action absent."""
    tickets = vault / "Tickets"
    tickets.mkdir(parents=True, exist_ok=True)
    path = tickets / f"{ticket_id} - T3 temp probe.md"
    path.write_text(
        f"""---
id: {ticket_id}
type: ticket
title: T3 temp probe
status: in-progress
created_at: '2026-10-11T12:00:00'
updated_at: '2026-10-11T12:00:00'
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
updated: '2026-10-11'
---

# {ticket_id} — T3 temp probe

Temp-only ticket for MC-2 T3 bridge field-edit rehearsal.
"""
    )
    return path


def write_users_with_bridge(home: Path) -> Path:
    """Temp users.yaml containing USR-000001 + USR-000002 (recreated in setup)."""
    cfg = home / "config"
    cfg.mkdir(parents=True, exist_ok=True)
    path = cfg / "users.yaml"
    path.write_text(
        """# temp users for MC-2 T3
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


class TestUpdateFieldsBridge(unittest.TestCase):
    """End-to-end T3 via TestClient against a TEMP vault+home (never shared sandbox)."""

    def setUp(self):
        sys.path.insert(0, str(CODE))
        self.tmp = Path(tempfile.mkdtemp(prefix="mc2t3-t3-"))
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
        self.real_snap = snapshot_real_tree()

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)
        # Real-tree check after EVERY test: any write to the real vault,
        # real counters or real users.yaml fails here with context.
        self.real_snap.assert_unchanged(self._testMethodName)

    def _run(self, script: str) -> subprocess.CompletedProcess:
        """Run a python snippet under the temp env (fresh interpreter)."""
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

    # ---- proof 1: no token -> 401 ------------------------------------
    def test_no_token_401(self):
        """Proof 1: PATCH /tickets/{id} with no token -> 401, file untouched."""
        script = f'''
import json, sys, hashlib
from pathlib import Path
sys.path.insert(0, {str(CODE)!r})
from fastapi.testclient import TestClient
from core.auth import TokenTable
from core.bridge import create_app
table = TokenTable({str(self.tmp / "auth" / "token-table.json")!r})
client = TestClient(create_app(table))
tf = Path({str(self.ticket_path)!r})
before = hashlib.sha256(tf.read_bytes()).hexdigest()
r = client.patch("/tickets/{self.ticket_id}", json={{"priority": "high"}})
assert r.status_code == 401, (r.status_code, r.text)
assert "token" in r.json()["detail"], r.text
after = hashlib.sha256(tf.read_bytes()).hexdigest()
assert before == after, "ticket file changed on 401"
print(json.dumps({{"ok": True, "code": r.status_code}}))
'''
        out = self._assert_ok(self._run(script))
        self.assertTrue(out["ok"])
        self.assertEqual(out["code"], 401)

    # ---- proof 2: write-scope token -> 200 ---------------------------
    def test_write_scope_update_200(self):
        """Proof 2: write-scope token -> 200; multi-field edit applied as USR-000002."""
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
r = client.patch(
    "/tickets/{self.ticket_id}",
    json={{
        "priority": "high",
        "category": "security",
        "next_action": "T3 multi-field edit (temp vault only)",
        "tags": ["t3", "temp"],
        "reason": "MC-2 T3 rehearsal (temp vault only)",
    }},
    headers={{"X-API-Token": sec}},
)
assert r.status_code == 200, (r.status_code, r.text)
d = r.json()
assert d["ticket_id"] == "{self.ticket_id}", d
assert d["actor"] == "USR-000002", d
assert d["updated_by"] == "USR-000002", d
assert d["version"] == 2, d
assert d["updated_fields"] == ["category", "next_action", "priority", "tags"], d
text = Path({str(self.ticket_path)!r}).read_text()
assert "priority: high" in text, text[:500]
assert "category: security" in text, text[:500]
assert "next_action: T3 multi-field edit (temp vault only)" in text, text[:500]
assert "updated_by: USR-000002" in text, text[:500]
print(json.dumps({{
    "ok": True,
    "updated_fields": d["updated_fields"],
    "version": d["version"],
    "actor": d["actor"],
}}))
'''
        out = self._assert_ok(self._run(script))
        self.assertTrue(out["ok"])
        self.assertEqual(
            out["updated_fields"],
            ["category", "next_action", "priority", "tags"],
        )
        self.assertEqual(out["version"], 2)
        self.assertEqual(out["actor"], "USR-000002")

    # ---- proof 3a: read-only refused -> 403 --------------------------
    def test_read_only_refused_403(self):
        """Proof 3: read-scope token -> 403 on PATCH, file untouched."""
        script = f'''
import json, sys, hashlib
from pathlib import Path
sys.path.insert(0, {str(CODE)!r})
from fastapi.testclient import TestClient
from core.auth import TokenTable
from core.bridge import create_app
table = TokenTable({str(self.tmp / "auth" / "token-table.json")!r})
client = TestClient(create_app(table))
_, sec = table.mint("phone", scope="read")
tf = Path({str(self.ticket_path)!r})
before = hashlib.sha256(tf.read_bytes()).hexdigest()
r = client.patch(
    "/tickets/{self.ticket_id}",
    json={{"priority": "high"}},
    headers={{"X-API-Token": sec}},
)
assert r.status_code == 403, (r.status_code, r.text)
assert "scope" in r.json()["detail"], r.text
after = hashlib.sha256(tf.read_bytes()).hexdigest()
assert before == after, "ticket file changed on 403"
print(json.dumps({{"ok": True, "code": r.status_code}}))
'''
        out = self._assert_ok(self._run(script))
        self.assertTrue(out["ok"])
        self.assertEqual(out["code"], 403)

    # ---- proof 3b: revoked refused -> 401 ----------------------------
    def test_revoked_refused_401(self):
        """Proof 3: revoked write token -> 401 on PATCH, file untouched."""
        script = f'''
import json, sys, hashlib
from pathlib import Path
sys.path.insert(0, {str(CODE)!r})
from fastapi.testclient import TestClient
from core.auth import TokenTable
from core.bridge import create_app
table = TokenTable({str(self.tmp / "auth" / "token-table.json")!r})
client = TestClient(create_app(table))
tid, sec = table.mint("laptop", scope="write")
assert table.revoke(tid), "revoke failed"
tf = Path({str(self.ticket_path)!r})
before = hashlib.sha256(tf.read_bytes()).hexdigest()
r = client.patch(
    "/tickets/{self.ticket_id}",
    json={{"priority": "high"}},
    headers={{"X-API-Token": sec}},
)
assert r.status_code == 401, (r.status_code, r.text)
after = hashlib.sha256(tf.read_bytes()).hexdigest()
assert before == after, "ticket file changed on 401 (revoked)"
print(json.dumps({{"ok": True, "code": r.status_code}}))
'''
        out = self._assert_ok(self._run(script))
        self.assertTrue(out["ok"])
        self.assertEqual(out["code"], 401)

    # ---- proof 4: bad payload -> 422, ticket file hash unchanged -----
    def test_bad_payload_422_ticket_file_hash_unchanged(self):
        """Proof 4: bad payloads -> 422 each; ticket file + counters byte-identical.

        Covers unknown key, forbidden keys (status/worklog steered to their
        routes), empty title, out-of-range title, bogus enum, wrong tag type,
        bad datetime, bad USR id, non-string reason, empty/absent field list,
        and a non-object body — all rejected before any service call.
        """
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
cf = Path({str(self.counters_path)!r})
def sha(p): return hashlib.sha256(p.read_bytes()).hexdigest()
sha_before = sha(tf)
counters_before = cf.read_bytes()
P = "/tickets/{self.ticket_id}"
cases = [
    ("missing body", None, False),
    ("empty object", {{}}, True),
    ("json list instead of object", [1, 2], True),
    ("no editable fields (reason only)", {{"reason": "no fields"}}, True),
    ("unknown field", {{"bogus": "x"}}, True),
    ("forbidden field status", {{"status": "resolved"}}, True),
    ("forbidden field worklog", {{"worklog": [{{"action": "x"}}]}}, True),
    ("empty title", {{"title": ""}}, True),
    ("title too long", {{"title": "x" * 201}}, True),
    ("title not a string", {{"title": 42}}, True),
    ("priority bogus", {{"priority": "urgent"}}, True),
    ("priority not a string", {{"priority": 3}}, True),
    ("tags not a list", {{"tags": "t3"}}, True),
    ("tags list of non-strings", {{"tags": [1]}}, True),
    ("due not a datetime", {{"due": "not-a-date"}}, True),
    ("assigned_to bad pattern", {{"assigned_to": "nope"}}, True),
    ("sla_paused not a boolean", {{"sla_paused": "yes"}}, True),
    ("next_action not a string", {{"next_action": 1}}, True),
    ("reason not a string", {{"priority": "high", "reason": 7}}, True),
]
codes = []
for name, payload, with_body in cases:
    if with_body:
        r = client.patch(P, json=payload, headers=H)
    else:
        r = client.patch(P, headers=H)
    codes.append(r.status_code)
    assert r.status_code == 422, (name, r.status_code, r.text)
assert sha(tf) == sha_before, "TICKET FILE CHANGED ON BAD PAYLOAD"
assert cf.read_bytes() == counters_before, "temp counters changed on bad payload"
print(json.dumps({{"ok": True, "codes": codes, "cases": len(cases)}}))
'''
        out = self._assert_ok(self._run(script))
        self.assertTrue(out["ok"])
        self.assertEqual(out["cases"], 19)
        self.assertEqual(out["codes"], [422] * 19)

    # ---- proof 5: audit line fields ----------------------------------
    def test_audit_line_holds_token_id_method_path_status(self):
        """Proof 5: request lines hold ONLY token_id/method/path/status
        (+ ts_utc/label/latency_ms/action); auth_fail lines hold method/
        path/status without token_id; secrets never appear in the file."""
        script = f'''
import json, sys
from pathlib import Path
sys.path.insert(0, {str(CODE)!r})
from fastapi.testclient import TestClient
from core.auth import TokenTable
from core.bridge import create_app
table = TokenTable({str(self.tmp / "auth" / "token-table.json")!r})
client = TestClient(create_app(table))
_, read_sec = table.mint("phone", scope="read")
write_tid, write_sec = table.mint("desktop", scope="write")
rev_tid, rev_sec = table.mint("laptop", scope="write")
assert table.revoke(rev_tid)
H = lambda s: {{"X-API-Token": s}}
P = "/tickets/{self.ticket_id}"
r1 = client.patch(P, json={{"priority": "high"}})                        # 401 no token
r2 = client.patch(P, json={{"priority": "high"}}, headers=H(read_sec))   # 403 read
r3 = client.patch(P, json={{"priority": "high"}}, headers=H(rev_sec))    # 401 revoked
r4 = client.patch(P, json={{"priority": "bogus"}}, headers=H(write_sec)) # 422 bad payload
r5 = client.patch(P, json={{"priority": "high"}}, headers=H(write_sec))  # 200 edit
codes = [r1.status_code, r2.status_code, r3.status_code, r4.status_code, r5.status_code]
assert codes == [401, 403, 401, 422, 200], codes

audit = Path({str(self.tmp / "auth" / "audit.log")!r})
lines = [json.loads(l) for l in audit.read_text().splitlines() if l.strip()]
actions = {{l["action"] for l in lines}}
assert actions <= {{"mint", "revoke", "request", "auth_fail"}}, actions
req = [l for l in lines if l["action"] == "request"]
fail = [l for l in lines if l["action"] == "auth_fail"]

REQ_KEYS = {{"ts_utc", "action", "token_id", "label", "method", "path", "status", "latency_ms"}}
FAIL_KEYS = {{"ts_utc", "action", "method", "path", "status", "latency_ms", "source", "reason"}}
for l in req:
    assert set(l.keys()) == REQ_KEYS, ("request line keys", sorted(l.keys()))
for l in fail:
    assert set(l.keys()) == FAIL_KEYS, ("auth_fail line keys", sorted(l.keys()))

# the five mutating attempts that passed auth are request lines (422 + 200
# carry token_id; 401/403 never reach success audit)
assert len(req) == 2, req
good = [l for l in req if l["status"] == 200]
assert len(good) == 1, good
g = good[0]
assert g["method"] == "PATCH", g
assert g["path"] == P, g
assert g["token_id"] == write_tid, "token_id on the 200 line is wrong"
assert g["label"] == "desktop", g

# auth_fail lines: method/path/status present, NO token_id ever
assert len(fail) == 3, fail
assert all("token_id" not in l for l in fail), fail
assert {{l["status"] for l in fail}} == {{401, 403}}, fail
assert all(l["method"] == "PATCH" and l["path"] == P for l in fail), fail

# secrets never appear anywhere in the audit file
raw = audit.read_text()
for sec in (read_sec, write_sec, rev_sec):
    assert sec not in raw, "SECRET LEAKED INTO AUDIT FILE"

print(json.dumps({{
    "ok": True,
    "codes": codes,
    "request_lines": len(req),
    "auth_fail_lines": len(fail),
    "total_lines": len(lines),
    "method": g["method"],
    "req_keys": sorted(REQ_KEYS),
    "fail_keys": sorted(FAIL_KEYS),
    "token_id_present": bool(g["token_id"]),
    "token_id_len": len(g["token_id"]),
}}))
'''
        out = self._assert_ok(self._run(script))
        self.assertTrue(out["ok"])
        self.assertEqual(out["codes"], [401, 403, 401, 422, 200])
        self.assertEqual(out["request_lines"], 2)
        self.assertEqual(out["auth_fail_lines"], 3)
        self.assertEqual(out["method"], "PATCH")
        self.assertEqual(
            out["req_keys"],
            ["action", "label", "latency_ms", "method", "path", "status", "token_id", "ts_utc"],
        )
        self.assertEqual(
            out["fail_keys"],
            ["action", "latency_ms", "method", "path", "reason", "source", "status", "ts_utc"],
        )
        self.assertTrue(out["token_id_present"])

    # ---- before/after state by ID with a revert ----------------------
    def test_field_edit_before_after_revert_by_id(self):
        """State proof: priority medium -> high -> medium by ID; every response
        carries the ticket ID; temp counters grow (temp-only, expected).

        Version note (fixed by version-fix 2026-10-11, folded into T3 scope):
        _serialize_for_yaml now persists `version` to frontmatter. First edit
        responds with version 2 and the file carries `version: 2`; the revert
        bumps version to 3 (persisted, not reset to 2).
        """
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
P = "/tickets/{self.ticket_id}"
tf = Path({str(self.ticket_path)!r})
def field_of(name):
    for line in tf.read_text().splitlines():
        if line.startswith(name + ": "):
            return line.split(": ", 1)[1]
    return None

before = field_of("priority")
assert before == "medium", before

# 1. edit priority -> high
r1 = client.patch(P, json={{"priority": "high", "reason": "MC-2 T3 state proof"}}, headers=H)
assert r1.status_code == 200, (r1.status_code, r1.text)
d1 = r1.json()
assert d1["ticket_id"] == "{self.ticket_id}", d1
assert d1["updated_fields"] == ["priority"], d1
assert d1["version"] == 2, d1
mid = field_of("priority")
assert mid == "high", mid
assert "updated_by: USR-000002" in tf.read_text()
assert "version: 2" in tf.read_text(), (
    "version must appear in frontmatter "
    "(version-fix 2026-10-11 persists version; if this fires, "
    "the service stopped persisting version — update this test)"
)

# 2. revert by ID back to medium
r2 = client.patch(P, json={{"priority": "medium", "reason": "MC-2 T3 revert"}}, headers=H)
assert r2.status_code == 200, (r2.status_code, r2.text)
d2 = r2.json()
assert d2["ticket_id"] == "{self.ticket_id}", d2
assert d2["updated_fields"] == ["priority"], d2
# version now persists across reads: first change -> 2, revert bumps -> 3.
assert d2["version"] == 3, d2
after = field_of("priority")
assert after == "medium", after

# 3. temp counters grew (expected: temp-only); real counters never in play
counters = json.loads(Path({str(self.counters_path)!r}).read_text())
assert counters.get("event", 0) >= 1, counters

print(json.dumps({{
    "ok": True,
    "before": before,
    "after_change": mid,
    "reverted": after,
    "version_change": d1["version"],
    "version_revert": d2["version"],
    "temp_events": counters.get("event"),
}}))
'''
        out = self._assert_ok(self._run(script))
        self.assertTrue(out["ok"])
        self.assertEqual(out["before"], "medium")
        self.assertEqual(out["after_change"], "high")
        self.assertEqual(out["reverted"], "medium")
        self.assertEqual(out["version_change"], 2)
        self.assertEqual(out["version_revert"], 3)  # version persists (version-fix 2026-10-11)

    # ---- guards -------------------------------------------------------
    def test_real_vault_and_home_unchanged(self):
        """Real tree hashes must match pre-T3 baselines after T3 suite."""
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
        """This test must not have created USR-000002 or TKT-2026-903 in the
        shared sandbox; read-only check of the shared tree (no writes)."""
        sandbox_home = REAL_HOME
        users = sandbox_home / "config" / "users.yaml"
        if users.exists():
            self.assertNotIn(
                "TKT-2026-903",
                users.read_text(),
                "temp ticket id leaked into shared users.yaml",
            )
        # shared sandbox Tickets dir (if present) must not hold the temp id
        shared_vault = REAL_VAULT
        for p in shared_vault.rglob("*"):
            if p.is_file() and TICKET_ID in p.name:
                self.fail(f"temp ticket leaked into shared sandbox: {p}")


if __name__ == "__main__":
    unittest.main()
