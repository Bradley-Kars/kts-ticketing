"""KTS Records — central paths module. SINGLE SOURCE OF TRUTH FOR ALL PATHS.

Environment variables (evaluated once, at import time — tests must launch
fresh subprocesses per environment):
  KTS_VAULT   — path to the markdown+YAML vault directory
  KTS_HOME    — path to the KTS package home (.ticketing)
  KTS_SANDBOX — "1" to activate sandbox mode

Activation rule: if ANY of the three variables is present in the
environment (even empty or "0"), ALL THREE must be present, KTS_SANDBOX
must be "1", and both paths must resolve to locations completely separate
from the real vault and real ~/.ticketing. Any partial or inconsistent
configuration refuses to start.

If NONE of the three is present, the real locations are used.

In sandbox mode:
  - no path may resolve to, inside, or contain the real vault or real home
  - the real ~/.ticketing/config/.env (Stripe keys) must never be read
  - all writes must go through core.safe_io, which validates every target

Import path functions from this module — never hardcode
~/Documents/KTS Records or ~/.ticketing anywhere else.
"""

import os
from pathlib import Path

# ---------------------------------------------------------------------------
# Real (non-overridable) locations — used for guard comparisons only
# ---------------------------------------------------------------------------
_REAL_VAULT_ROOT = Path(os.path.expanduser("~/Documents/KTS Records")).resolve()
_REAL_HOME_ROOT = Path(os.path.expanduser("~/.ticketing")).resolve()

# ---------------------------------------------------------------------------
# Raw environment values (None = not set)
# ---------------------------------------------------------------------------
_ENV_SANDBOX = os.environ.get("KTS_SANDBOX")        # may be "" or "0" — still "set"
_ENV_VAULT = os.environ.get("KTS_VAULT")
_ENV_HOME = os.environ.get("KTS_HOME")

_SANDBOX_SET = "KTS_SANDBOX" in os.environ
_VAULT_SET = "KTS_VAULT" in os.environ
_HOME_SET = "KTS_HOME" in os.environ


def _is_inside(child: Path, parent: Path) -> bool:
    """True if resolved child is strictly inside resolved parent."""
    return str(child).startswith(str(parent) + os.sep)


def _resolve_env() -> tuple[Path, Path, bool]:
    """Resolve active (vault, home, sandbox_mode). Raises SystemExit on any
    invalid or partial configuration."""
    any_set = _SANDBOX_SET or _VAULT_SET or _HOME_SET

    if not any_set:
        # Real mode — nothing overridden.
        return _REAL_VAULT_ROOT, _REAL_HOME_ROOT, False

    # At least one var is set: all three must be set and valid.
    missing = [n for n, s in (("KTS_SANDBOX", _SANDBOX_SET),
                              ("KTS_VAULT", _VAULT_SET),
                              ("KTS_HOME", _HOME_SET)) if not s]
    if missing:
        raise SystemExit(
            "FATAL: partial sandbox configuration. These environment variables "
            f"are missing: {', '.join(missing)}. When any of KTS_SANDBOX / "
            "KTS_VAULT / KTS_HOME is set, ALL THREE must be set. "
            "Unset them all to run against the real data."
        )

    if _ENV_SANDBOX != "1":
        raise SystemExit(
            f"FATAL: KTS_SANDBOX is set to {_ENV_SANDBOX!r} but must be "
            '"1" whenever KTS_VAULT or KTS_HOME is set. '
            "Unset all three to run against the real data."
        )

    vault = Path(_ENV_VAULT).resolve()
    home = Path(_ENV_HOME).resolve()

    # Each must be separate from the real location — no overlap either way.
    if vault == _REAL_VAULT_ROOT or _is_inside(vault, _REAL_VAULT_ROOT) \
            or _is_inside(_REAL_VAULT_ROOT, vault):
        raise SystemExit(
            "FATAL: KTS_VAULT overlaps the real vault "
            f"({vault} vs {_REAL_VAULT_ROOT}). Sandbox must use a "
            "completely separate directory (e.g. .../KTS Records-sandbox)."
        )

    if home == _REAL_HOME_ROOT or _is_inside(home, _REAL_HOME_ROOT) \
            or _is_inside(_REAL_HOME_ROOT, home):
        raise SystemExit(
            "FATAL: KTS_HOME overlaps the real KTS home "
            f"({home} vs {_REAL_HOME_ROOT}). Sandbox must use a "
            "completely separate directory (e.g. ~/.ticketing-sandbox)."
        )

    # Sandbox home must not contain the real .env reachable by config_dir.
    return vault, home, True


_VAULT_ROOT, _HOME_ROOT, _SANDBOX_MODE = _resolve_env()


# ---------------------------------------------------------------------------
# Public accessors
# ---------------------------------------------------------------------------
def is_sandbox_mode() -> bool:
    return _SANDBOX_MODE


def vault_root() -> Path:
    return _VAULT_ROOT


def home_root() -> Path:
    return _HOME_ROOT


def real_vault_root() -> Path:
    return _REAL_VAULT_ROOT


def real_home_root() -> Path:
    return _REAL_HOME_ROOT


def enforce_isolation() -> None:
    """Re-check sandbox invariants. Called before every mutating operation.

    In sandbox mode this must never pass if the process is about to touch
    real locations. Reads of the real .env are blocked by safe_io, not here.
    """
    if not _SANDBOX_MODE:
        return
    # Re-verify against the live environment (guards against a process
    # mutating os.environ after import).
    env_now = os.environ.get("KTS_VAULT"), os.environ.get("KTS_HOME")
    if env_now[0] is None or env_now[1] is None:
        raise SystemExit(
            "FATAL: sandbox variables disappeared from the environment at runtime."
        )
    v = Path(env_now[0]).resolve()
    h = Path(env_now[1]).resolve()
    if v == _REAL_VAULT_ROOT or h == _REAL_HOME_ROOT:
        raise SystemExit("FATAL: sandbox paths were redirected to real locations at runtime.")


# ---------------------------------------------------------------------------
# Vault folders (all under vault_root)
# ---------------------------------------------------------------------------
def tickets_dir() -> Path:
    return vault_root() / "Tickets"

def clients_dir() -> Path:
    return vault_root() / "Clients"

def contacts_dir() -> Path:
    return vault_root() / "Contacts"

def organizations_dir() -> Path:
    return vault_root() / "Organizations"

def assets_dir() -> Path:
    return vault_root() / "Assets"

def users_dir() -> Path:
    return vault_root() / "Users"

def projects_dir() -> Path:
    return vault_root() / "Projects"

def tasks_dir() -> Path:
    return vault_root() / "Tasks"

def knowledge_dir() -> Path:
    return vault_root() / "Knowledge"

def problems_dir() -> Path:
    return vault_root() / "Problems"

def changes_dir() -> Path:
    return vault_root() / "Changes"

def requests_dir() -> Path:
    return vault_root() / "Requests"

def contracts_dir() -> Path:
    return vault_root() / "Contracts"

def invoices_dir() -> Path:
    return vault_root() / "Invoices"

def receipts_dir() -> Path:
    return vault_root() / "Receipts"

def archive_dir(entity_type: str) -> Path:
    """Per-entity archive folder under _Archive/<Type>/<year>."""
    return vault_root() / "_Archive" / entity_type


# ---------------------------------------------------------------------------
# Home folders (all under home_root)
# ---------------------------------------------------------------------------
def events_dir() -> Path:
    return home_root() / "events"

def indexes_dir() -> Path:
    return home_root() / "indexes"

def ai_dir() -> Path:
    return home_root() / "ai"

def config_dir() -> Path:
    return home_root() / "config"

def migrations_dir() -> Path:
    return home_root() / "migrations"

def backups_dir() -> Path:
    return home_root() / "backups"

def schemas_dir() -> Path:
    return home_root() / "schemas"

def system_dir() -> Path:
    return home_root() / "system"

def counters_file() -> Path:
    return home_root() / "id-counters.json"

def env_file() -> Path:
    """The ONLY .env this process may read. In sandbox mode this points at
    the sandbox home; the real keys live under the real home and are
    unreachable through any function in this module."""
    return config_dir() / ".env"

def real_env_file() -> Path:
    """Path of the real Stripe .env — used only by guards to refuse access."""
    return _REAL_HOME_ROOT / "config" / ".env"


__all__ = [
    "is_sandbox_mode", "enforce_isolation",
    "vault_root", "home_root", "real_vault_root", "real_home_root",
    "tickets_dir", "clients_dir", "contacts_dir", "organizations_dir",
    "assets_dir", "users_dir", "projects_dir", "tasks_dir",
    "knowledge_dir", "problems_dir", "changes_dir", "requests_dir",
    "contracts_dir", "invoices_dir", "receipts_dir", "archive_dir",
    "events_dir", "indexes_dir", "ai_dir", "config_dir",
    "migrations_dir", "backups_dir", "schemas_dir", "system_dir",
    "counters_file", "env_file", "real_env_file",
]
