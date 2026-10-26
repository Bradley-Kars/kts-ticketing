"""Item V: worklog prose-count validator (Bradley approved 2026-10-07).

Old formula: content.count("### ") - 1 — subtracted a "header" that does
not exist (the section header is the h2 "## Work Log", which never matches
"### "), so every ticket reported prose one less than YAML, and stray h3s
outside the section were counted too. Fix: count ### entry headings inside
the Work Log section only (core.validator.count_prose_worklog_entries).

Covers: 0/1/2/3-entry counts, out-of-section h3 scoping, a missing section
header, a clean validate with 0/1/3-entry tickets (0 errors, 0 warnings),
and a real YAML/prose mismatch that must STILL be detected after the fix.
All data: sandbox only. Code under test: $KTS_CODE_HOME.
"""

import re
import unittest

from phasef_helpers import (
    CLI,
    CODE_HOME,
    SB_VAULT,
    require_sandbox,
    restore_pair,
    run,
    sb_env,
    snapshot_pair,
)

COUNTS_CODE = r"""
import sys
sys.path.insert(0, sys.argv[1])
from core.validator import count_prose_worklog_entries

def md(entries, resolution_h3=""):
    lines = [
        "---",
        "id: TKT-2026-999",
        "type: ticket",
        "status: new",
        "title: Count fixture",
        "---",
        "# TKT-2026-999 - Count fixture",
        "",
        "## Summary",
        "",
        "summary text",
        "",
        "## Work Log",
        "<!-- Oldest at top, newest at bottom. -->",
        "",
    ]
    for i, e in enumerate(entries):
        lines += ["### " + e, "", "description %d" % i, ""]
    lines += ["## Resolution", ""]
    if resolution_h3:
        lines += [resolution_h3, "", "note", ""]
    lines += ["## Related", ""]
    return "\n".join(lines)

cases = [
    ("ZERO", md([])),
    ("ONE", md(["2026-01-01 - remote - 1h - $0 - no-charge"])),
    ("TWO", md(["a", "b"])),
    ("THREE", md(["a", "b", "c"])),
    ("SCOPED", md(["a", "b", "c"], resolution_h3="### not a worklog entry")),
    ("NOSECTION", "# T\n\n### stray heading\n\ntext\n"),
]
for name, content in cases:
    print("%s=%d" % (name, count_prose_worklog_entries(content)))
"""


def kv(stdout, key):
    m = re.search(r"^%s=(\d+)$" % key, stdout, re.M)
    return m.group(1) if m else None


class TestProseCount(unittest.TestCase):
    def test_counts_zero_one_two_three_and_scoping(self):
        out = run(sb_env(), "python3", "-c", COUNTS_CODE, str(CODE_HOME))
        self.assertEqual(out.returncode, 0, out.stderr)
        # entry counts: 0, 1, 2, 3
        self.assertEqual(kv(out.stdout, "ZERO"), "0", out.stdout)
        self.assertEqual(kv(out.stdout, "ONE"), "1", out.stdout)
        self.assertEqual(kv(out.stdout, "TWO"), "2", out.stdout)
        self.assertEqual(kv(out.stdout, "THREE"), "3", out.stdout)
        # an h3 outside Work Log must not be counted
        self.assertEqual(kv(out.stdout, "SCOPED"), "3", out.stdout)
        # no Work Log section at all -> 0 (YAML entries then warn, not vice versa)
        self.assertEqual(kv(out.stdout, "NOSECTION"), "0", out.stdout)


class TestValidateIntegration(unittest.TestCase):
    def setUp(self):
        require_sandbox(self)
        self.env = sb_env()
        self.before = snapshot_pair()

    def tearDown(self):
        restore_pair(self.before)

    def cli(self, *args):
        r = run(self.env, "python3", str(CLI), *args)
        self.assertEqual(r.returncode, 0, (r.stdout or "") + (r.stderr or ""))
        return r

    @staticmethod
    def tid_of(r):
        m = re.search(r"TKT-\d{4}-\d{3}", (r.stdout or "") + (r.stderr or ""))
        return m.group(0) if m else None

    def make_ticket(self, title, logs=0):
        r = self.cli(
            "ticket", "new",
            "--client", "CloudIngest Inc",
            "--client-id", "CUS-000003",
            "--title", title,
        )
        tid = self.tid_of(r)
        self.assertIsNotNone(tid, (r.stdout or "") + (r.stderr or ""))
        for i in range(logs):
            self.cli("ticket", "log", tid, "--hours", "1",
                     "--note", "ItemV entry %d" % i)
        return tid

    def test_validate_clean_with_0_1_and_3_entry_tickets(self):
        self.make_ticket("ItemV zero entries", logs=0)
        self.make_ticket("ItemV one entry", logs=1)
        self.make_ticket("ItemV three entries", logs=3)
        v = self.cli("validate")
        self.assertIn("0 errors, 0 warnings", v.stdout, v.stdout)

    def test_validate_still_detects_mismatch_after_fix(self):
        tid = self.make_ticket("ItemV mismatch", logs=3)
        # Strip one prose entry heading; YAML keeps 3 -> must still warn.
        matches = list((SB_VAULT / "Tickets").glob(tid + " *.md"))
        self.assertEqual(len(matches), 1, matches)
        tf = matches[0]
        lines = tf.read_text(encoding="utf-8").splitlines(keepends=True)
        kept, removed = [], False
        for line in lines:
            if not removed and line.startswith("### "):
                removed = True
                continue
            kept.append(line)
        self.assertTrue(removed, "expected a prose ### heading to strip")
        tf.write_text("".join(kept), encoding="utf-8")

        v = self.cli("validate")
        self.assertIn("0 errors", v.stdout, v.stdout)
        self.assertIn("Worklog count mismatch: YAML=3, prose=2", v.stdout, v.stdout)
        self.assertIn(tid, v.stdout, v.stdout)


if __name__ == "__main__":
    unittest.main()
