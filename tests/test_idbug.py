"""Regression test for the worklog:null / next_id() ID-reuse bug.

Bug (found in A4, fixed by Bradley's approval 2026-10-06):
  - the standalone `ticket` script writes `worklog:` (YAML null) in frontmatter
  - the Ticket model requires a list, so Ticket(**fm) raised on explicit None
  - ticket_service.list() swallowed the exception silently
  - next_id() never saw the standalone-created ticket and reissued its ID,
    so api.create_ticket failed with "Entity TKT-... already exists"

Scope of this test (Bradley 2026-10-06): a standalone-created ticket must be
visible to next_id().

Code under test: $KTS_CODE_HOME if set, otherwise the real ~/.ticketing.
If the code under test still has the bug, the test SKIPS with an explicit
reason (so the suite stays green until the fix lands in real code) and turns
itself on automatically once the fix is applied.

Data: always the sandbox (all three KTS_* vars). Fresh subprocess per step.
No KTS_* is ever inherited. The test restores the sandbox data it touched
(manifest snapshot/restore, no `sandbox reset`, because reset would wipe a
rehearsal code patch in ~/.ticketing-sandbox).
"""

import hashlib
import os
import re
import subprocess
import unittest
from pathlib import Path

HOME = Path.home()
REAL_HOME = HOME / ".ticketing"
CODE_HOME = Path(os.environ.get("KTS_CODE_HOME", str(REAL_HOME)))
SB_VAULT = HOME / "Documents" / "KTS Records-sandbox"
SB_HOME = HOME / ".ticketing-sandbox"
TICKET = HOME / ".local" / "bin" / "ticket"
CLI = CODE_HOME / "cli.py"


def base_env(**extra):
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
    env = base_env(
        KTS_SANDBOX="1",
        KTS_VAULT=str(SB_VAULT),
        KTS_HOME=str(SB_HOME),
    )
    env.update(extra)
    return env


def run(env, *cmd, timeout=120):
    return subprocess.run(
        list(map(str, cmd)),
        env=env,
        capture_output=True,
        text=True,
        timeout=timeout,
    )


def manifest(root: Path):
    out = {}
    for p in sorted(root.rglob("*")):
        if not p.is_file() or p.is_symlink():
            continue
        rel = str(p.relative_to(root))
        if "__pycache__" in rel or rel.endswith(".pyc"):
            continue
        if rel.endswith("config/.env"):
            continue  # G1: never hash the secrets file
        out[rel] = hashlib.sha256(p.read_bytes()).hexdigest()
    return out


PROBE_CODE = r"""
import pathlib, sys, yaml
sys.path.insert(0, sys.argv[1])
from core.models import Ticket
files = sorted((pathlib.Path(sys.argv[2]) / "Tickets").glob("TKT-*.md"))
assert files, "no tickets in sandbox vault"
fm = dict(yaml.safe_load(files[0].read_text().split("---")[1]))
fm["worklog"] = None
t = Ticket(**fm)
print("PARSED", t.id, t.worklog)
"""

SVC_IDS_CODE = r"""
import sys
sys.path.insert(0, sys.argv[1])
from core.services.ticket_service import TicketService
svc = TicketService()
ids = [t.id for t in svc.list(limit=10000)]
print("IDS", ",".join(ids))
print("NEXT", svc.next_id())
"""

SVC_WORKLOG_CODE = r"""
import sys
sys.path.insert(0, sys.argv[1])
from core.services.ticket_service import TicketService
svc = TicketService()
hits = [t.worklog for t in svc.list(limit=10000) if t.id == sys.argv[2]]
print("WORKLOG", repr(hits[0]) if hits else "MISSING")
"""


def parse_out(stdout, key):
    for line in stdout.splitlines():
        if line.startswith(key + " "):
            return line[len(key) + 1:]
    return None


class TestIdBugRegression(unittest.TestCase):
    """Standalone-created tickets must be visible to list()/next_id()."""

    def setUp(self):
        self.env = sb_env()
        if not SB_HOME.exists() or not SB_VAULT.exists():
            self.skipTest("sandbox missing; run `ticket sandbox create` first")
        self.home_before = manifest(SB_HOME)
        self.vault_before = manifest(SB_VAULT)

    def tearDown(self):
        # Remove anything the test added; fail loudly if it changed existing
        # files. No `sandbox reset` (that would wipe rehearsal code patches).
        home_after = manifest(SB_HOME)
        vault_after = manifest(SB_VAULT)
        for label, before, after in (
            ("sandbox home", self.home_before, home_after),
            ("sandbox vault", self.vault_before, vault_after),
        ):
            added = set(after) - set(before)
            changed = {k for k in set(before) & set(after)
                       if before[k] != after[k]}
            removed = set(before) - set(after)
            for rel in added:
                p = (SB_HOME if label == "sandbox home" else SB_VAULT) / rel
                p.unlink()
            if changed or removed:
                self.fail(
                    f"{label}: test leaked changes, changed={sorted(changed)} "
                    f"removed={sorted(removed)}"
                )

    def test_standalone_ticket_visible_to_next_id(self):
        # Step 1: does the code under test accept worklog: null at all?
        # If not, the bug is still present there -> skip with reason.
        probe = run(self.env, "python3", "-c", PROBE_CODE,
                    str(CODE_HOME), str(SB_VAULT))
        if probe.returncode != 0:
            self.skipTest(
                f"ID bug still present in {CODE_HOME} "
                f"(Ticket rejects worklog: null); fix pending Bradley's OK"
            )
        self.assertIn("PARSED", probe.stdout)

        # Step 2: service baseline (the path api.create_ticket uses).
        before = run(self.env, "python3", "-c", SVC_IDS_CODE, str(CODE_HOME))
        self.assertEqual(before.returncode, 0, before.stderr)
        next_before = parse_out(before.stdout, "NEXT")
        ids_before = (parse_out(before.stdout, "IDS") or "").split(",")
        self.assertRegex(next_before, r"^TKT-\d{4}-\d{3}$")

        # Step 3: create a ticket with the STANDALONE script (writes
        # `worklog:` null frontmatter). Sandbox data only.
        created = run(
            self.env, str(TICKET), "new",
            "--client", "IDBug Regression",
            "--title", "standalone-created probe for next_id regression",
        )
        self.assertEqual(created.returncode, 0,
                         created.stdout + created.stderr)
        m = re.search(r"created (TKT-\d{4}-\d{3})", created.stdout)
        self.assertIsNotNone(m, created.stdout)
        cid = m.group(1)
        self.assertNotIn(cid, ids_before,
                         "standalone reused an existing ID before the test "
                         "even ran; sandbox is dirty")

        # Step 4: the regression itself.
        after = run(self.env, "python3", "-c", SVC_IDS_CODE, str(CODE_HOME))
        self.assertEqual(after.returncode, 0, after.stderr)
        ids_after = (parse_out(after.stdout, "IDS") or "").split(",")
        next_after = parse_out(after.stdout, "NEXT")
        self.assertIn(cid, ids_after,
                      "standalone-created ticket invisible to list(); "
                      "next_id would reissue its ID")
        self.assertNotEqual(next_after, cid,
                            "next_id reissued the standalone ticket's ID")
        self.assertEqual(
            int(next_after.split("-")[-1]), int(cid.split("-")[-1]) + 1,
            f"next_id {next_after} must be one past {cid}",
        )

        # worklog: null parsed as an empty list, not dropped.
        wl = run(self.env, "python3", "-c", SVC_WORKLOG_CODE,
                 str(CODE_HOME), cid)
        self.assertEqual(wl.returncode, 0, wl.stderr)
        self.assertIn("WORKLOG []", wl.stdout)

        # The standalone file-scan next-id must also move past cid.
        scan = run(self.env, str(TICKET), "next-id")
        self.assertEqual(scan.returncode, 0, scan.stderr)
        self.assertNotEqual(scan.stdout.strip(), cid)

        # Step 5: skipped files are REPORTED (stderr), not silent.
        bad = SB_VAULT / "Tickets" / "ZZZ-999 - malformed probe.md"
        bad.write_text(
            "---\n"
            "id: BAD-ID-NO-PATTERN\n"
            "type: ticket\ntitle: malformed\nclass: residential\n"
            "created_by: USR-000001\nupdated_by: USR-000001\n"
            "created_at: 2026-10-06T12:00:00\n"
            "updated_at: 2026-10-06T12:00:00\n"
            "---\n\n# malformed\n"
        )
        listing = run(self.env, "python3", str(CLI), "ticket", "list", "--all")
        self.assertEqual(listing.returncode, 0, listing.stderr)
        self.assertIn("warning: skipped unreadable", listing.stderr)
        self.assertIn(bad.name, listing.stderr)
        self.assertIn(cid, listing.stdout,
                      "valid tickets must still list when a bad file exists")
        # tearDown removes both the probe ticket and the malformed file.


if __name__ == "__main__":
    unittest.main()
