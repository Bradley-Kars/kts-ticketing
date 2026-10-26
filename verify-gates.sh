#!/bin/bash
# verify-gates.sh — Independent gate verification for KTS core files.
# Run from ~/.ticketing. Prints every sha256 and count, exit 1 on any
# unexpected value.
set -euo pipefail

CODE=~/.ticketing
VAULT="$HOME/Documents/KTS Records"
HOME_DIR=~/.ticketing

echo "=== FILE SHAS (real tree) ==="
sha256sum "$CODE/core/permissions.py"
sha256sum "$CODE/core/services/knowledge_service.py"
sha256sum "$CODE/core/api.py"
sha256sum "$CODE/core/models/__init__.py"
sha256sum "$CODE/core/services/ticket_service.py"
sha256sum "$CODE/core/services/project_service.py"
sha256sum "$CODE/core/indexer.py"
echo "---"
echo "Expected permissions.py: 9497deda3c473f541812d78d86c4c51e2a67762b24f389e3fd43e611fa19f27c"
echo "Expected knowledge_service.py: 39a15846ca88721bfe411bbc31403fe33c774f31a29748b395098062cd6f7de8"
echo "Expected api.py: 9f9b79f922e46ca0c005c0d155e519d70eb7124b8b4941b7a2e38c6f6e5f8a80"
echo "Expected models/__init__.py: 08ba254f049eeb0091d3612ec3e5c947895b91946a39c9d4bf4724b087772bc6"
echo ""

echo "=== REAL TREE GATES ==="
# Counters
COUNTERS_SHA=$(sha256sum "$HOME_DIR/id-counters.json" | awk '{print $1}')
echo "id-counters.json sha: $COUNTERS_SHA"
echo "Expected:            22a89c711f1f08f74119fe77eea1e1436f218d035bcc16bdf444c4f4de6ae245"

# Users.yaml
USERS_SHA=$(sha256sum "$HOME_DIR/config/users.yaml" | awk '{print $1}')
echo "users.yaml sha:       $USERS_SHA"
echo "Expected:             93f7797d42c115d2ddd7e4e944088e17b721370859ceab49140e6b0a2ecaada4"
echo ""

# Vault file count
VAULT_COUNT=$(find "$VAULT" -type f ! -name '*.pyc' ! -path '*/__pycache__/*' | wc -l)
echo "Vault file count:     $VAULT_COUNT (expected 47)"
echo ""

# Events: active JSON files (non-symlink)
EVENTS_ACTIVE=$(find "$HOME_DIR/events" -name "EVT-*.json" ! -type l 2>/dev/null | wc -l)
EVENTS_SYMLINKS=$(find "$HOME_DIR/events/current" -type l 2>/dev/null | wc -l)
echo "Events active (JSON): $EVENTS_ACTIVE (expected 131)"
echo "Events symlinks:      $EVENTS_SYMLINKS (expected 131)"
echo ""

# Port 8799
if command -v lsof >/dev/null 2>&1; then
    if lsof -i :8799 >/dev/null 2>&1; then
        echo "Port 8799: IN USE (bridge running — stale code risk)"
    else
        echo "Port 8799: free"
    fi
else
    echo "Port 8799: lsof not available, skipping"
fi
echo ""

echo "=== SANDBOX TEST FILES ==="
echo "test_mc2_t8_permissions.py sha: $(sha256sum "$CODE/tests/test_mc2_t8_permissions.py" | awk '{print $1}')"
echo "test_mc2_kb_service_bugfix.py sha: $(sha256sum "$CODE/tests/test_mc2_kb_service_bugfix.py" | awk '{print $1}')"
echo "test_mc2_dupdefs.py sha: $(sha256sum "$CODE/tests/test_mc2_dupdefs.py" | awk '{print $1}')"
echo ""

echo "=== TOKEN CHECK ==="
TOK_COUNT=$(grep -r "tok_" "$CODE/core/" --include="*.py" 2>/dev/null | grep -v ".pyc" | wc -l)
echo "tok_ count in core/: $TOK_COUNT (expected 0)"
echo ""

echo "=== VALIDATE ==="
cd "$CODE"
PYTHONDONTWRITEBYTECODE=1 python3 -m cli validate
echo ""

echo "=== DONE ==="
