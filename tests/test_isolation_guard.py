"""
Isolation guard for MC-2 tests. Fail fast if real paths could be touched.

Usage in each test file (module top, before any core.* imports):
    try:
        from tests.test_isolation_guard import require_isolation, snapshot_real_tree
    except ImportError:          # running as `python3 tests/<file>.py`
        from test_isolation_guard import require_isolation, snapshot_real_tree
    require_isolation()
    # ... test setup ...
    # In setUp: self.real_snap = snapshot_real_tree()
    # After each write test: self.real_snap.assert_unchanged("test_name")

require_isolation() rules (parent pytest process vs. write subprocess):
- The PARENT test process does NOT have KTS_HOME/KTS_VAULT set (temp paths
  are injected only into the write subprocess env). So an unset var PASSES.
- It REFUSES only when KTS_HOME or KTS_VAULT is SET and points at the real
  tree (~/.ticketing or ~/Documents/KTS Records, or anything inside them) —
  that is the only configuration that can leak into real state.

snapshot_real_tree() captures real vault file count, id-counters.json sha,
users.yaml sha and events/ file count. assert_unchanged() compares afterward.
"""

import hashlib
import os
from pathlib import Path
from typing import Optional


REAL_HOME = Path.home() / ".ticketing"
REAL_VAULT = Path.home() / "Documents" / "KTS Records"


def _points_at_real(path_str: str, real: Path) -> bool:
    """True when path_str resolves to `real` or anything inside it."""
    try:
        p = Path(path_str).expanduser().resolve()
        r = real.resolve()
    except Exception:
        return False
    return p == r or r in p.parents


def _sha256(path: Path) -> Optional[str]:
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except Exception:
        return None


def _vault_file_count(root: Path) -> int:
    if not root.is_dir():
        return 0
    return sum(1 for p in root.rglob("*") if p.is_file() and not p.is_symlink())


def _events_count(home: Path) -> int:
    ev = home / "events"
    if not ev.is_dir():
        return 0
    return sum(1 for p in ev.rglob("*") if p.is_file() and not p.is_symlink())


class RealTreeSnapshot:
    """Snapshot of real tree state for leak detection."""

    def __init__(self):
        self.vault_count = _vault_file_count(REAL_VAULT)
        self.counters_sha = _sha256(REAL_HOME / "id-counters.json")
        self.users_sha = _sha256(REAL_HOME / "config" / "users.yaml")
        self.events_count = _events_count(REAL_HOME)

    def assert_unchanged(self, context: str = "test"):
        """Fail if real tree changed since snapshot."""
        new_vault = _vault_file_count(REAL_VAULT)
        new_counters = _sha256(REAL_HOME / "id-counters.json")
        new_users = _sha256(REAL_HOME / "config" / "users.yaml")
        new_events = _events_count(REAL_HOME)

        msgs = []
        if new_vault != self.vault_count:
            msgs.append(f"vault file count {self.vault_count} -> {new_vault}")
        if new_counters != self.counters_sha:
            msgs.append(
                f"id-counters sha changed: "
                f"{self.counters_sha[:16] if self.counters_sha else 'none'}... -> "
                f"{new_counters[:16] if new_counters else 'none'}..."
            )
        if new_users != self.users_sha:
            msgs.append(
                f"users.yaml sha changed: "
                f"{self.users_sha[:16] if self.users_sha else 'none'}... -> "
                f"{new_users[:16] if new_users else 'none'}..."
            )
        if new_events != self.events_count:
            msgs.append(f"events count {self.events_count} -> {new_events}")

        if msgs:
            raise AssertionError(f"[{context}] Real tree leaked: " + "; ".join(msgs))


def require_isolation() -> bool:
    """Call at module top, BEFORE any core.* imports.

    Unset KTS_HOME / KTS_VAULT (the parent pytest process) -> PASS.
    Set to a temp path (the write subprocess) -> PASS.
    Set to the real tree or anything inside it -> REFUSE.
    """
    kts_home = os.environ.get("KTS_HOME")
    kts_vault = os.environ.get("KTS_VAULT")

    if kts_home and _points_at_real(kts_home, REAL_HOME):
        raise RuntimeError(
            f"ISOLATION GUARD: KTS_HOME={kts_home!r} points at the real "
            f"~/.ticketing — refusing to run (would leak into real state)"
        )

    if kts_vault and _points_at_real(kts_vault, REAL_VAULT):
        raise RuntimeError(
            f"ISOLATION GUARD: KTS_VAULT={kts_vault!r} points at the real "
            f"vault — refusing to run (would leak into real state)"
        )

    return True


def snapshot_real_tree() -> RealTreeSnapshot:
    """Capture real tree state for post-test leak check."""
    return RealTreeSnapshot()
