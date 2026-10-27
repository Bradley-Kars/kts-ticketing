"""G3 real read calls (Bradley approval 2026-10-08): GET-only, a few.

Exactly these endpoints, ONE attempt each, no retries:
  GET /v1/balance
  GET /v1/customers?limit=3
  GET /v1/charges?limit=3
  GET /v1/payment_intents?limit=3
  GET /v1/invoices?limit=3

Rules: 403 -> log endpoint + request id + permission message, skip that
endpoint, continue. Any other non-200 -> log endpoint/status/request id
and STOP. Logs ONLY endpoint, status, request id, result count - never
response bodies, customer data, amounts, or emails. Nothing fetched is
stored anywhere.
"""

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.stripe_client import request as stripe_get  # noqa: E402

ENDPOINTS = [
    ("balance", "/v1/balance", None),
    ("customers", "/v1/customers", {"limit": "3"}),
    ("charges", "/v1/charges", {"limit": "3"}),
    ("payment_intents", "/v1/payment_intents", {"limit": "3"}),
    ("invoices", "/v1/invoices", {"limit": "3"}),
]


def main() -> int:
    for name, path, params in ENDPOINTS:
        result = stripe_get(path, params=params)
        status = result.get("status")
        if status == 403:
            print(json.dumps({
                "endpoint": name,
                "status": 403,
                "request_id": result.get("request_id"),
                "permission": result.get("error_message"),
            }, sort_keys=True))
            continue  # record + skip + continue
        if status != 200:
            print(json.dumps({
                "endpoint": name,
                "status": status,
                "request_id": result.get("request_id"),
            }, sort_keys=True))
            print("STOP: non-200 non-403 error - no further endpoints")
            return 1
        out = {
            "endpoint": name,
            "status": 200,
            "request_id": result.get("request_id"),
        }
        if result.get("count") is not None:
            out["count"] = result.get("count")
        if result.get("parse_error"):
            out["parse_error"] = True
        print(json.dumps(out, sort_keys=True))
    print("G3 read calls complete: 5 endpoints attempted, one attempt each")
    return 0


if __name__ == "__main__":
    sys.exit(main())
