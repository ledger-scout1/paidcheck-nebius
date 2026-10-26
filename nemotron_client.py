"""Nemotron on Nebius Token Factory — the *understanding* layer of PaidCheck.

Design rule (enforced in code, covered by tests): this module NEVER decides
the financial verdict. The verdict comes only from ``matcher.evaluate`` over
payment-API evidence. Nemotron has exactly two jobs:

1. ``understand_claim`` — read a messy, free-text claim and *suggest* values
   for fields the deterministic parser left empty (payer name, item, date
   hint). The pipeline fills only empty fields and records provenance, so a
   model suggestion can never overwrite parsed payment facts.
2. ``explain_verdict`` — given the ALREADY-DECIDED verdict and its evidence
   summary, write a plain-language explanation and surface anomaly patterns
   (digit transpositions, amount/currency mismatches, repeat-claim signs).
   If the model's text tries to state a different verdict, that statement is
   discarded and flagged — the decided verdict stands.

Transport: Nebius Token Factory is OpenAI-compatible
(``POST {base}/chat/completions``). Stdlib only (urllib), like the rest of
PaidCheck. Without ``NEBIUS_API_KEY`` the client runs in offline fixture
mode: outputs are deterministic, empty of invented facts, and labelled
``offline_fixture`` wherever they surface.
"""

import json
import os
import urllib.request
import urllib.error

DEFAULT_BASE_URL = "https://api.tokenfactory.nebius.com/v1"
# Catalog id VERIFIED live on 2026-10-23 via GET /v1/models with the
# user's Builder key. Live chat smoke test returned the exact expected
# string (27 prompt + 69 completion tokens). The client never hard-fails
# on the model string; a bad id surfaces as a live-mode error and the
# pipeline degrades to offline behaviour.
DEFAULT_MODEL = os.environ.get(
    "NEBIUS_MODEL", "nvidia/NVIDIA-Nemotron-3-Nano-30B-A3B")

UNDERSTAND_SYSTEM = (
    "You extract structured fields from a payment claim message. "
    "Return ONLY a JSON object with keys: payer_name, item, date_hint "
    "(YYYY-MM-DD or null). Use null for anything not clearly stated. "
    "Never invent amounts, currencies, names or dates."
)

EXPLAIN_SYSTEM = (
    "You explain an ALREADY-DECIDED payment verification verdict to a "
    "freelancer. The verdict given to you is final and must not be changed, "
    "questioned or restated as a different outcome. Return ONLY a JSON "
    "object: {\"explanation\": string (max 120 words, plain language), "
    "\"anomaly_flags\": [strings]}. Flag patterns you notice in the "
    "evidence (digit transposition, amount mismatch, currency mismatch, "
    "payer mismatch, possible duplicate). Do not claim the payment was or "
    "was not made beyond the verdict you were given."
)


class NemotronError(Exception):
    pass


class NemotronClient:
    def __init__(self, api_key=None, base_url=None, model=None, timeout=30):
        self.api_key = api_key if api_key is not None else os.environ.get(
            "NEBIUS_API_KEY", "")
        self.base_url = (base_url or os.environ.get(
            "NEBIUS_BASE_URL", DEFAULT_BASE_URL)).rstrip("/")
        self.model = model or DEFAULT_MODEL
        self.timeout = timeout

    @property
    def mode(self):
        return "live" if self.api_key else "offline_fixture"

    # -- transport ---------------------------------------------------------
    def _chat_json(self, system, user, max_tokens=500):
        payload = {
            "model": self.model,
            "messages": [{"role": "system", "content": system},
                         {"role": "user", "content": user}],
            "temperature": 0.1,
            "max_tokens": max_tokens,
            "response_format": {"type": "json_object"},
        }
        req = urllib.request.Request(
            self.base_url + "/chat/completions",
            data=json.dumps(payload).encode(),
            headers={"Content-Type": "application/json",
                     "Authorization": "Bearer " + self.api_key})
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                data = json.loads(resp.read())
        except (urllib.error.URLError, TimeoutError, ValueError) as exc:
            raise NemotronError(f"Token Factory call failed: {exc}") from exc
        try:
            content = data["choices"][0]["message"]["content"]
            return json.loads(content)
        except (KeyError, IndexError, ValueError) as exc:
            raise NemotronError(f"Unparseable model response: {exc}") from exc

    # -- job 1: claim understanding (suggestions only) ----------------------
    def understand_claim(self, raw_text, known_fields):
        """Suggest values for fields the deterministic parser left empty.

        Returns {"suggestions": {field: value}, "mode": ...}. Suggestions
        for fields already known are dropped here AND re-checked by the
        pipeline — defence in depth for the no-overwrite rule.
        """
        empty = [k for k, v in known_fields.items() if v in (None, "")]
        if not empty:
            return {"suggestions": {}, "mode": self.mode,
                    "note": "no empty fields"}
        if self.mode == "offline_fixture":
            return {"suggestions": {}, "mode": self.mode,
                    "note": "offline fixture: no suggestions invented"}
        user = ("Claim message:\n" + raw_text +
                "\n\nFields still needed: " + ", ".join(empty))
        out = self._chat_json(UNDERSTAND_SYSTEM, user, max_tokens=300)
        suggestions = {k: out.get(k) for k in empty
                       if isinstance(out.get(k), str) and out.get(k).strip()}
        return {"suggestions": suggestions, "mode": self.mode}

    # -- job 2: verdict explanation (cannot change the verdict) ------------
    def explain_verdict(self, verdict_status, evidence_summary):
        """Explain a decided verdict. Returns dict with keys:
        explanation, anomaly_flags, mode, discarded_verdict_claim (bool).
        A model attempt to restate a different verdict is discarded.
        """
        if self.mode == "offline_fixture":
            return {
                "explanation": (
                    "Offline fixture mode: connect a Nebius Token Factory "
                    "key for the Nemotron-written explanation. The verdict "
                    "itself is unaffected — it comes from the payment API "
                    "evidence alone."),
                "anomaly_flags": [],
                "mode": self.mode,
                "discarded_verdict_claim": False,
            }
        user = ("Decided verdict: " + verdict_status +
                "\nEvidence summary:\n" + evidence_summary)
        out = self._chat_json(EXPLAIN_SYSTEM, user, max_tokens=500)
        explanation = str(out.get("explanation", "")).strip()
        flags = [str(f) for f in out.get("anomaly_flags", [])
                 if isinstance(f, str)]
        # Guardrail: if the model states a *different* verdict word as its
        # own conclusion, we drop the whole explanation rather than let a
        # contradictory statement reach the memo.
        discarded = False
        other_verdicts = {"MATCHED", "PARTIAL", "NOT_FOUND", "DUPLICATE",
                          "UNCONFIRMED"} - {verdict_status}
        if any(("\"" + v + "\"") in explanation or
               ("verdict: " + v.lower()) in explanation.lower()
               for v in other_verdicts):
            explanation = ("The explanation layer produced a statement "
                           "inconsistent with the decided verdict and was "
                           "discarded. Verdict stands as decided from "
                           "payment evidence.")
            discarded = True
        return {"explanation": explanation, "anomaly_flags": flags,
                "mode": self.mode, "discarded_verdict_claim": discarded}
