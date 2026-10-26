"""ticket_id contract tests (Bradley, 2026-10-10).

Invoices carry a stored ticket_id link (not client inference):
  - models/data: generate_invoice sets ticket_id at generation time
  - validator: ticket_id present, matches TKT-YYYY-NNN, ticket file exists;
    many-to-one allowed (several invoices may share one ticket)
  - indexer: ticket_id indexed on invoice docs + reverse ticket->invoice
    lookup (ticket_invoices)

All data: sandbox only. The generation proof sets the sandbox counter to
invoice:2026=4 and asserts the next invoice is INV-2026-005 WITH
ticket_id - proving what the real next issuance would carry WITHOUT
issuing anything real. Real vault, real counters and .env are untouched.
"""

import json
import unittest
from pathlib import Path

from phasef_helpers import (
    CODE_HOME,
    SB_HOME,
    SB_VAULT,
    require_sandbox,
    run,
    sb_env,
)

from core.indexer import Indexer
from core.validator import Validator, ValidationReport


def _inv_doc(tid, number):
    return {"ticket_id": tid, "invoice": {"number": number}}


def _write_mixed(root: Path, spec, tickets=None):
    """spec: list of (filename_stem, tid_or_None, inner_number).

    tickets: ticket ids that get a Tickets/*.md file; defaults to every
    tid in spec. Pass an explicit list (or empty) for dangling-ref tests.
    """
    (root / "Invoices").mkdir(parents=True, exist_ok=True)
    (root / "Receipts").mkdir(parents=True, exist_ok=True)
    (root / "Tickets").mkdir(parents=True, exist_ok=True)
    for stem, tid, number in spec:
        payload = {"invoice": {"number": number}}
        if tid is not None:
            payload["ticket_id"] = tid
        (root / "Invoices" / f"{stem}.json").write_text(
            json.dumps(payload), encoding="utf-8")
    if tickets is None:
        tickets = sorted({t for _, t, _ in spec if t})
    for tid in tickets:
        (root / "Tickets" / f"{tid}.md").write_text(f"# {tid}\n", encoding="utf-8")


class TestValidatorTicketId(unittest.TestCase):
    def setUp(self):
        import tempfile, shutil
        self.tmp = Path(tempfile.mkdtemp(prefix="tid-val-"))

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _check(self):
        report = ValidationReport()
        Validator(services={})._check_documents(report, root=self.tmp)
        return report

    def test_valid_ticket_id_passes(self):
        _write_mixed(self.tmp, [("INV-2026-001", "TKT-2026-001", "INV-2026-001")])
        report = self._check()
        self.assertEqual(report.errors, 0, [i.message for i in report.issues])

    def test_missing_ticket_id_is_error(self):
        _write_mixed(self.tmp, [("INV-2026-001", None, "INV-2026-001")])
        report = self._check()
        self.assertEqual(report.errors, 1)
        self.assertIn("missing ticket_id", report.issues[0].message)

    def test_bogus_ticket_id_pattern_is_error(self):
        _write_mixed(self.tmp, [("INV-2026-001", "CUS-000001", "INV-2026-001")])
        # CUS file not needed: pattern check fires first
        report = self._check()
        self.assertEqual(report.errors, 1)
        self.assertIn("not a TKT-YYYY-NNN", report.issues[0].message)

    def test_dangling_ticket_id_is_error(self):
        _write_mixed(self.tmp, [("INV-2026-001", "TKT-2026-099", "INV-2026-001")], tickets=[])
        report = self._check()
        self.assertEqual(report.errors, 1)
        self.assertIn("no ticket file", report.issues[0].message)

    def test_many_to_one_is_allowed(self):
        """A ticket may carry several invoices; that is not an error."""
        _write_mixed(self.tmp, [
            ("INV-2026-001", "TKT-2026-001", "INV-2026-001"),
            ("INV-2026-002", "TKT-2026-001", "INV-2026-002"),
        ])
        report = self._check()
        self.assertEqual(report.errors, 0, [i.message for i in report.issues])

    def test_receipts_not_required_to_carry_ticket_id(self):
        (self.tmp / "Receipts").mkdir(parents=True, exist_ok=True)
        (self.tmp / "Receipts" / "RCP-2026-001.json").write_text(
            json.dumps({"receipt": {"number": "RCP-2026-001"}}), encoding="utf-8")
        report = self._check()
        self.assertEqual(report.errors, 0, [i.message for i in report.issues])


class TestIndexerTicketId(unittest.TestCase):
    def setUp(self):
        import tempfile
        self.tmp = Path(tempfile.mkdtemp(prefix="tid-idx-"))
        _write_mixed(self.tmp, [
            ("INV-2026-001", "TKT-2026-001", "INV-2026-001"),
            ("INV-2026-002", "TKT-2026-001", "INV-2026-002"),
            ("INV-2026-003", "TKT-2026-002", "INV-2026-003"),
        ])
        (self.tmp / "Receipts").mkdir(exist_ok=True)
        (self.tmp / "Receipts" / "RCP-2026-001.json").write_text(
            json.dumps({"receipt": {"number": "RCP-2026-001"}}), encoding="utf-8")
        self.indexer = Indexer(services={})

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_scan_documents_includes_ticket_id(self):
        docs = self.indexer.scan_documents(root=self.tmp)
        inv = {d["number"]: d for d in docs if d["kind"] == "invoice"}
        self.assertEqual(inv["INV-2026-001"]["ticket_id"], "TKT-2026-001")
        self.assertEqual(inv["INV-2026-003"]["ticket_id"], "TKT-2026-002")

    def test_receipt_entries_have_no_ticket_id(self):
        docs = self.indexer.scan_documents(root=self.tmp)
        rcp = [d for d in docs if d["kind"] == "receipt"]
        self.assertEqual(len(rcp), 1)
        self.assertNotIn("ticket_id", rcp[0])

    def test_reverse_lookup_many_to_one(self):
        reverse = self.indexer.scan_ticket_invoices(root=self.tmp)
        self.assertEqual(reverse["TKT-2026-001"], ["INV-2026-001", "INV-2026-002"])
        self.assertEqual(reverse["TKT-2026-002"], ["INV-2026-003"])


GEN_005_SNIP = r"""
import json, os, sys
from pathlib import Path
sys.path.insert(0, sys.argv[1])
from core.api import KTSAPI
from core.counters import peek_seq
from core.models import EventActor

# Recreate the exact renumber state INSIDE THE SANDBOX so the proof is
# deterministic: counter invoice:2026=4 and no generated INV-2026-005+
# files left over from earlier sandbox runs. Real counters and the real
# vault are never touched (KTS_HOME/KTS_VAULT point at the sandbox).
home = Path(os.environ["KTS_HOME"])
counters = home / "id-counters.json"
state = json.loads(counters.read_text()) if counters.exists() else {}
state["invoice:2026"] = 4
counters.write_text(json.dumps(state, indent=2) + "\n")
inv_dir = Path(os.environ["KTS_VAULT"]) / "Invoices"
for f in inv_dir.glob("INV-2026-*.json"):
    try:
        if int(f.stem.split("-")[-1]) > 4:
            f.unlink()
    except (ValueError, IndexError):
        pass

api = KTSAPI()
api.ensure_initialized()
nxt = peek_seq("invoice:2026", 0)
print("PEEK", nxt)
actor = EventActor(id="USR-000001", type="human", name="Bradley Kars")
inv = api.generate_invoice("TKT-2026-001", actor)
print("NUM", inv["invoice"]["number"])
print("TID", inv.get("ticket_id"))
v = Path(os.environ["KTS_VAULT"])
on_disk = json.loads((v / "Invoices" / (inv["invoice"]["number"] + ".json")).read_text())
print("DISK_TID", on_disk.get("ticket_id"))
"""


class TestNextInvoiceCarriesTicketId(unittest.TestCase):
    """INV-2026-005 (sandbox) would carry ticket_id - no real issuance."""

    def test_generation_sets_ticket_id_and_would_be_005(self):
        require_sandbox(self)
        out = run(sb_env(), "python3", "-c", GEN_005_SNIP, str(CODE_HOME))
        lines = dict(
            ln.split(" ", 1) for ln in out.stdout.splitlines() if " " in ln
        )
        # sandbox counter invoice:2026=4 (renumber state) -> next is 5
        self.assertEqual(lines.get("PEEK"), "5", out.stdout + out.stderr)
        self.assertEqual(lines.get("NUM"), "INV-2026-005", out.stdout + out.stderr)
        self.assertEqual(lines.get("TID"), "TKT-2026-001", out.stdout + out.stderr)
        self.assertEqual(lines.get("DISK_TID"), "TKT-2026-001", out.stdout + out.stderr)


if __name__ == "__main__":
    unittest.main()
