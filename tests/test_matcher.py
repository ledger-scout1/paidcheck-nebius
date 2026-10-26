"""Unit tests for the verdict engine: all four verdicts + anomaly flags."""

import os
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import matcher  # noqa: E402
from claim_parser import parse_claim  # noqa: E402

NOW = datetime(2026, 10, 22, 12, 0, tzinfo=timezone.utc)


def tx(tid="TXN00000000000001", amount="180", currency="USD",
       payer_name="John Smith", payer_email="john@example.com", days_ago=1):
    return {
        "transaction_id": tid,
        "event_code": "T0006",
        "status": "S",
        "amount": Decimal(amount),
        "currency": currency,
        "fee": Decimal("5.52"),
        "time": NOW - timedelta(days=days_ago),
        "payer_email": payer_email,
        "payer_name": payer_name,
        "subject": None,
        "invoice_id": None,
        "raw": {},
    }


class VerdictTests(unittest.TestCase):
    def test_matched(self):
        claim = parse_claim("Paid $180 for the logo yesterday, "
                            "from John Smith", now=NOW)
        v = matcher.evaluate(claim, [tx()], now=NOW)
        self.assertEqual(v.status, matcher.MATCHED)
        self.assertEqual(v.transaction["transaction_id"],
                         "TXN00000000000001")

    def test_matched_by_initial_name(self):
        claim = parse_claim("Paid $180 for the logo, from J. Smith", now=NOW)
        v = matcher.evaluate(claim, [tx()], now=NOW)
        self.assertEqual(v.status, matcher.MATCHED)

    def test_partial_amount_mismatch(self):
        claim = parse_claim("Paid $175 for the logo, from John Smith",
                            now=NOW)
        v = matcher.evaluate(claim, [tx()], now=NOW)
        self.assertEqual(v.status, matcher.PARTIAL)
        self.assertIn("amount_mismatch", v.flags)

    def test_digit_transposition_flag(self):
        claim = parse_claim("Paid $108 for the logo, from John Smith",
                            now=NOW)
        v = matcher.evaluate(claim, [tx(amount="180")], now=NOW)
        self.assertEqual(v.status, matcher.PARTIAL)
        self.assertIn("possible_digit_transposition", v.flags)

    def test_partial_currency_mismatch(self):
        claim = parse_claim("Paid €180 for the logo, from John Smith",
                            now=NOW)
        v = matcher.evaluate(claim, [tx(currency="USD")], now=NOW)
        self.assertEqual(v.status, matcher.PARTIAL)
        self.assertIn("currency_mismatch", v.flags)

    def test_partial_payer_mismatch(self):
        claim = parse_claim("Paid $180 for the logo, from Jane Doe", now=NOW)
        v = matcher.evaluate(claim, [tx()], now=NOW)
        self.assertEqual(v.status, matcher.PARTIAL)
        self.assertIn("payer_mismatch", v.flags)

    def test_not_found(self):
        claim = parse_claim("Paid $999 for a yacht, from John Smith", now=NOW)
        v = matcher.evaluate(claim, [tx()], now=NOW)
        self.assertEqual(v.status, matcher.NOT_FOUND)

    def test_not_found_empty_ledger(self):
        claim = parse_claim("Paid $180 for the logo, from John Smith",
                            now=NOW)
        v = matcher.evaluate(claim, [], now=NOW)
        self.assertEqual(v.status, matcher.NOT_FOUND)

    def test_outside_window_ignored(self):
        claim = parse_claim("Paid $180 for the logo on 2026-10-20, "
                            "from John Smith", now=NOW)
        v = matcher.evaluate(claim, [tx(days_ago=20)], now=NOW)
        self.assertEqual(v.status, matcher.NOT_FOUND)

    def test_match_by_transaction_ref_outside_window(self):
        ref = "8AB12345CD678901E"
        claim = parse_claim(f"Paid $180 for the logo on 2026-10-20, "
                            f"transaction {ref}", now=NOW)
        v = matcher.evaluate(claim, [tx(tid=ref, days_ago=20)], now=NOW)
        self.assertEqual(v.status, matcher.MATCHED)

    def test_stale_window_flag(self):
        old = NOW - timedelta(days=200)
        claim = parse_claim("Paid $180 for the logo on "
                            + old.date().isoformat() + ", from John Smith",
                            now=NOW)
        v = matcher.evaluate(claim, [], now=NOW)
        self.assertIn("stale_window", v.flags)

    def test_future_date_flag(self):
        future = NOW + timedelta(days=10)
        claim = parse_claim("Paid $180 for the logo on "
                            + future.date().isoformat() + ", from John Smith",
                            now=NOW)
        v = matcher.evaluate(claim, [], now=NOW)
        self.assertIn("future_date", v.flags)


class DuplicateTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = os.path.join(self.tmp.name, "claims.db")

    def tearDown(self):
        self.tmp.cleanup()

    def test_duplicate_claim_on_same_transaction(self):
        claim1 = parse_claim("Paid $180 for the logo, from John Smith",
                             now=NOW)
        v1 = matcher.evaluate(claim1, [tx()], db_path=self.db, now=NOW)
        self.assertEqual(v1.status, matcher.MATCHED)
        # A *different* claim text pointing at the same payment.
        claim2 = parse_claim("I sent you $180 by PayPal, John Smith here, "
                             "for the logo work", now=NOW)
        v2 = matcher.evaluate(claim2, [tx()], db_path=self.db, now=NOW)
        self.assertEqual(v2.status, matcher.DUPLICATE)
        self.assertIn("duplicate_claim", v2.flags)

    def test_same_claim_resubmitted_is_not_duplicate(self):
        text = "Paid $180 for the logo, from John Smith"
        claim1 = parse_claim(text, now=NOW)
        matcher.evaluate(claim1, [tx()], db_path=self.db, now=NOW)
        claim2 = parse_claim(text, now=NOW)
        v2 = matcher.evaluate(claim2, [tx()], db_path=self.db, now=NOW)
        self.assertEqual(v2.status, matcher.MATCHED)
        self.assertIn("repeat_submission", v2.flags)


class MemoTests(unittest.TestCase):
    def test_memo_contains_verdict_and_evidence(self):
        claim = parse_claim("Paid $180 for the logo, from John Smith",
                            now=NOW)
        v = matcher.evaluate(claim, [tx()], now=NOW)
        md = matcher.memo_markdown(v, generated_at=NOW)
        self.assertIn("MATCHED", md)
        self.assertIn("TXN00000000000001", md)
        rendered = matcher.memo_html(v, generated_at=NOW)
        self.assertIn("MATCHED", rendered)


if __name__ == "__main__":
    unittest.main()
