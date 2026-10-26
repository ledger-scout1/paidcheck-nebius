"""PaidCheck demo app — local web UI and CLI.

Why the standard-library ``http.server`` instead of Flask: the demo is a
single intake form plus a memo page. Using only the stdlib (plus
``requests``, already required by the PayPal client) means judges run it
with zero framework install friction, and there is nothing else to audit.
Flask would add a dependency without adding anything this demo needs.

Usage:
    # web UI (http://127.0.0.1:8000)
    PAYPAL_CLIENT_ID=... PAYPAL_CLIENT_SECRET=... python3 app.py

    # one-shot CLI check (prints the Markdown memo)
    PAYPAL_CLIENT_ID=... PAYPAL_CLIENT_SECRET=... \
        python3 app.py --cli "Paid $180 for the logo, from John Smith"
"""

from __future__ import annotations

import argparse
import html
import os
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs

import claim_parser
import matcher
from paypal_client import PayPalError, client_from_env

FORM_PAGE = """<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8">
<title>PaidCheck — verify an "I paid you" claim</title>
<style>body{font-family:system-ui,sans-serif;max-width:760px;margin:2rem auto;
padding:0 1rem}textarea{width:100%;box-sizing:border-box}
button{padding:.5rem 1.2rem;font-size:1rem}.note{color:#555}</style></head>
<body>
<h1>PaidCheck</h1>
<p>Paste the "I paid you" claim you received. PaidCheck checks it against
your real PayPal <b>sandbox</b> transactions and returns an evidence-backed
verdict memo.</p>
<form method="post" action="/check">
<textarea name="claim" rows="6" placeholder="e.g. Hi, I paid $180 for the logo design yesterday via PayPal. Transaction from John Smith."></textarea>
<p><button type="submit">Verify claim</button></p>
</form>
<p class="note">Runs against api-m.sandbox.paypal.com (test money). Verdicts:
MATCHED / PARTIAL / NOT FOUND / DUPLICATE / UNCONFIRMED (the API call
failed, so no check could be completed).</p>
</body></html>"""


def run_check(claim_text: str) -> matcher.Verdict:
    """End-to-end: parse -> PayPal search -> verdict (the live path).

    A PayPal API failure during the search is NOT an exception to the
    caller and NOT a NOT FOUND: it returns an UNCONFIRMED verdict whose
    memo records the API error, so the user can tell "we could not check"
    apart from "we checked and found nothing".
    """
    claim = claim_parser.parse_claim(claim_text)
    client = client_from_env()
    start, end = claim.window()
    db_path = os.environ.get("PAIDCHECK_DB", matcher.DEFAULT_DB_PATH)
    try:
        transactions = client.search_transactions(start, end)
    except PayPalError as exc:
        return matcher.unconfirmed_verdict(claim, exc, db_path=db_path)
    if claim.transaction_ref:
        # Make sure a referenced transaction outside the window is seen too.
        known = {tx.get("transaction_id") for tx in transactions}
        if claim.transaction_ref not in known:
            try:
                transactions.append(
                    client.get_transaction(claim.transaction_ref))
            except PayPalError:
                # Secondary route: if the reference is a capture id, the
                # Payments v2 capture lookup needs no reporting scope.
                try:
                    transactions.append(
                        client.get_capture(claim.transaction_ref))
                except PayPalError:
                    pass  # the window result stands; verdict will say so
    return matcher.evaluate(claim, transactions, db_path=db_path)


class Handler(BaseHTTPRequestHandler):
    server_version = "PaidCheck/0.1"

    def log_message(self, *args):  # keep the demo console readable
        pass

    def _send(self, code: int, body: str, content_type: str):
        data = body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        if self.path in ("/", "/index.html"):
            self._send(200, FORM_PAGE, "text/html; charset=utf-8")
        elif self.path == "/health":
            import json
            self._send(200, json.dumps({"status": "ok",
                                        "environment": os.environ.get(
                                            "PAYPAL_ENV", "sandbox")}),
                       "application/json")
        else:
            self._send(404, "Not found", "text/plain")

    def do_POST(self):
        if self.path != "/check":
            self._send(404, "Not found", "text/plain")
            return
        length = int(self.headers.get("Content-Length", 0))
        form = parse_qs(self.rfile.read(length).decode("utf-8"))
        claim_text = (form.get("claim") or [""])[0].strip()
        if not claim_text:
            self._send(400, "Paste a claim first.", "text/plain")
            return
        try:
            verdict = run_check(claim_text)
        except PayPalError as exc:
            self._send(502, "PayPal API error: " + html.escape(str(exc)),
                       "text/html; charset=utf-8")
            return
        except ValueError as exc:  # missing credentials, etc.
            self._send(500, "Configuration error: " + html.escape(str(exc))
                       + "<br>Set PAYPAL_CLIENT_ID / PAYPAL_CLIENT_SECRET "
                         "(sandbox) — see README.",
                       "text/html; charset=utf-8")
            return
        self._send(200, matcher.memo_html(verdict),
                   "text/html; charset=utf-8")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="PaidCheck demo")
    parser.add_argument("--cli", metavar="CLAIM",
                        help="check one claim text and print the memo")
    args = parser.parse_args(argv)
    if args.cli:
        try:
            verdict = run_check(args.cli)
        except ValueError as exc:  # missing credentials = config problem
            print(f"PaidCheck configuration error: {exc}", file=sys.stderr)
            return 1
        # An UNCONFIRMED verdict is a completed check with an honest
        # outcome (the memo says exactly what the API answered), not a
        # crash: print it and exit 0.
        print(matcher.memo_markdown(verdict), end="")
        return 0
    port = int(os.environ.get("PAIDCHECK_PORT", "8000"))
    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    print(f"PaidCheck UI on http://127.0.0.1:{port} "
          f"(environment: {os.environ.get('PAYPAL_ENV', 'sandbox')})")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
