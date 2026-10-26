"""PaidCheck verdict engine + evidence memo rendering.

Given a parsed :class:`claim_parser.Claim` and the normalized PayPal
transactions found in the claim's window, decide one of:

* ``MATCHED``    — a transaction agrees on amount + currency (+ payer when
                   the claim names one).
* ``PARTIAL``    — a close transaction exists but a field disagrees
                   (amount, currency or payer). The differing fields and
                   anomaly flags say exactly which.
* ``NOT FOUND``  — nothing in the window resembles the claim.
* ``DUPLICATE``  — the transaction matches, but a *different* claim was
                   already recorded against the same transaction
                   (checked against the local claims ledger).
* ``UNCONFIRMED`` — the PayPal API call itself failed (auth error, HTTP
                   4xx/5xx, network/timeout), so no search could be
                   completed and the claim is *neither verified nor
                   disproven*. This is deliberately distinct from
                   ``NOT FOUND``, which means the search succeeded and
                   found nothing. Built by :func:`unconfirmed_verdict`.

Anomaly flags produced: ``currency_mismatch``, ``amount_mismatch``,
``possible_digit_transposition``, ``payer_mismatch``, ``duplicate_claim``,
``repeat_submission``, ``stale_window``, ``future_date``,
``missing_amount_in_claim``, ``missing_currency_in_claim``.

The claims ledger is a small local SQLite database (stdlib only). It is the
only state PaidCheck keeps, and it never leaves the seller's machine.
"""

from __future__ import annotations

import html
import json
import os
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
from typing import Optional

MATCHED = "MATCHED"
PARTIAL = "PARTIAL"
NOT_FOUND = "NOT FOUND"
DUPLICATE = "DUPLICATE"
UNCONFIRMED = "UNCONFIRMED"

DEFAULT_DB_PATH = os.environ.get("PAIDCHECK_DB", "data/claims.db")


# ---------------------------------------------------------------------------
# claims ledger (SQLite)
# ---------------------------------------------------------------------------

def _connect(db_path: str) -> sqlite3.Connection:
    directory = os.path.dirname(db_path)
    if directory:
        os.makedirs(directory, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.execute(
        """CREATE TABLE IF NOT EXISTS claims (
               id INTEGER PRIMARY KEY AUTOINCREMENT,
               claim_hash TEXT NOT NULL,
               transaction_id TEXT,
               verdict TEXT NOT NULL,
               claim_text TEXT,
               created_at TEXT NOT NULL
           )""")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_claims_tx "
                 "ON claims (transaction_id)")
    return conn


def record_claim(db_path: str, claim, verdict: str,
                 transaction_id: Optional[str]) -> None:
    conn = _connect(db_path)
    try:
        conn.execute(
            "INSERT INTO claims (claim_hash, transaction_id, verdict, "
            "claim_text, created_at) VALUES (?, ?, ?, ?, ?)",
            (claim.claim_hash, transaction_id, verdict,
             claim.raw_text[:2000],
             datetime.now(timezone.utc).isoformat()))
        conn.commit()
    finally:
        conn.close()


def prior_claims_for_transaction(db_path: str,
                                 transaction_id: str) -> list:
    """All ledger rows previously recorded against a transaction."""
    if not transaction_id or not os.path.exists(db_path):
        return []
    conn = _connect(db_path)
    try:
        rows = conn.execute(
            "SELECT claim_hash, verdict, created_at FROM claims "
            "WHERE transaction_id = ? ORDER BY id", (transaction_id,)
        ).fetchall()
    finally:
        conn.close()
    return [{"claim_hash": r[0], "verdict": r[1], "created_at": r[2]}
            for r in rows]


# ---------------------------------------------------------------------------
# comparison helpers
# ---------------------------------------------------------------------------

def _norm_text(value: Optional[str]) -> str:
    if not value:
        return ""
    return " ".join(
        "".join(ch.lower() if ch.isalnum() else " " for ch in value)
        .split())


def names_match(claim_name: Optional[str], tx_name: Optional[str]) -> bool:
    """Loose payer-name comparison.

    Handles 'J. Smith' vs 'John Smith' (same surname, same first initial),
    case/punctuation differences and token reordering.
    """
    a, b = _norm_text(claim_name), _norm_text(tx_name)
    if not a or not b:
        return False
    if a == b or a in b or b in a:
        return True
    ta, tb = a.split(), b.split()
    if ta[-1] == tb[-1]:  # same surname
        if ta[0] == tb[0] or ta[0][0] == tb[0][0]:
            return True
    return bool(set(ta) & set(tb)) and ta[-1] == tb[-1]


def _is_digit_transposition(a: Decimal, b: Decimal) -> bool:
    """True when two amounts differ only by swapped digits (180 vs 108)."""
    da = str(a).replace(".", "").replace("-", "").lstrip("0") or "0"
    db = str(b).replace(".", "").replace("-", "").lstrip("0") or "0"
    if da == db or sorted(da) != sorted(db):
        return False
    # require same length so 180 vs 18 does not count as a transposition
    return len(da) == len(db)


def _payer_differs(claim, tx: dict) -> bool:
    """True only when the claim identifies a payer AND it disagrees."""
    if claim.payer_email and tx.get("payer_email"):
        return claim.payer_email.lower() != tx["payer_email"].lower()
    if claim.payer_email or claim.payer_name:
        if tx.get("payer_email") and claim.payer_email:
            return claim.payer_email.lower() != tx["payer_email"].lower()
        if claim.payer_name and tx.get("payer_name"):
            return not names_match(claim.payer_name, tx["payer_name"])
    return False


def _is_plausible_partial(claim, tx: dict, diff: dict) -> bool:
    """Is a near candidate plausibly *the same payment, misstated*?

    A PARTIAL verdict means "we found your payment but a field disagrees".
    That needs an anchor: at most two differing fields, and when the amount
    differs it must still be close (within 2x either way) or a digit
    transposition — a $999 claim against a $180 payment from the same
    person is not a misstated $999 payment, it is NOT FOUND.
    """
    if not diff or len(diff) > 2:
        return False
    if "amount" in diff:
        claimed, actual = claim.amount, tx.get("amount")
        if claimed is None or actual is None or actual == 0:
            return False
        if _is_digit_transposition(claimed, actual):
            return True
        ratio = claimed / actual
        return Decimal("0.5") <= ratio <= Decimal("2")
    return True


# ---------------------------------------------------------------------------
# verdict
# ---------------------------------------------------------------------------

@dataclass
class Verdict:
    status: str
    claim: object
    transaction: Optional[dict] = None      # best (near-)match, if any
    flags: list = field(default_factory=list)
    differences: dict = field(default_factory=dict)
    explanation: str = ""
    candidates_considered: int = 0
    error: Optional[dict] = None            # API failure detail (UNCONFIRMED)


def unconfirmed_verdict(claim, error, *, db_path: Optional[str] = None,
                        record: bool = True) -> Verdict:
    """Build an UNCONFIRMED verdict for a verification the API refused.

    ``error`` is duck-typed: anything with ``status`` / ``name`` /
    ``debug_id`` attributes (e.g. ``paypal_client.PayPalError``) — the
    matcher stays decoupled from the client module. The memo records the
    failure verbatim so a human can see *why* no verdict on the claim
    itself was possible, instead of mistaking it for NOT FOUND.
    """
    detail = {
        "status": getattr(error, "status", None),
        "name": getattr(error, "name", None),
        "message": str(error),
        "debug_id": getattr(error, "debug_id", None),
    }
    verdict = Verdict(status=UNCONFIRMED, claim=claim,
                      flags=["api_error"], error=detail)
    verdict.explanation = _explain(verdict)
    if db_path and record:
        record_claim(db_path, claim, UNCONFIRMED, None)
    return verdict


def evaluate(claim, transactions: list, *, db_path: Optional[str] = None,
             now: Optional[datetime] = None,
             record: bool = True) -> Verdict:
    """Decide the verdict for a claim against normalized transactions."""
    now = now or datetime.now(timezone.utc)
    flags: list = []
    differences: dict = {}

    if claim.amount is None:
        flags.append("missing_amount_in_claim")
    if claim.currency is None:
        flags.append("missing_currency_in_claim")
    if claim.claimed_date is not None:
        age_days = (now - claim.claimed_date).days
        if age_days > 90:
            flags.append("stale_window")
        if claim.claimed_date > now:
            flags.append("future_date")

    win_start, win_end = claim.window(now=now)
    in_window = [tx for tx in transactions
                 if tx.get("time") is None
                 or (win_start <= tx["time"] <= win_end)]

    # A claim quoting a transaction id points straight at its candidate.
    by_ref = None
    if claim.transaction_ref:
        by_ref = next((tx for tx in transactions
                       if tx.get("transaction_id") == claim.transaction_ref),
                      None)

    def amount_eq(tx) -> bool:
        return (claim.amount is not None and tx.get("amount") is not None
                and claim.amount == tx["amount"])

    def currency_eq(tx) -> bool:
        return (claim.currency is not None and tx.get("currency") is not None
                and claim.currency.upper() == tx["currency"].upper())

    pool = [by_ref] if by_ref is not None else in_window

    exact = None
    near = None        # best partial candidate
    near_diff: dict = {}
    for tx in pool:
        if tx is None:
            continue
        diff = {}
        if not amount_eq(tx):
            diff["amount"] = {"claimed": str(claim.amount),
                              "actual": str(tx.get("amount"))}
        if not currency_eq(tx):
            diff["currency"] = {"claimed": claim.currency,
                                "actual": tx.get("currency")}
        if _payer_differs(claim, tx):
            diff["payer"] = {
                "claimed": claim.payer_email or claim.payer_name,
                "actual": tx.get("payer_email") or tx.get("payer_name")}
        if not diff:
            exact = tx
            break
        if near is None or len(diff) < len(near_diff):
            near, near_diff = tx, diff

    status = NOT_FOUND
    chosen = None
    if exact is not None:
        status, chosen = MATCHED, exact
    elif near is not None and _is_plausible_partial(claim, near, near_diff):
        status, chosen, differences = PARTIAL, near, near_diff
        if "currency" in near_diff:
            flags.append("currency_mismatch")
        if "amount" in near_diff:
            flags.append("amount_mismatch")
            if (claim.amount is not None and near.get("amount") is not None
                    and _is_digit_transposition(claim.amount,
                                                near["amount"])):
                flags.append("possible_digit_transposition")
        if "payer" in near_diff:
            flags.append("payer_mismatch")
    elif near is not None:
        # A candidate exists but disagrees on everything that matters:
        # report it as evidence, still NOT FOUND for the claim as stated.
        chosen, differences = near, near_diff

    # DUPLICATE check against the ledger (only meaningful with a match).
    if db_path and chosen is not None and status in (MATCHED, PARTIAL):
        priors = prior_claims_for_transaction(db_path,
                                              chosen["transaction_id"])
        others = [p for p in priors if p["claim_hash"] != claim.claim_hash]
        same = [p for p in priors if p["claim_hash"] == claim.claim_hash]
        if others:
            status = DUPLICATE
            flags.append("duplicate_claim")
        elif same:
            flags.append("repeat_submission")

    verdict = Verdict(status=status, claim=claim, transaction=chosen,
                      flags=flags, differences=differences,
                      candidates_considered=len(in_window))
    verdict.explanation = _explain(verdict)

    if db_path and record:
        record_claim(db_path, claim, status,
                     chosen["transaction_id"] if chosen else None)
    return verdict


def _fmt_money(amount, currency) -> str:
    if amount is None:
        return "(no amount)"
    return f"{amount} {currency or ''}".strip()


def _explain(verdict: Verdict) -> str:
    claim, tx = verdict.claim, verdict.transaction
    claimed = _fmt_money(claim.amount, claim.currency)
    if verdict.status == UNCONFIRMED:
        err = verdict.error or {}
        cause = err.get("name") or err.get("message") or "unknown error"
        status = f" (HTTP {err['status']})" if err.get("status") else ""
        return (f"The PayPal API call failed{status}: {cause}. The search "
                "could not be completed, so this claim is UNCONFIRMED — "
                "neither verified nor disproven. This is not a NOT FOUND "
                "result; fix the API problem and run the check again.")
    if verdict.status == MATCHED and tx:
        return (f"A PayPal transaction of {_fmt_money(tx['amount'], tx['currency'])} "
                f"(id {tx['transaction_id']}) matches the claimed {claimed}"
                + (f" from {tx.get('payer_name') or tx.get('payer_email')}"
                   if (tx.get("payer_name") or tx.get("payer_email")) else "")
                + ". The claim checks out against PayPal's records.")
    if verdict.status == DUPLICATE and tx:
        return (f"Transaction {tx['transaction_id']} matches this claim, but "
                "a different claim was already verified against the same "
                "transaction. One payment cannot settle two claims — ask "
                "the payer for the second payment's own transaction id.")
    if verdict.status == PARTIAL and tx:
        parts = []
        d = verdict.differences
        if "amount" in d:
            parts.append(f"amount differs (claimed {d['amount']['claimed']}, "
                         f"actual {d['amount']['actual']})")
        if "currency" in d:
            parts.append(f"currency differs (claimed {d['currency']['claimed']}, "
                         f"actual {d['currency']['actual']})")
        if "payer" in d:
            parts.append(f"payer differs (claimed {d['payer']['claimed']}, "
                         f"actual {d['payer']['actual']})")
        return ("A similar PayPal transaction exists "
                f"({_fmt_money(tx['amount'], tx['currency'])}, id "
                f"{tx['transaction_id']}) but it does not fully match: "
                + "; ".join(parts) + ".")
    return (f"No PayPal transaction matching {claimed} was found in the "
            "searched window. The claim is NOT verified — do not treat a "
            "screenshot or message as proof of payment.")


# ---------------------------------------------------------------------------
# memo rendering (HTML + Markdown) — the shareable evidence artifact
# ---------------------------------------------------------------------------

def _tx_rows(tx: Optional[dict]) -> list:
    if not tx:
        return []
    rows = [
        ("Transaction ID", tx.get("transaction_id")),
        ("Amount", _fmt_money(tx.get("amount"), tx.get("currency"))),
        ("Status", tx.get("status")),
        ("Event code", tx.get("event_code")),
        ("Payer", tx.get("payer_name") or tx.get("payer_email")),
        ("Payer email", tx.get("payer_email")),
        ("Time (UTC)", tx["time"].isoformat() if tx.get("time") else None),
        ("Invoice ID", tx.get("invoice_id")),
        ("Subject", tx.get("subject")),
    ]
    return [(k, v) for k, v in rows if v not in (None, "")]


def memo_markdown(verdict: Verdict, *, generated_at: Optional[datetime] = None
                  ) -> str:
    generated_at = generated_at or datetime.now(timezone.utc)
    claim = verdict.claim
    lines = [
        "# PaidCheck — Payment Claim Verification Memo",
        "",
        f"**Verdict: {verdict.status}**",
        f"Generated: {generated_at.isoformat()} (PayPal SANDBOX evidence)",
        "",
        "## The claim",
        f"> {claim.raw_text.strip()}",
        "",
        f"- Claimed amount: {_fmt_money(claim.amount, claim.currency)}",
        f"- Claimed payer: {claim.payer_name or claim.payer_email or '—'}",
        f"- Claimed date: {claim.claimed_date.date().isoformat() if claim.claimed_date else '—'}",
        f"- Item: {claim.item or '—'}",
        "",
        "## PayPal evidence",
    ]
    rows = _tx_rows(verdict.transaction)
    if rows:
        lines += ["| Field | Value |", "|---|---|"]
        lines += [f"| {k} | {v} |" for k, v in rows]
    elif verdict.status == UNCONFIRMED:
        lines.append("No evidence could be gathered: the PayPal search "
                     "did not complete (see the API error below).")
    else:
        lines.append("No matching transaction was found in the window "
                     f"({verdict.candidates_considered} transactions "
                     "considered).")
    if verdict.error:
        lines += ["", "## API error", "```json",
                  json.dumps(verdict.error, indent=2, default=str),
                  "```"]
    if verdict.differences:
        lines += ["", "## Differences", "```json",
                  json.dumps(verdict.differences, indent=2, default=str),
                  "```"]
    if verdict.flags:
        lines += ["", "## Anomaly flags",
                  *[f"- `{f}`" for f in verdict.flags]]
    lines += ["", "## Explanation", verdict.explanation, "",
              "_Produced by PaidCheck against the PayPal sandbox "
              "(api-m.sandbox.paypal.com). Sandbox transactions use test "
              "money; the verification logic is identical to production._"]
    return "\n".join(lines) + "\n"


def memo_html(verdict: Verdict, *, generated_at: Optional[datetime] = None
              ) -> str:
    md = memo_markdown(verdict, generated_at=generated_at)
    color = {MATCHED: "#137333", PARTIAL: "#b06000",
             NOT_FOUND: "#b3261e", DUPLICATE: "#7b1fa2",
             UNCONFIRMED: "#5f6368"}.get(verdict.status, "#333")
    rows = "".join(
        f"<tr><th>{html.escape(k)}</th><td>{html.escape(str(v))}</td></tr>"
        for k, v in _tx_rows(verdict.transaction))
    flags = "".join(f"<li><code>{html.escape(f)}</code></li>"
                    for f in verdict.flags)
    return f"""<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8">
<title>PaidCheck memo — {html.escape(verdict.status)}</title>
<style>body{{font-family:system-ui,sans-serif;max-width:760px;margin:2rem auto;
padding:0 1rem;color:#1f1f1f}} .verdict{{font-size:1.6rem;font-weight:700;
color:{color}}} table{{border-collapse:collapse}} th,td{{border:1px solid #ccc;
padding:.35rem .6rem;text-align:left}} blockquote{{background:#f5f5f5;
padding:.6rem 1rem;border-left:4px solid #999;white-space:pre-wrap}}
pre{{background:#111;color:#0f0;padding:1rem;overflow:auto}}</style></head>
<body>
<h1>PaidCheck — Payment Claim Verification Memo</h1>
<p class="verdict">Verdict: {html.escape(verdict.status)}</p>
<h2>The claim</h2>
<blockquote>{html.escape(verdict.claim.raw_text.strip())}</blockquote>
<h2>PayPal evidence (sandbox)</h2>
<table>{rows or ('<tr><td>The PayPal search did not complete — no evidence gathered.</td></tr>' if verdict.status == UNCONFIRMED else '<tr><td>No matching transaction found.</td></tr>')}</table>
<h2>Explanation</h2><p>{html.escape(verdict.explanation)}</p>
{f'<h2>Anomaly flags</h2><ul>{flags}</ul>' if flags else ''}
<h2>Memo as Markdown</h2>
<p><button onclick="downloadMd()">Download memo (.md)</button></p>
<script type="text/plain" id="md">{html.escape(md)}</script>
<script>function downloadMd(){{var t=document.getElementById('md').textContent;
var a=document.createElement('a');a.href=URL.createObjectURL(new Blob([t],
{{type:'text/markdown'}}));a.download='paidcheck-memo.md';a.click();}}</script>
</body></html>"""
