"""KTS Stripe read client - GET-only (Bradley G3 approval 2026-10-08).

THIS MODULE HOLDS THE SINGLE GET-ONLY GUARD for every Stripe access in
this codebase. tools/stripe_balance_get.py re-exports from here; there
is no second allowlist anywhere. check_method() refuses POST/PUT/PATCH/
DELETE BEFORE the key is loaded and BEFORE any socket opens, so a
refused method can never become a request.

Rules enforced with the module (Bradley's G3 scope):
* GET only. No write call is approved; this client cannot issue one.
* The key is read from ~/.ticketing/config/.env at call time. It is
  never printed, hashed, or written anywhere; tracebacks print source
  lines only (no key material in this file), and request dumps never
  include the Authorization header.
* Callers log request IDs, status codes, and result counts ONLY.
  Response bodies are returned to the caller in memory and never
  stored - not in the vault, not in the ticketing tree, nowhere.
  Balance payloads (dicts) produce count=None so amounts are never
  part of a log line.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.parse
import urllib.request

ALLOWED_METHODS = frozenset({"GET"})
API_BASE = "https://api.stripe.com"
DEFAULT_TIMEOUT = 30


def check_method(method: str) -> str:
    """Return the uppercased method, or raise ValueError if not GET.

    The allowlist proof: anything outside ALLOWED_METHODS is refused
    here, before load_secret() and before any socket is opened.
    """
    m = str(method).upper()
    if m not in ALLOWED_METHODS:
        raise ValueError("method %s refused: KTS Stripe allowlist is GET-only" % m)
    return m


def default_env_path() -> str:
    return os.path.join(os.path.expanduser("~"), ".ticketing", "config", ".env")


def load_secret(env_path: str | None = None) -> str:
    """Return STRIPE_SECRET_KEY from the .env file. Never logged."""
    path = env_path or default_env_path()
    with open(path, "r", encoding="utf-8") as fh:
        for line in fh:
            if line.startswith("STRIPE_SECRET_KEY="):
                value = line.split("=", 1)[1].strip()
                if value:
                    return value
    raise RuntimeError("STRIPE_SECRET_KEY missing or empty in config/.env")


def request(path: str, params: dict | None = None, method: str = "GET",
            env_path: str | None = None) -> dict:
    """Perform one allowlisted GET. Returns status/request-id/count/data.

    On 200: {"status", "request_id", "count", "data", "parse_error"}.
      count is len(data) for JSON arrays, None otherwise (so balance
      amounts are never logged). Malformed bodies set parse_error=True
      instead of raising.
    On HTTP error: {"status", "request_id", "error_message"} where
      error_message is Stripe's error.message only (for naming a
      missing permission on 403).
    On network failure: {"status": None, "request_id": None,
      "error_message": "network error: <Type>"}.
    """
    check_method(method)  # refuse non-GET before key load, before I/O
    key = load_secret(env_path)
    url = API_BASE + path
    if params:
        url += "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(
        url, method="GET", headers={"Authorization": "Bearer %s" % key}
    )
    try:
        with urllib.request.urlopen(req, timeout=DEFAULT_TIMEOUT) as resp:
            status = resp.status
            request_id = resp.headers.get("Request-Id")
            body = resp.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", "replace")
        out = {
            "status": exc.code,
            "request_id": exc.headers.get("Request-Id") if exc.headers else None,
            "error_message": None,
        }
        try:
            msg = json.loads(body).get("error", {}).get("message")
            if msg:
                out["error_message"] = msg
        except ValueError:
            pass
        del key
        return out
    except urllib.error.URLError as exc:
        out = {
            "status": None,
            "request_id": None,
            "error_message": "network error: %s" % type(exc.reason).__name__,
        }
        del key
        return out
    out = {"status": status, "request_id": request_id, "count": None,
           "data": None, "parse_error": False}
    try:
        data = json.loads(body)
    except ValueError:
        out["parse_error"] = True
        del key
        return out
    out["data"] = data
    if isinstance(data, list):
        out["count"] = len(data)
    elif (isinstance(data, dict) and data.get("object") == "list"
          and isinstance(data.get("data"), list)):
        # Stripe list envelope: {"object": "list", "data": [...]}
        out["count"] = len(data["data"])
    del key
    return out
