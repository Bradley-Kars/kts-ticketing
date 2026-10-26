"""A4: sandbox isolation test suite for KTS Records ticketing.

Stdlib unittest only. Each case runs a fresh subprocess with its own
environment (PYTHONDONTWRITEBYTECODE=1, no KTS_* inherited unless the
test sets all three). Refusal cases assert the guard's exact FATAL text.
"""

import hashlib
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
SB_VAULT = HOME / "Documents" / "KTS Records-sandbox"
SB_HOME = HOME / ".ticketing-sandbox"
TICKET = HOME / ".local" / "bin" / "ticket"
CLI = REAL_HOME / "cli.py"
FIXTURES = Path(__file__).resolve().parent / "fixtures"


def base_env(**extra):
    """Fresh env: system essentials only, zero KTS_* unless passed in."""
    env = {
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        "HOME": str(HOME),
        "LANG": os.environ.get("LANG", "C.UTF-8"),
        "PYTHONDONTWRITEBYTECODE": "1",
    }
    env.update(extra)
    return env


def sb_env(**extra):
    """Sandbox env: all three KTS vars set correctly."""
    env = base_env(
        KTS_SANDBOX="1",
        KTS_VAULT=str(SB_VAULT),
        KTS_HOME=str(SB_HOME),
    )
    env.update(extra)
    return env


def run(env, *cmd, timeout=120):
    """Run one command in a fresh subprocess. Returns CompletedProcess."""
    return subprocess.run(
        list(map(str, cmd)),
        env=env,
        capture_output=True,
        text=True,
        timeout=timeout,
    )


def manifest(root: Path, exclude_pycache: bool = True) -> str:
    """Sha256 over sorted 'sha256  relpath' lines. Skips __pycache__/*.pyc
    (python bytecode writes are excluded by design, reported as such) and
    config/.env (G1: secrets are never hashed or listed)."""
    lines = []
    for p in sorted(root.rglob("*")):
        if not p.is_file() or p.is_symlink():
            continue
        rel = p.relative_to(root)
        if str(rel).endswith("config/.env"):
            continue
        if exclude_pycache and (
            "__pycache__" in rel.parts or p.suffix == ".pyc"
        ):
            continue
        h = hashlib.sha256(p.read_bytes()).hexdigest()
        lines.append(f"{h}  {rel}")
    return hashlib.sha256("\n".join(lines).encode()).hexdigest()


def file_count(root: Path, exclude_pycache: bool = True) -> int:
    n = 0
    for p in root.rglob("*"):
        if not p.is_file() or p.is_symlink():
            continue
        rel = p.relative_to(root)
        if str(rel).endswith("config/.env"):
            continue
        if exclude_pycache and (
            "__pycache__" in rel.parts or p.suffix == ".pyc"
        ):
            continue
        n += 1
    return n


class TestIsolation(unittest.TestCase):
    maxDiff = None

    # ------------------------------------------------------------------
    # test01: real vault passed as KTS_VAULT must be refused
    # ------------------------------------------------------------------
    def test01_real_vault_refused(self):
        r = run(
            base_env(
                KTS_SANDBOX="1",
                KTS_VAULT=str(REAL_VAULT),
                KTS_HOME=str(SB_HOME),
            ),
            TICKET, "list",
        )
        out = r.stdout + r.stderr
        self.assertNotEqual(r.returncode, 0, out)
        self.assertIn("FATAL: KTS_VAULT overlaps the real vault", out)

    # ------------------------------------------------------------------
    # test02: real home passed as KTS_HOME must be refused
    # ------------------------------------------------------------------
    def test02_real_home_refused(self):
        r = run(
            base_env(
                KTS_SANDBOX="1",
                KTS_VAULT=str(SB_VAULT),
                KTS_HOME=str(REAL_HOME),
            ),
            TICKET, "list",
        )
        out = r.stdout + r.stderr
        self.assertNotEqual(r.returncode, 0, out)
        self.assertIn("FATAL: KTS_HOME overlaps the real KTS home", out)

    # ------------------------------------------------------------------
    # test03: every partial combination of the three vars must be refused
    # ------------------------------------------------------------------
    def test03_partial_combos_refused(self):
        vals = {"KTS_SANDBOX": "1", "KTS_VAULT": str(SB_VAULT),
                "KTS_HOME": str(SB_HOME)}
        keys = ["KTS_SANDBOX", "KTS_VAULT", "KTS_HOME"]
        combos = []
        for mask in range(1, 7):  # all non-empty subsets
            subset = [k for i, k in enumerate(keys) if mask & (1 << i)]
            combos.append(subset)
        self.assertEqual(len(combos), 6)
        for subset in combos:
            env = base_env(**{k: vals[k] for k in subset})
            r = run(env, TICKET, "list")
            out = r.stdout + r.stderr
            self.assertNotEqual(
                r.returncode, 0, f"combo {subset} passed: {out}"
            )
            self.assertIn(
                "FATAL: partial sandbox configuration", out,
                f"combo {subset}: {out}",
            )

    # ------------------------------------------------------------------
    # test04: KTS_SANDBOX values other than "1" must be refused
    # ------------------------------------------------------------------
    def test04_sandbox_flag_values_refused(self):
        for bad in ("0", "", "yes"):
            env = base_env(
                KTS_SANDBOX=bad,
                KTS_VAULT=str(SB_VAULT),
                KTS_HOME=str(SB_HOME),
            )
            r = run(env, TICKET, "list")
            out = r.stdout + r.stderr
            self.assertNotEqual(r.returncode, 0, f"KTS_SANDBOX={bad!r}: {out}")
            self.assertIn("KTS_SANDBOX is set to", out)
            self.assertIn('must be', out)

    # ------------------------------------------------------------------
    # test05: KTS_VAULT as a symlink pointing at the real vault refused
    # ------------------------------------------------------------------
    def test05_symlink_to_real_vault_refused(self):
        tmp = Path(tempfile.mkdtemp(prefix="kts-a4-link-"))
        link = tmp / "vault-link"
        try:
            link.symlink_to(REAL_VAULT)
            env = base_env(
                KTS_SANDBOX="1",
                KTS_VAULT=str(link),
                KTS_HOME=str(SB_HOME),
            )
            r = run(env, TICKET, "list")
            out = r.stdout + r.stderr
            self.assertNotEqual(r.returncode, 0, out)
            # resolve() follows the symlink to the real vault -> overlap
            self.assertIn("FATAL: KTS_VAULT overlaps the real vault", out)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    # ------------------------------------------------------------------
    # test06: symlink in place of the sandbox vault: sandbox create must
    # refuse. Parks only the sandbox vault, restores in finally, then
    # proves counts + manifest match the pre-park state.
    # ------------------------------------------------------------------
    def test06_symlink_target_refused_park_restore(self):
        self.assertTrue(SB_VAULT.is_dir(), f"sandbox vault missing: {SB_VAULT}")
        before_count = file_count(SB_VAULT)
        before_manifest = manifest(SB_VAULT)
        park = SB_VAULT.parent / (SB_VAULT.name + ".parked-a4")
        self.assertFalse(park.exists(), f"park path already exists: {park}")
        isolated = Path(tempfile.mkdtemp(prefix="kts-a4-isolated-"))
        os.rename(SB_VAULT, park)          # park (only the sandbox vault)
        link = SB_VAULT
        try:
            link.symlink_to(isolated, target_is_directory=True)
            env = base_env(PYTHONPATH=str(REAL_HOME))
            r = run(env, "python3", CLI, "sandbox", "create")
            out = r.stdout + r.stderr
            self.assertNotEqual(r.returncode, 0, out)
            self.assertIn("is a symlink - refusing", out)
        finally:
            if link.is_symlink():
                link.unlink()
            if park.exists() and not SB_VAULT.exists():
                os.rename(park, SB_VAULT)  # restore
            shutil.rmtree(isolated, ignore_errors=True)
        # prove restoration
        self.assertTrue(SB_VAULT.is_dir(), "sandbox vault not restored")
        self.assertFalse(SB_VAULT.is_symlink(), "sandbox vault still a link")
        self.assertFalse(park.exists(), "park leftover not restored")
        after_count = file_count(SB_VAULT)
        after_manifest = manifest(SB_VAULT)
        print(f"\n[test06] sandbox vault files before={before_count} "
              f"after={after_count}")
        self.assertEqual(before_count, after_count)
        self.assertEqual(before_manifest, after_manifest)

    # ------------------------------------------------------------------
    # test07: --vault that disagrees with the active vault refused;
    # a matching --vault is accepted.
    # ------------------------------------------------------------------
    def test07_vault_disagreement_refused(self):
        env = sb_env(PYTHONPATH=str(REAL_HOME))
        r = run(env, "python3", CLI, "--vault", "/tmp/kts-a4-never-matches",
                "ticket", "list")
        out = r.stdout + r.stderr
        self.assertNotEqual(r.returncode, 0, out)
        self.assertIn("disagrees with the active", out)
        # matching --vault must be accepted
        r2 = run(env, "python3", CLI, "--vault", str(SB_VAULT),
                 "ticket", "list")
        self.assertEqual(
            r2.returncode, 0, r2.stdout + r2.stderr
        )

    # ------------------------------------------------------------------
    # test08: sibling sandbox path is accepted, not treated as inside
    # ~/Documents/KTS Records.
    # ------------------------------------------------------------------
    def test08_sibling_accepted_not_inside(self):
        probe = (
            "import sys; sys.path.insert(0, sys.argv[1]); "
            "from core.paths import is_sandbox_mode, vault_root, "
            "real_vault_root, home_root; "
            "v = vault_root(); rv = real_vault_root(); "
            "inside = str(v).startswith(str(rv) + '/'); "
            "print(is_sandbox_mode()); print(v); print(inside)"
        )
        r = run(sb_env(), "python3", "-c", probe, str(REAL_HOME))
        out = (r.stdout + r.stderr).strip()
        self.assertEqual(r.returncode, 0, out)
        # stdout: one field per line (vault path may contain spaces)
        lines = r.stdout.strip().splitlines()
        self.assertEqual(len(lines), 3, out)
        self.assertEqual(lines[0].strip(), "True", out)
        self.assertEqual(Path(lines[1].strip()).resolve(),
                         SB_VAULT.resolve(), out)
        self.assertEqual(lines[2].strip(), "False", out)  # NOT inside
        # and it actually works
        r2 = run(sb_env(), TICKET, "list", "--all")
        self.assertEqual(r2.returncode, 0, r2.stdout + r2.stderr)

    # ------------------------------------------------------------------
    # test09: real .env unreadable from sandbox.
    # (a) with the REAL home: safe_read(real .env) refused (path outside
    #     the sandbox). (b) fake real home under /tmp with a fake .env
    #     (non-secret markers, lives under /tmp, NEVER the real path):
    #     sandbox starts, load_env returns sandbox stub contents only,
    #     safe_read refuses the real path.
    # ------------------------------------------------------------------
    def test09_real_env_unreadable(self):
        probe_a = """
import sys
sys.path.insert(0, sys.argv[1])
from core.paths import real_env_file, is_sandbox_mode
from core.safe_io import safe_read
print("SANDBOX_MODE", is_sandbox_mode())
re = real_env_file()
print("REAL_ENV_EXISTS", re.exists())
try:
    safe_read(re)
    print("REAL_READ ALLOWED")
except PermissionError:
    print("REAL_READ REFUSED")
"""
        r = run(sb_env(PYTHONPATH=str(REAL_HOME)),
                "python3", "-c", probe_a, str(REAL_HOME))
        out = r.stdout + r.stderr
        self.assertEqual(r.returncode, 0, out)
        self.assertIn("SANDBOX_MODE True", out)
        self.assertIn("REAL_READ REFUSED", out)

        # (b) fake real home + fake .env under /tmp only
        fake = Path(tempfile.mkdtemp(prefix="kts-a4-fakehome-"))
        try:
            fenv = fake / ".ticketing" / "config" / ".env"
            fenv.parent.mkdir(parents=True)
            fenv.write_text(
                "FAKE_REAL_MARKER=a4fakevalue123\n"
                "ANOTHER_FAKE=justamarker\n"
            )
            probe_b = """
import sys
sys.path.insert(0, sys.argv[1])
from core.paths import real_env_file, home_root, vault_root, is_sandbox_mode
from core.safe_io import load_env, safe_read
print("SANDBOX_MODE", is_sandbox_mode())
print("VAULT", vault_root())
print("HOME_ROOT", home_root())
re = real_env_file()
print("REAL_ENV_EXISTS", re.exists())
env = load_env()
print("ENV_KEYS", len(env))
print("MARKER_LEAK", any("FAKE_REAL_MARKER" in k for k in env))
stub = (home_root() / "config" / ".env").read_text()
exp = {}
for ln in stub.splitlines():
    ln = ln.strip()
    if ln and not ln.startswith("#") and "=" in ln:
        k, _, v = ln.partition("=")
        exp[k.strip()] = v.strip().strip(chr(34)).strip("'")
print("ENV_MATCH_STUB", env == exp)
try:
    safe_read(re)
    print("REAL_READ ALLOWED")
except PermissionError:
    print("REAL_READ REFUSED")
"""
            env_b = sb_env(HOME=str(fake), PYTHONPATH=str(REAL_HOME))
            r = run(env_b, "python3", "-c", probe_b, str(REAL_HOME))
            out = r.stdout + r.stderr
            self.assertEqual(r.returncode, 0, out)
            self.assertIn("SANDBOX_MODE True", out)
            self.assertIn("REAL_ENV_EXISTS True", out)   # fake .env seen
            self.assertIn("MARKER_LEAK False", out)      # not in load_env
            self.assertIn("ENV_MATCH_STUB True", out)    # sandbox only
            self.assertIn("REAL_READ REFUSED", out)
            # Sandbox startup under the fake HOME is proven by probe_b
            # itself (exit 0: guards passed, load_env ran, only stub
            # contents). The `ticket` CLI cannot run with a fake HOME
            # because it locates its paths module via ~/.ticketing, which
            # by design does not exist under /tmp; that is a CLI lookup
            # limitation, not a guard bypass.
        finally:
            shutil.rmtree(fake, ignore_errors=True)

    # ------------------------------------------------------------------
    # test10: real vault + real home hashes unchanged across (1) a
    # normal-mode validate and (2) a sandbox mutation. pycache excluded.
    # ------------------------------------------------------------------
    def test10_real_hashes_unchanged(self):
        v1 = manifest(REAL_VAULT)
        h1 = manifest(REAL_HOME)
        n_v = file_count(REAL_VAULT)
        n_h = file_count(REAL_HOME)
        print(f"\n[test10] real vault files={n_v} real home files={n_h} "
              "(pycache excluded)")
        # (1) normal-mode validate
        env = base_env(PYTHONPATH=str(REAL_HOME))
        r = run(env, "python3", CLI, "validate")
        out = r.stdout + r.stderr
        self.assertEqual(r.returncode, 0, out)
        self.assertIn("0 errors", out)
        v2 = manifest(REAL_VAULT)
        h2 = manifest(REAL_HOME)
        if v2 != v1 or h2 != h1:
            self.fail(
                "STOP: normal-mode validate changed real file hashes. "
                f"vault_changed={v2 != v1} home_changed={h2 != h1}"
            )
        # (2) sandbox mutation (creates a ticket inside the sandbox only)
        r2 = run(sb_env(), TICKET, "new",
                 "--client", "A4 Hash Probe",
                 "--title", "sandbox mutation probe")
        out2 = r2.stdout + r2.stderr
        self.assertEqual(r2.returncode, 0, out2)
        v3 = manifest(REAL_VAULT)
        h3 = manifest(REAL_HOME)
        if v3 != v1 or h3 != h1:
            self.fail(
                "STOP: sandbox mutation changed real file hashes. "
                f"vault_changed={v3 != v1} home_changed={h3 != h1}"
            )
        print("[test10] validate: 0 change; sandbox mutation: 0 change")

    # ------------------------------------------------------------------
    # test11: normal mode still works (no KTS_* set): list + validate
    # ------------------------------------------------------------------
    def test11_normal_mode_works(self):
        r = run(base_env(), TICKET, "list", "--all")
        out = r.stdout + r.stderr
        self.assertEqual(r.returncode, 0, out)
        rows = [l for l in r.stdout.splitlines() if "TKT-" in l]
        print(f"[test11] list --all rows={len(rows)}")
        self.assertEqual(len(rows), 4, out)
        r2 = run(base_env(PYTHONPATH=str(REAL_HOME)),
                 "python3", CLI, "validate")
        out2 = r2.stdout + r2.stderr
        self.assertEqual(r2.returncode, 0, out2)
        self.assertIn("0 errors", out2)

    # ------------------------------------------------------------------
    # test12: default actor on a new event is Bradley Kars
    # ------------------------------------------------------------------
    def test12_default_actor(self):
        # ID bug fixed 2026-10-06 (models worklog validator + list()
        # reports skips): the reset-first workaround is gone. test10's
        # standalone-created ticket stays visible to list()/next_id(),
        # so this API create proceeds on a dirty sandbox safely.
        # API create runs WITH the sandbox env, as required
        r = run(sb_env(), "python3", CLI, "ticket", "new",
                "--client", "A4 Actor Probe", "--title", "actor probe ticket")
        out = r.stdout + r.stderr
        self.assertEqual(r.returncode, 0, out)
        # newest event file in the sandbox home (never the real home)
        evroot = SB_HOME / "events"
        evs = [p for p in evroot.rglob("EVT-*.json") if p.is_file()]
        self.assertTrue(evs, f"no events under {evroot}")
        newest = max(evs, key=lambda p: p.stat().st_mtime)
        txt = newest.read_text(encoding="utf-8")
        import json as _json
        doc = _json.loads(txt)
        actor_name = (doc.get("actor") or {}).get("name")
        created_by = doc.get("created_by")
        print(f"\n[test12] newest event={newest.name} "
              f"actor.name={actor_name!r} actor.id="
              f"{(doc.get('actor') or {}).get('id')!r} "
              f"created_by={created_by!r}")
        # Display name must be Bradley Kars; created_by is the actor ID,
        # and Bradley's default human actor ID is USR-000001.
        self.assertEqual(actor_name, "Bradley Kars")
        self.assertEqual(created_by, "USR-000001")

    # ------------------------------------------------------------------
    # test13: parity original (RECONSTRUCTED fixture, sandbox-only) vs
    # live ~/.local/bin/ticket, sandbox only: new, log, status, next-id.
    # Throwaway ticket is snapshotted/restored between runs, deleted last.
    # ------------------------------------------------------------------
    def test13_parity_new_log_status_nextid(self):
        fix = FIXTURES / "ticket_orig_sandbox.py"
        self.assertTrue(fix.is_file(), f"fixture missing: {fix}")
        orig_env = base_env()          # fixture has no sandbox logic
        live_env = sb_env()

        # 1) next-id parity (no state change)
        a = run(orig_env, "python3", fix, "next-id")
        b = run(live_env, TICKET, "next-id")
        self.assertEqual(a.returncode, 0, a.stdout + a.stderr)
        self.assertEqual(b.returncode, 0, b.stdout + b.stderr)
        print(f"[test13] next-id orig={a.stdout.strip()} "
              f"live={b.stdout.strip()}")
        self.assertEqual(a.stdout, b.stdout)
        tid = a.stdout.strip()
        self.assertRegex(tid, r"^TKT-\d{4}-\d{3}$")

        # 2) new parity: orig creates -> snapshot bytes -> delete (restore
        #    state) -> live creates -> compare output and file bytes
        args_new = ["new", "--client", "Parity Probe",
                    "--title", "A4 parity throwaway"]
        a = run(orig_env, "python3", fix, *args_new)
        self.assertEqual(a.returncode, 0, a.stdout + a.stderr)
        import glob as _glob
        pat = str(SB_VAULT / "Tickets" / f"{tid} - Parity Probe - *.md")
        files = _glob.glob(pat)
        self.assertEqual(len(files), 1, f"expected 1 throwaway, got {files}")
        bytes_orig = Path(files[0]).read_bytes()
        Path(files[0]).unlink()          # restore pre-new state
        b = run(live_env, TICKET, *args_new)
        self.assertEqual(b.returncode, 0, b.stdout + b.stderr)
        files = _glob.glob(pat)
        self.assertEqual(len(files), 1, f"expected 1 throwaway, got {files}")
        throw = Path(files[0])
        bytes_live = throw.read_bytes()
        print(f"[test13] new out equal={a.stdout == b.stdout} "
              f"bytes equal={bytes_orig == bytes_live}")
        self.assertEqual(a.stdout, b.stdout)
        self.assertEqual(bytes_orig, bytes_live)

        # 3) log parity: snapshot -> orig -> restore -> live -> compare
        def parity(mutate_args, label):
            snap = throw.read_bytes()
            o = run(orig_env, "python3", fix, *mutate_args)
            self.assertEqual(o.returncode, 0, o.stdout + o.stderr)
            bytes_o = throw.read_bytes()
            throw.write_bytes(snap)       # restore
            l = run(live_env, TICKET, *mutate_args)
            self.assertEqual(l.returncode, 0, l.stdout + l.stderr)
            bytes_l = throw.read_bytes()
            print(f"[test13] {label} out equal={o.stdout == l.stdout} "
                  f"bytes equal={bytes_o == bytes_l}")
            self.assertEqual(o.stdout, l.stdout, f"{label} stdout")
            self.assertEqual(bytes_o, bytes_l, f"{label} file bytes")

        parity(["log", tid, "--date", "2026-10-06", "--mode", "remote",
                "--hours", "1", "--amount", "100",
                "--payment", "uninvoiced", "--note", "parity check"], "log")
        parity(["status", tid, "--status", "in-progress"], "status")

        # 4) delete the throwaway (sandbox only, at the end)
        throw.unlink()
        self.assertFalse(throw.exists())

    # ------------------------------------------------------------------
    # test90: reset the sandbox in a fresh subprocess; prove fresh+usable
    # ------------------------------------------------------------------
    def test90_reset_leaves_usable_sandbox(self):
        r = run(base_env(PYTHONPATH=str(REAL_HOME)),
                "python3", CLI, "sandbox", "reset")
        out = r.stdout + r.stderr
        self.assertEqual(r.returncode, 0, out)
        import re as _re
        mv = _re.search(r"vault:\s+(\d+) files", out)
        mh = _re.search(r"home:\s+(\d+) files", out)
        verified = "verified: True" in out
        self.assertTrue(mv and mh, out)
        vfiles, hfiles = int(mv.group(1)), int(mh.group(1))
        # expected = same counting logic the sandbox uses, on the real
        # sources (pycache/.env excluded by _count_files)
        sys.path.insert(0, str(REAL_HOME))
        from core.sandbox import _count_files
        exp_v = _count_files(REAL_VAULT)
        exp_h = _count_files(REAL_HOME)
        # print actual vs expected BEFORE asserting
        print(f"[test90] after reset: vault={vfiles} (expected {exp_v}) "
              f"home={hfiles} (expected {exp_h}) verified={verified}")
        self.assertTrue(verified, out)
        self.assertEqual(vfiles, exp_v, out)
        self.assertEqual(hfiles, exp_h, out)
        # usable: sandbox ticket list works
        r2 = run(sb_env(), TICKET, "list", "--all")
        out2 = r2.stdout + r2.stderr
        self.assertEqual(r2.returncode, 0, out2)
        rows = [l for l in r2.stdout.splitlines() if "TKT-" in l]
        print(f"[test90] ticket list --all rows={len(rows)}")
        self.assertEqual(len(rows), 4, out2)
