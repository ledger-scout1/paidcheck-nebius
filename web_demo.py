"""Hosted demo server for the Nebius edition (stdlib only).

Serves a small form + the full pipeline against seeded sandbox-shaped
fixture transactions (the same fixtures as demo_run.py). If PayPal sandbox
credentials are configured, a claim carrying a transaction reference is
additionally checked against the real sandbox — the fixture path is the
default so the public demo can never touch a real account.

Credit protection (the Builder credits must survive judging, 1–15 Dec):
- live Nemotron/Tavily calls are capped per day (DEMO_DAILY_LIVE_CAP,
  default 300); beyond the cap the demo transparently continues in
  offline-fixture mode and says so on the page;
- Tavily uses basic depth only (1 credit); Nemotron calls are short.
"""

import html
import os
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer

from demo_run import SCENARIOS
from pipeline import verify_claim

DAILY_CAP = int(os.environ.get("DEMO_DAILY_LIVE_CAP", "300"))
_state = {"day": None, "live_calls": 0}

FORM = """<!doctype html><html><head><meta charset=utf-8>
<title>PaidCheck — Nebius edition demo</title></head><body>
<h1>PaidCheck — payment-claim verification (Nebius edition)</h1>
<p>Verdicts come only from payment evidence. Nemotron (Nebius Token
Factory) explains them; Tavily adds public context. Neither can change a
verdict — try to make them.</p>
<form method=post action=/verify>
<textarea name=claim rows=3 cols=80>{sample}</textarea><br>
<button type=submit>Verify claim</button></form>
<p>Seeded fixture transactions: $180 from John Miller (logo design);
$108 from John Miller. Demo resets daily; no real accounts involved.</p>
</body></html>"""


def _live_budget_left():
    today = datetime.now(timezone.utc).date()
    if _state["day"] != today:
        _state.update(day=today, live_calls=0)
    return _state["live_calls"] < DAILY_CAP


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        sample = SCENARIOS[1][1]
        self._send(FORM.format(sample=html.escape(sample)))

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length).decode("utf-8", "replace")
        from urllib.parse import parse_qs
        claim = parse_qs(body).get("claim", [""])[0].strip()
        txs = [t for _, _, t in SCENARIOS for t in t]
        # de-duplicate fixture transactions by id
        txs = list({t["transaction_id"]: t for t in txs}.values())
        live = _live_budget_left()
        if live:
            _state["live_calls"] += 1
        res = verify_claim(claim, txs, record=False) if live else \
            verify_claim(claim, txs, use_nemotron=False, use_tavily=False,
                         record=False)
        v = res["verdict"]
        page = ("<!doctype html><html><head><meta charset=utf-8>"
                "<title>Verdict</title></head><body>"
                f"<h1>Verdict: {html.escape(v.status)}</h1>"
                f"<pre>{html.escape(res['memo_markdown'])}</pre>"
                "<p><a href='/'>Back</a></p></body></html>")
        self._send(page)

    def _send(self, page):
        data = page.encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *args):
        pass


if __name__ == "__main__":
    port = int(os.environ.get("PORT", "7860"))
    HTTPServer(("0.0.0.0", port), Handler).serve_forever()
