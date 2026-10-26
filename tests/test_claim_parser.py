"""Unit tests for the deterministic claim parser (no network, no LLM)."""

import sys
import unittest
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from claim_parser import parse_claim  # noqa: E402

NOW = datetime(2026, 10, 22, 12, 0, tzinfo=timezone.utc)


class AmountTests(unittest.TestCase):
    def test_symbol_prefix(self):
        c = parse_claim("I paid $180 for the logo", now=NOW)
        self.assertEqual(c.amount, Decimal("180"))
        self.assertEqual(c.currency, "USD")

    def test_symbol_euro_with_decimals(self):
        c = parse_claim("Sent €75.50 via PayPal", now=NOW)
        self.assertEqual(c.amount, Decimal("75.50"))
        self.assertEqual(c.currency, "EUR")

    def test_code_suffix(self):
        c = parse_claim("Payment of 250 USD sent", now=NOW)
        self.assertEqual(c.amount, Decimal("250"))
        self.assertEqual(c.currency, "USD")

    def test_currency_word(self):
        c = parse_claim("I sent 90 pounds yesterday", now=NOW)
        self.assertEqual(c.amount, Decimal("90"))
        self.assertEqual(c.currency, "GBP")

    def test_thousands_us_format(self):
        c = parse_claim("Paid $1,200.50 for the redesign", now=NOW)
        self.assertEqual(c.amount, Decimal("1200.50"))
        self.assertEqual(c.currency, "USD")

    def test_thousands_european_format(self):
        c = parse_claim("Paid 1.200,50 EUR for hosting", now=NOW)
        self.assertEqual(c.amount, Decimal("1200.50"))
        self.assertEqual(c.currency, "EUR")

    def test_no_amount(self):
        c = parse_claim("I paid for the logo, check PayPal", now=NOW)
        self.assertIsNone(c.amount)


class PayerTests(unittest.TestCase):
    def test_from_name(self):
        c = parse_claim("Paid $180 for the logo, from John Smith", now=NOW)
        self.assertEqual(c.payer_name, "John Smith")

    def test_initial_name(self):
        c = parse_claim("Paid $180 for the logo, transaction from J. Smith",
                        now=NOW)
        self.assertEqual(c.payer_name, "J. Smith")

    def test_email(self):
        c = parse_claim("Payment sent from john.smith@example.com, $40",
                        now=NOW)
        self.assertEqual(c.payer_email, "john.smith@example.com")


class DateTests(unittest.TestCase):
    def test_iso_date(self):
        c = parse_claim("Paid $50 on 2026-10-15", now=NOW)
        self.assertEqual(c.claimed_date.date().isoformat(), "2026-10-15")

    def test_month_name(self):
        c = parse_claim("Paid $50 on October 5, 2026", now=NOW)
        self.assertEqual(c.claimed_date.date().isoformat(), "2026-10-05")

    def test_day_month_name(self):
        c = parse_claim("Paid $50 on 5 October 2026", now=NOW)
        self.assertEqual(c.claimed_date.date().isoformat(), "2026-10-05")

    def test_yesterday(self):
        c = parse_claim("I paid $50 yesterday", now=NOW)
        self.assertEqual(c.claimed_date.date().isoformat(), "2026-10-21")

    def test_days_ago(self):
        c = parse_claim("Paid $50 three days ago".replace("three", "3"),
                        now=NOW)
        self.assertEqual(c.claimed_date.date().isoformat(), "2026-10-19")

    def test_no_date_gives_default_window(self):
        c = parse_claim("Paid $50 via PayPal", now=NOW)
        self.assertIsNone(c.claimed_date)
        start, end = c.window(now=NOW)
        self.assertEqual((end - start).days, 31)


class ItemAndRefTests(unittest.TestCase):
    def test_item(self):
        c = parse_claim("Paid $180 for the logo design, from John Smith",
                        now=NOW)
        self.assertEqual(c.item, "logo design")

    def test_transaction_ref(self):
        ref = "8AB12345CD678901E"
        c = parse_claim(f"Paid $25, transaction {ref}", now=NOW)
        self.assertEqual(c.transaction_ref, ref)

    def test_short_number_is_not_a_ref(self):
        c = parse_claim("Paid $25, order 12345", now=NOW)
        self.assertIsNone(c.transaction_ref)


if __name__ == "__main__":
    unittest.main()
