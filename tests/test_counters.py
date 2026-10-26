"""Phase F: non-reusing counter regression tests (id-counters.json).

Bradley's numbering contract (locked 2026-10-07): events and invoices
are monotonic and never reuse a number, even after the file carrying it
is deleted. counters.next_seq stores the LAST ISSUED number and returns
max(stored, scan_hint) + 1; the two real consumers are event generation
(core/events.py) and per-day invoice generation (core/api.py).

All data: sandbox only (all three KTS_* vars). Code under test:
$KTS_CODE_HOME if set, otherwise the real ~/.ticketing. Full byte
snapshot/restore around every test.
"""

import re
import unittest

from phasef_helpers import (
    CODE_HOME,
    SB_HOME,
    SB_VAULT,
    require_sandbox,
    restore_pair,
    run,
    sb_env,
    snapshot_pair,
)

NEXT_SEQ_CODE = r"""
import sys
sys.path.insert(0, sys.argv[1])
from core.counters import next_seq
print("A", next_seq("phasef-probe", 0))
print("B", next_seq("phasef-probe", 0))
print("C", next_seq("phasef-probe", 50))
print("D", next_seq("phasef-probe", 0))
print("E", next_seq("phasef-probe2", 10))
print("F", next_seq("phasef-probe2", 0))
"""

EVENT_REUSE_CODE = r"""
import pathlib, sys
sys.path.insert(0, sys.argv[1])
from core.events import EventEmitter, EventStore
from core.models import EntityRef, EventActor

em = EventEmitter()
actor = EventActor(id="USR-000001", type="human", name="Bradley Kars")
e1 = em.emit(
    event_type="phasef.counter_probe",
    entity=EntityRef(type="ticket", id="TKT-2026-001"),
    changes={},
    actor=actor,
    reason="phaseF event non-reuse probe",
)
print("EVT1", e1.event_id)

# append-only guard FIRST, while the file still exists: re-appending an
# existing event must be rejected and must not change the file.
store = EventStore()
try:
    store.append(e1)
    print("REAPPEND accepted (BUG)")
except FileExistsError as exc:
    print("REAPPEND rejected:", str(exc)[:60])

# Delete the file that carried the number (the exact reuse hazard fixed
# in counters.py): unlink real file AND the current/ symlink.
evdir = pathlib.Path(sys.argv[2]) / "events"
real = [p for p in evdir.rglob(e1.event_id + ".json")
        if "current" not in p.parts]
assert real, "event file not found"
real[0].unlink()
link = evdir / "current" / (e1.event_id + ".json")
if link.is_symlink() or link.exists():
    link.unlink()

e2 = em.emit(
    event_type="phasef.counter_probe",
    entity=EntityRef(type="ticket", id="TKT-2026-001"),
    changes={},
    actor=actor,
    reason="phaseF event non-reuse probe after delete",
)
print("EVT2", e2.event_id)
"""

INVOICE_REUSE_CODE = r"""
import json, pathlib, sys
sys.path.insert(0, sys.argv[1])
from core.api import KTSAPI
from core.models import EventActor

api = KTSAPI()
api.ensure_initialized()
actor = EventActor(id="USR-000001", type="human", name="Bradley Kars")

inv1 = api.generate_invoice("TKT-2026-003", actor)
num1 = inv1["invoice"]["number"]
print("NUM1", num1)
p1 = pathlib.Path(sys.argv[2]) / "Invoices" / (num1 + ".json")
assert p1.exists(), "invoice file not written: " + str(p1)
p1.unlink()  # delete the file carrying the number - the reuse hazard

inv2 = api.generate_invoice("TKT-2026-003", actor)
num2 = inv2["invoice"]["number"]
print("NUM2", num2)
"""


def kv(stdout, key):
    for line in stdout.splitlines():
        parts = line.split()
        if len(parts) == 2 and parts[0] == key:
            return parts[1]
    return None


class TestCounterNonReuse(unittest.TestCase):
    """id-counters.json: monotonic, never reuses a deleted number."""

    def setUp(self):
        require_sandbox(self)
        self.env = sb_env()
        self.before = snapshot_pair()

    def tearDown(self):
        restore_pair(self.before)

    def test_next_seq_monotonic_and_persists(self):
        out = run(self.env, "python3", "-c", NEXT_SEQ_CODE, str(CODE_HOME))
        self.assertEqual(out.returncode, 0, out.stderr)
        a, b = kv(out.stdout, "A"), kv(out.stdout, "B")
        c, d = kv(out.stdout, "C"), kv(out.stdout, "D")
        e, f = kv(out.stdout, "E"), kv(out.stdout, "F")
        self.assertEqual((a, b), ("1", "2"), out.stdout)
        # scan hint 50 beats stored 2 -> 51 (max(stored, hint) + 1)
        self.assertEqual(c, "51", out.stdout)
        # stored now 51; stale hint 0 must not pull it back -> 52
        self.assertEqual(d, "52", out.stdout)
        self.assertEqual((e, f), ("11", "12"), out.stdout)

    def test_event_id_not_reused_after_file_delete(self):
        out = run(
            self.env, "python3", "-c", EVENT_REUSE_CODE,
            str(CODE_HOME), str(SB_HOME),
        )
        self.assertEqual(out.returncode, 0, out.stderr + out.stdout)
        evt1, evt2 = kv(out.stdout, "EVT1"), kv(out.stdout, "EVT2")
        self.assertRegex(evt1 or "", r"^EVT-\d{8}$")
        self.assertRegex(evt2 or "", r"^EVT-\d{8}$")
        self.assertNotEqual(evt1, evt2, "deleted event number was reused")
        self.assertEqual(
            int(evt2.split("-")[1]), int(evt1.split("-")[1]) + 1,
            f"{evt1} was deleted; next must be {evt2} = +1, never {evt1} again",
        )
        self.assertIn("REAPPEND rejected:", out.stdout, out.stdout)

    def test_invoice_number_not_reused_after_file_delete(self):
        out = run(
            self.env, "python3", "-c", INVOICE_REUSE_CODE,
            str(CODE_HOME), str(SB_VAULT),
        )
        self.assertEqual(out.returncode, 0, out.stderr + out.stdout)
        num1, num2 = kv(out.stdout, "NUM1"), kv(out.stdout, "NUM2")
        # Option B (Bradley 2026-10-08): year-scoped INV-YYYY-###.
        self.assertRegex(num1 or "", r"^INV-\d{4}-\d{3}$")
        self.assertRegex(num2 or "", r"^INV-\d{4}-\d{3}$")
        self.assertNotEqual(num1, num2, "deleted invoice number was reused")
        year1, n1 = num1.rsplit("-", 1)
        year2, n2 = num2.rsplit("-", 1)
        self.assertEqual(year1, year2)
        self.assertEqual(int(n2), int(n1) + 1,
                         f"{num1} deleted; next must be {num2} = +1")


if __name__ == "__main__":
    unittest.main()
