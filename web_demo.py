"""Hosted demo server for the Nebius edition (stdlib only).

Serves a small form + the full pipeline against seeded fixture
transactions (the same fixtures as demo_run.py). The hosted demo checks
claims ONLY against these fixtures — simulated data, seeded locally. It
never reads PayPal (or any payment API) records, and no page may present
the fixture path as live or sandbox verification.

Demonstration fixtures: the hosted demo re-anchors the seeded fixture
transactions to (today - 2 days), the offset they were written with, so
the demo keeps working after the original fixture dates age. Amounts,
payers and ids are untouched. Every page labels these as fixtures, NOT
live PayPal records.

Credit protection (the Builder credits must survive judging, 9–23 Dec):
- live Nemotron/Tavily calls are capped per day (DEMO_DAILY_LIVE_CAP,
  default 300); beyond the cap the demo transparently continues in
  offline-fixture mode and says so on the page;
- Tavily uses basic depth only (1 credit); Nemotron calls are short.

Observability: every /verify request writes one structured log line to
stdout (Render logs): verdict, evidence source, Nemotron mode,
explanation source and Tavily status/result count — so live calls are
distinguishable from offline/fallback behaviour in the logs. No claim
text, no keys, no secrets are ever logged.
"""

import html
import os
import re
from datetime import datetime, timezone, timedelta
from http.server import BaseHTTPRequestHandler, HTTPServer

from demo_run import SCENARIOS
from pipeline import verify_claim

DAILY_CAP = int(os.environ.get("DEMO_DAILY_LIVE_CAP", "300"))
_state = {"day": None, "live_calls": 0}

CSS = """<style>
body{font-family:system-ui,sans-serif;max-width:760px;margin:1.2rem auto;
padding:0 1rem;color:#1f1f1f;line-height:1.45}
h1{font-size:1.35rem} h2{font-size:1.12rem;margin-top:1.4rem}
textarea{width:100%;box-sizing:border-box;font:inherit;padding:.5rem}
button{font:inherit;padding:.5rem 1.1rem;margin-top:.5rem}
blockquote{background:#f5f5f5;padding:.6rem 1rem;border-left:4px solid #999;
margin:.6rem 0;white-space:pre-wrap;overflow-wrap:break-word}
.tablewrap{overflow-x:auto}
table{border-collapse:collapse;max-width:100%}
th,td{border:1px solid #ccc;padding:.35rem .6rem;text-align:left;
overflow-wrap:break-word}
pre{background:#f6f6f6;padding:.8rem;overflow-x:auto;white-space:pre-wrap;
overflow-wrap:break-word;font-size:.85rem}
code{background:#eee;padding:.05rem .3rem;border-radius:3px}
pre code{background:none;padding:0}
.note{background:#fff8e1;border:1px solid #e0c36a;padding:.6rem 1rem}
.meta{color:#555;font-size:.9rem}
a{overflow-wrap:break-word}
</style>"""

FORM = """<!doctype html><html><head><meta charset=utf-8>
<meta name=viewport content="width=device-width, initial-scale=1">
<title>PaidCheck — Nebius edition demo</title>{css}</head><body>
<h1>PaidCheck — payment-claim verification (Nebius edition)</h1>
<p>Verdicts come only from payment evidence. Nemotron (Nebius Token
Factory) explains them; Tavily adds public context. Neither can change a
verdict — try to make them.</p>
<form method=post action=/verify>
<textarea name=claim rows=3>{sample}</textarea><br>
<button type=submit>Verify claim</button></form>
<p class=note>Demonstration fixtures — simulated data, NOT live PayPal
records and NOT read from any PayPal API (dates relative to today so the
demo keeps working): $180 from John Miller (logo design); $108 from John
Miller. Demo resets daily; no real accounts involved.</p>
</body></html>"""


def _inline(text):
    """Inline markdown subset, applied to already-escaped text."""
    links = []

    def stash(match):
        links.append((match.group(1), match.group(2)))
        return f"\x00{len(links) - 1}\x00"

    text = re.sub(r"\[([^\]]+)\]\(([^)\s]+)\)", stash, text)
    text = re.sub(r"`([^`]+)`", r"<code>\1</code>", text)
    text = re.sub(r"\*\*([^*]+)\*\*", r"<b>\1</b>", text)
    text = re.sub(r"(?m)^_([^_]+)_$", r"<i>\1</i>", text)
    text = re.sub(r"\*([^*]+)\*", r"<i>\1</i>", text)

    def unstash(match):
        label, url = links[int(match.group(1))]
        return f'<a href="{url}">{label}</a>'

    return re.sub("\x00(\\d+)\x00", unstash, text)


def memo_to_html(md):
    """Render the memo's markdown subset to formatted HTML: headings,
    blockquote, lists, tables (scrollable on narrow screens), code
    fences and paragraphs — never raw markdown in a <pre>."""
    out, para, items, table, fence = [], [], [], [], []

    def flush_para():
        if para:
            out.append("<p>" + _inline(" ".join(para)) + "</p>")
            para.clear()

    def flush_items():
        if items:
            out.append("<ul>" + "".join(
                f"<li>{_inline(i)}</li>" for i in items) + "</ul>")
            items.clear()

    def flush_table():
        if table:
            rows = [[html.escape(c.strip())
                     for c in row.strip().strip("|").split("|")]
                    for row in table]
            rows = [r for r in rows
                    if not all(set(c) <= set(":- ") for c in r)]
            if rows:
                head = "".join(f"<th>{_inline(c)}</th>" for c in rows[0])
                body = "".join(
                    "<tr>" + "".join(f"<td>{_inline(c)}</td>" for c in r)
                    + "</tr>" for r in rows[1:])
                out.append(f'<div class=tablewrap><table><tr>{head}</tr>'
                           f"{body}</table></div>")
            table.clear()

    def flush_all():
        flush_para()
        flush_items()
        flush_table()

    lines = md.split("\n")
    i = 0
    while i < len(lines):
        line = lines[i]
        stripped = line.strip()
        if fence or stripped.startswith("```"):
            if stripped.startswith("```"):
                if fence:
                    out.append("<pre><code>" + html.escape(
                        "\n".join(fence).strip("\n")) + "</code></pre>")
                    fence = []
                else:
                    flush_all()
                    fence = [""]
                i += 1
                continue
            fence.append(line)
            i += 1
            continue
        if not stripped:
            flush_all()
        elif stripped.startswith("### "):
            flush_all()
            out.append(f"<h3>{_inline(html.escape(stripped[4:]))}</h3>")
        elif stripped.startswith("## "):
            flush_all()
            out.append(f"<h2>{_inline(html.escape(stripped[3:]))}</h2>")
        elif stripped.startswith("# "):
            flush_all()
            out.append(f"<h1>{_inline(html.escape(stripped[2:]))}</h1>")
        elif stripped.startswith("> "):
            flush_all()
            out.append("<blockquote>" + _inline(
                html.escape(stripped[2:])) + "</blockquote>")
        elif stripped.startswith("|"):
            flush_para()
            flush_items()
            table.append(stripped)
        elif stripped.startswith("- "):
            flush_para()
            flush_table()
            items.append(html.escape(stripped[2:]))
        else:
            flush_items()
            flush_table()
            para.append(html.escape(stripped))
        i += 1
    if fence:
        out.append("<pre><code>" + html.escape(
            "\n".join(fence).strip("\n")) + "</code></pre>")
    flush_all()
    return "\n".join(out)


def _live_budget_left():
    today = datetime.now(timezone.utc).date()
    if _state["day"] != today:
        _state.update(day=today, live_calls=0)
    return _state["live_calls"] < DAILY_CAP


def _explanation_source(res):
    nemo = res.get("nemotron", {})
    explanation = nemo.get("explanation") or ""
    if explanation.startswith("Explanation layer unavailable"):
        return "live_call_failed_fallback_notice"
    if nemo.get("mode") == "live":
        return "live"
    if nemo.get("mode") == "offline_fixture":
        return "offline_fixture"
    return nemo.get("mode") or "disabled"


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        sample = SCENARIOS[1][1]
        self._send(FORM.format(css=CSS, sample=html.escape(sample)))

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length).decode("utf-8", "replace")
        from urllib.parse import parse_qs
        claim = parse_qs(body).get("claim", [""])[0].strip()
        txs = [t for _, _, t in SCENARIOS for t in t]
        # de-duplicate fixture transactions by id
        txs = list({t["transaction_id"]: t for t in txs}.values())
        # Demonstration fixtures only: re-anchor to (today - 2 days),
        # the offset the fixtures were written with. Amounts, payers
        # and ids are untouched; real API records are never altered.
        now = datetime.now(timezone.utc)
        txs = [dict(t, time=now - timedelta(days=2)) for t in txs]
        live = _live_budget_left()
        if live:
            _state["live_calls"] += 1
        res = verify_claim(claim, txs, record=False,
                           evidence_source="fixtures") if live else \
            verify_claim(claim, txs, use_nemotron=False, use_tavily=False,
                         record=False, evidence_source="fixtures")
        v = res["verdict"]
        tav = res.get("tavily", {})
        print("verify ts={} verdict={} evidence=fixtures "
              "nemotron_mode={} explanation_source={} tavily_status={} "
              "tavily_results={} live_budget_used={}/{}".format(
                  now.isoformat(), v.status,
                  res.get("nemotron", {}).get("mode"),
                  _explanation_source(res), tav.get("status"),
                  len(tav.get("results", [])), _state["live_calls"],
                  DAILY_CAP), flush=True)
        meta = ("evidence: simulated fixtures (not PayPal records) · "
                "Nemotron: {} · explanation: {} · Tavily: {} ({} results)"
                .format(res.get("nemotron", {}).get("mode"),
                        _explanation_source(res), tav.get("status"),
                        len(tav.get("results", []))))
        page = ("<!doctype html><html><head><meta charset=utf-8>"
                "<meta name=viewport content=\"width=device-width, "
                "initial-scale=1\">"
                "<title>Verdict</title>" + CSS + "</head><body>"
                "<p class=note><b>Demonstration mode:</b> the transactions "
                "below are seeded fixtures with dates relative to today — "
                "simulated data, not live PayPal records and not read "
                "from any PayPal API. The verdict logic is identical; "
                "only the evidence is demonstrative.</p>"
                f"<h1>Verdict: {html.escape(v.status)}</h1>"
                f"<p class=meta>{html.escape(meta)}</p>"
                + memo_to_html(res["memo_markdown"])
                + "<p><a href='/'>Back</a></p></body></html>")
        self._send(page)

    def _send(self, page):
        data = page.encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


if __name__ == "__main__":
    port = int(os.environ.get("PORT", "7860"))
    HTTPServer(("0.0.0.0", port), Handler).serve_forever()
