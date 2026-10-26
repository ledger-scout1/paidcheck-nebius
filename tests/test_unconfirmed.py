"""Tests for the UNCONFIRMED verdict: API failure != NOT FOUND.

A PayPal API failure during verification must produce UNCONFIRMED (the
check could not be completed, error recorded in the memo), while a
successful search with no match must stay NOT FOUND. The two outcomes
are opposites operationally and must never blur.
"""

import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import app  # noqa: E402
import matcher  # noqa: E402
from claim_parser import parse_claim  # noqa: E402
from paypal_client import PayPalClient, PayPalError  # noqa: E402
from test_paypal_client import FakeHTTP, FakeResponse, token_ok  # noqa: E402


class _StubError(Exception):
    """Duck-typed stand-in for PayPalError (status/name/debug_id)."""

    def __init__(self):
        super().__init__("Caller is not authorized")
        self.status = 403
        self.name = "NOT_AUTHORIZED"
        self.debug_id = "dbg-403"


class UnconfirmedVerdictTests(unittest.TestCase):
    def test_unconfirmed_records_the_api_error(self):
        claim = parse_claim("Paid $180 for the logo, from John Smith")
        v = matcher.unconfirmed_verdict(claim, _StubError())
        self.assertEqual(v.status, matcher.UNCONFIRMED)
        self.assertNotEqual(v.status, matcher.NOT_FOUND)
        self.assertIn("api_error", v.flags)
        self.assertEqual(v.error["status"], 403)
        self.assertEqual(v.error["name"], "NOT_AUTHORIZED")
        self.assertEqual(v.error["debug_id"], "dbg-403")
        self.assertIsNone(v.transaction)

    def test_unconfirmed_memo_shows_error_and_is_not_not_found(self):
        claim = parse_claim("Paid $180 for the logo, from John Smith")
        v = matcher.unconfirmed_verdict(claim, _StubError())
        md = matcher.memo_markdown(v)
        self.assertIn("UNCONFIRMED", md)
        self.assertIn("NOT_AUTHORIZED", md)
        self.assertIn("did not complete", md)
        self.assertNotIn("No matching transaction was found", md)
        rendered = matcher.memo_html(v)
        self.assertIn("UNCONFIRMED", rendered)


def _client_with_search(route_response):
    http = FakeHTTP()
    http.when("/v1/oauth2/token", token_ok())
    http.when("/v1/reporting/transactions", route_response)
    return PayPalClient("cid", "secret", http=http)


class RunCheckFailureTests(unittest.TestCase):
    """End-to-end through app.run_check with a fake transport."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = os.path.join(self.tmp.name, "claims.db")
        self.env = mock.patch.dict(os.environ, {"PAIDCHECK_DB": self.db})

    def tearDown(self):
        self.tmp.cleanup()

    def _run(self, client, text="Paid $180 for the logo, from John Smith"):
        with self.env, mock.patch.object(app, "client_from_env",
                                         return_value=client):
            return app.run_check(text)

    def test_api_403_during_search_gives_unconfirmed(self):
        client = _client_with_search(FakeResponse(
            403, {"name": "NOT_AUTHORIZED",
                  "message": "Caller is not authorized",
                  "debug_id": "dbg-x"}))
        v = self._run(client)
        self.assertEqual(v.status, matcher.UNCONFIRMED)
        self.assertEqual(v.error["name"], "NOT_AUTHORIZED")

    def test_api_500_during_search_gives_unconfirmed(self):
        client = _client_with_search(FakeResponse(
            500, {"name": "INTERNAL_ERROR", "message": "boom"}))
        v = self._run(client)
        self.assertEqual(v.status, matcher.UNCONFIRMED)

    def test_successful_empty_search_gives_not_found(self):
        client = _client_with_search(FakeResponse(
            200, {"transaction_details": [], "total_pages": 1, "page": 1}))
        v = self._run(client)
        self.assertEqual(v.status, matcher.NOT_FOUND)
        self.assertIsNone(v.error)

    def test_paypal_error_object_shape(self):
        # The conversion path relies on PayPalError carrying these fields.
        err = PayPalError("nope", status=500, name="INTERNAL_ERROR",
                          debug_id="d1")
        claim = parse_claim("Paid $180 for the logo, from John Smith")
        v = matcher.unconfirmed_verdict(claim, err)
        self.assertEqual(v.error["status"], 500)
        self.assertIn("INTERNAL_ERROR", v.explanation)


if __name__ == "__main__":
    unittest.main()
