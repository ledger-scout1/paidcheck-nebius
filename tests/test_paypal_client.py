"""Unit tests for the PayPal client with a fake HTTP layer (no network)."""

import sys
import unittest
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from paypal_client import (REPORTING_SEARCH_SCOPE, PayPalClient,  # noqa: E402
                           PayPalError, normalize_transaction)


class FakeResponse:
    def __init__(self, status=200, body=None):
        self.status_code = status
        self._body = body or {}
        self.content = b"{}" if body is not None else b""

    def json(self):
        return self._body


class FakeHTTP:
    """Records requests and replays queued responses per URL fragment."""

    def __init__(self):
        self.calls = []
        self.routes = []

    def when(self, fragment, response):
        self.routes.append((fragment, response))
        return self

    def request(self, method, url, **kwargs):
        self.calls.append((method, url, kwargs))
        for fragment, response in self.routes:
            if fragment in url:
                if callable(response):
                    return response(method, url, kwargs)
                return response
        return FakeResponse(404, {"name": "RESOURCE_NOT_FOUND",
                                  "message": "no route in fake"})


def token_ok():
    return FakeResponse(200, {"access_token": "tok-123",
                              "expires_in": 3600})


class TokenTests(unittest.TestCase):
    def test_token_fetched_and_cached(self):
        http = FakeHTTP().when("/v1/oauth2/token", token_ok())
        client = PayPalClient("cid", "secret", http=http)
        self.assertEqual(client.get_access_token(), "tok-123")
        self.assertEqual(client.get_access_token(), "tok-123")
        token_calls = [c for c in http.calls if "oauth2" in c[1]]
        self.assertEqual(len(token_calls), 1)

    def test_bad_credentials_raise_paypal_error(self):
        http = FakeHTTP().when(
            "/v1/oauth2/token",
            FakeResponse(401, {"error": "invalid_client",
                               "error_description": "Client auth failed"}))
        client = PayPalClient("cid", "wrong", http=http)
        with self.assertRaises(PayPalError) as ctx:
            client.get_access_token()
        self.assertEqual(ctx.exception.status, 401)


class OrderTests(unittest.TestCase):
    def _client(self, http):
        http.when("/v1/oauth2/token", token_ok())
        return PayPalClient("cid", "secret", http=http)

    def test_create_and_get_order(self):
        order = {"id": "ORDER123", "status": "CREATED"}
        http = FakeHTTP()
        http.when("/v2/checkout/orders/ORDER123",
                  FakeResponse(200, order))
        http.when("/v2/checkout/orders", FakeResponse(201, order))
        client = self._client(http)
        created = client.create_order("180.00", "USD",
                                      description="Logo design")
        self.assertEqual(created["id"], "ORDER123")
        fetched = client.get_order("ORDER123")
        self.assertEqual(fetched["status"], "CREATED")
        methods = [c[0] for c in http.calls]
        self.assertEqual(methods.count("POST"), 2)  # token + create
        self.assertEqual(methods.count("GET"), 1)


class SearchTests(unittest.TestCase):
    RAW_TX = {
        "transaction_info": {
            "transaction_id": "TXN999",
            "transaction_event_code": "T0006",
            "transaction_status": "S",
            "transaction_amount": {"currency_code": "USD", "value": "180.00"},
            "fee_amount": {"currency_code": "USD", "value": "-5.52"},
            "transaction_initiation_date": "2026-10-21T09:15:00.000Z",
            "transaction_subject": "Logo design",
        },
        "payer_info": {
            "email_address": "john@example.com",
            "payer_info": {"first_name": "John", "last_name": "Smith"},
        },
    }

    def test_search_normalizes_results(self):
        http = FakeHTTP()
        http.when("/v1/oauth2/token", token_ok())
        http.when("/v1/reporting/transactions",
                  FakeResponse(200, {"transaction_details": [self.RAW_TX],
                                     "total_pages": 1, "page": 1}))
        client = PayPalClient("cid", "secret", http=http)
        txs = client.search_transactions(
            datetime(2026, 10, 1, tzinfo=timezone.utc),
            datetime(2026, 10, 22, tzinfo=timezone.utc))
        self.assertEqual(len(txs), 1)
        tx = txs[0]
        self.assertEqual(tx["transaction_id"], "TXN999")
        self.assertEqual(tx["amount"], Decimal("180.00"))
        self.assertEqual(tx["currency"], "USD")
        self.assertEqual(tx["payer_name"], "John Smith")
        self.assertEqual(tx["payer_email"], "john@example.com")
        self.assertEqual(tx["time"].isoformat(),
                         "2026-10-21T09:15:00+00:00")

    def test_normalize_handles_missing_fields(self):
        tx = normalize_transaction({"transaction_info": {}})
        self.assertIsNone(tx["transaction_id"])
        self.assertIsNone(tx["amount"])

    def test_get_capture_normalizes(self):
        capture = {
            "id": "CAP123",
            "status": "COMPLETED",
            "amount": {"value": "180.00", "currency_code": "USD"},
            "create_time": "2026-10-21T09:15:00Z",
            "seller_receivable_breakdown": {
                "paypal_fee": {"value": "5.52", "currency_code": "USD"}},
        }
        http = FakeHTTP()
        http.when("/v1/oauth2/token", token_ok())
        http.when("/v2/payments/captures/CAP123",
                  FakeResponse(200, capture))
        client = PayPalClient("cid", "secret", http=http)
        tx = client.get_capture("CAP123")
        self.assertEqual(tx["transaction_id"], "CAP123")
        self.assertEqual(tx["amount"], Decimal("180.00"))
        self.assertEqual(tx["fee"], Decimal("5.52"))
        self.assertIsNone(tx["payer_email"])  # captures expose no payer

    def test_search_error_is_paypal_error(self):
        http = FakeHTTP()
        http.when("/v1/oauth2/token", token_ok())
        http.when("/v1/reporting/transactions",
                  FakeResponse(400, {"name": "INVALID_REQUEST",
                                     "message": "bad window",
                                     "debug_id": "dbg1"}))
        client = PayPalClient("cid", "secret", http=http)
        with self.assertRaises(PayPalError) as ctx:
            client.search_transactions(
                datetime(2026, 10, 1, tzinfo=timezone.utc),
                datetime(2026, 10, 22, tzinfo=timezone.utc))
        self.assertEqual(ctx.exception.name, "INVALID_REQUEST")
        self.assertEqual(ctx.exception.debug_id, "dbg1")


def scoped_token_endpoint(method, url, kwargs):
    """Token endpoint that answers by requested scope, like real PayPal.

    A plain client-credentials request gets the "general" token; only a
    request carrying the reporting scope gets the search token. This is
    the verified sandbox behaviour the client is built around.
    """
    data = kwargs.get("data") or {}
    if data.get("scope") == REPORTING_SEARCH_SCOPE:
        return FakeResponse(200, {"access_token": "tok-search",
                                  "expires_in": 3600})
    return FakeResponse(200, {"access_token": "tok-general",
                              "expires_in": 3600})


class ScopedTokenTests(unittest.TestCase):
    def _client(self, http):
        http.when("/v1/oauth2/token", scoped_token_endpoint)
        http.when("/v1/reporting/transactions",
                  FakeResponse(200, {"transaction_details": [],
                                     "total_pages": 1, "page": 1}))
        return PayPalClient("cid", "secret", http=http)

    def test_search_requests_scoped_token_and_uses_it(self):
        http = FakeHTTP()
        client = self._client(http)
        txs = client.search_transactions(
            datetime(2026, 10, 1, tzinfo=timezone.utc),
            datetime(2026, 10, 22, tzinfo=timezone.utc))
        self.assertEqual(txs, [])
        token_calls = [c for c in http.calls if "oauth2" in c[1]]
        scoped = [c for c in token_calls
                  if (c[2].get("data") or {}).get("scope")
                  == REPORTING_SEARCH_SCOPE]
        self.assertTrue(scoped,
                        "search must request a token with the reporting "
                        "scope parameter")
        search_calls = [c for c in http.calls if "reporting" in c[1]]
        self.assertTrue(search_calls)
        for _, _, kwargs in search_calls:
            self.assertEqual(kwargs["headers"]["Authorization"],
                             "Bearer tok-search")

    def test_v1_empty_result_is_a_successful_empty_search(self):
        http = FakeHTTP()
        client = self._client(http)
        txs = client.search_transactions(
            datetime(2026, 10, 15, tzinfo=timezone.utc),
            datetime(2026, 10, 22, tzinfo=timezone.utc))
        self.assertEqual(txs, [])
        urls = [c[1] for c in http.calls if "reporting" in c[1]]
        self.assertTrue(urls)
        self.assertTrue(all("/v1/reporting/transactions" in u
                            for u in urls))

    def test_general_calls_do_not_use_the_scoped_token(self):
        http = FakeHTTP()
        http.when("/v2/checkout/orders",
                  FakeResponse(201, {"id": "ORDER1", "status": "CREATED"}))
        client = self._client(http)
        client.create_order("10.00", "USD")
        order_calls = [c for c in http.calls if "checkout" in c[1]]
        self.assertEqual(order_calls[0][2]["headers"]["Authorization"],
                         "Bearer tok-general")
        token_calls = [c for c in http.calls if "oauth2" in c[1]]
        self.assertTrue(all((c[2].get("data") or {}).get("scope") is None
                            for c in token_calls))


if __name__ == "__main__":
    unittest.main()
