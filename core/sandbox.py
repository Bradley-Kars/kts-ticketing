"""KTS sandbox lifecycle — create / reset / status / env.

The sandbox is a FULL copy of the real vault + real system home in isolated
sibling locations:

    ~/Documents/KTS Records-sandbox/    (vault copy — sibling, never inside)
    ~/.ticketing-sandbox/               (system copy — sibling, never inside)

Activate it with:

    export KTS_SANDBOX=1
    export KTS_VAULT="/home/slayerbrk/Documents/KTS Records-sandbox"
    export KTS_HOME="/home/slayerbrk/.ticketing-sandbox"

Rules enforced here:
  - create/reset REFUSE to run while any sandbox env var is active
    (you cannot create a sandbox of a sandbox, and cannot delete files
    out from under a running sandbox session)
  - no symlinks anywhere: every file is copied (full copy, no seed subset)
  - the real ~/.ticketing/config/.env (Stripe keys) is NEVER copied; the
    sandbox gets its own stub .env with no secrets (mode 600)
  - every target is verified to be a SIBLING of the real location:
    not equal to it, not inside it, and not containing it
"""

import os
import shutil
from pathlib import Path
from typing import Dict, List

from .paths import real_vault_root, real_home_root, is_sandbox_mode

# Files/dirs never copied out of the real system home into the sandbox.
_IGNORE = shutil.ignore_patterns(".env", "__pycache__", "*.pyc")

# Stripe secret-key prefixes that must never appear in a sandbox file.
_KEY_MARKERS = ("sk_live_", "sk_test_", "rk_live_", "rk_test_", "whsec_")

_STUB_ENV = (
    "# KTS sandbox environment - intentionally contains NO real secrets.\n"
    "# Real keys live only in ~/.ticketing/config/.env and are never copied here.\n"
)


class SandboxError(RuntimeError):
    """User-facing sandbox lifecycle error."""


# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

def sandbox_vault_path() -> Path:
    """Sibling-of-real-vault location for the sandbox vault.

    LEXICAL only — deliberately NOT resolved. Resolving would follow a
    planted symlink and hand the guards an already-redirected path
    (they'd never see the symlink). Resolution happens inside the guards,
    where following is done knowingly."""
    real = real_vault_root()
    return real.parent / f"{real.name}-sandbox"


def sandbox_home_path() -> Path:
    """Sibling-of-real-home location for the sandbox system home.

    LEXICAL only — see sandbox_vault_path()."""
    real = real_home_root()
    return real.parent / f"{real.name}-sandbox"


def _assert_isolated(target: Path, real: Path, label: str) -> None:
    """Target must be a sibling of (never equal to / inside / containing) the
    real location. Symlinks are resolved before comparison."""
    t = target.resolve()
    r = real.resolve()
    if t == r:
        raise SandboxError(f"{label}: target {t} IS the real location - refusing")
    if t in r.parents or r in t.parents:
        # one is inside the other (either direction)
        raise SandboxError(
            f"{label}: target {t} overlaps the real location {r} - refusing"
        )
    # real dir must not be reachable as a symlink alias of target
    if t.exists():
        resolved_existing = Path(os.path.realpath(t))
        if resolved_existing == r or r in resolved_existing.parents or resolved_existing in r.parents:
            raise SandboxError(
                f"{label}: target {t} resolves to {resolved_existing}, which "
                f"overlaps the real location {r} - refusing"
            )


def _refuse_if_sandbox_active(action: str) -> None:
    set_vars = [
        v
        for v in ("KTS_SANDBOX", "KTS_VAULT", "KTS_HOME")
        if v in os.environ
    ]
    if set_vars or is_sandbox_mode():
        raise SandboxError(
            f"cannot {action} while sandbox environment variables are active "
            f"({', '.join(set_vars) or 'KTS_SANDBOX'}). Unset KTS_SANDBOX, "
            "KTS_VAULT and KTS_HOME first, then run this from a normal shell."
        )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _count_files(root: Path) -> int:
    """Count regular files below root, excluding ignored patterns."""
    if not root.exists():
        return 0
    n = 0
    for p in root.rglob("*"):
        if not p.is_file():
            continue
        rel_parts = p.relative_to(root).parts
        if any(
            part in (".env", "__pycache__") or part.endswith(".pyc")
            for part in rel_parts
        ):
            continue
        n += 1
    return n


def _has_symlinks(root: Path) -> bool:
    if not root.exists():
        return False
    return any(p.is_symlink() for p in root.rglob("*"))


def _write_stub_env(home_target: Path) -> Path:
    env_path = home_target / "config" / ".env"
    env_path.parent.mkdir(parents=True, exist_ok=True)
    env_path.write_text(_STUB_ENV, encoding="utf-8")
    env_path.chmod(0o600)
    return env_path


def _verify_no_real_keys(env_path: Path) -> None:
    content = env_path.read_text(encoding="utf-8") if env_path.exists() else ""
    lowered = content.lower()
    for marker in _KEY_MARKERS:
        if marker in lowered:
            raise SandboxError(
                f"SAFETY ABORT: sandbox .env appears to contain a real key "
                f"({marker}...). Sandbox left as-is for inspection; real data "
                "was not touched."
            )


def _prepare_target(target: Path, label: str) -> None:
    """Ensure target is a clean sibling location ready for copytree."""
    # is_symlink() first and unconditional: it is true even for BROKEN
    # symlinks, which Path.exists() would report as missing.
    if target.is_symlink():
        raise SandboxError(f"{label}: {target} is a symlink - refusing")
    if target.exists():
        if any(target.iterdir()):
            raise SandboxError(
                f"{label}: {target} already exists and is not empty. "
                "Use `kts sandbox reset` to wipe and recreate it."
            )
        # exists but empty (leftover) -> remove so copytree starts fresh
        target.rmdir()
    target.parent.mkdir(parents=True, exist_ok=True)


# ---------------------------------------------------------------------------
# Lifecycle
# ---------------------------------------------------------------------------

def create() -> Dict[str, object]:
    """Create a fresh sandbox. Refuses if a non-empty sandbox exists."""
    _refuse_if_sandbox_active("create the sandbox")

    real_vault = real_vault_root()
    real_home = real_home_root()
    sb_vault = sandbox_vault_path()
    sb_home = sandbox_home_path()

    if not real_vault.is_dir():
        raise SandboxError(f"real vault not found at {real_vault}")
    if not real_home.is_dir():
        raise SandboxError(f"real system home not found at {real_home}")

    _assert_isolated(sb_vault, real_vault, "sandbox vault")
    _assert_isolated(sb_home, real_home, "sandbox home")

    _prepare_target(sb_vault, "sandbox vault")
    _prepare_target(sb_home, "sandbox home")

    # Full copy of the vault (no .env / __pycache__ / *.pyc anywhere).
    shutil.copytree(real_vault, sb_vault, ignore=_IGNORE, symlinks=False)
    # Full copy of the system home EXCEPT the real .env (Stripe keys).
    shutil.copytree(real_home, sb_home, ignore=_IGNORE, symlinks=False)

    env_path = _write_stub_env(sb_home)
    _verify_no_real_keys(env_path)

    return _summary(real_vault, real_home, sb_vault, sb_home, action="created")


def reset() -> Dict[str, object]:
    """Wipe the sandbox (ONLY the sandbox) and recreate it fresh."""
    _refuse_if_sandbox_active("reset the sandbox")

    real_vault = real_vault_root()
    real_home = real_home_root()
    sb_vault = sandbox_vault_path()
    sb_home = sandbox_home_path()

    # Re-verify isolation BEFORE deleting anything.
    _assert_isolated(sb_vault, real_vault, "sandbox vault")
    _assert_isolated(sb_home, real_home, "sandbox home")

    existed = sb_vault.exists() or sb_home.exists()
    for target, label in ((sb_vault, "sandbox vault"), (sb_home, "sandbox home")):
        # is_symlink() unconditional: true even for broken symlinks.
        if target.is_symlink():
            raise SandboxError(f"{label}: {target} is a symlink - refusing")
        if target.exists():
            shutil.rmtree(target)

    summary = create()
    summary["action"] = "reset (wiped and recreated)" if existed else "created"
    summary["previously_existed"] = existed
    return summary


def status() -> Dict[str, object]:
    """Report active mode, active paths, and sandbox state. Read-only."""
    from .paths import vault_root, home_root

    real_vault = real_vault_root()
    real_home = real_home_root()
    sb_vault = sandbox_vault_path()
    sb_home = sandbox_home_path()

    return {
        "mode": "sandbox" if is_sandbox_mode() else "real",
        "active_vault": str(vault_root()),
        "active_home": str(home_root()),
        "real_vault": str(real_vault),
        "real_home": str(real_home),
        "sandbox_vault": str(sb_vault),
        "sandbox_home": str(sb_home),
        "sandbox_exists": sb_vault.exists() or sb_home.exists(),
        "sandbox_vault_exists": sb_vault.exists(),
        "sandbox_home_exists": sb_home.exists(),
        "sandbox_vault_files": _count_files(sb_vault),
        "sandbox_home_files": _count_files(sb_home),
        "real_vault_files": _count_files(real_vault),
        "activation": activation_env(),
    }


def activation_env() -> Dict[str, str]:
    return {
        "KTS_SANDBOX": "1",
        "KTS_VAULT": str(sandbox_vault_path()),
        "KTS_HOME": str(sandbox_home_path()),
    }


def env_lines() -> List[str]:
    env = activation_env()
    return [
        f'export KTS_SANDBOX="{env["KTS_SANDBOX"]}"',
        f'export KTS_VAULT="{env["KTS_VAULT"]}"',
        f'export KTS_HOME="{env["KTS_HOME"]}"',
    ]


def _summary(real_vault: Path, real_home: Path, sb_vault: Path,
             sb_home: Path, action: str) -> Dict[str, object]:
    real_v_files = _count_files(real_vault)
    sb_v_files = _count_files(sb_vault)
    real_h_files = _count_files(real_home)
    sb_h_files = _count_files(sb_home)

    env_path = sb_home / "config" / ".env"
    _verify_no_real_keys(env_path)

    problems: List[str] = []
    if sb_v_files != real_v_files:
        problems.append(
            f"vault file count mismatch: real={real_v_files} sandbox={sb_v_files}"
        )
    # home copy excludes .env + __pycache__ + *.pyc on BOTH sides of the count
    if sb_h_files != real_h_files:
        problems.append(
            f"home file count mismatch: real={real_h_files} sandbox={sb_h_files}"
        )
    if _has_symlinks(sb_vault) or _has_symlinks(sb_home):
        problems.append("sandbox contains symlinks (must be plain copies)")
    if not env_path.exists():
        problems.append("sandbox .env missing")

    return {
        "action": action,
        "sandbox_vault": str(sb_vault),
        "sandbox_home": str(sb_home),
        "vault_files": sb_v_files,
        "home_files": sb_h_files,
        "real_vault_files": real_v_files,
        "real_home_files": real_h_files,
        "sandbox_env_has_no_real_keys": True,
        "verified": not problems,
        "problems": problems,
        "activation": activation_env(),
    }
