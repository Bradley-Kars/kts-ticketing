"""MC-2 T4 createTicket — sandbox-only, temp copies, real tree never written.

Bradley GO 2026-10-11 (T4 only): add POST /tickets (createTicket) via the
real ticket-sequence write path (TicketService.next_id + create). Same
sandbox/temp rules as T1–T3.

Changes under test:
  - core/bridge.py: POST /tickets route; CREATE_ALLOWED whitelist; extend
    _normalize_field for create-only free/ID fields.

Required proofs (by test name):
  1. happy path (title+fields) -> 200, version 1  -> test_create_ticket_200_version_1
  2. missing title -> 422, no ticket written      -> test_create_missing_title_422
  3. system-owned field (id/status/worklog/version) -> 422 -> test_create_system_field_422
  4. bad if_version type -> 422                   -> test_create_bad_if_version_422
  5. valid if_version on create is ignored (always v1) -> test_create_if_version_ignored
  + audited: create 201/200 + a stale-if_version 409 on update shows token_id/method/path
    only (no ticket content)                       -> test_create_audited_no_content
  + real tree / shared sandbox untouched           -> test_real_vault_and_home_unchanged,
                                                      test_shared_sandbox_not_touched

if_version note: create allocates a brand-new id, so there is no prior version
to precondition against. The route still type-checks if_version (422 on bad
type) for consistency with the update routes; a valid if_version is ignored
(new ticket is always version 1).
"""

# ---- ISOLATION GUARD: must be FIRST, before ANY core.* imports -------------
try:
    from tests.test_isolation_guard import require_isolation, snapshot_real_tree
except ImportError:  # running as `python3 tests/test_mc2_t4_create_ticket.py`
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


def write_users_with_bridge(home: Path) -> Path:
    cfg = home / "config"
    cfg.mkdir(parents=True, exist_ok=True)
    path = cfg / "users.yaml"
    path.write_text(
        """# temp users for T4 createTicket rehearsal
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


class TestCreateTicket(unittest.TestCase):
    """End-to-end createTicket via TestClient against a TEMP vault+home."""

    def setUp(self):
        sys.path.insert(0, str(CODE))
        self.tmp = Path(tempfile.mkdtemp(prefix="mc2t4-"))
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
        # Real-tree check after EVERY test: any write to the real vault,
        # real counters or real users.yaml fails here with context.
        self.real_snap.assert_unchanged(self._testMethodName)

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

    # ---- proof 1: happy path 200, version 1 ----------------------------
    def test_create_ticket_200_version_1(self):
        """POST /tickets with title+optional fields -> 200; new ticket version 1;
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
    "/tickets",
    json={{"title": "T4 create probe", "priority": "high", "client_name": "Temp Co"}},
    headers=H,
)
assert r.status_code == 200, (r.status_code, r.text)
d = r.json()
assert re.match(r"^TKT-\\d{{4}}-\\d{{3}}$", d["ticket_id"]), d
assert d["version"] == 1, d
assert d["status"] == "new", d
assert d["actor"] == "USR-000002", d
assert d["created_by"] == "USR-000002", d
# vault file exists with version: 1
from core.api import get_api
t = get_api().get_ticket(d["ticket_id"])
assert t is not None
tf = Path({str(self.vault)!r}) / "Tickets"
hits = list(tf.glob(f"{{d['ticket_id']}}*.md"))
assert len(hits) == 1, hits
text = hits[0].read_text()
assert "version: 1" in text, text[:400]
print(json.dumps({{"ok": True, "ticket_id": d["ticket_id"], "version": d["version"], "status": d["status"]}}))
'''
        out = self._assert_ok(self._run(script))
        self.assertTrue(out["ok"])
        self.assertEqual(out["version"], 1)
        self.assertEqual(out["status"], "new")
        self.assertRegex(out["ticket_id"], r"^TKT-\d{4}-\d{3}$")

    # ---- proof 2: missing title -> 422, no write -----------------------
    def test_create_missing_title_422(self):
        """POST /tickets without title -> 422; no ticket file in vault."""
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
r = client.post("/tickets", json={{"priority": "high"}}, headers=H)
assert r.status_code == 422, (r.status_code, r.text)
tf = Path({str(self.vault)!r}) / "Tickets"
assert not tf.exists() or list(tf.glob("*.md")) == [], list(tf.glob("*.md"))
print(json.dumps({{"ok": True, "code": r.status_code}}))
'''
        out = self._assert_ok(self._run(script))
        self.assertTrue(out["ok"])
        self.assertEqual(out["code"], 422)

    # ---- proof 3: system-owned field -> 422 ----------------------------
    def test_create_system_field_422(self):
        """POST /tickets with id/status/worklog/version (system-owned) -> 422 each."""
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
for key, val in (("id", "TKT-2026-999"), ("status", "closed"), ("worklog", []), ("version", 9)):
    r = client.post("/tickets", json={{"title": "sys-owned probe", key: val}}, headers=H)
    assert r.status_code == 422, (key, r.status_code, r.text)
    assert "system-owned" in r.json()["detail"], (key, r.json())
# no ticket file was written
tf = Path({str(self.vault)!r}) / "Tickets"
assert not tf.exists() or list(tf.glob("*.md")) == [], list(tf.glob("*.md"))
print(json.dumps({{"ok": True, "checked": ["id","status","worklog","version"]}}))
'''
        out = self._assert_ok(self._run(script))
        self.assertTrue(out["ok"])
        self.assertEqual(out["checked"], ["id", "status", "worklog", "version"])

    # ---- proof 4: bad if_version type -> 422 ---------------------------
    def test_create_bad_if_version_422(self):
        """POST /tickets with if_version bool/str/negative -> 422; no ticket written."""
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
    r = client.post("/tickets", json={{"title": "bad-if_version probe", "if_version": bad}}, headers=H)
    assert r.status_code == 422, (bad, r.status_code, r.text)
tf = Path({str(self.vault)!r}) / "Tickets"
assert not tf.exists() or list(tf.glob("*.md")) == [], list(tf.glob("*.md"))
print(json.dumps({{"ok": True, "checked": ["bool","str","negative"]}}))
'''
        out = self._assert_ok(self._run(script))
        self.assertTrue(out["ok"])
        self.assertEqual(out["checked"], ["bool", "str", "negative"])

    # ---- proof 5: valid if_version on create is ignored (always v1) ----
    def test_create_if_version_ignored(self):
        """Valid if_version on create is ignored: new ticket is always version 1.
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
r = client.post("/tickets", json={{"title": "if_version-on-create", "if_version": 1}}, headers=H)
assert r.status_code == 200, (r.status_code, r.text)
d = r.json()
assert d["version"] == 1, d
from core.api import get_api
t = get_api().get_ticket(d["ticket_id"])
assert t.version == 1, t.version
print(json.dumps({{"ok": True, "ticket_id": d["ticket_id"], "version": d["version"]}}))
'''
        out = self._assert_ok(self._run(script))
        self.assertTrue(out["ok"])
        self.assertEqual(out["version"], 1)

    # ---- audited: create 200 + stale-if_version 409 on update show token_id only ----
    def test_create_audited_no_content(self):
        """POST /tickets -> 200 (audited action=request); then a stale-if_version
        PATCH on the new ticket -> 409; audit lines record token_id/method/path
        only (no ticket content, no token chars)."""
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
r = client.post("/tickets", json={{"title": "audited create"}}, headers=H)
assert r.status_code == 200, (r.status_code, r.text)
tid_new = r.json()["ticket_id"]
# stale-if_version 409 on update of the new ticket
r2 = client.patch(f"/tickets/{{tid_new}}", json={{"priority": "high", "if_version": 99}}, headers=H)
assert r2.status_code == 409, (r2.status_code, r2.text)
# read audit.log (token table default: next to the table file)
ap = Path({str(self.tmp / "auth" / "token-table.json")!r}).parent / "audit.log"
lines = [json.loads(x) for x in ap.read_text().splitlines() if x.strip()]
create_lines = [x for x in lines if x.get("path") == "/tickets" and x.get("method") == "POST" and x.get("status") == 200]
conflict_lines = [x for x in lines if x.get("status") == 409 and x.get("path", "").startswith(f"/tickets/{{tid_new}}")]
assert create_lines, ("no create 200 audit line", lines)
assert conflict_lines, ("no 409 audit line", lines)
assert "token_id" in create_lines[0] and "label" in create_lines[0], create_lines[0]
assert "detail" not in create_lines[0] and "title" not in create_lines[0], create_lines[0]
assert "if_version" not in json.dumps(conflict_lines[0]), conflict_lines[0]
assert sec not in json.dumps(lines), "secret leaked into audit"
assert all("secret" not in json.dumps(x) for x in lines), "secret key leaked into audit"
assert all(set(x.keys()) <= ALLOWED for x in lines), lines[0]
print(json.dumps({{"ok": True, "create_200": True, "conflict_409": True, "audit_lines": len(lines), "create_audit": create_lines[0], "token_id": tid}}))
'''
        out = self._assert_ok(self._run(script))
        self.assertTrue(out["ok"])
        self.assertTrue(out["create_200"])
        self.assertTrue(out["conflict_409"])
        self.assertGreater(out["audit_lines"], 0)
        self.assertEqual(out["create_audit"].get("method"), "POST")
        self.assertEqual(out["create_audit"].get("path"), "/tickets")
        self.assertEqual(out["create_audit"].get("status"), 200)
        self.assertEqual(out["create_audit"].get("token_id"), out.get("token_id"))
        self.assertIn("token_id", out["create_audit"])
        self.assertIn("label", out["create_audit"])
        self.assertNotIn("detail", out["create_audit"])
        self.assertNotIn("title", out["create_audit"])

    # ---- T4 follow-up: clientless/client filename shape ----------------
    def test_create_filename_shape_clientless_and_with_client(self):
        """Clientless ticket -> {id} - {title}.md (found by _find_file).
        Ticket with client_name -> {id} - {client} - {title}.md.
        Existing real tickets (always client-shaped) still load."""
        script = f'''
import json, re, sys
from pathlib import Path
sys.path.insert(0, {str(CODE)!r})
from fastapi.testclient import TestClient
from core.auth import TokenTable
from core.bridge import create_app
from core.api import get_api
from core.services.ticket_service import TicketService
table = TokenTable({str(self.tmp / "auth" / "token-table.json")!r})
client = TestClient(create_app(table))
_, sec = table.mint("desktop", scope="write")
H = {{"X-API-Token": sec}}
# clientless: filename must be "{{id}} - {{title}}.md" and be findable
r = client.post("/tickets", json={{"title": "Clientless T4 probe"}}, headers=H)
assert r.status_code == 200, (r.status_code, r.text)
tid_cl = r.json()["ticket_id"]
tf = Path({str(self.vault)!r}) / "Tickets"
hits_cl = list(tf.glob(f"{{tid_cl}}*.md"))
assert len(hits_cl) == 1, hits_cl
assert hits_cl[0].name == f"{{tid_cl}} - Clientless T4 probe.md", hits_cl[0].name
assert "{{" not in hits_cl[0].name  # no leftover braces
found = get_api()._services["ticket"]._find_file(tid_cl)
assert found is not None and found == hits_cl[0], (found, hits_cl[0])
# with client: filename keeps old "{{id}} - {{client}} - {{title}}.md" shape
r2 = client.post("/tickets", json={{"title": "With client probe", "client_name": "Temp Co"}}, headers=H)
assert r2.status_code == 200, (r2.status_code, r2.text)
tid_c = r2.json()["ticket_id"]
hits_c = list(tf.glob(f"{{tid_c}}*.md"))
assert len(hits_c) == 1, hits_c
assert hits_c[0].name == f"{{tid_c}} - Temp Co - With client probe.md", hits_c[0].name
# service-layer path builder (same code create uses)
svc = get_api()._services["ticket"]
p_cl = svc._get_file_path("TKT-9999-001", title="No client", client="")
assert p_cl.name == "TKT-9999-001 - No client.md", p_cl.name
p_c = svc._get_file_path("TKT-9999-002", title="Has client", client="[[Temp Co]]")
assert p_c.name == "TKT-9999-002 - Temp Co - Has client.md", p_c.name
# existing real tickets still load: _find_file on a known real TKT id
# (read-only; returns path only — no title/client content)
sample = next(Path({str(REAL_VAULT)!r}).glob("Tickets/TKT-*.md"))
tid_real = sample.name.split(" - ")[0]
assert re.match(r"^TKT-\\d{{4}}-\\d{{3}}$", tid_real), tid_real
real_svc = TicketService()
found_real = real_svc._find_file(tid_real)
assert found_real is not None and found_real.name.startswith(tid_real + " - "), found_real
print(json.dumps({{"ok": True, "clientless_name": hits_cl[0].name, "client_name": hits_c[0].name, "real_loaded": tid_real}}))
'''
        out = self._assert_ok(self._run(script))
        self.assertTrue(out["ok"])
        self.assertRegex(out["clientless_name"], r"^TKT-\d{4}-\d{3} - .+\.md$")
        self.assertRegex(out["client_name"], r"^TKT-\d{4}-\d{3} - .+ - .+\.md$")
        self.assertRegex(out["real_loaded"], r"^TKT-\d{4}-\d{3}$")

    # ---- guards: real tree untouched -----------------------------------
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
                "TKT-2026-903",
                users.read_text(),
                "temp ticket id leaked into shared users.yaml",
            )


if __name__ == "__main__":
    unittest.main(verbosity=2)
