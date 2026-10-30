"""Demo scenarios for the Nebius edition — runnable with zero keys.

Uses PayPal-sandbox-shaped fixture transactions (the same normalized shape
``paypal_client`` returns) so the full pipeline — parser, matcher verdict,
Nemotron layer, Tavily layer, memo — runs end to end offline. With
NEBIUS_API_KEY / TAVILY_API_KEY set, the two AI/web layers go live; the
verdict path does not change.

Run:  python3 demo_run.py
"""

from datetime import datetime, timezone, timedelta
from decimal import Decimal

from pipeline import verify_claim

NOW = datetime(2026, 10, 24, 12, 0, tzinfo=timezone.utc)


def tx(amount, currency, payer, tid, days_ago=2):
    return {"amount": Decimal(amount), "currency": currency,
            "payer_name": payer, "payer_email": None,
            "transaction_id": tid, "event_code": "T0006",
            "time": NOW - timedelta(days=days_ago), "raw": {}}


SCENARIOS = [
    ("MATCHED — a true claim (seeded fixture, sandbox-shaped)",
     "Hi, I paid $180 for the logo design yesterday. Transaction from "
     "John Miller, thanks!",
     [tx("180", "USD", "John Miller", "8AB12345CD678901E")]),
    ("PARTIAL — digit transposition ($180 claimed, $108 actually paid)",
     "Paid $180 for the logo design, from John Miller.",
     [tx("108", "USD", "John Miller", "9BC23456DE789012F")]),
    ("NOT FOUND — confident claim, no transaction exists",
     "I already paid $450 for the website work last week, from "
     "Bright Studio LLC.",
     [tx("180", "USD", "John Miller", "8AB12345CD678901E")]),
]


def main():
    for title, text, txs in SCENARIOS:
        res = verify_claim(text, txs, now=NOW, record=False,
                           evidence_source="fixtures")
        v = res["verdict"]
        print("=" * 72)
        print(title)
        print("VERDICT:", v.status, "| flags:", ", ".join(v.flags) or "none")
        print("Nemotron mode:", res["nemotron"]["mode"],
              "| Tavily status:", res["tavily"]["status"])
        print("-" * 72)
        print(res["memo_markdown"][:1200])
        print()


if __name__ == "__main__":
    main()
