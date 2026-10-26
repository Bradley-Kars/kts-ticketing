"""Phase F: DEC-8/DEC-9 evidence as automated tests (Bradley, 2026-10-07).

Maps 1:1 to the Phase F checklist:
  - delete and rebuild indexes; validation on a fresh index
  - a real mutation that produces an event (actor, old, new, timestamp,
    correlation_id shown from the event file)
  - an attempt to overwrite an existing event that is rejected
  - AI observation -> suggestion -> approval -> mutation -> event, end to end
  - migrations 001-003 run twice with backups; migration 004 refuses

All data: sandbox only. Code under test: $KTS_CODE_HOME or real code.
"""

import json
import re
import unittest
from pathlib import Path

from phasef_helpers import (
    CLI,
    CODE_HOME,
    SB_HOME,
    SB_VAULT,
    require_sandbox,
    restore_pair,
    run,
    sb_env,
    snapshot_pair,
)

AI_PIPELINE_CODE = r"""
import json, pathlib, sys
sys.path.insert(0, sys.argv[1])
from datetime import datetime
from core.api import KTSAPI
from core.models import EventActor, AISuggestion

api = KTSAPI()
api.ensure_initialized()
actor = EventActor(id="USR-000001", type="human", name="Bradley Kars")
home = pathlib.Path(sys.argv[2])

# 1. create a fresh probe ticket (real production path)
t = api.create_ticket({
    "title": "PhaseF AI pipeline probe",
    "client_name": "PhaseF Probe",
    "created_by": "USR-000001",
    "updated_by": "USR-000001",
}, actor)
print("STAGE ticket", t.id)

# 2. OBSERVATION: classify (production path writes AIO + emits event)
ai = api.get_service("ai")
cls = ai.classify_ticket(t.id, actor=actor)
print("STAGE observation classify", ",".join(cls["categories"]))
obs_dir = home / "ai" / "observations"
obs = [p.name for p in obs_dir.glob("AIO-*.json") if t.id in p.read_text()]
print("STAGE observation_files", len(obs))

# 3. SUGGESTION: AI submits a change_status proposal (append-only)
sug = AISuggestion(
    type="ai_suggestion",
    id="AIS-999901",
    suggestion_id="AIS-999901",
    title="PhaseF status suggestion for " + t.id,
    status="pending",
    created_at=datetime.now(),
    model="phasef-test",
    suggestion={
        "action": "change_status",
        "target": {"type": "ticket", "id": t.id},
        "proposed_value": "triaged",
        "confidence": 0.93,
        "reasoning": "PhaseF end-to-end approval probe",
        "requires_approval": True,
    },
    created_by="AI-OPENCODE",
    updated_by="AI-OPENCODE",
)
api.submit_ai_suggestion(sug)
print("STAGE suggestion submitted")
try:
    api.submit_ai_suggestion(sug)
    print("STAGE duplicate accepted (BUG)")
except FileExistsError:
    print("STAGE duplicate rejected")

# 4. APPROVAL -> MUTATION -> EVENT (human accepts; AI never acts alone)
ai_action = api.resolve_ai_suggestion("AIS-999901", "accept", actor)
t2 = api.get_service("ticket").get(t.id)
print("STAGE approval action", ai_action.status, ai_action.result_event)
print("STAGE mutation status", t2.status)
sug_f = home / "ai" / "suggestions" / "AIS-999901.json"
print("STAGE suggestion_status", json.loads(sug_f.read_text())["status"])
act_f = home / "ai" / "actions" / (ai_action.action_id + ".json")
print("STAGE action_file", act_f.exists())

# 5. show the mutation event file contents (the audit record)
ev = None
for p in (home / "events").rglob("*.json"):
    if "current" in p.parts:
        continue
    d = json.loads(p.read_text())
    if d.get("event_type") == "ticket.status_changed" and \
       d.get("entity", {}).get("id") == t.id:
        ev = d
assert ev, "status_changed event not found"
print("STAGE event|" + "|".join([
    ev["event_id"], ev["actor"]["id"],
    ev["changes"]["status"]["old"], ev["changes"]["status"]["new"],
    str(ev["timestamp"]), str(ev["correlation_id"]),
]))
"""

APPEND_REJECT_CODE = r"""
import hashlib, json, pathlib, sys
sys.path.insert(0, sys.argv[1])
from core.events import EventEmitter, EventStore
from core.models import EntityRef, EventActor

em = EventEmitter()
actor = EventActor(id="USR-000001", type="human", name="Bradley Kars")
e1 = em.emit(
    event_type="phasef.append_guard_probe",
    entity=EntityRef(type="ticket", id="TKT-2026-001"),
    changes={},
    actor=actor,
    reason="phaseF append-only guard probe",
)
print("EVENT", e1.event_id)

evdir = pathlib.Path(sys.argv[2]) / "events"
real = [p for p in evdir.rglob(e1.event_id + ".json")
        if "current" not in p.parts]
assert real, "event file not found"
before = hashlib.sha256(real[0].read_bytes()).hexdigest()

store = EventStore()
try:
    store.append(e1)
    print("OVERWRITE accepted (BUG)")
except FileExistsError as exc:
    print("OVERWRITE rejected:", str(exc)[:70])

after = hashlib.sha256(real[0].read_bytes()).hexdigest()
print("HASH unchanged", before == after)

# no edit/update path exists on the event store
mutators = [m for m in ("update", "edit", "overwrite", "replace", "write")
            if hasattr(EventStore, m)]
print("MUTATOR_METHODS", mutators)
"""

# CLI helpers ---------------------------------------------------------------

def parse_created(stdout):
    m = re.search(r"Created (TKT-\d{4}-\d{3}):", stdout)
    return m.group(1) if m else None


class TestIndexAndValidate(unittest.TestCase):
    def setUp(self):
        require_sandbox(self)
        self.env = sb_env()
        self.before = snapshot_pair()

    def tearDown(self):
        restore_pair(self.before)

    def test_delete_rebuild_indexes_then_validate_fresh(self):
        idx = SB_HOME / "indexes"
        deleted = [p.name for p in idx.glob("*.json")]
        self.assertTrue(deleted, "no index files in sandbox to delete")
        for p in idx.glob("*.json"):
            p.unlink()
        self.assertEqual(list(idx.glob("*.json")), [],
                         "index deletion incomplete")

        r = run(self.env, "python3", str(CLI), "index")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("Index rebuild complete:", r.stdout, r.stdout)
        for name in ("tickets.json", "clients.json", "organizations.json",
                     "search-index.json", "index-stats.json"):
            self.assertTrue((idx / name).exists(), f"{name} not rebuilt")

        v = run(self.env, "python3", str(CLI), "validate")
        self.assertEqual(v.returncode, 0, v.stderr)
        self.assertIn("0 errors", v.stdout, v.stdout)
        self.assertIn("entities", v.stdout, v.stdout)


class TestMutationEvent(unittest.TestCase):
    def setUp(self):
        require_sandbox(self)
        self.env = sb_env()
        self.before = snapshot_pair()

    def tearDown(self):
        restore_pair(self.before)

    def test_status_change_emits_full_event_file(self):
        created = run(
            self.env, "python3", str(CLI), "ticket", "new",
            "--client", "PhaseF Evidence",
            "--title", "PhaseF mutation event probe",
        )
        self.assertEqual(created.returncode, 0, created.stderr)
        tid = parse_created(created.stdout)
        self.assertIsNotNone(tid, created.stdout)

        changed = run(
            self.env, "python3", str(CLI), "ticket", "status", tid,
            "--status", "triaged",
        )
        self.assertEqual(changed.returncode, 0, changed.stderr)
        self.assertIn("status -> triaged", changed.stdout, changed.stdout)

        # Finding 1 patched (Bradley 2026-10-07): CLI `ticket status` now
        # routes through change_ticket_status, so the dedicated
        # ticket.status_changed event MUST be present (no fallback to the
        # generic ticket.updated).
        status_events = []
        for p in (SB_HOME / "events").rglob("EVT-*.json"):
            if "current" in p.parts:
                continue
            d = json.loads(p.read_text())
            if d.get("event_type") == "ticket.status_changed" and \
               d.get("entity", {}).get("id") == tid:
                status_events.append(d)
        self.assertTrue(
            status_events,
            "no ticket.status_changed event for probe ticket",
        )
        d = status_events[-1]
        self.assertRegex(d["event_id"], r"^EVT-\d{8}$")
        self.assertEqual(d["event_type"], "ticket.status_changed")
        self.assertEqual(d["actor"]["id"], "USR-000001")
        self.assertEqual(d["actor"]["type"], "human")
        self.assertTrue(d["actor"].get("name"))
        self.assertEqual(d["changes"]["status"]["old"], "new")
        self.assertEqual(d["changes"]["status"]["new"], "triaged")
        self.assertTrue(d["timestamp"], "timestamp missing")
        self.assertIn("correlation_id", d, "correlation_id field missing")
        # field present; value is null when no correlation chain is set
        print(f"EVENT FILE {d['event_id']} ({d['event_type']}): "
              f"actor={d['actor']} "
              f"old={d['changes']['status']['old']} "
              f"new={d['changes']['status']['new']} "
              f"timestamp={d['timestamp']} "
              f"correlation_id={d['correlation_id']}")


class TestEventImmutability(unittest.TestCase):
    def setUp(self):
        require_sandbox(self)
        self.env = sb_env()
        self.before = snapshot_pair()

    def tearDown(self):
        restore_pair(self.before)

    def test_overwrite_of_existing_event_rejected(self):
        out = run(
            self.env, "python3", "-c", APPEND_REJECT_CODE,
            str(CODE_HOME), str(SB_HOME),
        )
        self.assertEqual(out.returncode, 0, out.stderr + out.stdout)
        self.assertIn("OVERWRITE rejected:", out.stdout, out.stdout)
        self.assertIn("HASH unchanged True", out.stdout, out.stdout)
        self.assertIn("MUTATOR_METHODS []", out.stdout, out.stdout)


class TestAIPipelineEndToEnd(unittest.TestCase):
    def setUp(self):
        require_sandbox(self)
        self.env = sb_env()
        self.before = snapshot_pair()

    def tearDown(self):
        restore_pair(self.before)

    def test_observation_suggestion_approval_mutation_event(self):
        out = run(
            self.env, "python3", "-c", AI_PIPELINE_CODE,
            str(CODE_HOME), str(SB_HOME),
        )
        self.assertEqual(out.returncode, 0, out.stderr + out.stdout)
        s = out.stdout
        self.assertIn("STAGE ticket TKT-", s, s)
        self.assertIn("STAGE observation classify", s, s)
        m_obs = re.search(r"STAGE observation_files (\d+)", s)
        self.assertIsNotNone(m_obs, s)
        self.assertGreaterEqual(int(m_obs.group(1)), 1,
                                "no AIO observation file written")
        self.assertIn("STAGE suggestion submitted", s)
        self.assertIn("STAGE duplicate rejected", s, "submit not append-only")
        self.assertIn("STAGE approval action completed EVT-", s, s)
        self.assertIn("STAGE mutation status triaged", s, s)
        self.assertIn("STAGE suggestion_status accepted", s, s)
        self.assertIn("STAGE action_file True", s, s)
        m = re.search(r"STAGE event\|(\S+)\|(\S+)\|(\S+)\|(\S+)\|(.+)\|(\S+)\s*$", s)
        self.assertIsNotNone(m, s)
        evt_id, actor_id, old, new, ts, corr = m.groups()
        self.assertRegex(evt_id, r"^EVT-\d{8}$")
        self.assertEqual(actor_id, "USR-000001")
        self.assertEqual((old, new), ("new", "triaged"))
        self.assertTrue(ts and ts not in ("None", ""))
        self.assertEqual(corr, "None",
                         "correlation_id should be null (no chain configured)")


class TestMigrations(unittest.TestCase):
    def setUp(self):
        require_sandbox(self)
        self.env = sb_env()
        self.before = snapshot_pair()
        self.applied = SB_HOME / "migrations" / "applied.json"
        self.orig_applied = self.applied.read_text()

    def tearDown(self):
        restore_pair(self.before)

    def test_001_003_run_twice_and_004_refuses(self):
        self.applied.write_text("[]")
        backups = SB_HOME / "backups"
        for v in ("001", "002", "003"):
            r1 = run(self.env, "python3", str(CLI), "migrate", v)
            self.assertIn(f"Migration {v}: SUCCESS", r1.stdout,
                          r1.stdout + r1.stderr)
            bdirs = sorted(backups.glob(f"pre-{v}-*"))
            self.assertTrue(bdirs, f"no pre-{v} backup created")
            mig_txt = bdirs[0] / "MIGRATION.txt"
            self.assertTrue(mig_txt.exists(), f"{mig_txt} missing")
            self.assertIn(v, mig_txt.read_text())

            r2 = run(self.env, "python3", str(CLI), "migrate", v)
            self.assertIn("already applied", r2.stdout,
                          f"second run of {v} not refused: {r2.stdout}")
            self.assertIn(f"Migration {v}: FAILED", r2.stdout)

        r4 = run(self.env, "python3", str(CLI), "migrate", "004")
        self.assertIn("Migration 004: FAILED", r4.stdout, r4.stdout)
        self.assertIn("destructive", r4.stdout, r4.stdout)
        # destructive refusal happens BEFORE any backup is taken
        self.assertFalse(list(backups.glob("pre-004-*")),
                         "004 must refuse before creating a backup")

        st = run(self.env, "python3", str(CLI), "migrate-status")
        self.assertEqual(st.returncode, 0, st.stderr)
        self.assertIn("001", st.stdout)
        self.assertIn("004", st.stdout)  # still pending


if __name__ == "__main__":
    unittest.main()
