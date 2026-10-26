"""H2b — per-device token table + bridge auth (Bradley 2026-10-08).

Required proofs:
  1. no token -> 401
  2. valid token -> 200
  3. revoked token -> 401 at once
  4. one device's revoke does not affect another
  5. loopback request without a token -> 401 (no local-trust bypass)

Plus: secrets stored hashed (never plaintext), mint/revoke audit carries
token ID + label only (no secret), sandbox default path routing.
"""

import json
import os
import socket
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from urllib import request as urllib_request
from urllib import error as urllib_error

import uvicorn
from fastapi.testclient import TestClient

# Ensure we can import core modules from the ticketing tree
sys.path.insert(
    0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
)

from core.auth import TokenTable  # noqa: E402
from core.bridge import create_app  # noqa: E402
from phasef_helpers import CODE_HOME, sb_env, run, require_sandbox  # noqa: E402


HEADER = "X-API-Token"


class TestTokenTable(unittest.TestCase):
    """Unit tests for TokenTable using hermetic temp dirs (no env needed)."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="h2b-auth-"))
        self.table = TokenTable(self.tmp / "auth" / "token-table.json")

    def test_secret_hashed_not_plaintext(self):
        """Secret returned from mint never appears in the table file."""
        _, secret = self.table.mint("desktop")
        text = (self.tmp / "auth" / "token-table.json").read_text()
        self.assertNotIn(secret, text)
        data = json.loads(text)
        rec = data["tokens"].popitem()[1]
        self.assertIn("dk", rec)
        self.assertEqual(len(rec["dk"]), 64)  # sha256 hex
        self.assertIn("salt", rec)
        self.assertEqual(len(rec["salt"]), 32)  # 16 bytes hex

    def test_two_mints_distinct(self):
        """Two mints for the same label produce different secrets and IDs."""
        tid1, sec1 = self.table.mint("desktop")
        tid2, sec2 = self.table.mint("desktop")
        self.assertNotEqual(tid1, tid2)
        self.assertNotEqual(sec1, sec2)

    def test_verify_valid_secret(self):
        """Verify accepts the exact secret returned by mint."""
        tid, secret = self.table.mint("phone")
        rec = self.table.verify(secret)
        self.assertIsNotNone(rec)
        self.assertEqual(rec["token_id"], tid)
        self.assertEqual(rec["label"], "phone")

    def test_verify_wrong_secret_fails(self):
        """Verify rejects a wrong secret."""
        self.table.mint("desktop")
        rec = self.table.verify("kts_bogus")
        self.assertIsNone(rec)

    def test_revoke_immediate(self):
        """Revoked token is rejected at once on the next verify."""
        tid, secret = self.table.mint("desktop")
        self.assertIsNotNone(self.table.verify(secret))
        self.table.revoke(tid)
        self.assertIsNone(self.table.verify(secret))

    def test_revoke_isolated_per_device(self):
        """Revoking one device's token does not affect another device."""
        tid_a, sec_a = self.table.mint("desktop")
        tid_b, sec_b = self.table.mint("phone")
        self.assertIsNotNone(self.table.verify(sec_a))
        self.assertIsNotNone(self.table.verify(sec_b))
        self.table.revoke(tid_a)
        self.assertIsNone(self.table.verify(sec_a))
        self.assertIsNotNone(self.table.verify(sec_b))
        # revoke b as well
        self.table.revoke(tid_b)
        self.assertIsNone(self.table.verify(sec_b))

    def test_audit_mint_revoke_fields_only(self):
        """Mint/revoke audit entries carry token_id + label only, never secret."""
        _, sec_a = self.table.mint("desktop")
        _, sec_b = self.table.mint("phone")
        self.table.revoke(self.table._load()["tokens"].popitem()[0])  # revoke one

        audit_text = (self.tmp / "auth" / "audit.log").read_text()
        lines = audit_text.strip().split("\n")
        self.assertEqual(len(lines), 3)  # 2 mint + 1 revoke
        for line in lines:
            obj = json.loads(line)
            self.assertIn("action", obj)
            self.assertIn("token_id", obj)
            self.assertIn("label", obj)
            self.assertNotIn("secret", obj)
            self.assertNotIn(sec_a, line)
            self.assertNotIn(sec_b, line)


class TestBridgeAuth(unittest.TestCase):
    """Bridge auth tests using fast TestClient (in-process, no real socket)."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="h2b-bridge-"))
        self.table = TokenTable(self.tmp / "auth" / "token-table.json")
        self.app = create_app(self.table)
        self.client = TestClient(self.app)

    def test_no_token_401(self):
        """Request without X-API-Token returns 401."""
        r = self.client.get("/health")
        self.assertEqual(r.status_code, 401)
        self.assertEqual(r.json()["detail"], "missing or invalid token")

    def test_valid_token_200(self):
        """Request with a valid token returns 200."""
        _, secret = self.table.mint("desktop")
        r = self.client.get("/health", headers={HEADER: secret})
        self.assertEqual(r.status_code, 200)
        self.assertTrue(r.json()["ok"])

    def test_wrong_token_401(self):
        """Request with an invalid token returns 401."""
        self.table.mint("desktop")
        r = self.client.get("/health", headers={HEADER: "kts_bogus"})
        self.assertEqual(r.status_code, 401)

    def test_revoked_token_401_at_once(self):
        """Revoked token returns 401 immediately on next request."""
        _, secret = self.table.mint("desktop")
        r = self.client.get("/health", headers={HEADER: secret})
        self.assertEqual(r.status_code, 200)
        self.table.revoke(self.table._load()["tokens"].popitem()[0])
        r = self.client.get("/health", headers={HEADER: secret})
        self.assertEqual(r.status_code, 401)

    def test_revoke_isolated_per_device_bridge(self):
        """Device A revoke does not affect Device B."""
        tid_a, sec_a = self.table.mint("desktop")
        tid_b, sec_b = self.table.mint("phone")
        # both work
        self.assertEqual(self.client.get("/health", headers={HEADER: sec_a}).status_code, 200)
        self.assertEqual(self.client.get("/health", headers={HEADER: sec_b}).status_code, 200)
        # revoke A
        self.table.revoke(tid_a)
        self.assertEqual(self.client.get("/health", headers={HEADER: sec_a}).status_code, 401)
        # B still works
        self.assertEqual(self.client.get("/health", headers={HEADER: sec_b}).status_code, 200)

    def test_request_audit_line_fields(self):
        """Every request writes a verbatim audit line: token_id, label, method,
        path, status, latency — and never the secret (spec: bridge logs every
        remote call). Also covers the auth_fail line for a tokenless request."""
        tid, sec = self.table.mint("desktop")
        r = self.client.get("/health", headers={HEADER: sec})
        self.assertEqual(r.status_code, 200)
        r2 = self.client.get("/health")  # no token -> auth_fail line
        self.assertEqual(r2.status_code, 401)

        audit = (self.tmp / "auth" / "audit.log").read_text().strip().splitlines()
        objs = [json.loads(line) for line in audit]
        req = [o for o in objs if o["action"] == "request"]
        fail = [o for o in objs if o["action"] == "auth_fail"]
        self.assertEqual(len(req), 1)
        self.assertEqual(len(fail), 1)

        obj = req[0]
        for key in ("ts_utc", "token_id", "label", "method", "path", "status", "latency_ms"):
            self.assertIn(key, obj)
        self.assertEqual(obj["token_id"], tid)
        self.assertEqual(obj["label"], "desktop")
        self.assertEqual(obj["method"], "GET")
        self.assertEqual(obj["path"], "/health")
        self.assertEqual(obj["status"], 200)
        self.assertIsInstance(obj["latency_ms"], int)
        self.assertNotIn(sec, json.dumps(objs))  # secret nowhere in the audit file

        fobj = fail[0]
        self.assertEqual(fobj["status"], 401)
        self.assertIn("source", fobj)
        self.assertIn("reason", fobj)
        self.assertNotIn("token_id", fobj)


class TestBridgeLoopback(unittest.TestCase):
    """Gate test: real uvicorn on 127.0.0.1 ephemeral port, real HTTP requests.

    This proves no local-trust bypass: a loopback (127.0.0.1) request without
    a token gets 401 exactly like any other source.
    """

    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp(prefix="h2b-loopback-"))
        cls.table = TokenTable(cls.tmp / "auth" / "token-table.json")
        cls.app = create_app(cls.table)
        cls._start_server()

    @classmethod
    def _start_server(cls):
        # Find a free port
        s = socket.socket()
        s.bind(("127.0.0.1", 0))
        cls.port = s.getsockname()[1]
        s.close()

        config = uvicorn.Config(
            cls.app, host="127.0.0.1", port=cls.port, log_level="error"
        )
        cls.server = uvicorn.Server(config)
        cls.thread = threading.Thread(target=cls.server.run, daemon=True)
        cls.thread.start()
        # wait for startup
        for _ in range(200):
            if cls.server.started:
                break
            time.sleep(0.05)
        if not cls.server.started:
            raise RuntimeError("uvicorn failed to start")

    @classmethod
    def tearDownClass(cls):
        cls.server.should_exit = True
        cls.thread.join(timeout=5)

    def _get(self, path: str, token: str | None = None) -> int:
        url = f"http://127.0.0.1:{self.port}{path}"
        req = urllib_request.Request(url)
        if token:
            req.add_header(HEADER, token)
        try:
            with urllib_request.urlopen(req, timeout=5) as resp:
                return resp.status
        except urllib_error.HTTPError as e:
            return e.code

    def test_loopback_no_token_401(self):
        """Gate test: loopback request without token -> 401."""
        self.assertEqual(self._get("/health"), 401)

    def test_loopback_valid_token_200(self):
        """Loopback request with valid token -> 200."""
        _, secret = self.table.mint("desktop")
        self.assertEqual(self._get("/health", secret), 200)

    def test_loopback_wrong_token_401(self):
        """Loopback request with wrong token -> 401."""
        self.table.mint("desktop")
        self.assertEqual(self._get("/health", "kts_bogus"), 401)

    def test_unknown_path_no_token_401(self):
        """Auth middleware runs on every route, unknown path without token -> 401."""
        self.assertEqual(self._get("/nope"), 401)

    def test_revoke_isolated_request_level(self):
        """Request-level isolation over real HTTP: revoke A -> A 401, B 200."""
        tid_a, sec_a = self.table.mint("desktop")
        _, sec_b = self.table.mint("phone")
        # both valid before revoke
        self.assertEqual(self._get("/health", sec_a), 200)
        self.assertEqual(self._get("/health", sec_b), 200)
        # revoke A through the bridge's token table
        self.table.revoke(tid_a)
        # A now 401, B unaffected — both over real loopback HTTP
        self.assertEqual(self._get("/health", sec_a), 401)
        self.assertEqual(self._get("/health", sec_b), 200)


class TestSandboxDefaultPath(unittest.TestCase):
    """Verify the default token table path resolves under the sandbox home
    when KTS_SANDBOX=1, KTS_VAULT/KTS_HOME set."""

    def test_default_path_under_sandbox_home(self):
        require_sandbox(self)
        # Run a subprocess with sandbox env that imports core.auth and prints
        # the default token table path
        cmd = [
            sys.executable,
            "-c",
            "import sys; sys.path.insert(0, sys.argv[1]); "
            "from core.auth import default_token_table; "
            "t = default_token_table(); print(t.path)",
            str(CODE_HOME),
        ]
        proc = run(sb_env(), *cmd)
        self.assertEqual(proc.returncode, 0)
        out = proc.stdout.strip()
        self.assertTrue(
            out.startswith(str(Path.home() / ".ticketing-sandbox" / "auth")),
            f"default path {out!r} not under sandbox home",
        )


if __name__ == "__main__":
    unittest.main()