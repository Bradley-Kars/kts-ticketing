# KTS Ticketing Backend — v0.1 Status

## Current milestone: B2 — Publish backend to GitHub ✅
## Next milestone: F1 — Tauri ticketing UI

## Milestone status

| Milestone | Status | Details |
|-----------|--------|---------|
| B1 — Reconcile & verify M1-M5 | Complete | Full suite: 246 passed / 24 failed (sandbox-isolation tests only). 15 tests in test_a6_security_regression.py. Diff: 531 lines, 4 files. |
| B2 — Publish backend | Complete | Repo: Bradley-Kars/kts-ticketing. Branch: work/v0.1. Tag: m-B1. Commit: 51daae6. |
| F1 — Tauri ticketing UI | In progress | Read src-tauri/src/services/ticketing.rs and TicketingPage.tsx. Add create ticket, worklog, status change commands and UI. |
| F2 — Live check against bridge | Not started | |
| B3 — Token-to-actor identity | Not started | Sandbox only |
| B4 — Backlog cleanup | Not started | Sandbox only |

## Branch state
- Branch: work/v0.1 (orphan, no prior history)
- Tag: m-B1
- Commit: 51daae6
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

## Next actions
- F1: Add create ticket, worklog, status change UI to Tauri TicketingPage.tsx
- F2: Live check against running bridge with kts-ticket CLI
- B3: Token-to-actor identity (sandbox only)
- B4: Backlog cleanup (sandbox only)
