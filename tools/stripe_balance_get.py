"""G2 one-call balance tool - thin shim over core.stripe_client.

Bradley G3 scope 2026-10-08: the GET-only guard MOVED into
core/stripe_client.py so there is ONE guard, not two. This shim
re-exports the guard names (so nothing can pass through here that the
core guard would refuse) and keeps the G2 command line working.

Run (the single G2 read, already executed 2026-10-08):
  python3 ~/.ticketing/tools/stripe_balance_get.py
"""

from __future__ import annotations

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Re-export THE single guard (defined only in core/stripe_client.py):
from core.stripe_client import (  # noqa: E402,F401
    ALLOWED_METHODS,
    check_method,
    load_secret,
    request,
)

BALANCE_PATH = "/v1/balance"


def main() -> int:
    result = request(BALANCE_PATH)
    out = {"status": result.get("status"), "request_id": result.get("request_id")}
    print(json.dumps(out, sort_keys=True))
    return 0 if result.get("status") == 200 else 1


if __name__ == "__main__":
    sys.exit(main())
