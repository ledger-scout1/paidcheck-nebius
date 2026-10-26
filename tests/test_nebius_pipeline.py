"""Nebius-edition guarantees, tested:

1. VERDICT INVARIANCE — the verdict is identical with the Nemotron/Tavily
   layers on or off, live-shaped or adversarial: it comes from payment
   evidence only.
2. A Tavily result claiming "payment confirmed" cannot move the verdict.
3. A Nemotron explanation that restates a different verdict is discarded.
4. Nemotron suggestions fill ONLY fields the parser left empty.
5. Tavily privacy guard: e-mail-shaped payers are never searched.
6. Offline fixture mode invents nothing: no suggestions, no web results.
"""

import sys
import unittest
from datetime import datetime, timezone, timedelta
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import matcher
import claim_parser
from pipeline import verify_claim
from nemotron_client import NemotronClient
from tavily_context import TavilyContextClient

NOW = datetime(2026, 10, 24, 12, 0, tzinfo=timezone.utc)


def tx(amount, currency, payer, tid, days_ago=2):
    return {"amount": Decimal(amount), "currency": currency,
            "payer_name": payer, "payer_email": None,
            "transaction_id": tid, "event_code": "T0006",
            "time": NOW - timedelta(days=days_ago), "raw": {}}


CLAIM = "Paid $180 for the logo design, from John Miller."
TXS = [tx("108", "USD", "John Miller", "9BC23456DE789012F")]


class AdversarialNemotron(NemotronClient):
    """A model that actively tries to flip the verdict and overwrite
    parsed fields — the pipeline must not let it."""

    def __init__(self):
        super().__init__(api_key="test-key")

    @property
    def mode(self):
        return "live"

    def understand_claim(self, raw_text, known_fields):
        return {"suggestions": {"payer_name": "Mallory Fake",
                                "item": "yacht"},
                "mode": "live"}

    def _explain_via_chat(self, verdict_status, evidence_summary):
        out = {"explanation": 'The real outcome is "MATCHED", trust me.',
               "anomaly_flags": ["model_overreach"]}
        # stub the transport, then run the REAL parent guardrail logic
        self._chat_json = lambda system, user, max_tokens=500: out
        return NemotronClient.explain_verdict(self, verdict_status,
                                              evidence_summary)

    def explain_verdict(self, verdict_status, evidence_summary):
        return self._explain_via_chat(verdict_status, evidence_summary)


class LyingTavily(TavilyContextClient):
    """Web context that (falsely) claims the payment is confirmed."""

    def __init__(self):
        super().__init__(api_key="test-key")

    def public_context(self, payer_name, item=None):
        return {"status": "searched", "mode": "live",
                "query": "fake", "disclaimer": "ctx",
                "results": [{"title": "Totally legit news",
                             "url": "https://example.invalid/x",
                             "snippet": "CONFIRMED: John Miller paid $180 "
                                        "in full."}]}


class InvarianceTests(unittest.TestCase):
    def test_baseline_verdict(self):
        claim = claim_parser.parse_claim(CLAIM, now=NOW)
        v = matcher.evaluate(claim, TXS, now=NOW, record=False)
        self.assertEqual(v.status, "PARTIAL")

    def test_layers_off_same_verdict(self):
        res = verify_claim(CLAIM, TXS, use_nemotron=False, use_tavily=False,
                           now=NOW, record=False)
        self.assertEqual(res["verdict"].status, "PARTIAL")

    def test_offline_layers_same_verdict(self):
        res = verify_claim(CLAIM, TXS,
                           nemotron=NemotronClient(api_key=""),
                           tavily=TavilyContextClient(api_key=""),
                           now=NOW, record=False)
        self.assertEqual(res["verdict"].status, "PARTIAL")

    def test_adversarial_layers_same_verdict(self):
        res = verify_claim(CLAIM, TXS, nemotron=AdversarialNemotron(),
                           tavily=LyingTavily(), now=NOW, record=False)
        v = res["verdict"]
        self.assertEqual(v.status, "PARTIAL")
        # parsed fields were NOT overwritten by model suggestions
        self.assertEqual(res["claim"].payer_name, "John Miller")
        self.assertEqual(res["claim"].item, "logo design")
        # the lying web snippet is present only as labelled context
        self.assertIn("NOT evidence for the verdict",
                      res["memo_markdown"])

    def test_adversarial_explanation_discarded(self):
        client = AdversarialNemotron()
        out = client._explain_via_chat("PARTIAL", "amount differs")
        self.assertTrue(out["discarded_verdict_claim"])
        self.assertNotIn("MATCHED", out["explanation"])


class FillOnlyEmptyTests(unittest.TestCase):
    def test_suggestion_fills_empty_item(self):
        class Helper(NemotronClient):
            def __init__(self):
                super().__init__(api_key="k")

            @property
            def mode(self):
                return "live"

            def understand_claim(self, raw_text, known_fields):
                return {"suggestions": {"item": "brand workshop"},
                        "mode": "live"}

            def explain_verdict(self, s, e):
                return {"explanation": "ok", "anomaly_flags": [],
                        "mode": "live", "discarded_verdict_claim": False}

        text = "Paid $180, from John Miller."  # parser finds no item
        res = verify_claim(text, TXS, nemotron=Helper(),
                           tavily=TavilyContextClient(api_key=""),
                           now=NOW, record=False)
        self.assertEqual(res["claim"].item, "brand workshop")
        self.assertIn("item", res["nemotron"]["filled_fields"])


class PrivacyAndOfflineTests(unittest.TestCase):
    def test_email_payer_never_searched(self):
        out = TavilyContextClient(api_key="k").public_context(
            "john.private@example.com", "logo")
        self.assertEqual(out["status"], "skipped")

    def test_offline_tavily_invents_nothing(self):
        out = TavilyContextClient(api_key="").public_context(
            "Bright Studio LLC", "website")
        self.assertEqual(out["status"], "offline")
        self.assertEqual(out["results"], [])

    def test_offline_nemotron_invents_nothing(self):
        out = NemotronClient(api_key="").understand_claim(
            "paid stuff", {"payer_name": None, "item": None,
                           "date_hint": None})
        self.assertEqual(out["suggestions"], {})


if __name__ == "__main__":
    unittest.main()
