# KTS Ticketing Backend — v0.1 Status

## Current milestone: B2 — Publish backend ✅
## Next milestone: F1 — Tauri ticketing UI

## Milestone status

| Milestone | Status | Details |
|-----------|--------|---------|
| B1 — Reconcile & verify M1-M5 | Complete | Full suite: 246 passed / 24 failed (sandbox-isolation tests only). 15 tests in tests/test_a6_security_regression.py. Diff: 5004 lines, 30 files. |
| B2 — Publish backend | Complete | Repo: Bradley-Kars/kts-ticketing. Branch: work/v0.1. Tag: m-B1. Commit: 8436f10. Pushed to origin. |
| F1 — Tauri ticketing UI | Not started | Read src-tauri/src/services/ticketing.rs and TicketingPage.tsx. Add create ticket, worklog, status change commands and UI. |
| F2 — Live check against bridge | Not started | |
| B3 — Token-to-actor identity | Not started | Sandbox only |
| B4 — Backlog cleanup | Not started | Sandbox only |

## Branch state
- Branch: work/v0.1 (orphan, no prior history)
- Tag: m-B1
- Commit: 8436f10
- Remote: git@github.com:Bradley-Kars/kts-ticketing.git (PUSHED ✅)

## Files in repo
- core/ — Python service layer (ticket, task, project, knowledge, user, auth)
- tests/ — 15 test functions in test_a6_security_regression.py
- docs/evidence/ — B1 evidence, baseline/after-smoke snapshots
- cli.py, pyproject.toml, verify-gates.sh
- .gitignore (excludes vault dirs, users.yaml, tokens, secrets)
- .git/hooks/pre-push (secret/vault scan hook)

## Bridge status
- Running via systemd on 127.0.0.1:8799 (systemd unit: kts-bridge.service)
- Token: ~/.config/kts/bridge-token (mode 600)
- CLI: ~/bin/kts-ticket (create, list, show, worklog, status, import)

## Verification evidence (this session)

- `git rev-parse HEAD`: aaf373e (local HEAD before push; remote now at 8436f10)
- `git ls-remote origin`: work/v0.1 at 8436f10 ✅
- `systemctl --user is-active kts-bridge.service`: active ✅
- `ss -ltn | grep 8799`: LISTEN 127.0.0.1:8799 ✅
- `curl -s -o /dev/null -w "%{http_code}" http://127.0.0.1:8799/tickets` (no token): 401 ✅
- `curl -s -o /dev/null -w "%{http_code}" http://127.0.0.1:8799/tickets?all=true` (with token): 200 ✅
- `git rev-parse HEAD`: 8436f1036e7c49a24ff3ccaaeda9da4d93d4db8b (after push)

## Users.yaml (read-only check)

```
  USR-000002:
    id: "USR-000002"
    role: "technician"
```
users.yaml sha: 698299532291b59072775a52f2d0cf4a17ea6662561c98fec7ff7c9885568913

## Git history (local)

```
aaf373e chore: initial commit for KTS ticketing backend v0.1
88b0846 docs: add STATUS.md with milestone status and B1 evidence
6868e12 chore: initial commit for KTS ticketing backend v0.1
dc24d1f chore: finalize KTS ticketing backend v0.1
8436f10 chore: remove sandbox.py from tracking; add auth/audit.log to gitignore
```

## Next actions

- F1: Add create ticket, worklog, status change UI to Tauri TicketingPage.tsx
- F2: Live check against running bridge with kts-ticket CLI
- B3: Token-to-actor identity (sandbox only)
- B4: Backlog cleanup (sandbox only)
