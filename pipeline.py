"""PaidCheck verification pipeline — Nebius edition.

Order of operations is the product's core guarantee:

1. The deterministic parser reads the claim.
2. Nemotron (Token Factory) may *suggest* values for fields the parser left
   empty. Suggestions never overwrite parsed values; every filled field is
   recorded with its provenance.
3. ``matcher.evaluate`` decides the verdict from PAYMENT API evidence only.
   This function is byte-identical to the PayPal-hackathon build — neither
   Nemotron output nor Tavily output is among its inputs.
4. Nemotron explains the decided verdict (it cannot change it — see
   ``nemotron_client.explain_verdict`` guardrails).
5. Tavily adds public web context about the payer's business, rendered in
   its own clearly-labelled memo section. It is never verdict evidence.
"""

from datetime import datetime, timezone

import claim_parser
import matcher
from nemotron_client import NemotronClient, NemotronError
from tavily_context import TavilyContextClient


def _evidence_summary(verdict):
    lines = [f"status={verdict.status}",
             f"flags={', '.join(verdict.flags) or 'none'}",
             f"differences={verdict.differences or 'none'}",
             f"candidates_considered={verdict.candidates_considered}"]
    tx = verdict.transaction
    if tx:
        lines.append(
            "best_candidate: amount={} currency={} payer={} id={}".format(
                tx.get("amount"), tx.get("currency"),
                tx.get("payer_name") or tx.get("payer_email"),
                tx.get("transaction_id")))
    return "\n".join(lines)


def verify_claim(text, transactions, *, use_nemotron=True, use_tavily=True,
                 nemotron=None, tavily=None, db_path=None, now=None,
                 record=True):
    """Run the full pipeline. ``transactions`` must come from a payment
    provider API (or its sandbox) — the only evidence the verdict sees."""
    now = now or datetime.now(timezone.utc)
    result = {"nemotron": {"mode": "disabled", "filled_fields": []},
              "tavily": {"status": "disabled", "results": []}}

    claim = claim_parser.parse_claim(text, now=now)

    nemo = nemotron or (NemotronClient() if use_nemotron else None)
    if nemo is not None:
        known = {"payer_name": claim.payer_name, "item": claim.item,
                 "date_hint": claim.claimed_date}
        try:
            und = nemo.understand_claim(text, known)
        except NemotronError as exc:
            und = {"suggestions": {}, "mode": nemo.mode,
                   "note": f"understanding layer unavailable: {exc}"}
        filled = []
        sugg = und.get("suggestions", {})
        if claim.payer_name is None and sugg.get("payer_name"):
            claim.payer_name = sugg["payer_name"]
            filled.append("payer_name")
        if claim.item is None and sugg.get("item"):
            claim.item = sugg["item"]
            filled.append("item")
        if claim.claimed_date is None and sugg.get("date_hint"):
            try:
                claim.claimed_date = datetime.strptime(
                    sugg["date_hint"], "%Y-%m-%d").replace(
                        tzinfo=timezone.utc)
                filled.append("claimed_date")
            except ValueError:
                pass
        if filled:
            claim.notes.append(
                "Fields suggested by Nemotron (Token Factory) and filled "
                "because the deterministic parser left them empty: "
                + ", ".join(filled))
        result["nemotron"] = {"mode": und.get("mode", nemo.mode),
                              "filled_fields": filled}

    # THE verdict — payment evidence only, original matcher, unmodified.
    verdict = matcher.evaluate(claim, transactions, db_path=db_path,
                               now=now, record=record)
    result["claim"] = claim
    result["verdict"] = verdict

    if nemo is not None:
        try:
            exp = nemo.explain_verdict(verdict.status,
                                       _evidence_summary(verdict))
        except NemotronError as exc:
            exp = {"explanation": f"Explanation layer unavailable: {exc}",
                   "anomaly_flags": [], "mode": nemo.mode,
                   "discarded_verdict_claim": False}
        result["nemotron"].update(exp)

    if use_tavily:
        tav = tavily or TavilyContextClient()
        result["tavily"] = tav.public_context(claim.payer_name, claim.item)

    result["memo_markdown"] = render_memo(result)
    return result


def render_memo(result):
    verdict = result["verdict"]
    md = matcher.memo_markdown(verdict)
    nemo = result.get("nemotron", {})
    if nemo.get("explanation"):
        md += (
            "\n\n## AI explanation — Nemotron on Nebius Token Factory\n"
            "_This section explains the verdict above in plain language. "
            "It did not decide it: the verdict comes only from payment-API "
            "evidence._\n\n" + nemo["explanation"])
        if nemo.get("anomaly_flags"):
            md += ("\n\nAnomaly patterns noted: "
                   + "; ".join(nemo["anomaly_flags"]))
        if nemo.get("filled_fields"):
            md += ("\n\nClaim fields filled from Nemotron suggestions "
                   "(parser had left them empty): "
                   + ", ".join(nemo["filled_fields"]))
        if nemo.get("discarded_verdict_claim"):
            md += ("\n\nNote: an explanation draft inconsistent with the "
                   "decided verdict was discarded by the guardrail.")
    tav = result.get("tavily", {})
    if tav.get("status") not in (None, "disabled"):
        md += ("\n\n## Public web context — Tavily\n"
               "_Informational only — NOT evidence for the verdict. "
               "Web pages cannot confirm or deny a private payment._\n")
        if tav["status"] == "searched":
            for r in tav["results"]:
                md += f"\n- [{r['title']}]({r['url']}) — {r['snippet']}"
            if not tav["results"]:
                md += "\n- No public results found for the payer name."
        else:
            md += f"\n- Status: {tav['status']} — {tav.get('reason', '')}"
    return md
