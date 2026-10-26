"""Seed the PayPal sandbox with demo evidence for PaidCheck.

What this does, honestly, against the live sandbox API:

1. Verifies OAuth works.
2. Creates demo Orders (Orders v2) — these are real sandbox objects, but an
   order only becomes a *payment* after a payer approves it in a checkout
   flow, which a server-side script cannot do. We fetch each order back,
   log its status (CREATED), and try a capture to record PayPal's actual
   answer (expected: ORDER_NOT_APPROVED). Orders therefore document the
   checkout path; they are NOT what the matcher verifies against.
3. Sends a sandbox Payout to the personal test account — a payout is a
   real ledger movement inside the sandbox, so afterwards the business
   account has genuine Transaction Search entries for the matcher demo.
4. Runs a Transaction Search over the trailing 30 days and logs what it
   returns (this is the exact read path PaidCheck verifies claims with).
   Note: Transaction Search indexing can lag a few minutes behind new
   activity; an empty result right after seeding is a lag, not a failure.

Results are appended to SEEDING.md and written to seed_results.json
(git-ignored). Nothing here fakes a PayPal response.

Credentials: env vars PAYPAL_CLIENT_ID / PAYPAL_CLIENT_SECRET, or
``--from-state <path-to-paypal_dev.json>`` to read the sandbox fields of a
MOSE-style state file (keys: sandbox_client_id, sandbox_secret,
sandbox_business_account, sandbox_personal_account).
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timedelta, timezone

from paypal_client import PayPalClient, PayPalError

DEMO_ORDERS = [
    ("180.00", "USD", "Logo design — PaidCheck demo order"),
    ("75.00", "EUR", "Consulting session — PaidCheck demo order"),
    ("45.50", "USD", "Icon set — PaidCheck demo order"),
]
DEMO_PAYOUT_AMOUNT = "12.34"
DEMO_PAYOUT_CURRENCY = "USD"


def load_config(args) -> dict:
    cfg = {
        "client_id": os.environ.get("PAYPAL_CLIENT_ID", ""),
        "client_secret": os.environ.get("PAYPAL_CLIENT_SECRET", ""),
        "business_email": os.environ.get("PAYPAL_SANDBOX_BUSINESS", ""),
        "personal_email": os.environ.get("PAYPAL_SANDBOX_PERSONAL", ""),
    }
    if args.from_state:
        with open(args.from_state) as fh:
            state = json.load(fh)
        cfg["client_id"] = state.get("sandbox_client_id", cfg["client_id"])
        cfg["client_secret"] = state.get("sandbox_secret",
                                         cfg["client_secret"])
        cfg["business_email"] = state.get("sandbox_business_account",
                                          cfg["business_email"])
        cfg["personal_email"] = state.get("sandbox_personal_account",
                                          cfg["personal_email"])
    return cfg


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Seed the PayPal sandbox")
    parser.add_argument("--from-state",
                        help="JSON state file with sandbox_* fields")
    parser.add_argument("--skip-payout", action="store_true")
    parser.add_argument("--skip-orders", action="store_true")
    args = parser.parse_args(argv)

    cfg = load_config(args)
    if not cfg["client_id"] or not cfg["client_secret"]:
        print("Missing sandbox credentials (env or --from-state).",
              file=sys.stderr)
        return 2

    client = PayPalClient(cfg["client_id"], cfg["client_secret"],
                          environment=os.environ.get("PAYPAL_ENV",
                                                     "sandbox"))
    log: list = []
    results: dict = {"orders": [], "payout": None, "search": None}

    def note(section: str, message: str):
        line = f"[{section}] {message}"
        print(line, flush=True)
        log.append(line)

    # 1) OAuth ------------------------------------------------------------
    try:
        client.get_access_token()
        note("oauth", "OK — client-credentials token issued by sandbox")
    except PayPalError as exc:
        note("oauth", f"FAILED — {exc}")
        _write_outputs(log, results)
        return 1

    # 2) Orders ------------------------------------------------------------
    if not args.skip_orders:
        for amount, currency, desc in DEMO_ORDERS:
            try:
                order = client.create_order(amount, currency,
                                            description=desc)
                oid = order.get("id")
                fetched = client.get_order(oid)
                note("orders", f"created+fetched {oid}: "
                               f"status={fetched.get('status')}")
                entry = {"id": oid, "amount": amount, "currency": currency,
                         "status": fetched.get("status")}
                try:
                    client.capture_order(oid)
                    entry["capture"] = "succeeded (unexpected pre-approval)"
                except PayPalError as exc:
                    issue = None
                    if isinstance(exc.details, list) and exc.details:
                        issue = exc.details[0].get("issue")
                    entry["capture"] = (f"refused as expected: "
                                        f"{exc.name or exc.status}"
                                        f" / {issue}" if issue else
                                        f"refused as expected: "
                                        f"{exc.name or exc.status}")
                    note("orders", f"capture {oid}: {entry['capture']} "
                                   "(checkout approval is a browser step)")
                results["orders"].append(entry)
            except PayPalError as exc:
                note("orders", f"FAILED for {amount} {currency} — {exc}")

    # 3) Payout ------------------------------------------------------------
    if not args.skip_payout:
        recipient = cfg["personal_email"]
        if not recipient:
            note("payout", "SKIPPED — no personal test account email known")
        else:
            try:
                payout = client.send_payout(
                    recipient, DEMO_PAYOUT_AMOUNT, DEMO_PAYOUT_CURRENCY,
                    note="PaidCheck seed payout (sandbox test money)")
                header = payout.get("batch_header", {})
                note("payout", f"sent {DEMO_PAYOUT_AMOUNT} "
                               f"{DEMO_PAYOUT_CURRENCY} to {recipient}: "
                               f"batch_id={header.get('payout_batch_id')} "
                               f"status={header.get('batch_status')}")
                results["payout"] = {
                    "batch_id": header.get("payout_batch_id"),
                    "status": header.get("batch_status"),
                    "recipient": recipient,
                    "amount": DEMO_PAYOUT_AMOUNT,
                    "currency": DEMO_PAYOUT_CURRENCY}
            except PayPalError as exc:
                note("payout", f"FAILED — {exc}")
                results["payout"] = {"error": str(exc)}

    # 4) Transaction Search (the matcher's read path) ----------------------
    end = datetime.now(timezone.utc)
    start = end - timedelta(days=30)
    try:
        txs = client.search_transactions(start, end)
        note("search", f"{len(txs)} transaction(s) in trailing 30 days")
        for tx in txs[:10]:
            note("search", f"  - {tx.get('transaction_id')} "
                           f"{tx.get('amount')} {tx.get('currency')} "
                           f"status={tx.get('status')} "
                           f"payer={tx.get('payer_email')}")
        results["search"] = {
            "count": len(txs),
            "ids": [tx.get("transaction_id") for tx in txs[:20]]}
    except PayPalError as exc:
        note("search", f"FAILED — {exc}")
        results["search"] = {"error": str(exc)}

    _write_outputs(log, results)
    return 0


def _write_outputs(log: list, results: dict) -> None:
    stamp = datetime.now(timezone.utc).isoformat()
    with open("seed_results.json", "w") as fh:
        json.dump({"generated_at": stamp, "results": results}, fh, indent=2)
    with open("SEEDING.md", "a") as fh:
        fh.write(f"\n## Seed run {stamp}\n\n")
        for line in log:
            fh.write(f"- {line}\n")
    print("Wrote seed_results.json and appended SEEDING.md")


if __name__ == "__main__":
    raise SystemExit(main())
