"""Persistent ID counters — monotonic, non-reusing sequence numbers.

Contract: next = max(stored, scan_hint) + 1, persisted atomically BEFORE
the caller uses the number. Deleting the file that carried a number never
frees it again (fixes the EVT ID-reuse collision of 2026-10-07).
"""

import json
import os

from .paths import counters_file


def _load() -> dict:
    path = counters_file()
    try:
        if path.exists():
            data = json.loads(path.read_text())
            if isinstance(data, dict):
                return data
    except (json.JSONDecodeError, OSError):
        pass
    return {}


def _save(state: dict) -> None:
    path = counters_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(state, indent=2, sort_keys=True))
    os.replace(tmp, path)


def next_seq(key: str, scan_hint: int = 0) -> int:
    """Return next sequence number for key, persisted before returning."""
    state = _load()
    try:
        stored = int(state.get(key, 0))
    except (TypeError, ValueError):
        stored = 0
    try:
        hint = int(scan_hint)
    except (TypeError, ValueError):
        hint = 0
    nxt = max(stored, hint) + 1
    state[key] = nxt
    _save(state)
    return nxt


def peek_seq(key: str, scan_hint: int = 0) -> int:
    """Preview next sequence number WITHOUT persisting (Option B, 2026-10-08).

    Same arithmetic as next_seq (max(stored, scan_hint) + 1) but never
    writes the state file: for CLI next-id previews, where no number is
    actually issued. Issuance always goes through next_seq.
    """
    state = _load()
    try:
        stored = int(state.get(key, 0))
    except (TypeError, ValueError):
        stored = 0
    try:
        hint = int(scan_hint)
    except (TypeError, ValueError):
        hint = 0
    return max(stored, hint) + 1
