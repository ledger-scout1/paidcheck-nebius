"""PaidCheck verification pipeline — Nebius edition.

Order of operations is the product's core guarantee:

1. The deterministic parser reads the claim.
2. Nemotron (Token Factory) may *suggest* values for fields the parser left
   empty. Suggestions are recorded separately, labelled UNCONFIRMED, and
   are NEVER fed to the matcher — not even for the transaction window.
   The verdict is computed from the deterministically parsed claim alone,
   so a wrong or invented suggestion cannot change it.
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
                 record=True, evidence_source="paypal_sandbox"):
    """Run the full pipeline. ``transactions`` is the evidence the verdict
    sees: normally records from a payment provider API (or its sandbox).
    The hosted demonstration passes seeded fixtures instead and MUST say
    so via ``evidence_source="fixtures"``, so the memo labels them as
    simulated data — never as API or sandbox records."""
    now = now or datetime.now(timezone.utc)
    result = {"nemotron": {"mode": "disabled", "suggestions": {},
                           "suggested_fields": []},
              "tavily": {"status": "disabled", "results": []},
              "evidence_source": evidence_source}

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
        # Suggestions are recorded ONLY. The claim below stays exactly as
        # the deterministic parser produced it, so no suggestion (right
        # or wrong) can move the verdict — including via the transaction
        # window that matcher.evaluate derives from the claimed date.
        sugg = und.get("suggestions", {})
        suggestions = {}
        if claim.payer_name is None and sugg.get("payer_name"):
            suggestions["payer_name"] = sugg["payer_name"]
        if claim.item is None and sugg.get("item"):
            suggestions["item"] = sugg["item"]
        if claim.claimed_date is None and sugg.get("date_hint"):
            try:
                datetime.strptime(sugg["date_hint"], "%Y-%m-%d")
                suggestions["date_hint"] = sugg["date_hint"]
            except (ValueError, TypeError):
                pass
        result["nemotron"] = {"mode": und.get("mode", nemo.mode),
                              "suggestions": suggestions,
                              "suggested_fields": sorted(suggestions)}

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
    simulated = result.get("evidence_source") == "fixtures"
    md = matcher.memo_markdown(
        verdict,
        evidence_source=result.get("evidence_source", "paypal_sandbox"))
    nemo = result.get("nemotron", {})
    if nemo.get("explanation"):
        if simulated:
            intro = ("_This section explains the verdict above in plain "
                     "language. It did not decide it: the verdict comes "
                     "only from the demonstration evidence above — "
                     "simulated fixture data, not live payment records._")
        else:
            intro = ("_This section explains the verdict above in plain "
                     "language. It did not decide it: the verdict comes "
                     "only from payment-API evidence._")
        explanation = nemo["explanation"]
        if explanation.startswith("Explanation layer unavailable"):
            source = ("_Explanation source: the live Nemotron call failed "
                      "— fallback notice below._")
        elif nemo.get("mode") == "live":
            source = ("_Explanation source: live Nemotron call (Nebius "
                      "Token Factory)._")
        elif nemo.get("mode") == "offline_fixture":
            source = ("_Explanation source: offline fixture text — no "
                      "live model call was made._")
        else:
            source = ""
        md += ("\n\n## AI explanation — Nemotron on Nebius Token Factory\n"
               + intro + ("\n\n" + source if source else "")
               + "\n\n" + explanation)
        if nemo.get("anomaly_flags"):
            md += ("\n\nAnomaly patterns noted: "
                   + "; ".join(nemo["anomaly_flags"]))
        if nemo.get("suggested_fields"):
            pairs = ", ".join(
                f"{f}={nemo['suggestions'][f]}"
                for f in nemo["suggested_fields"])
            decided_from = ("the demonstration evidence only"
                            if simulated else
                            "payment-API evidence only")
            md += ("\n\nNemotron suggestions (UNCONFIRMED — they did not "
                   "affect the verdict, which was decided from the parsed "
                   f"claim and {decided_from}): " + pairs)
        if nemo.get("discarded_verdict_claim"):
            md += ("\n\nNote: an explanation draft inconsistent with the "
                   "decided verdict was discarded by the guardrail.")
    tav = result.get("tavily", {})
    if tav.get("status") not in (None, "disabled"):
        md += ("\n\n## Public web context — Tavily\n"
               "_Informational only — NOT evidence for the verdict. "
               "Web pages cannot confirm or deny a private payment._\n")
        if tav["status"] == "searched":
            md += "\n_Search source: live Tavily search of the public web._"
            for r in tav["results"]:
                md += f"\n- [{r['title']}]({r['url']}) — {r['snippet']}"
            if not tav["results"]:
                md += "\n- No public results found for the payer name."
        else:
            md += f"\n- Status: {tav['status']} — {tav.get('reason', '')}"
    return md
