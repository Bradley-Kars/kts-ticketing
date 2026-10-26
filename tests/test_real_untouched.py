"""Phase F: proof that sandbox activity never changes the real vault,
real home, or the real counters (Bradley, 2026-10-07 checklist item 2).

Method: byte-level snapshot of the REAL vault and REAL ~/.ticketing
(files + symlinks, pycache excluded) before and after a battery of
sandbox mutations (ticket create/status/log, invoice generation, AI
observation, event emission, index rebuild). Any byte difference fails
the test; on failure the real trees are restored from the snapshot and
the diff is reported.
"""

import unittest

from phasef_helpers import (
    CLI,
    CODE_HOME,
    REAL_HOME,
    REAL_VAULT,
    SB_VAULT,
    require_sandbox,
    restore_pair,
    run,
    sb_env,
    snapshot,
    snapshot_pair,
)

INVOICE_SNIP = r"""
import sys
sys.path.insert(0, sys.argv[1])
from core.api import KTSAPI
from core.models import EventActor
api = KTSAPI()
api.ensure_initialized()
inv = api.generate_invoice(
    "TKT-2026-003",
    EventActor(id="USR-000001", type="human", name="Bradley Kars"),
)
print("NUM", inv["invoice"]["number"])
"""

OBSERVE_SNIP = r"""
import sys
sys.path.insert(0, sys.argv[1])
from core.api import KTSAPI
from core.models import EventActor
api = KTSAPI()
api.ensure_initialized()
r = api.get_service("ai").classify_ticket(
    "TKT-2026-001",
    actor=EventActor(id="USR-000001", type="human", name="Bradley Kars"),
)
print("CLASSIFIED", ",".join(r["categories"]))
"""

EMIT_SNIP = r"""
import sys
sys.path.insert(0, sys.argv[1])
from core.events import EventEmitter
from core.models import EntityRef, EventActor
em = EventEmitter()
e = em.emit(
    event_type="phasef.real_shield_probe",
    entity=EntityRef(type="ticket", id="TKT-2026-001"),
    changes={},
    actor=EventActor(id="USR-000001", type="human", name="Bradley Kars"),
    reason="real-untouched battery event",
)
print("EMITTED", e.event_id)
"""


def diff_summary(before, after, root_label):
    added = sorted(set(after["files"]) - set(before["files"]))
    removed = sorted(set(before["files"]) - set(after["files"]))
    changed = sorted(
        rel for rel in set(before["files"]) & set(after["files"])
        if before["files"][rel] != after["files"][rel]
    )
    links_added = sorted(set(after["links"]) - set(before["links"]))
    links_removed = sorted(set(before["links"]) - set(after["links"]))
    return (
        f"{root_label}: added={added} removed={removed} changed={changed} "
        f"links_added={links_added} links_removed={links_removed}"
    )


class TestRealDataUntouched(unittest.TestCase):
    def setUp(self):
        require_sandbox(self)
        self.env = sb_env()
        # sandbox state is restored after the battery (only REAL data is
        # asserted-on; sandbox itself must not leak probe artifacts)
        self.sb_before = snapshot_pair()

    def tearDown(self):
        restore_pair(self.sb_before)

    def test_sandbox_battery_never_changes_real_vault_or_counters(self):
        real_before = {
            "vault": snapshot(REAL_VAULT),
            "home": snapshot(REAL_HOME),
        }

        steps = [
            ("ticket new", run(
                self.env, "python3", str(CLI), "ticket", "new",
                "--client", "RealShield",
                "--title", "sandbox battery probe",
            )),
        ]
        for name, r in steps:
            self.assertEqual(r.returncode, 0, f"{name}: {r.stderr}")
        probe_id = None
        import re
        m = re.search(r"Created (TKT-\d{4}-\d{3}):", steps[0][1].stdout)
        self.assertIsNotNone(m, steps[0][1].stdout)
        probe_id = m.group(1)

        more = [
            ("ticket status", run(
                self.env, "python3", str(CLI), "ticket", "status",
                probe_id, "--status", "triaged",
            )),
            ("ticket log", run(
                self.env, "python3", str(CLI), "ticket", "log", probe_id,
                "--hours", "0.5", "--note", "real shield probe",
            )),
            ("invoice", run(
                self.env, "python3", "-c", INVOICE_SNIP, str(CODE_HOME),
            )),
            ("observation", run(
                self.env, "python3", "-c", OBSERVE_SNIP, str(CODE_HOME),
            )),
            ("event emit", run(
                self.env, "python3", "-c", EMIT_SNIP, str(CODE_HOME),
            )),
            ("index rebuild", run(
                self.env, "python3", str(CLI), "index",
            )),
        ]
        for name, r in more:
            self.assertEqual(
                r.returncode, 0,
                f"{name} failed rc={r.returncode}\n{r.stdout}\n{r.stderr}",
            )
        self.assertIn("NUM INV-", more[2][1].stdout, more[2][1].stdout)
        self.assertIn("CLASSIFIED", more[3][1].stdout, more[3][1].stdout)
        self.assertIn("EMITTED EVT-", more[4][1].stdout, more[4][1].stdout)

        real_after = {
            "vault": snapshot(REAL_VAULT),
            "home": snapshot(REAL_HOME),
        }

        problems = []
        if real_before["vault"]["files"] != real_after["vault"]["files"] or \
           real_before["vault"]["links"] != real_after["vault"]["links"]:
            problems.append(diff_summary(
                real_before["vault"], real_after["vault"], "REAL VAULT"))
        if real_before["home"]["files"] != real_after["home"]["files"] or \
           real_before["home"]["links"] != real_after["home"]["links"]:
            problems.append(diff_summary(
                real_before["home"], real_after["home"], "REAL HOME"))

        if problems:
            # protect real data first, then fail loudly with the diff
            from phasef_helpers import restore
            restore(REAL_VAULT, real_before["vault"])
            restore(REAL_HOME, real_before["home"])
            self.fail(
                "sandbox battery changed REAL data (restored from "
                "snapshot):\n" + "\n".join(problems)
            )

        print(
            f"REAL UNTOUCHED: vault {len(real_after['vault']['files'])} files "
            f"+ {len(real_after['vault']['links'])} links, "
            f"home {len(real_after['home']['files'])} files "
            f"+ {len(real_after['home']['links'])} links, byte-identical "
            f"(id-counters.json included)"
        )


if __name__ == "__main__":
    unittest.main()
