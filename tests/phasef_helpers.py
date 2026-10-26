"""Shared helpers for the Phase F test files (Bradley, 2026-10-07).

Harness style mirrors test_idbug.py: stdlib only, fresh subprocess per
step, no KTS_* inherited from the parent, all data access sandbox-only.

snapshot()/restore() give every test a full byte-level snapshot of a
directory tree (files, symlinks, dirs) and a restore that undoes
anything the test added, changed, or removed. That lets tests mutate
sandbox data freely (bump counters, create tickets, run migrations)
without leaking state between tests, and lets the real-data guard test
prove byte-identity of the real vault and real home.
"""

import os
import subprocess
from pathlib import Path

HOME = Path.home()
REAL_VAULT = HOME / "Documents" / "KTS Records"
REAL_HOME = HOME / ".ticketing"
SB_VAULT = HOME / "Documents" / "KTS Records-sandbox"
SB_HOME = HOME / ".ticketing-sandbox"
CODE_HOME = Path(os.environ.get("KTS_CODE_HOME", str(REAL_HOME)))
CLI = CODE_HOME / "cli.py"
TICKET_BIN = HOME / ".local" / "bin" / "ticket"


def base_env(**extra):
    """Fresh env: system essentials only, zero KTS_* unless passed in."""
    env = {
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        "HOME": str(HOME),
        "LANG": os.environ.get("LANG", "C.UTF-8"),
        "PYTHONDONTWRITEBYTECODE": "1",
        "PYTHONPATH": str(CODE_HOME),
    }
    env.update(extra)
    return env


def sb_env(**extra):
    """Sandbox env: all three KTS vars set, code from CODE_HOME."""
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


def _skippable(rel):
    # G1: config/.env holds Stripe keys — never snapshot, hash, or restore it.
    return ("__pycache__" in rel or rel.endswith(".pyc")
            or rel.endswith("config/.env"))


def snapshot(root):
    """Byte snapshot of a tree: {files: {rel: bytes}, links: {rel: target},
    dirs: set(rel)}."""
    files, links, dirs = {}, {}, set()
    root = Path(root)
    if not root.exists():
        return {"files": files, "links": links, "dirs": dirs}
    for p in sorted(root.rglob("*")):
        rel = str(p.relative_to(root))
        if _skippable(rel):
            continue
        if p.is_symlink():
            links[rel] = os.readlink(p)
        elif p.is_dir():
            dirs.add(rel)
        elif p.is_file():
            files[rel] = p.read_bytes()
    return {"files": files, "links": links, "dirs": dirs}


def restore(root, snap):
    """Restore a tree to a snapshot: remove additions, rewrite changes,
    re-add removals (files, symlinks, empty dirs)."""
    root = Path(root)
    cur = snapshot(root)

    # 1. remove anything the test added
    for rel in sorted(set(cur["files"]) - set(snap["files"])):
        try:
            (root / rel).unlink()
        except FileNotFoundError:
            pass
    for rel in sorted(set(cur["links"]) - set(snap["links"])):
        try:
            (root / rel).unlink()
        except FileNotFoundError:
            pass

    # 2. rewrite changed files, re-add removed files
    for rel, data in snap["files"].items():
        p = root / rel
        if cur["files"].get(rel) != data:
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_bytes(data)

    # 3. re-add / retarget symlinks
    for rel, tgt in snap["links"].items():
        p = root / rel
        if rel in cur["links"]:
            if cur["links"][rel] != tgt:
                p.unlink()
                p.parent.mkdir(parents=True, exist_ok=True)
                p.symlink_to(tgt)
        else:
            p.parent.mkdir(parents=True, exist_ok=True)
            try:
                p.symlink_to(tgt)
            except FileExistsError:
                pass

    # 4. prune dirs the test added (bottom-up), re-create dirs it removed
    for d in sorted(set(cur["dirs"]) - snap["dirs"], reverse=True):
        dp = root / d
        if dp.is_dir():
            try:
                dp.rmdir()
            except OSError:
                pass
    for d in sorted(snap["dirs"] - set(cur["dirs"])):
        (root / d).mkdir(parents=True, exist_ok=True)


def snapshot_pair():
    return {"home": snapshot(SB_HOME), "vault": snapshot(SB_VAULT)}


def restore_pair(before):
    restore(SB_HOME, before["home"])
    restore(SB_VAULT, before["vault"])


def require_sandbox(testcase):
    if not SB_HOME.exists() or not SB_VAULT.exists():
        testcase.skipTest("sandbox missing; run `kts sandbox reset` first")
