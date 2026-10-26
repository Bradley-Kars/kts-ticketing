"""MC-2 T7 createKnowledge — sandbox-only, temp copies, real tree never written.

Bradley GO 2026-10-12 (T7 createKnowledge only): add POST /knowledge via the
real knowledge-sequence write path (KnowledgeService.next_id + create).
updateKnowledge is a SEPARATE step with its own GO — not covered here.

Changes under test:
  - core/api.py: create_knowledge (allocates id via next_id + create)
  - core/bridge.py: POST /knowledge route; KNOWLEDGE_ALLOWED whitelist;
    _normalize_knowledge_field for knowledge-only fields.

Required proofs (by test name):
  1. happy path (title+content+category+author) -> 200, draft, version 1
                                                    -> test_create_knowledge_200_version_1
  2. missing required field -> 422, no article written
                                                    -> test_create_missing_required_422
  3. system-owned field (id/type/version/status/ai) -> 422
                                                    -> test_create_system_field_422
  4. bad if_version type -> 422                      -> test_create_bad_if_version_422
  5. valid if_version on create is ignored (always v1) -> test_create_if_version_ignored
  + audited: create 200 shows token_id/method/path only (no content)
                                                    -> test_create_audited_no_content
  + Bradley 2026-10-12: field length limits & empty/oversized bodies
     title max 200 / content 1..100000 / empty body / oversized body -> 422
                                                    -> test_field_limits_and_body_edges
  + real tree / shared sandbox untouched            -> test_real_vault_and_home_unchanged
                                                      test_shared_sandbox_not_touched
  + isolation guard enforced                        -> test_isolation_guard_enforced

Free-text note (Bradley 2026-10-12): knowledge content is free text, so the
whitelist + normalizer must reject empty content, oversized content and
oversized body payloads with 422 without writing anything.

if_version note: create allocates a brand-new id, so there is no prior version
to precondition against. The route still type-checks if_version (422 on bad
type); a valid if_version is ignored (new article is always version 1).
status is system-owned: always "draft" on create (published_at/review_date are
set only by the desktop publish flow, not the bridge).
"""

# ---- ISOLATION GUARD: must be FIRST, before ANY core.* imports -------------
try:
    from tests.test_isolation_guard import require_isolation, snapshot_real_tree
except ImportError:  # running as `python3 tests/test_mc2_t7_create_knowledge.py`
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
        """# temp users for T7 createKnowledge rehearsal
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


class TestCreateKnowledge(unittest.TestCase):
    """End-to-end createKnowledge via TestClient against a TEMP vault+home."""

    def setUp(self):
        sys.path.insert(0, str(CODE))
        self.tmp = Path(tempfile.mkdtemp(prefix="mc2t7-"))
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

    def _kb_dir(self) -> Path:
        return self.vault / "Knowledge"

    def _kb_files(self):
        d = self._kb_dir()
        if not d.exists():
            return []
        return list(d.glob("KB-*.md"))

    # ---- isolation guard enforcement ------------------------------------
    def test_isolation_guard_enforced(self):
        """The require_isolation() call at module top-level must have passed."""
        self.assertTrue(True, "isolation guard active")

    # ---- proof 1: happy path 200, draft, version 1 ----------------------
    def test_create_knowledge_200_version_1(self):
        """POST /knowledge with required fields -> 200; draft; version 1;
        KB file created with status: draft; created_by/author USR-000002."""
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
    "/knowledge",
    json={{
        "title": "T7 create probe",
        "content": "T7 rehearsal article body",
        "category": "troubleshooting",
        "author": "USR-000002",
        "tags": ["t7"],
    }},
    headers=H,
)
assert r.status_code == 200, (r.status_code, r.text)
d = r.json()
assert re.match(r"^KB-\\d{{6,}}$", d["knowledge_id"]), d
assert d["version"] == 1, d
assert d["status"] == "draft", d
assert d["category"] == "troubleshooting", d
assert d["author"] == "USR-000002", d
assert d["actor"] == "USR-000002", d
assert d["created_by"] == "USR-000002", d
from core.api import get_api
k = get_api()._services["knowledge"].get(d["knowledge_id"])
assert k is not None and k.version == 1 and k.status == "draft", k
d2 = Path({str(self.vault)!r}) / "Knowledge"
hits = list(d2.glob(f"{{d['knowledge_id']}}*.md"))
assert len(hits) == 1, hits
text = hits[0].read_text()
assert "status: draft" in text and "category: troubleshooting" in text, text[:400]
print(json.dumps({{"ok": True, "id": d["knowledge_id"], "version": d["version"], "status": d["status"]}}))
'''
        out = self._assert_ok(self._run(script))
        self.assertTrue(out["ok"])
        self.assertEqual(out["version"], 1)
        self.assertEqual(out["status"], "draft")
        self.assertRegex(out["id"], r"^KB-\d{6,}$")
        self.real_snap.assert_unchanged("test_create_knowledge_200_version_1")

    # ---- proof 2: missing required -> 422, no write ---------------------
    def test_create_missing_required_422(self):
        """POST /knowledge missing title/content/category/author -> 422;
        no KB file in vault."""
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
for missing in ("title", "content", "category", "author"):
    body = {{
        "title": "probe",
        "content": "probe body",
        "category": "general",
        "author": "USR-000002",
    }}
    del body[missing]
    r = client.post("/knowledge", json=body, headers=H)
    assert r.status_code == 422, (missing, r.status_code, r.text)
d = Path({str(self.vault)!r}) / "Knowledge"
assert not d.exists() or list(d.glob("KB-*.md")) == [], list(d.glob("KB-*.md"))
print(json.dumps({{"ok": True, "checked": ["title","content","category","author"]}}))
'''
        out = self._assert_ok(self._run(script))
        self.assertTrue(out["ok"])
        self.assertEqual(
            out["checked"], ["title", "content", "category", "author"]
        )
        self.real_snap.assert_unchanged("test_create_missing_required_422")

    # ---- proof 3: system-owned field -> 422 -----------------------------
    def test_create_system_field_422(self):
        """POST /knowledge with id/type/version/status/ai (system-owned) -> 422."""
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
for key, val in (
    ("id", "KB-999999"),
    ("type", "knowledge"),
    ("version", 9),
    ("status", "published"),
    ("ai", {{"foo": 1}}),
):
    r = client.post(
        "/knowledge",
        json={{"title": "sys-owned probe", "content": "body", "category": "general", "author": "USR-000002", key: val}},
        headers=H,
    )
    assert r.status_code == 422, (key, r.status_code, r.text)
    assert "system-owned" in r.json()["detail"], (key, r.json())
d = Path({str(self.vault)!r}) / "Knowledge"
assert not d.exists() or list(d.glob("KB-*.md")) == [], list(d.glob("KB-*.md"))
print(json.dumps({{"ok": True, "checked": ["id","type","version","status","ai"]}}))
'''
        out = self._assert_ok(self._run(script))
        self.assertTrue(out["ok"])
        self.assertEqual(
            out["checked"], ["id", "type", "version", "status", "ai"]
        )
        self.real_snap.assert_unchanged("test_create_system_field_422")

    # ---- proof 4: bad if_version type -> 422 ----------------------------
    def test_create_bad_if_version_422(self):
        """POST /knowledge with if_version bool/str/negative -> 422; no write."""
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
    r = client.post(
        "/knowledge",
        json={{"title": "bad-if_version probe", "content": "body", "category": "general", "author": "USR-000002", "if_version": bad}},
        headers=H,
    )
    assert r.status_code == 422, (bad, r.status_code, r.text)
d = Path({str(self.vault)!r}) / "Knowledge"
assert not d.exists() or list(d.glob("KB-*.md")) == [], list(d.glob("KB-*.md"))
print(json.dumps({{"ok": True, "checked": ["bool","str","negative"]}}))
'''
        out = self._assert_ok(self._run(script))
        self.assertTrue(out["ok"])
        self.assertEqual(out["checked"], ["bool", "str", "negative"])
        self.real_snap.assert_unchanged("test_create_bad_if_version_422")

    # ---- proof 5: valid if_version on create is ignored (always v1) -----
    def test_create_if_version_ignored(self):
        """Valid if_version on create is ignored: new article is always version 1."""
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
r = client.post(
    "/knowledge",
    json={{"title": "if_version-on-create", "content": "body", "category": "general", "author": "USR-000002", "if_version": 1}},
    headers=H,
)
assert r.status_code == 200, (r.status_code, r.text)
d = r.json()
assert d["version"] == 1, d
from core.api import get_api
k = get_api()._services["knowledge"].get(d["knowledge_id"])
assert k.version == 1, k.version
print(json.dumps({{"ok": True, "id": d["knowledge_id"], "version": d["version"]}}))
'''
        out = self._assert_ok(self._run(script))
        self.assertTrue(out["ok"])
        self.assertEqual(out["version"], 1)
        self.real_snap.assert_unchanged("test_create_if_version_ignored")

    # ---- Bradley 2026-10-12: field limits & body edges ------------------
    def test_field_limits_and_body_edges(self):
        """Free-text/whitelist edges -> 422, no write:
        empty body; oversized body; title empty/201; content empty/100001;
        category invalid; author not USR-; unknown field."""
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
base = {{"title": "limit probe", "content": "body", "category": "general", "author": "USR-000002"}}
# empty body
r = client.post("/knowledge", json={{}}, headers=H)
assert r.status_code == 422, (r.status_code, r.text)
# oversized body (unknown huge key + large string)
big = "x" * 200000
r = client.post("/knowledge", json={{"title": "t", "content": big, "category": "general", "author": "USR-000002"}}, headers=H)
assert r.status_code == 422, (r.status_code, r.text)
# title empty / 201
r = client.post("/knowledge", json={{**base, "title": ""}}, headers=H)
assert r.status_code == 422, (r.status_code, r.text)
r = client.post("/knowledge", json={{**base, "title": "x" * 201}}, headers=H)
assert r.status_code == 422, (r.status_code, r.text)
# content empty / 100001
r = client.post("/knowledge", json={{**base, "content": ""}}, headers=H)
assert r.status_code == 422, (r.status_code, r.text)
r = client.post("/knowledge", json={{**base, "content": "x" * 100001}}, headers=H)
assert r.status_code == 422, (r.status_code, r.text)
# category invalid
r = client.post("/knowledge", json={{**base, "category": "not-a-category"}}, headers=H)
assert r.status_code == 422, (r.status_code, r.text)
# author not USR- id
r = client.post("/knowledge", json={{**base, "author": "not-a-user"}}, headers=H)
assert r.status_code == 422, (r.status_code, r.text)
# unknown field
r = client.post("/knowledge", json={{**base, "nope": 1}}, headers=H)
assert r.status_code == 422, (r.status_code, r.text)
d = Path({str(self.vault)!r}) / "Knowledge"
assert not d.exists() or list(d.glob("KB-*.md")) == [], list(d.glob("KB-*.md"))
print(json.dumps({{"ok": True, "edges": 9}}))
'''
        out = self._assert_ok(self._run(script))
        self.assertTrue(out["ok"])
        self.assertEqual(out["edges"], 9)
        self.real_snap.assert_unchanged("test_field_limits_and_body_edges")

    # ---- audited: create 200 shows token_id/method/path only ------------
    def test_create_audited_no_content(self):
        """POST /knowledge -> 200; audit lines record token_id/method/path only
        (no title/content, no token chars)."""
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
r = client.post(
    "/knowledge",
    json={{"title": "audited create", "content": "secret-ish body", "category": "general", "author": "USR-000002"}},
    headers=H,
)
assert r.status_code == 200, (r.status_code, r.text)
ap = Path({str(self.tmp / "auth" / "token-table.json")!r}).parent / "audit.log"
lines = [json.loads(x) for x in ap.read_text().splitlines() if x.strip()]
create_lines = [x for x in lines if x.get("path") == "/knowledge" and x.get("method") == "POST" and x.get("status") == 200]
assert create_lines, ("no create 200 audit line", lines)
assert "token_id" in create_lines[0] and "label" in create_lines[0], create_lines[0]
blob = json.dumps(lines)
assert "detail" not in create_lines[0] and "secret-ish" not in blob, create_lines[0]
assert sec not in blob, "raw secret leaked into audit"
assert all(set(x.keys()) <= ALLOWED for x in lines), lines[0]
print(json.dumps({{"ok": True, "create_200": True, "audit_lines": len(lines), "create_audit": create_lines[0], "token_id": tid}}))
'''
        out = self._assert_ok(self._run(script))
        self.assertTrue(out["ok"])
        self.assertTrue(out["create_200"])
        self.assertGreater(out["audit_lines"], 0)
        self.assertEqual(out["create_audit"].get("method"), "POST")
        self.assertEqual(out["create_audit"].get("path"), "/knowledge")
        self.assertEqual(out["create_audit"].get("status"), 200)
        self.assertEqual(out["create_audit"].get("token_id"), out.get("token_id"))
        self.assertNotIn("detail", out["create_audit"])
        self.assertNotIn("secret-ish", json.dumps(out["create_audit"]))
        self.real_snap.assert_unchanged("test_create_audited_no_content")

    # ---- guards: real tree untouched ------------------------------------
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
                "T7 create probe",
                users.read_text(),
                "temp knowledge title leaked into shared users.yaml",
            )


if __name__ == "__main__":
    unittest.main(verbosity=2)
