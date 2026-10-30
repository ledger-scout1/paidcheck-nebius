"""Demonstration labelling + rendering guarantees, tested:

1. Fixture evidence is labelled SIMULATED everywhere in the memo — the
   memo never claims fixture data came from PayPal or its sandbox API.
2. The default (real sandbox API) memo keeps its sandbox labelling —
   the "Sandbox" label is kept only for data actually read there.
3. The AI explanation section states its source: live model call,
   offline fixture text, or failed-call fallback notice.
4. The hosted demo renders the memo as formatted HTML (no raw markdown,
   no <pre> dump) and both pages are mobile-viewport pages.
5. Verdict invariance: labelling changes never move a verdict.
"""

import sys
import unittest
from datetime import datetime, timezone, timedelta
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import matcher
import claim_parser
import web_demo
from pipeline import verify_claim
from nemotron_client import NemotronClient
from tavily_context import TavilyContextClient

NOW = datetime(2026, 10, 24, 12, 0, tzinfo=timezone.utc)
CLAIM = "Paid $180 for the logo design, from John Miller."


def tx(amount, currency, payer, tid, days_ago=2):
    return {"amount": Decimal(amount), "currency": currency,
            "payer_name": payer, "payer_email": None,
            "transaction_id": tid, "event_code": "T0006",
            "time": NOW - timedelta(days=days_ago), "raw": {}}


TXS = [tx("180", "USD", "John Miller", "8AB12345CD678901E")]


class LiveShapedNemotron(NemotronClient):
    def __init__(self):
        super().__init__(api_key="test-key")

    @property
    def mode(self):
        return "live"

    def explain_verdict(self, verdict_status, evidence_summary):
        return {"explanation": "The amounts line up exactly.",
                "anomaly_flags": [], "mode": "live",
                "discarded_verdict_claim": False}


class FixtureLabellingTests(unittest.TestCase):
    def fixtures_memo(self, **kw):
        kw.setdefault("nemotron", NemotronClient(api_key=""))
        kw.setdefault("tavily", TavilyContextClient(api_key=""))
        return verify_claim(CLAIM, TXS, now=NOW, record=False,
                            evidence_source="fixtures", **kw)

    def test_fixture_memo_labels_simulated_evidence(self):
        md = self.fixtures_memo()["memo_markdown"]
        self.assertIn("SIMULATED", md)
        self.assertIn("## Simulated evidence (demonstration fixtures)", md)
        self.assertIn("simulated fixture data", md)

    def test_fixture_memo_never_claims_paypal_source(self):
        md = self.fixtures_memo()["memo_markdown"]
        self.assertNotIn("api-m.sandbox.paypal.com", md)
        self.assertNotIn("(PayPal SANDBOX evidence)", md)
        self.assertNotIn("## PayPal evidence", md)
        self.assertNotIn("against the PayPal sandbox", md)

    def test_sandbox_memo_keeps_sandbox_label(self):
        claim = claim_parser.parse_claim(CLAIM, now=NOW)
        v = matcher.evaluate(claim, TXS, now=NOW, record=False)
        md = matcher.memo_markdown(v, generated_at=NOW)
        self.assertIn("(PayPal SANDBOX evidence)", md)
        self.assertIn("## PayPal evidence", md)
        md_fx = matcher.memo_markdown(v, generated_at=NOW,
                                       evidence_source="fixtures")
        self.assertIn("SIMULATED", md_fx)
        self.assertNotIn("PayPal SANDBOX", md_fx)

    def test_offline_explanation_labelled_as_fixture_text(self):
        md = self.fixtures_memo()["memo_markdown"]
        self.assertIn("offline fixture text — no live model call", md)

    def test_live_explanation_labelled_as_live_call(self):
        res = verify_claim(CLAIM, TXS, nemotron=LiveShapedNemotron(),
                           tavily=TavilyContextClient(api_key=""),
                           now=NOW, record=False,
                           evidence_source="fixtures")
        self.assertIn("live Nemotron call", res["memo_markdown"])

    def test_labelling_does_not_move_verdict(self):
        res = self.fixtures_memo()
        self.assertEqual(res["verdict"].status, "MATCHED")


class DemoRenderingTests(unittest.TestCase):
    def test_memo_rendered_as_formatted_html(self):
        res = verify_claim(CLAIM, TXS, nemotron=NemotronClient(api_key=""),
                           tavily=TavilyContextClient(api_key=""),
                           now=NOW, record=False,
                           evidence_source="fixtures")
        page = web_demo.memo_to_html(res["memo_markdown"])
        self.assertIn("<table>", page)
        self.assertIn('class=tablewrap', page)
        self.assertIn("<h2>Simulated evidence", page)
        self.assertNotIn("## Simulated evidence", page)
        self.assertNotIn("| Field | Value |", page)
        self.assertNotIn("<pre>" + res["memo_markdown"][:40], page)

    def test_pages_are_mobile_viewport_pages(self):
        self.assertIn('name=viewport', web_demo.FORM)
        self.assertIn("width:100%", web_demo.CSS)
        self.assertNotIn("cols=80", web_demo.FORM)


if __name__ == "__main__":
    unittest.main()
