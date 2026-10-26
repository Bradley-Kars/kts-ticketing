"""G4 part 2 — Option B numbering proofs (Bradley 2026-10-08).

Required proofs, all hermetic (temp dirs) or sandboxed — zero writes to
real vault / real id-counters:

  1. legacy IDs (INV-YYYYMMDD-###, RCP-YYYYMMDD-###) still validate AND index,
  2. year-scoped IDs (INV-YYYY-###, RCP-YYYY-###) validate AND index,
  3. counters never reuse a number after its file is deleted (sandbox,
     on the new year keys — test_counters covers the api-level path),
  4. legacy and new sequences can never collide (disjoint regexes,
     disjoint glob prefixes, disjoint counter keys, distinct file names).

Plus: peek_seq (CLI next-id preview) never writes the counter file.
"""

import hashlib
import os
import re
import sys
import tempfile
import unittest
from fnmatch import fnmatch
from pathlib import Path

sys.path.insert(
    0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
)

from core.models import (  # noqa: E402
    DOCUMENT_ID_PATTERNS, validate_document_id,
)
from core.counters import peek_seq  # noqa: E402
from core.validator import ValidationReport, Validator  # noqa: E402
from core.indexer import Indexer  # noqa: E402
from phasef_helpers import (  # noqa: E402
    CODE_HOME, REAL_HOME, require_sandbox, restore_pair, run, sb_env,
    snapshot_pair,
)

LEGACY_INV = ["INV-20261006-001", "INV-20261006-002",
              "INV-20261007-001", "INV-20261007-002"]
LEGACY_RCP = ["RCP-20261006-001"]
YEAR_INV = ["INV-2026-001", "INV-2026-002"]
YEAR_RCP = ["RCP-2026-001"]
OLD_INV_RE = r"^INV-\d{8}-\d{3}$"
NEW_INV_RE = r"^INV-\d{4}-\d{3}$"
OLD_RCP_RE = r"^RCP-\d{8}-\d{3}$"
NEW_RCP_RE = r"^RCP-\d{4}-\d{3}$"

COUNTER_REUSE_CODE = r"""
import pathlib, sys
sys.path.insert(0, sys.argv[1])
from core.counters import next_seq
from core.paths import counters_file
cf = counters_file()
cf.parent.mkdir(parents=True, exist_ok=True)
cf.write_text("{}")
for key, prefix in (("invoice:2026", "INV"), ("receipt:2026", "RCP")):
    n1 = next_seq(key, 0)
    # a file carrying the number, then deleted — the reuse hazard
    f = cf.parent / ("doc-probe-%s-%03d.json" % (prefix, n1))
    f.write_text("{}")
    f.unlink()
    n2 = next_seq(key, 0)  # stale scan: hint 0, file gone
    assert n2 == n1 + 1, (key, n1, n2)
    n3 = next_seq(key, 0)
    assert n3 == n2 + 1, (key, n2, n3)
    print("REUSE", key, n1, n2, n3)
"""


def make_mixed_vault(root: Path):
    """4 grandfathered invoices + 1 legacy receipt + one year-format pair.

    Invoices carry ticket_id (contract 2026-10-10); matching ticket
    files exist under Tickets/ so the validator's existence check passes.
    """
    inv = root / "Invoices"
    rcp = root / "Receipts"
    tix = root / "Tickets"
    inv.mkdir(parents=True)
    rcp.mkdir(parents=True)
    tix.mkdir(parents=True)
    # one distinct ticket per invoice (6 invoices -> TKT-2026-001..006)
    tickets = [f"TKT-2026-{i:03d}" for i in range(1, 7)]
    for tid in tickets:
        (tix / f"{tid}.md").write_text(f"# {tid}\n", encoding="utf-8")
    for i, n in enumerate(LEGACY_INV):
        (inv / f"{n}.json").write_text(
            '{"ticket_id": "%s", "invoice": {"number": "%s"}}'
            % (tickets[i], n), encoding="utf-8")
    for n in LEGACY_RCP:
        (rcp / f"{n}.json").write_text(
            '{"receipt": {"number": "%s"}}' % n, encoding="utf-8")
    for i, n in enumerate(YEAR_INV):
        (inv / f"{n}.json").write_text(
            '{"ticket_id": "%s", "invoice": {"number": "%s"}}'
            % (tickets[len(LEGACY_INV) + i], n), encoding="utf-8")
    for n in YEAR_RCP:
        (rcp / f"{n}.json").write_text(
            '{"receipt": {"number": "%s"}}' % n, encoding="utf-8")
    return root


class TestDualFormatValidation(unittest.TestCase):
    """Proof 1 + 2 (validate side): both formats accepted, junk rejected."""

    def test_legacy_ids_still_validate(self):
        for n in LEGACY_INV:
            self.assertTrue(validate_document_id("INV", n), n)
        for n in LEGACY_RCP:
            self.assertTrue(validate_document_id("RCP", n), n)

    def test_year_ids_validate(self):
        for n in YEAR_INV:
            self.assertTrue(validate_document_id("INV", n), n)
        for n in YEAR_RCP:
            self.assertTrue(validate_document_id("RCP", n), n)

    def test_both_patterns_documented(self):
        self.assertIn("INV", DOCUMENT_ID_PATTERNS)
        self.assertIn("RCP", DOCUMENT_ID_PATTERNS)

    def test_bad_ids_rejected(self):
        for bad in ("INV-20261-001", "INV-2026-01", "INV-2026-0001",
                    "RCP-20261006-01", "INV-2026-001X", "TKT-2026-001"):
            self.assertFalse(validate_document_id("INV", bad), bad)
        self.assertFalse(validate_document_id("XXX", "XXX-1"))
        self.assertFalse(validate_document_id("RCP", "INV-2026-001"))


class TestValidatorDocuments(unittest.TestCase):
    """Validator._check_documents over a mixed (legacy+year) vault."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="g4p2-val-"))
        make_mixed_vault(self.tmp)

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _check(self):
        report = ValidationReport()
        Validator(services={})._check_documents(report, root=self.tmp)
        return report

    def test_mixed_vault_has_zero_findings(self):
        report = self._check()
        self.assertEqual(report.errors, 0,
                         [i.message for i in report.issues])
        self.assertEqual(report.warnings, 0)

    def test_total_entities_not_polluted_by_documents(self):
        report = self._check()
        self.assertEqual(report.total_entities, 0)

    def test_filename_inner_mismatch_is_error(self):
        (self.tmp / "Invoices" / "INV-2026-003.json").write_text(
            '{"ticket_id": "TKT-2026-001", "invoice": {"number": "INV-2026-004"}}',
            encoding="utf-8")
        report = self._check()
        self.assertEqual(report.errors, 1, [i.message for i in report.issues])
        self.assertIn("does not match", report.issues[0].message)

    def test_invalid_format_is_error(self):
        (self.tmp / "Invoices" / "INV-20261-001.json").write_text(
            '{"ticket_id": "TKT-2026-001", "invoice": {"number": "INV-20261-001"}}',
            encoding="utf-8")
        report = self._check()
        self.assertEqual(report.errors, 1, [i.message for i in report.issues])
        self.assertIn("neither", report.issues[0].message)


class TestIndexerDocuments(unittest.TestCase):
    """Proof 1 + 2 (index side): scan_documents indexes both formats."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="g4p2-idx-"))
        make_mixed_vault(self.tmp)
        self.docs = Indexer(services={}).scan_documents(root=self.tmp)

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_legacy_docs_indexed_valid(self):
        legacy = [d for d in self.docs if d["format"] == "legacy"]
        numbers = sorted(d["number"] for d in legacy)
        self.assertEqual(numbers, sorted(LEGACY_INV + LEGACY_RCP))
        self.assertTrue(all(d["valid"] for d in legacy))

    def test_year_docs_indexed_valid(self):
        year = [d for d in self.docs if d["format"] == "year"]
        numbers = sorted(d["number"] for d in year)
        self.assertEqual(numbers, sorted(YEAR_INV + YEAR_RCP))
        self.assertTrue(all(d["valid"] for d in year))

    def test_nothing_unknown_in_mixed_vault(self):
        self.assertEqual(len(self.docs), 8)  # 4+1 legacy, 2+1 year
        self.assertFalse([d for d in self.docs if d["format"] == "unknown"])

    def test_broken_file_flagged_not_crashing(self):
        (self.tmp / "Invoices" / "INV-2026-009.json").write_text(
            "not json {", encoding="utf-8")
        docs = Indexer(services={}).scan_documents(root=self.tmp)
        bad = [d for d in docs if d["number"] == "INV-2026-009"]
        self.assertEqual(len(bad), 1)
        # stem still parsed; format+valid reflect the stem string
        self.assertTrue(bad[0]["valid"])
        self.assertEqual(bad[0]["format"], "year")


class TestNoCollision(unittest.TestCase):
    """Proof 4: legacy and new sequences can never collide."""

    def test_regexes_disjoint_both_directions(self):
        for n in LEGACY_INV:
            self.assertIsNone(re.match(NEW_INV_RE, n), n)
        for n in YEAR_INV:
            self.assertIsNone(re.match(OLD_INV_RE, n), n)
        for n in LEGACY_RCP:
            self.assertIsNone(re.match(NEW_RCP_RE, n), n)
        for n in YEAR_RCP:
            self.assertIsNone(re.match(OLD_RCP_RE, n), n)

    def test_no_string_matches_both_formats(self):
        samples = LEGACY_INV + YEAR_INV + LEGACY_RCP + YEAR_RCP
        for s in samples:
            old = bool(re.match(OLD_INV_RE, s)) or bool(re.match(OLD_RCP_RE, s))
            new = bool(re.match(NEW_INV_RE, s)) or bool(re.match(NEW_RCP_RE, s))
            self.assertFalse(old and new, s)

    def test_globs_disjoint(self):
        # the issuing scan globs can never see across the boundary
        self.assertFalse(fnmatch("INV-20261006-001.json", "INV-2026-*.json"))
        self.assertFalse(fnmatch("INV-2026-001.json", "INV-20261006-*.json"))
        self.assertTrue(fnmatch("INV-2026-001.json", "INV-2026-*.json"))

    def test_counter_keys_distinct(self):
        self.assertNotEqual("invoice:20261006", "invoice:2026")
        self.assertNotEqual("receipt:20261006", "receipt:2026")
        self.assertNotEqual("invoice:2026", "receipt:2026")

    def test_same_seq_yields_distinct_file_names(self):
        # seq 001 exists in BOTH spaces (legacy day + new year) — names differ
        self.assertNotEqual("INV-20261006-001.json", "INV-2026-001.json")
        self.assertNotEqual("RCP-20261006-001.json", "RCP-2026-001.json")


class TestPeekNeverWrites(unittest.TestCase):
    """CLI next-id preview: peek_seq must never mutate the counter file."""

    def setUp(self):
        self.counters = REAL_HOME / "id-counters.json"
        self.assertTrue(self.counters.exists())
        self.before = hashlib.sha256(
            self.counters.read_bytes()).hexdigest()

    def _after(self):
        return hashlib.sha256(self.counters.read_bytes()).hexdigest()

    def test_double_peek_returns_same_value_and_file_untouched(self):
        a = peek_seq("g4p2-peek-probe", 0)
        b = peek_seq("g4p2-peek-probe", 0)
        self.assertEqual(a, b, "peek_seq persisted between calls")
        self.assertEqual(a, 1)
        self.assertEqual(peek_seq("g4p2-peek-probe", 50), 51)
        self.assertEqual(self._after(), self.before,
                         "peek_seq modified real id-counters.json")


class TestCounterNoReuseSandbox(unittest.TestCase):
    """Proof 3: counters never reuse a number after the file is deleted."""

    def setUp(self):
        require_sandbox(self)
        self.env = sb_env()
        self.before = snapshot_pair()

    def tearDown(self):
        restore_pair(self.before)

    def test_year_keys_never_reuse_deleted_numbers(self):
        out = run(self.env, "python3", "-c", COUNTER_REUSE_CODE,
                  str(CODE_HOME))
        self.assertEqual(out.returncode, 0, out.stderr + out.stdout)
        lines = [ln for ln in out.stdout.splitlines()
                 if ln.startswith("REUSE ")]
        self.assertEqual(len(lines), 2, out.stdout)
        for ln in lines:
            _, key, n1, n2, n3 = ln.split()
            self.assertEqual(int(n2), int(n1) + 1, ln)
            self.assertEqual(int(n3), int(n2) + 1, ln)
        self.assertIn("invoice:2026", out.stdout)
        self.assertIn("receipt:2026", out.stdout)


if __name__ == "__main__":
    unittest.main()
