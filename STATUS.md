# KTS Ticketing Backend — v0.1 Status

## Current milestone: B1 — Reconcile and verify M1-M5 ✅
## Next milestone: B2 — Publish backend to GitHub — WAITING FOR REMOTE

## Milestone status

| Milestone | Status | Details |
|-----------|--------|---------|
| B1 — Reconcile & verify M1-M5 | Complete | Full suite: 246 passed / 24 failed (sandbox-isolation tests only). 15 tests in test_a6_security_regression.py. Diff: 531 lines, 4 files. |
| B2 — Publish backend | Waiting for remote | Repo Bradley-Kars/kts-ticketing not yet created on GitHub. Local branch work/v0.1 ready with tag m-B1. |
| F1 — Tauri ticketing UI | Not started | |
| F2 — Live check against bridge | Not started | |
| B3 — Token-to-actor identity | Not started | Sandbox only |
| B4 — Backlog cleanup | Not started | Sandbox only |

## Branch state
- Branch: work/v0.1 (orphan, no prior history)
- Tag: m-B1
- Remote: git@github.com:Bradley-Kars/kts-ticketing.git (NOT YET CREATED — waiting for remote)

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

## Next actions (when remote is available)
- git push -u origin work/v0.1
- git push origin m-B1
- Update this STATUS.md with commit hash and push confirmation
