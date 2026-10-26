"""Phase F: CLI regression tests (Bradley, 2026-10-07).

Covers the checklist line: kts --help, ticket --help, ticket list,
ticket show (checklist says "ticket get"; the real subcommand is
"show"), ticket status, ticket log, search, validate, index rebuild,
event commands, plus ticket next-id (Phase E unify).

All commands run against sandbox data with code under test from
$KTS_CODE_HOME (or the real ~/.ticketing).
"""

import json
import re
import unittest

from phasef_helpers import (
    CLI,
    SB_HOME,
    TICKET_BIN,
    require_sandbox,
    restore_pair,
    run,
    sb_env,
    snapshot_pair,
)


def parse_created(stdout):
    m = re.search(r"Created (TKT-\d{4}-\d{3}):", stdout)
    return m.group(1) if m else None


class TestCLIRegression(unittest.TestCase):
    def setUp(self):
        require_sandbox(self)
        self.env = sb_env()
        self.before = snapshot_pair()

    def tearDown(self):
        restore_pair(self.before)

    def cli(self, *args):
        r = run(self.env, "python3", str(CLI), *args)
        self.assertEqual(
            r.returncode, 0,
            f"kts {' '.join(args)} failed rc={r.returncode}\n"
            f"stdout: {r.stdout}\nstderr: {r.stderr}",
        )
        return r

    def test_help_shows_kts_prog(self):
        r = self.cli("--help")
        self.assertIn("usage: kts", r.stdout)
        self.assertIn("KTS Records", r.stdout)

    def test_ticket_help(self):
        r = self.cli("ticket", "--help")
        self.assertIn("usage: kts ticket", r.stdout)
        self.assertIn("next-id", r.stdout)
        self.assertIn("Change ticket status", r.stdout)

    def test_ticket_list(self):
        r = self.cli("ticket", "list", "--all")
        self.assertIn("TKT-2026-001", r.stdout)
        self.assertIn("TKT-2026-003", r.stdout)

    def test_ticket_show(self):  # checklist calls this "ticket get"
        r = self.cli("ticket", "show", "TKT-2026-003")
        self.assertIn("TKT-2026-003", r.stdout)
        self.assertIn("Adam", r.stdout)

    def test_ticket_status_and_log_on_probe(self):
        created = self.cli(
            "ticket", "new",
            "--client", "PhaseF CLI Probe",
            "--title", "PhaseF CLI regression probe",
        )
        tid = parse_created(created.stdout)
        self.assertIsNotNone(tid, created.stdout)

        st = self.cli("ticket", "status", tid, "--status", "triaged")
        self.assertIn(tid, st.stdout + created.stdout)

        # Finding 1 regression (Bradley 2026-10-07): CLI ticket status must
        # emit the dedicated ticket.status_changed event, not only the
        # generic ticket.updated from the inner update.
        ev_hits = []
        for p in (SB_HOME / "events").rglob("EVT-*.json"):
            if "current" in p.parts:
                continue
            d = json.loads(p.read_text())
            if d.get("event_type") == "ticket.status_changed" and \
               d.get("entity", {}).get("id") == tid:
                ev_hits.append(d)
        self.assertTrue(
            ev_hits,
            "CLI ticket status did not emit ticket.status_changed",
        )
        d = ev_hits[-1]
        self.assertEqual(d["changes"]["status"]["old"], "new")
        self.assertEqual(d["changes"]["status"]["new"], "triaged")
        self.assertEqual(d["actor"]["id"], "USR-000001")

        lg = self.cli(
            "ticket", "log", tid,
            "--hours", "0.25",
            "--note", "phaseF cli probe entry",
        )
        self.assertIn("Logged 0.25h", lg.stdout, lg.stdout)

        show = self.cli("ticket", "show", tid)
        self.assertIn("triaged", show.stdout)
        self.assertIn("phaseF cli probe entry", show.stdout)

    def test_ticket_next_id_unified(self):
        r = self.cli("ticket", "next-id")
        self.assertRegex(r.stdout.strip(), r"^TKT-\d{4}-\d{3}$")

    def test_search(self):
        r = self.cli("search", "Adam")
        self.assertIn("TKT-2026-003", r.stdout, r.stdout)

    def test_validate(self):
        r = self.cli("validate")
        self.assertIn("Validation complete:", r.stdout)
        self.assertIn("0 errors", r.stdout)

    def test_index_rebuild(self):
        r = self.cli("index")
        self.assertIn("Index rebuild complete:", r.stdout)

    def test_events_command(self):
        r = self.cli("events", "TKT-2026-001", "--limit", "5")
        self.assertIn("ticket", r.stdout + r.stderr)
        # at least one timestamped event line
        self.assertRegex(r.stdout, r"\d{4}-\d{2}-\d{2}")

    def test_standalone_ticket_help(self):
        r = run(self.env, str(TICKET_BIN), "--help")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("usage", r.stdout.lower())


if __name__ == "__main__":
    unittest.main()
