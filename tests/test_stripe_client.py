"""Mock tests for core.stripe_client (Bradley G3 scope 2026-10-08).

Covers: success, 401, 403, 429, 5xx, timeout, malformed body - all with
MOCKED urlopen, NO network. Plus the fail-if-write tests: any
POST/PUT/PATCH/DELETE must raise ValueError with urlopen never called.
Response data never leaves the returned dict; nothing is stored.
"""

import io
import os
import sys
import unittest
import urllib.error
from unittest import mock

sys.path.insert(
    0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
)
from core import stripe_client as sc  # noqa: E402

# Dummy key file: never a prefix the key-pattern grep recognizes, never
# the real key. Non-GET refusals must fire before this file is touched.
FAKE_ENV = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                        "_fake_env_g3.tmp")
FAKE_ENV_CONTENT = "STRIPE_SECRET_KEY=unit-test-not-a-real-key\n"


def _http_error(code, body, request_id="req_mock_1"):
    return urllib.error.HTTPError(
        "https://api.stripe.com/v1/mock", code, "err",
        {"Request-Id": request_id}, io.BytesIO(body),
    )


class MockStripeClient(unittest.TestCase):
    def setUp(self):
        with open(FAKE_ENV, "w", encoding="utf-8") as fh:
            fh.write(FAKE_ENV_CONTENT)
        self.addCleanup(lambda: os.path.exists(FAKE_ENV) and os.remove(FAKE_ENV))

    def _mock_urlopen(self, side_effect=None, status=200, body=b"[]",
                      request_id="req_mock_1"):
        patcher = mock.patch("urllib.request.urlopen")
        self.addCleanup(patcher.stop)
        m = patcher.start()
        if side_effect is not None:
            m.side_effect = side_effect
            return m
        resp = mock.MagicMock()
        resp.status = status
        resp.headers = {"Request-Id": request_id}
        resp.read.return_value = body
        m.return_value.__enter__.return_value = resp
        return m

    # --- success ---
    def test_success_list_counts_items(self):
        m = self._mock_urlopen(body=b'[{"id":1},{"id":2},{"id":3}]')
        out = sc.request("/v1/customers", params={"limit": "3"},
                         env_path=FAKE_ENV)
        self.assertEqual(out["status"], 200)
        self.assertEqual(out["count"], 3)
        self.assertEqual(out["request_id"], "req_mock_1")
        self.assertFalse(out["parse_error"])
        # request really was a GET with limit=3
        req = m.call_args[0][0]
        self.assertEqual(req.get_method(), "GET")
        self.assertIn("limit=3", req.full_url)
        # auth header present but never part of our output
        self.assertIn("Authorization", req.headers)

    def test_balance_dict_yields_no_count(self):
        self._mock_urlopen(body=b'{"livemode": true, "pending": 0}')
        out = sc.request("/v1/balance", env_path=FAKE_ENV)
        self.assertEqual(out["status"], 200)
        self.assertIsNone(out["count"])  # amounts can never be logged

    def test_list_envelope_counts_items(self):
        # Stripe returns lists as {"object": "list", "data": [...]}
        self._mock_urlopen(
            body=b'{"object": "list", "data": [{"id":1},{"id":2},{"id":3}], "has_more": false}')
        out = sc.request("/v1/customers", params={"limit": "3"},
                         env_path=FAKE_ENV)
        self.assertEqual(out["status"], 200)
        self.assertEqual(out["count"], 3)
        self.assertFalse(out["parse_error"])

    # --- error statuses ---
    def test_401(self):
        self._mock_urlopen(
            side_effect=_http_error(401, b'{"error": {"message": "Invalid API Key"}}'))
        out = sc.request("/v1/balance", env_path=FAKE_ENV)
        self.assertEqual(out["status"], 401)
        self.assertEqual(out["error_message"], "Invalid API Key")
        self.assertEqual(out["request_id"], "req_mock_1")

    def test_403_surfaces_permission_message(self):
        self._mock_urlopen(
            side_effect=_http_error(
                403,
                b'{"error": {"message": "You do not have access to this resource"}}'))
        out = sc.request("/v1/charges", params={"limit": "3"}, env_path=FAKE_ENV)
        self.assertEqual(out["status"], 403)
        self.assertIn("do not have access", out["error_message"])

    def test_429(self):
        self._mock_urlopen(
            side_effect=_http_error(429, b'{"error": {"message": "TooManyRequests"}}'))
        out = sc.request("/v1/customers", env_path=FAKE_ENV)
        self.assertEqual(out["status"], 429)

    def test_500(self):
        self._mock_urlopen(
            side_effect=_http_error(500, b'{"error": {"message": "boom"}}'))
        out = sc.request("/v1/invoices", env_path=FAKE_ENV)
        self.assertEqual(out["status"], 500)

    def test_timeout_is_not_raised(self):
        self._mock_urlopen(side_effect=urllib.error.URLError(TimeoutError()))
        out = sc.request("/v1/balance", env_path=FAKE_ENV)
        self.assertIsNone(out["status"])
        self.assertEqual(out["error_message"], "network error: TimeoutError")

    def test_malformed_body_sets_parse_error(self):
        self._mock_urlopen(status=200, body=b"<html>not json</html>")
        out = sc.request("/v1/customers", env_path=FAKE_ENV)
        self.assertEqual(out["status"], 200)
        self.assertTrue(out["parse_error"])
        self.assertIsNone(out["count"])
        self.assertIsNone(out["data"])

    # --- fail-if-write tests (the amendment: the module must not be
    #     able to issue any non-GET; urlopen must never be called) ---
    def test_post_put_patch_delete_all_refused_without_network(self):
        for method in ("POST", "PUT", "PATCH", "DELETE"):
            m = mock.patch("urllib.request.urlopen")
            self.addCleanup(m.stop)
            mocked = m.start()
            with self.assertRaises(ValueError):
                sc.request("/v1/customers", method=method, env_path=FAKE_ENV)
            mocked.assert_not_called()

    def test_refused_method_never_reads_key(self):
        # guard fires before load_secret: a nonexistent env file is fine
        for method in ("POST", "PUT", "PATCH", "DELETE"):
            with self.assertRaises(ValueError):
                sc.request("/v1/customers", method=method,
                           env_path="/nonexistent/does-not-exist")

    def test_allowlist_is_exactly_get(self):
        self.assertEqual(set(sc.ALLOWED_METHODS), {"GET"})


if __name__ == "__main__":
    unittest.main()
