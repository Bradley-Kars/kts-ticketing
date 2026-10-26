"""safe_io — the ONLY file layer for the KTS core.

Every write, rename, move, delete, mkdir, and every .env read in the core
must go through these functions. Each one resolves the target (following
symlinks) and refuses anything that does not resolve inside the active
vault or the active home. In sandbox mode it additionally refuses any
attempt to read or write the real ~/.ticketing/config/.env (Stripe keys).

Usage:
    from .safe_io import safe_write, safe_mkdir, safe_move, load_env
"""

import json
import os
import shutil
from pathlib import Path
from typing import Any

from .paths import (
    vault_root, home_root, is_sandbox_mode, env_file, real_env_file,
    enforce_isolation,
)


def _check_target(target: "Path | str", *, must_exist: bool = False) -> Path:
    """Resolve target (following symlinks) and verify it is inside the
    active vault or active home. Also refuses the real .env in sandbox mode.

    Returns the resolved path. Raises PermissionError or SystemExit on refusal.
    """
    p = Path(target)
    # Resolve as far as possible (parent must exist for full symlink
    # resolution of the final component; resolve() with strict=False
    # handles non-existent leaves).
    resolved = p.resolve()

    vr = vault_root().resolve()
    hr = home_root().resolve()

    inside = str(resolved).startswith(str(vr) + os.sep) or resolved == vr \
        or str(resolved).startswith(str(hr) + os.sep) or resolved == hr
    if not inside:
        raise PermissionError(
            f"safe_io refused: {resolved} is outside the active vault "
            f"({vr}) and home ({hr})."
        )

    # Sandbox must never touch the real .env — read or write.
    if is_sandbox_mode():
        real_env = real_env_file()
        if real_env.exists() and resolved == real_env.resolve():
            raise PermissionError(
                "safe_io refused: sandbox process may not access the real "
                "Stripe .env."
            )

    enforce_isolation()
    return resolved


def safe_mkdir(path: "Path | str", *, parents: bool = True,
               exist_ok: bool = True) -> Path:
    resolved = _check_target(path)
    Path(resolved).mkdir(parents=parents, exist_ok=exist_ok)
    return resolved


def safe_write(path: "Path | str", content: str, *,
               encoding: str = "utf-8") -> Path:
    """Write text content, replacing any existing file."""
    resolved = _check_target(path)
    Path(resolved).write_text(content, encoding=encoding)
    return resolved


def safe_write_json(path: "Path | str", obj: Any, *, indent: int = 2) -> Path:
    resolved = _check_target(path)
    Path(resolved).write_text(
        json.dumps(obj, indent=indent, default=str), encoding="utf-8"
    )
    return resolved


def safe_append(path: "Path | str", content: str, *,
                encoding: str = "utf-8") -> Path:
    resolved = _check_target(path)
    with open(resolved, "a", encoding=encoding) as f:
        f.write(content)
    return resolved


def safe_rename(src: "Path | str", dst: "Path | str") -> Path:
    s = _check_target(src, must_exist=True)
    d = _check_target(dst)
    Path(s).rename(d)
    return d


def safe_move(src: "Path | str", dst: "Path | str") -> Path:
    s = _check_target(src, must_exist=True)
    d = _check_target(dst)
    shutil.move(str(s), str(d))
    return d


def safe_delete(path: "Path | str") -> None:
    """Delete a single file. Directories are removed with safe_rmtree."""
    s = _check_target(path, must_exist=True)
    Path(s).unlink()


def safe_rmtree(path: "Path | str") -> None:
    s = _check_target(path, must_exist=True)
    shutil.rmtree(str(s))


def safe_check(path: "Path | str") -> Path:
    """Validate a target path without writing. Returns the resolved path.
    Use before non-text writes (e.g. np.save) so every write target is
    still checked against the active vault/home."""
    return _check_target(path)


def safe_read(path: "Path | str", *, encoding: str = "utf-8") -> str:
    """Read a text file. Refuses the real .env in sandbox mode (the target
    check inside _check_target covers it, since the real .env is outside
    the sandbox home anyway; this is a second explicit guard)."""
    resolved = _check_target(path, must_exist=True)
    if is_sandbox_mode() and real_env_file().exists() \
            and resolved == real_env_file().resolve():
        raise PermissionError(
            "safe_io refused: sandbox may not read the real Stripe .env."
        )
    return Path(resolved).read_text(encoding=encoding)


def load_env() -> dict:
    """Load KEY=VALUE pairs from the ACTIVE env file only
    (paths.env_file() — sandbox home in sandbox mode, real home otherwise).

    Never opens the real .env while in sandbox mode; the target guard
    enforces that even if paths are tampered with.
    """
    target = env_file()
    resolved = _check_target(target)   # works for non-existent files too
    if not Path(resolved).exists():
        return {}
    text = Path(resolved).read_text(encoding="utf-8")
    out: dict = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, _, v = line.partition("=")
        out[k.strip()] = v.strip().strip('"').strip("'")
    return out


__all__ = [
    "safe_mkdir", "safe_write", "safe_write_json", "safe_append",
    "safe_rename", "safe_move", "safe_delete", "safe_rmtree",
    "safe_read", "safe_check", "load_env",
]
