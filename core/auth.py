"""Per-device token table + verification for the KTS bridge (H2b).

- Secrets are generated once (returned only from mint), stored only as
  PBKDF2-HMAC-SHA256 (per-token random salt, 200k iterations) — plaintext
  never written anywhere (not in the table, audit, logs, plan, backups).
- Every token has a token_id + label; audit events carry ONLY those.
- Revocation is immediate: verify() rejects revoked tokens at once.
- Per-device isolation: revoking one token never affects another.
"""
import hashlib, json, os, secrets
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

TABLE_VERSION = 1
PBKDF2_ITERATIONS = 200_000
HEADER = "X-API-Token"  # spike-compatible header name


def _utcnow() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _hash_secret(secret: str, salt_hex: str, iterations: int) -> str:
    dk = hashlib.pbkdf2_hmac("sha256", secret.encode(), bytes.fromhex(salt_hex), iterations)
    return dk.hex()


class TokenTable:
    """
    Thread-safe enough for the bridge (single process, uvicorn workers
    share the file via OS — each verify re-loads; mint/revoke are rare).
    """

    def __init__(self, path: str | Path, audit_path: Optional[str | Path] = None):
        # No directory creation here: constructing a table (including via
        # `import core.bridge` -> module-level app) must never touch the
        # filesystem. Dirs are created lazily on first write (_save/_audit).
        self.path = Path(path)
        self.audit_path = Path(audit_path) if audit_path else self.path.parent / "audit.log"

    def _load(self) -> dict:
        if not self.path.exists():
            return {"version": TABLE_VERSION, "tokens": {}}
        return json.loads(self.path.read_text())

    def _save(self, data: dict) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, indent=2))
        os.chmod(tmp, 0o600)
        os.replace(tmp, self.path)

    def _audit(self, action: str, token_id: str, label: str) -> None:
        """Append a JSONL line with ts_utc, action, token_id, label ONLY."""
        line = json.dumps(
            {"ts_utc": _utcnow(), "action": action, "token_id": token_id, "label": label},
            separators=(",", ":"),
        )
        self.audit_path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(self.audit_path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
        with os.fdopen(fd, "a") as f:
            f.write(line + "\n")

    def mint(self, label: str, scope: str = "read") -> tuple[str, str]:
        """Create a new per-device token. Returns (token_id, secret).
        The secret is returned ONCE and never stored anywhere.
        scope: "read" (default) or "write". Write tokens may call
        mutating bridge routes (T1+); read tokens may not."""
        if scope not in ("read", "write"):
            raise ValueError(f"invalid scope {scope!r}: must be 'read' or 'write'")
        data = self._load()
        token_id = "tok_" + secrets.token_urlsafe(9)
        secret = "kts_" + secrets.token_urlsafe(32)
        salt = secrets.token_hex(16)
        dk = _hash_secret(secret, salt, PBKDF2_ITERATIONS)
        data["tokens"][token_id] = {
            "label": label,
            "scope": scope,
            "salt": salt,
            "dk": dk,
            "iterations": PBKDF2_ITERATIONS,
            "created_utc": _utcnow(),
            "revoked": False,
            "revoked_utc": None,
        }
        self._save(data)
        self._audit("mint", token_id, label)
        return token_id, secret

    def revoke(self, token_id: str) -> bool:
        """Revoke a token by its ID. Immediate effect on subsequent verify()."""
        data = self._load()
        rec = data["tokens"].get(token_id)
        if rec is None:
            return False
        rec["revoked"] = True
        rec["revoked_utc"] = _utcnow()
        label = rec["label"]
        self._save(data)
        self._audit("revoke", token_id, label)
        return True

    def verify(self, secret: str) -> Optional[dict]:
        """
        Verify a presented secret against all active (non-revoked) tokens.
        Returns the token record (token_id, label, scope) on success, else None.
        Tokens minted before the scope field was added default to "read".
        """
        if not secret:
            return None
        data = self._load()
        for tid, rec in data["tokens"].items():
            if rec["revoked"]:
                continue
            dk = _hash_secret(secret, rec["salt"], rec["iterations"])
            if secrets.compare_digest(dk, rec["dk"]):
                return {
                    "token_id": tid,
                    "label": rec["label"],
                    "scope": rec.get("scope", "read"),
                }
        return None

    def audit_request(
        self,
        *,
        method: str,
        path: str,
        status: int,
        latency_ms: int,
        token_id: Optional[str] = None,
        label: Optional[str] = None,
        source: Optional[str] = None,
        reason: Optional[str] = None,
    ) -> None:
        """Append a per-request audit line to the JSONL audit file.
        For auth_fail: token_id/label are None; source + reason included.
        For request: token_id/label present; source/reason omitted.
        Never includes secrets."""
        if token_id is not None and label is not None:
            # successful request
            obj = {
                "ts_utc": _utcnow(),
                "action": "request",
                "token_id": token_id,
                "label": label,
                "method": method,
                "path": path,
                "status": status,
                "latency_ms": latency_ms,
            }
        else:
            # auth failure
            obj = {
                "ts_utc": _utcnow(),
                "action": "auth_fail",
                "method": method,
                "path": path,
                "status": status,
                "latency_ms": latency_ms,
                "source": source or "unknown",
                "reason": reason or "missing or invalid token",
            }
        line = json.dumps(obj, separators=(",", ":"))
        self.audit_path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(self.audit_path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
        with os.fdopen(fd, "a") as f:
            f.write(line + "\n")


# Default factory used by the bridge when no table is injected
def default_token_table() -> "TokenTable":
    from . import paths
    base = paths.home_root() / "auth"
    return TokenTable(base / "token-table.json")