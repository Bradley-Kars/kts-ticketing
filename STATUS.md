# KTS Ticketing Backend — v0.1 Status

## Current milestone: B4 — Backlog cleanup ✅
## Next milestone: F1 — Tauri ticketing UI

## Milestone status

| Milestone | Status | Details |
|-----------|--------|---------|
| B1 — Reconcile & verify M1-M5 | Complete | Full suite: 246 passed / 24 failed (sandbox-isolation tests only). 15 tests in tests/test_a6_security_regression.py. Diff: 5004 lines, 30 files. |
| B2 — Publish backend | Complete | Repo: Bradley-Kars/kts-ticketing. Branch: work/v0.1. Tag: m-B1. Commit: 8436f10. Pushed to origin. |
| F1 — Tauri ticketing UI | Complete | Tauri commands: ticketing_create_ticket, ticketing_add_worklog, ticketing_change_status. Tauri UI: create ticket form, worklog form, status change dropdown. TypeScript compiles. |
| F2 — Live check against bridge | Complete | Bridge running on 127.0.0.1:8799 via systemd. kts-ticket CLI: create, list, show, worklog, status, import all tested. |
| B3 — Token-to-actor identity | Complete | Sandbox only. token_service.mint() accepts actor_id, verify() returns actor_id. bridge.py middleware rejects token without actor_id (401, audited). BRIDGE_ACTOR_ID constant removed. |
| B4 — Backlog cleanup | Complete | Sandbox only. ticket.assign and ticket.change_status are now functional permission checks (not vestigial). ticket.assign required for assigned_to/assigned_team changes; ticket.change_status required for status changes. |

## Branch state
- Branch: work/b3 (sandbox, B3/B4 complete)
- Tag: m-B3
- Commit: 8caab2c
- Remote: git@github.com:Bradley-Kars/kts-ticketing.git (PUSHED ✅)
- Branch work/v0.1: at 8436f10 (B2 complete)
- Branch work/b3: at 8caab2c (B3/B4 complete)

## Files in repo
- core/ — Python service layer (ticket, task, project, knowledge, user, auth)
- tests/ — 21 test functions in test_a6_security_regression.py (15 original + 6 B3/B4)
- docs/evidence/ — B1 evidence, baseline/after-smoke snapshots
- cli.py, pyproject.toml, verify-gates.sh
- .gitignore (excludes vault dirs, users.yaml, tokens, secrets)
- .git/hooks/pre-push (secret/vault scan hook)

## Bridge status
- Running via systemd on 127.0.0.1:8799 (systemd unit: kts-bridge.service)
- Token: ~/.config/kts/bridge-token (mode 600)
- CLI: ~/bin/kts-ticket (create, list, show, worklog, status, import)

## Verification evidence (this session)

- `git rev-parse HEAD`: 8caab2c (sandbox HEAD)
- `git ls-remote origin work/b3`: 8caab2c ✅
- `systemctl --user is-active kts-bridge.service`: active ✅
- `ss -ltn | grep 8799`: LISTEN 127.0.0.1:8799 ✅
- `curl -s -o /dev/null -w "%{http_code}" http://127.0.0.1:8799/tickets` (no token): 401 ✅
- `curl -s -o /dev/null -w "%{http_code}" http://127.0.0.1:8799/tickets?all=true` (with token): 200 ✅

## Users.yaml (read-only check)

```
  USR-000002:
    id: "USR-000002"
    role: "technician"
```
users.yaml sha: 698299532291b59072775a52f2d0cf4a17ea6662561c98fec7ff7c9885568913

## Git history (local sandbox)

```
8caab2c feat: B3 - token-to-actor identity in bridge middleware
69c8eb5 feat: AI4 - token-to-actor identity in auth and bridge
16c1025 docs: add STATUS.md with milestone status and B1 evidence
51daae6 chore: initial commit for KTS ticketing backend v0.1
```

## Next actions (not started)

- B3/B4 promotion to real tree: WAITING FOR USER REVIEW of diff on GitHub
- Any further milestones: pending user direction
