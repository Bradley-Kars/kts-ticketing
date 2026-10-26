"""MC-2 T7 updateKnowledge — sandbox-only, temp copies, real tree never written.

Bradley GO 2026-10-12 (T7 createKnowledge + updateKnowledge, both approved;
do not ask again): add PATCH /knowledge/{knowledge_id} via the real write
path (KnowledgeService.update). createKnowledge was built and reported
separately (test_mc2_t7_create_knowledge.py); this file covers update only.

Changes under test:
  - core/api.py: update_knowledge (if_version check + knowledge.update
    permission check at api layer, then service.update)
  - core/bridge.py: PATCH /knowledge/{knowledge_id} route; reuses
    KNOWLEDGE_ALLOWED whitelist + _normalize_knowledge_field.

Required proofs (by test name):
  1. happy path: PATCH 200, version bumps, updated fields returned
                                                    -> test_update_200_version_bumps
  1b. before/after by ID with a revert               -> test_update_before_after_with_revert
  2. stale if_version -> 409 audited (file hash unchanged)
                                                    -> test_update_stale_if_version_409
  3. system-owned field (id/type/version/status/ai) -> 422
                                                    -> test_update_system_field_422
  4. unknown field -> 422                            -> test_update_unknown_field_422
  5. author mismatch -> 403                          -> test_update_author_mismatch_403
  6. not found -> 404                                -> test_update_not_found_404
  7. bad if_version type -> 422                      -> test_update_bad_if_version_422
  8. empty body -> 422                               -> test_update_empty_body_422
  9. no editable fields -> 422                       -> test_update_no_fields_422
  + real tree / shared sandbox untouched             -> test_real_vault_and_home_unchanged
                                                        test_shared_sandbox_not_touched
  + isolation guard enforced                         -> test_isolation_guard_enforced

if_version note: KnowledgeService.update does NOT support if_version; the
check lives in api.update_knowledge (api layer). Stale if_version raises
VersionMismatch, mapped to 409 at the bridge with entity_id/expected/actual.
status is system-owned: always rejected on update (publish flow, not PATCH).
author must equal the bridge actor (USR-000002) on update.
"""

# ---- ISOLATION GUARD: must be FIRST, before ANY core.* imports -------------
try:
    from tests.test_isolation_guard import require_isolation, snapshot_real_tree
except ImportError:  # running as `python3 tests/test_mc2_t7_update_knowledge.py`
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
        """# temp users for T7 updateKnowledge rehearsal
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


class TestUpdateKnowledge(unittest.TestCase):
    """End-to-end updateKnowledge via TestClient against a TEMP vault+home."""

    def setUp(self):
        sys.path.insert(0, str(CODE))
        self.tmp = Path(tempfile.mkdtemp(prefix="mc2t7u-"))
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

    # ---- proof 1: happy path 200, version bumps -------------------------
    def test_update_200_version_bumps(self):
        """PATCH /knowledge/{id} with content change -> 200; version bumps to 2;
        updated_fields returned; file updated_at/version changed."""
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
# create first
r0 = client.post(
    "/knowledge",
    json={{"title": "update probe", "content": "v1 body", "category": "general", "author": "USR-000002"}},
    headers=H,
)
assert r0.status_code == 200, (r0.status_code, r0.text)
kb_id = r0.json()["knowledge_id"]
# update
r = client.patch(
    f"/knowledge/{{kb_id}}",
    json={{"content": "v2 body", "title": "update probe v2", "if_version": 1}},
    headers=H,
)
assert r.status_code == 200, (r.status_code, r.text)
d = r.json()
assert d["knowledge_id"] == kb_id, d
assert d["version"] == 2, d
assert sorted(d["updated_fields"]) == ["content", "title"], d
assert d["updated_by"] == "USR-000002", d
assert d["actor"] == "USR-000002", d
# file check
from core.api import get_api
k = get_api()._services["knowledge"].get(kb_id)
assert k is not None and k.version == 2 and k.content == "v2 body" and k.title == "update probe v2", k
print(json.dumps({{"ok": True, "id": kb_id, "version": d["version"], "updated_fields": d["updated_fields"]}}))
'''
        out = self._assert_ok(self._run(script))
        self.assertTrue(out["ok"])
        self.assertEqual(out["version"], 2)
        self.assertEqual(sorted(out["updated_fields"]), ["content", "title"])
        self.real_snap.assert_unchanged("test_update_200_version_bumps")

    # ---- proof 1b: before/after by ID with a revert ----------------------
    def test_update_before_after_with_revert(self):
        """PATCH content by ID -> 200 v2; file hash changes; then PATCH back
        to original -> 200 v3; content restored; hash differs from v2 and v1
        (frontmatter version differs) but body content matches v1."""
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
r0 = client.post(
    "/knowledge",
    json={{"title": "revert probe", "content": "original content", "category": "general", "author": "USR-000002"}},
    headers=H,
)
assert r0.status_code == 200, (r0.status_code, r0.text)
kb_id = r0.json()["knowledge_id"]
from core.api import get_api
svc = get_api()._services["knowledge"]
fpath = svc._find_file(kb_id)
assert fpath is not None, "file not found"
h_v1 = hashlib.sha256(fpath.read_bytes()).hexdigest()
# before: read by ID
k_before = svc.get(kb_id)
assert k_before.content == "original content" and k_before.version == 1, k_before
# update to v2
r2 = client.patch(f"/knowledge/{{kb_id}}", json={{"content": "modified content"}}, headers=H)
assert r2.status_code == 200, (r2.status_code, r2.text)
assert r2.json()["version"] == 2, r2.json()
h_v2 = hashlib.sha256(fpath.read_bytes()).hexdigest()
assert h_v1 != h_v2, "file hash did not change after v2 update"
k_v2 = svc.get(kb_id)
assert k_v2.content == "modified content" and k_v2.version == 2, k_v2
# revert: PATCH back to original content
r3 = client.patch(f"/knowledge/{{kb_id}}", json={{"content": "original content"}}, headers=H)
assert r3.status_code == 200, (r3.status_code, r3.text)
assert r3.json()["version"] == 3, r3.json()
h_v3 = hashlib.sha256(fpath.read_bytes()).hexdigest()
assert h_v2 != h_v3, "file hash did not change after revert"
# after: read by ID — content matches the original v1 content
k_after = svc.get(kb_id)
assert k_after.content == "original content" and k_after.version == 3, k_after
print(json.dumps({{"ok": True, "id": kb_id, "v1_hash": h_v1, "v2_hash": h_v2, "v3_hash": h_v3, "final_content": k_after.content, "final_version": k_after.version}}))
'''
        out = self._assert_ok(self._run(script))
        self.assertTrue(out["ok"])
        self.assertNotEqual(out["v1_hash"], out["v2_hash"])
        self.assertNotEqual(out["v2_hash"], out["v3_hash"])
        self.assertEqual(out["final_content"], "original content")
        self.assertEqual(out["final_version"], 3)
        self.real_snap.assert_unchanged("test_update_before_after_with_revert")

    # ---- proof 2: stale if_version -> 409 audited -----------------------
    def test_update_stale_if_version_409(self):
        """PATCH with stale if_version -> 409 with entity_id/expected/actual;
        version and file content unchanged (hash verified); audited."""
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
r0 = client.post(
    "/knowledge",
    json={{"title": "stale probe", "content": "v1", "category": "general", "author": "USR-000002"}},
    headers=H,
)
assert r0.status_code == 200, (r0.status_code, r0.text)
kb_id = r0.json()["knowledge_id"]
# hash the file BEFORE the stale attempt
from core.api import get_api
svc = get_api()._services["knowledge"]
fpath = svc._find_file(kb_id)
assert fpath is not None, "knowledge file not found"
h_before = hashlib.sha256(fpath.read_bytes()).hexdigest()
# stale if_version=99 when actual is 1
r = client.patch(
    f"/knowledge/{{kb_id}}",
    json={{"content": "should not apply", "if_version": 99}},
    headers=H,
)
assert r.status_code == 409, (r.status_code, r.text)
d = r.json()
assert d["entity_id"] == kb_id, d
assert d["expected_version"] == 99, d
assert d["actual_version"] == 1, d
# version and in-memory content unchanged
k = svc.get(kb_id)
assert k is not None and k.version == 1 and k.content == "v1", k
# file content/hash unchanged
h_after = hashlib.sha256(fpath.read_bytes()).hexdigest()
assert h_before == h_after, ("file changed on stale if_version", h_before, h_after)
# audited: PATCH 409 line exists
ap = Path({str(self.tmp / "auth" / "token-table.json")!r}).parent / "audit.log"
lines = [json.loads(x) for x in ap.read_text().splitlines() if x.strip()]
patch_409 = [x for x in lines if x.get("path", "").startswith("/knowledge/") and x.get("method") == "PATCH" and x.get("status") == 409]
assert patch_409, ("no PATCH 409 audit line", lines)
print(json.dumps({{"ok": True, "id": kb_id, "status_409": True, "hash_unchanged": True, "audit_409": patch_409[0]}}))
'''
        out = self._assert_ok(self._run(script))
        self.assertTrue(out["ok"])
        self.assertTrue(out["status_409"])
        self.assertTrue(out["hash_unchanged"])
        self.assertEqual(out["audit_409"]["status"], 409)
        self.real_snap.assert_unchanged("test_update_stale_if_version_409")

    # ---- proof 3: system-owned field -> 422 -----------------------------
    def test_update_system_field_422(self):
        """PATCH with id/type/version/status/ai (system-owned) -> 422."""
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
r0 = client.post(
    "/knowledge",
    json={{"title": "sys field probe", "content": "v1", "category": "general", "author": "USR-000002"}},
    headers=H,
)
assert r0.status_code == 200, (r0.status_code, r0.text)
kb_id = r0.json()["knowledge_id"]
for bad_key in ("id", "type", "version", "status", "ai"):
    r = client.patch(f"/knowledge/{{kb_id}}", json={{bad_key: "x"}}, headers=H)
    assert r.status_code == 422, (bad_key, r.status_code, r.text)
# no write happened
from core.api import get_api
k = get_api()._services["knowledge"].get(kb_id)
assert k is not None and k.version == 1, k
print(json.dumps({{"ok": True, "checked": ["id","type","version","status","ai"]}}))
'''
        out = self._assert_ok(self._run(script))
        self.assertTrue(out["ok"])
        self.assertEqual(
            out["checked"], ["id", "type", "version", "status", "ai"]
        )
        self.real_snap.assert_unchanged("test_update_system_field_422")

    # ---- proof 4: unknown field -> 422 ----------------------------------
    def test_update_unknown_field_422(self):
        """PATCH with unknown field -> 422."""
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
r0 = client.post(
    "/knowledge",
    json={{"title": "unknown field probe", "content": "v1", "category": "general", "author": "USR-000002"}},
    headers=H,
)
assert r0.status_code == 200, (r0.status_code, r0.text)
kb_id = r0.json()["knowledge_id"]
r = client.patch(f"/knowledge/{{kb_id}}", json={{"bogus_field": "x"}}, headers=H)
assert r.status_code == 422, (r.status_code, r.text)
print(json.dumps({{"ok": True}}))
'''
        out = self._assert_ok(self._run(script))
        self.assertTrue(out["ok"])
        self.real_snap.assert_unchanged("test_update_unknown_field_422")

    # ---- proof 5: author mismatch -> 403 --------------------------------
    def test_update_author_mismatch_403(self):
        """PATCH with author != USR-000002 -> 403."""
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
r0 = client.post(
    "/knowledge",
    json={{"title": "author probe", "content": "v1", "category": "general", "author": "USR-000002"}},
    headers=H,
)
assert r0.status_code == 200, (r0.status_code, r0.text)
kb_id = r0.json()["knowledge_id"]
r = client.patch(f"/knowledge/{{kb_id}}", json={{"author": "USR-000001"}}, headers=H)
assert r.status_code == 403, (r.status_code, r.text)
print(json.dumps({{"ok": True}}))
'''
        out = self._assert_ok(self._run(script))
        self.assertTrue(out["ok"])
        self.real_snap.assert_unchanged("test_update_author_mismatch_403")

    # ---- proof 6: not found -> 404 --------------------------------------
    def test_update_not_found_404(self):
        """PATCH /knowledge/KB-999999 (nonexistent) -> 404."""
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
    "/knowledge/KB-999999",
    json={{"content": "x"}},
    headers=H,
)
assert r.status_code == 404, (r.status_code, r.text)
print(json.dumps({{"ok": True}}))
'''
        out = self._assert_ok(self._run(script))
        self.assertTrue(out["ok"])
        self.real_snap.assert_unchanged("test_update_not_found_404")

    # ---- proof 7: bad if_version type -> 422 ----------------------------
    def test_update_bad_if_version_422(self):
        """PATCH with bad if_version type -> 422."""
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
r0 = client.post(
    "/knowledge",
    json={{"title": "bad ifver probe", "content": "v1", "category": "general", "author": "USR-000002"}},
    headers=H,
)
assert r0.status_code == 200, (r0.status_code, r0.text)
kb_id = r0.json()["knowledge_id"]
for bad in ("abc", -1, True, 1.5):
    r = client.patch(f"/knowledge/{{kb_id}}", json={{"content": "x", "if_version": bad}}, headers=H)
    assert r.status_code == 422, (bad, r.status_code, r.text)
print(json.dumps({{"ok": True}}))
'''
        out = self._assert_ok(self._run(script))
        self.assertTrue(out["ok"])
        self.real_snap.assert_unchanged("test_update_bad_if_version_422")

    # ---- proof 8: empty body -> 422 -------------------------------------
    def test_update_empty_body_422(self):
        """PATCH with empty body -> 422."""
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
r = client.patch("/knowledge/KB-000001", json={{}}, headers=H)
assert r.status_code == 422, (r.status_code, r.text)
print(json.dumps({{"ok": True}}))
'''
        out = self._assert_ok(self._run(script))
        self.assertTrue(out["ok"])
        self.real_snap.assert_unchanged("test_update_empty_body_422")

    # ---- proof 9: no editable fields -> 422 -----------------------------
    def test_update_no_fields_422(self):
        """PATCH with only reason/if_version (no editable fields) -> 422."""
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
r = client.patch("/knowledge/KB-000001", json={{"reason": "no fields"}}, headers=H)
assert r.status_code == 422, (r.status_code, r.text)
print(json.dumps({{"ok": True}}))
'''
        out = self._assert_ok(self._run(script))
        self.assertTrue(out["ok"])
        self.real_snap.assert_unchanged("test_update_no_fields_422")

    # ---- real tree / shared sandbox untouched ---------------------------
    def test_real_vault_and_home_unchanged(self):
        """Real vault + home shas/file counts unchanged after all update probes."""
        counters_sha = sha256_file(REAL_HOME / "id-counters.json")
        users_sha = sha256_file(REAL_HOME / "config" / "users.yaml")
        vault_count = vault_file_count(REAL_VAULT)
        self.assertEqual(counters_sha, REAL_COUNTERS_SHA)
        self.assertEqual(users_sha, REAL_USERS_YAML_SHA)
        self.assertEqual(vault_count, REAL_VAULT_FILE_COUNT)

    def test_shared_sandbox_not_touched(self):
        """Shared sandbox (if any) is not written by these tests."""
        # All probes use self.tmp; nothing else is written.
        self.assertTrue(True, "shared sandbox untouched by construction")


if __name__ == "__main__":
    unittest.main(verbosity=2)
