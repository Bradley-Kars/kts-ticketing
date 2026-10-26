"""G2 GET-only allowlist proof (Bradley amendment 2026-10-08).

The guard MOVED to core/stripe_client.py (Bradley G3 scope, same day:
one guard, not two), so these tests now exercise the guard at its home.
GET only, with tests that fail if any POST, PUT, PATCH, or DELETE is
ever accepted. No network I/O happens in this module; a broken guard
would surface as URLError instead of ValueError and fail the test
rather than reach Stripe.
"""

import os
import sys
import unittest

sys.path.insert(
    0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
)
from core import stripe_client as g2  # noqa: E402


class G2GetOnlyAllowlist(unittest.TestCase):
    def test_allowlist_is_exactly_get(self):
        self.assertEqual(set(g2.ALLOWED_METHODS), {"GET"})

    def test_get_allowed(self):
        self.assertEqual(g2.check_method("GET"), "GET")
        self.assertEqual(g2.check_method("get"), "GET")

    def test_post_refused(self):
        with self.assertRaises(ValueError):
            g2.check_method("POST")

    def test_put_refused(self):
        with self.assertRaises(ValueError):
            g2.check_method("PUT")

    def test_patch_refused(self):
        with self.assertRaises(ValueError):
            g2.check_method("PATCH")

    def test_delete_refused(self):
        with self.assertRaises(ValueError):
            g2.check_method("DELETE")

    def test_request_refuses_non_get_before_network(self):
        for m in ("POST", "PUT", "PATCH", "DELETE"):
            with self.assertRaises(ValueError):
                g2.request("https://127.0.0.1:9/v1/never", method=m,
                           env_path="/nonexistent/env")


if __name__ == "__main__":
    unittest.main()
