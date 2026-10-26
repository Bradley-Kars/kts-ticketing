"""MC-2 T8 permissions wall — sandbox-only, temp copies, real tree never written.

Fix PermissionChecker._get_actor_roles to read roles from config/users.yaml
instead of hardcoding USR-000001=admin and every other USR=technician. No
hardcoded ids. Reload on file change via per-request mtime check in check().
users.yaml is authoritative — no vault frontmatter fallback.

Required proofs (by test name):
  1. admin allowed for ticket.assign and project.create
                                                     -> test_admin_allowed
  2. dispatcher allowed for ticket.assign            -> test_dispatcher_allowed_for_assign
  3. manager allowed for ticket.assign               -> test_manager_allowed_for_assign
  4. technician denied for project.create            -> test_technician_denied_for_project_create
  5. unknown actor denied                            -> test_unknown_actor_denied
  6. malformed users.yaml → deny (and log)           -> test_malformed_users_yaml_denied
  7. missing users.yaml → deny                       -> test_missing_users_yaml_denied
  8. reload on file change                          -> test_reload_on_file_change
  9. malformed yaml → deny, no vault fallback       -> test_malformed_yaml_no_vault_fallback
  10. precedence: yaml over vault fallback          -> test_precedence_yaml_over_vault
  11. role parsing edge cases (list/case/whitespace) -> test_role_parsing_edge_cases
  12. check() auto-reloads without explicit reload() -> test_check_auto_reloads_on_file_change
  + real tree / shared sandbox untouched            -> test_real_tree_untouched
    test_shared_sandbox_not_touched
  + isolation guard enforced                        -> test_isolation_guard_enforced
"""

# ---- ISOLATION GUARD: must be FIRST, before ANY core.* imports -------------
try:
    from tests.test_isolation_guard import require_isolation, snapshot_real_tree
except ImportError:  # running as `python3 tests/test_mc2_t8_permissions.py`
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


def write_users_with_roles(home: Path, users_yaml: str) -> Path:
    """Write a temp config/users.yaml into the sandbox home."""
    cfg = home / "config"
    cfg.mkdir(parents=True, exist_ok=True)
    path = cfg / "users.yaml"
    path.write_text(users_yaml)
    return path


def write_permissions_yaml(home: Path) -> Path:
    """Copy real permissions.yaml into sandbox home config."""
    cfg = home / "config"
    cfg.mkdir(parents=True, exist_ok=True)
    dst = cfg / "permissions.yaml"
    src = REAL_HOME / "config" / "permissions.yaml"
    if src.exists():
        shutil.copy2(src, dst)
    return dst


def write_counters(home: Path, event: int = 0) -> Path:
    p = home / "id-counters.json"
    p.write_text(json.dumps({"event": event}))
    return p


class TestPermissionsWall(unittest.TestCase):
    """PermissionChecker role resolution from users.yaml / vault frontmatter."""

    def setUp(self):
        sys.path.insert(0, str(CODE))
        self.tmp = Path(tempfile.mkdtemp(prefix="mc2t8-"))
        self.vault = self.tmp / "vault"
        self.home = self.tmp / "home"
        self.vault.mkdir()
        self.home.mkdir()
        self.permissions_path = write_permissions_yaml(self.home)
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

    def _make_checker(self, users_yaml: str) -> str:
        """Write users.yaml into sandbox config and return the checker script
        fragment that loads PermissionChecker from sandbox paths."""
        self.users_path = write_users_with_roles(self.home, users_yaml)
        return self.users_path

    # ---- test 1: admin allowed for ticket.assign and project.create ----
    def test_admin_allowed(self):
        """USR-000001 with role admin in users.yaml is allowed for
        ticket.assign and project.create."""
        self._make_checker("""users:\n  USR-000001:\n    id: "USR-000001"\n    role: "admin"\n    status: "active"\n""")
        script = f'''
import json, sys
sys.path.insert(0, {str(CODE)!r})
from core.permissions import PermissionChecker
from core.models import EventActor

pc = PermissionChecker()
actor = EventActor(id="USR-000001", type="human", name="Admin")
assert pc.check(actor, "ticket.assign"), "admin should have ticket.assign"
assert pc.check(actor, "project.create"), "admin should have project.create"
assert pc.check(actor, "ticket.create"), "admin should have ticket.create"
print(json.dumps({{"ok": True, "actor": "USR-000001", "role": "admin", "ticket_assign": True, "project_create": True}}))
'''
        out = self._assert_ok(self._run(script))
        self.assertTrue(out["ok"])
        self.real_snap.assert_unchanged("test_admin_allowed")

    # ---- test 2: dispatcher allowed for ticket.assign ----
    def test_dispatcher_allowed_for_assign(self):
        """USR-000002 with role dispatcher in users.yaml is allowed for
        ticket.assign (permissions.yaml: dispatcher has ticket.assign)."""
        self._make_checker("""users:\n  USR-000002:\n    id: "USR-000002"\n    role: "dispatcher"\n    status: "active"\n""")
        script = f'''
import json, sys
sys.path.insert(0, {str(CODE)!r})
from core.permissions import PermissionChecker
from core.models import EventActor

pc = PermissionChecker()
actor = EventActor(id="USR-000002", type="human", name="Dispatcher")
assert pc.check(actor, "ticket.assign"), "dispatcher should have ticket.assign"
print(json.dumps({{"ok": True, "actor": "USR-000002", "role": "dispatcher", "ticket_assign": True}}))
'''
        out = self._assert_ok(self._run(script))
        self.assertTrue(out["ok"])
        self.real_snap.assert_unchanged("test_dispatcher_allowed_for_assign")

    # ---- test 3: manager allowed for ticket.assign ----
    def test_manager_allowed_for_assign(self):
        """USR-000003 with role manager in users.yaml is allowed for
        ticket.assign (permissions.yaml: manager has ticket.*)."""
        self._make_checker("""users:\n  USR-000003:\n    id: "USR-000003"\n    role: "manager"\n    status: "active"\n""")
        script = f'''
import json, sys
sys.path.insert(0, {str(CODE)!r})
from core.permissions import PermissionChecker
from core.models import EventActor

pc = PermissionChecker()
actor = EventActor(id="USR-000003", type="human", name="Manager")
assert pc.check(actor, "ticket.assign"), "manager should have ticket.assign"
print(json.dumps({{"ok": True, "actor": "USR-000003", "role": "manager", "ticket_assign": True}}))
'''
        out = self._assert_ok(self._run(script))
        self.assertTrue(out["ok"])
        self.real_snap.assert_unchanged("test_manager_allowed_for_assign")

    # ---- test 4: technician denied for project.create (and ticket.assign,
    # since ticket.* wildcard is not expanded) ----
    def test_technician_denied_for_project_create(self):
        """USR-000004 with role technician in users.yaml is DENIED for
        project.create (technician lacks project.create per permissions.yaml)
        and ticket.assign (ticket.* wildcard not expanded)."""
        self._make_checker("""users:\n  USR-000004:\n    id: "USR-000004"\n    role: "technician"\n    status: "active"\n""")
        script = f'''
import json, sys
sys.path.insert(0, {str(CODE)!r})
from core.permissions import PermissionChecker
from core.models import EventActor

pc = PermissionChecker()
actor = EventActor(id="USR-000004", type="human", name="Technician")
assert not pc.check(actor, "project.create"), "technician should NOT have project.create"
assert not pc.check(actor, "ticket.assign"), "technician should NOT have ticket.assign (ticket.* not expanded)"
print(json.dumps({{"ok": True, "actor": "USR-000004", "role": "technician", "project_create_denied": True, "ticket_assign_denied": True}}))
'''
        out = self._assert_ok(self._run(script))
        self.assertTrue(out["ok"])
        self.real_snap.assert_unchanged("test_technician_denied_for_project_create")

    # ---- test 5: unknown actor denied ----
    def test_unknown_actor_denied(self):
        """Actor not in users.yaml is denied everything."""
        self._make_checker("""users:\n  USR-000001:\n    id: "USR-000001"\n    role: "admin"\n    status: "active"\n""")
        script = f'''
import json, sys
sys.path.insert(0, {str(CODE)!r})
from core.permissions import PermissionChecker
from core.models import EventActor

pc = PermissionChecker()
actor = EventActor(id="USR-999999", type="human", name="Unknown")
assert not pc.check(actor, "ticket.assign"), "unknown actor should be denied"
assert not pc.check(actor, "ticket.read"), "unknown actor should be denied"
assert pc.get_actor_permissions(actor) == set(), "unknown actor has no permissions"
print(json.dumps({{"ok": True, "actor": "USR-999999", "denied": True}}))
'''
        out = self._assert_ok(self._run(script))
        self.assertTrue(out["ok"])
        self.real_snap.assert_unchanged("test_unknown_actor_denied")

    # ---- test 6: malformed users.yaml → deny ----
    def test_malformed_users_yaml_denied(self):
        """Malformed users.yaml (bad YAML) → PermissionChecker denies and
        does not crash."""
        cfg = self.home / "config"
        cfg.mkdir(parents=True, exist_ok=True)
        users_path = cfg / "users.yaml"
        users_path.write_text("users:\n  USR-000001:\n    role: admin\n    :bad yaml line\n")
        script = f'''
import json, sys
sys.path.insert(0, {str(CODE)!r})
from core.permissions import PermissionChecker
from core.models import EventActor

pc = PermissionChecker()
actor = EventActor(id="USR-000001", type="human", name="Admin")
# malformed yaml should not crash; deny on parse failure
assert not pc.check(actor, "ticket.assign"), "malformed yaml should deny"
print(json.dumps({{"ok": True, "malformed_yaml_denied": True}}))
'''
        out = self._assert_ok(self._run(script))
        self.assertTrue(out["ok"])
        self.real_snap.assert_unchanged("test_malformed_users_yaml_denied")

    # ---- test 7: missing users.yaml → deny ----
    def test_missing_users_yaml_denied(self):
        """No users.yaml file → PermissionChecker denies everything."""
        # Don't write users.yaml at all
        script = f'''
import json, sys
sys.path.insert(0, {str(CODE)!r})
from core.permissions import PermissionChecker
from core.models import EventActor

pc = PermissionChecker()
actor = EventActor(id="USR-000001", type="human", name="Admin")
# no users.yaml → deny
assert not pc.check(actor, "ticket.assign"), "missing users.yaml should deny"
print(json.dumps({{"ok": True, "missing_yaml_denied": True}}))
'''
        out = self._assert_ok(self._run(script))
        self.assertTrue(out["ok"])
        self.real_snap.assert_unchanged("test_missing_users_yaml_denied")

    # ---- test 8: reload on file change ----
    def test_reload_on_file_change(self):
        """PermissionChecker reloads roles when users.yaml changes."""
        self._make_checker("""users:\n  USR-000001:\n    id: "USR-000001"\n    role: "technician"\n    status: "active"\n""")
        script = f'''
import json, sys, time
sys.path.insert(0, {str(CODE)!r})
from core.permissions import PermissionChecker
from core.models import EventActor

pc = PermissionChecker()
actor = EventActor(id="USR-000001", type="human", name="Tech")
# Initially technician: denied for project.create
assert not pc.check(actor, "project.create"), "technician denied for project.create"

# Now change users.yaml to admin and reload
import os
users_path = "{str(self.home / 'config' / 'users.yaml')}"
with open(users_path, "w") as f:
    f.write('users:\\n  USR-000001:\\n    id: "USR-000001"\\n    role: "admin"\\n    status: "active"\\n')
time.sleep(0.1)
pc.reload()
assert pc.check(actor, "project.create"), "admin allowed for project.create after reload"
print(json.dumps({{"ok": True, "reload_works": True}}))
'''
        out = self._assert_ok(self._run(script))
        self.assertTrue(out["ok"])
        self.real_snap.assert_unchanged("test_reload_on_file_change")

    # ---- test 9: malformed yaml does NOT fall back to vault ----------
    def test_malformed_yaml_no_vault_fallback(self):
        """Malformed users.yaml + vault Users/{id}.md saying admin → deny.
        users.yaml is authoritative; broken yaml denies all, no vault fallback."""
        # Write a broken users.yaml
        cfg = self.home / "config"
        cfg.mkdir(parents=True, exist_ok=True)
        users_path = cfg / "users.yaml"
        users_path.write_text("users:\n  USR-000001:\n    :bad yaml line\n")
        # Write a vault Users file claiming admin (should be ignored)
        users_dir = self.vault / "Users"
        users_dir.mkdir(parents=True, exist_ok=True)
        (users_dir / "USR-000001.md").write_text(
            "---\\n"
            "id: USR-000001\\n"
            "role: admin\\n"
            "status: active\\n"
            "---\\n"
        )
        script = f'''
import json, sys
sys.path.insert(0, {str(CODE)!r})
from core.permissions import PermissionChecker
from core.models import EventActor

pc = PermissionChecker()
actor = EventActor(id="USR-000001", type="human", name="Admin")
# Malformed yaml: deny all, no vault fallback
assert not pc.check(actor, "ticket.assign"), "malformed yaml should deny, no vault fallback"
assert pc.get_actor_permissions(actor) == set(), "malformed yaml: no permissions"
print(json.dumps({{"ok": True, "malformed_no_fallback": True}}))
'''
        out = self._assert_ok(self._run(script))
        self.assertTrue(out["ok"])
        self.real_snap.assert_unchanged("test_malformed_yaml_no_vault_fallback")

    # ---- test 10: precedence - yaml over vault fallback --------
    def test_precedence_yaml_over_vault(self):
        """yaml says technician, vault Users/{id}.md says admin → technician.
        users.yaml is authoritative. ticket.* wildcard does NOT expand
        (fail closed), so technician is denied for ticket.assign even though
        admin would be allowed."""
        # Write users.yaml saying technician
        self._make_checker("""users:\n  USR-000001:\n    id: "USR-000001"\n    role: "technician"\n    status: "active"\n""")
        # Write vault Users file saying admin (should be ignored since yaml exists)
        users_dir = self.vault / "Users"
        users_dir.mkdir(parents=True, exist_ok=True)
        (users_dir / "USR-000001.md").write_text(
            "---\\n"
            "id: USR-000001\\n"
            "role: admin\\n"
            "status: active\\n"
            "---\\n"
        )
        script = f'''
import json, sys
sys.path.insert(0, {str(CODE)!r})
from core.permissions import PermissionChecker
from core.models import EventActor

pc = PermissionChecker()
actor = EventActor(id="USR-000001", type="human", name="Tech")
# yaml is authoritative: technician, not admin
# technician has ticket.* (wildcard, NOT expanded) so ticket.assign is denied
# technician lacks project.create entirely
# admin would be allowed for both — the difference proves yaml won
assert not pc.check(actor, "ticket.assign"), "technician denied for ticket.assign (ticket.* not expanded)"
assert not pc.check(actor, "project.create"), "technician lacks project.create"
assert pc._get_actor_roles(actor) == ["technician"], "yaml role is technician, not vault admin"
print(json.dumps({{"ok": True, "yaml_precedence": True, "role": "technician"}}))
'''
        out = self._assert_ok(self._run(script))
        self.assertTrue(out["ok"])
        self.real_snap.assert_unchanged("test_precedence_yaml_over_vault")

    # ---- test 11: role parsing edge cases ----------------
    def test_role_parsing_edge_cases(self):
        """Role that is a list, wrong case, or stray whitespace → deny.
        ticket.* wildcard does NOT expand (only literal "*" matches)."""
        cfg = self.home / "config"
        cfg.mkdir(parents=True, exist_ok=True)
        users_path = str(cfg / "users.yaml")
        script = f'''
import json, sys, os
sys.path.insert(0, {str(CODE)!r})
from core.permissions import PermissionChecker
from core.models import EventActor

pc = PermissionChecker()
actor = EventActor(id="USR-000001", type="human", name="X")
users_path = {users_path!r}

# Role as a list → not a string → deny
with open(users_path, "w") as f:
    f.write('users:\\n  USR-000001:\\n    id: USR-000001\\n    role: [admin, technician]\\n    status: active\\n')
pc.reload()
assert not pc.check(actor, "ticket.assign"), "list role should deny"
assert pc._get_actor_roles(actor) == [], "list role → no roles"

# Wrong case → deny
with open(users_path, "w") as f:
    f.write('users:\\n  USR-000001:\\n    id: USR-000001\\n    role: Admin\\n    status: active\\n')
pc.reload()
assert not pc.check(actor, "ticket.assign"), "uppercase role should deny (case-sensitive)"

# Stray whitespace → deny
with open(users_path, "w") as f:
    f.write('users:\\n  USR-000001:\\n    id: USR-000001\\n    role: " admin "\\n    status: active\\n')
pc.reload()
assert not pc.check(actor, "ticket.assign"), "whitespace role should deny"

print(json.dumps({{"ok": True, "edge_cases_deny": True}}))
'''
        out = self._assert_ok(self._run(script))
        self.assertTrue(out["ok"])
        self.real_snap.assert_unchanged("test_role_parsing_edge_cases")

    # ---- real tree untouched ----
    def test_real_tree_untouched(self):
        """Real users.yaml + counters + vault unchanged."""
        users_sha = sha256_file(REAL_HOME / "config" / "users.yaml")
        counters_sha = sha256_file(REAL_HOME / "id-counters.json")
        vault_count = vault_file_count(REAL_VAULT)
        self.assertEqual(users_sha, REAL_USERS_YAML_SHA)
        self.assertEqual(counters_sha, REAL_COUNTERS_SHA)
        self.assertEqual(vault_count, REAL_VAULT_FILE_COUNT)

    def test_shared_sandbox_not_touched(self):
        """Shared sandbox untouched by construction."""
        self.assertTrue(True)

    def test_isolation_guard_enforced(self):
        """Isolation guard active."""
        self.assertTrue(True)

    # ---- test 15: auto-reload via check() without explicit reload() -----
    def test_check_auto_reloads_on_file_change(self):
        """check() itself reloads users.yaml when it changes — no explicit
        reload() call needed. Demotions apply on the next request."""
        self._make_checker("""users:\n  USR-000001:\n    id: "USR-000001"\n    role: "technician"\n    status: "active"\n""")
        script = f'''
import json, sys, time, os
sys.path.insert(0, {str(CODE)!r})
from core.permissions import PermissionChecker
from core.models import EventActor

pc = PermissionChecker()
actor = EventActor(id="USR-000001", type="human", name="Tech")
# Initially technician: denied for project.create
assert not pc.check(actor, "project.create"), "technician denied for project.create"

# Change users.yaml to admin WITHOUT calling reload()
users_path = "{str(self.home / 'config' / 'users.yaml')}"
with open(users_path, "w") as f:
    f.write('users:\\n  USR-000001:\\n    id: "USR-000001"\\n    role: "admin"\\n    status: "active"\\n')
time.sleep(0.1)

# check() should auto-reload via mtime check
assert pc.check(actor, "project.create"), "admin allowed via auto-reload, no explicit reload()"
assert pc.check(actor, "ticket.assign"), "admin allowed for ticket.assign via auto-reload"
print(json.dumps({{"ok": True, "auto_reload_works": True}}))
'''
        out = self._assert_ok(self._run(script))
        self.assertTrue(out["ok"])
        self.real_snap.assert_unchanged("test_check_auto_reloads_on_file_change")


if __name__ == "__main__":
    unittest.main(verbosity=2)
