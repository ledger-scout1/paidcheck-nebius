"""Tavily — the *public context* layer of PaidCheck.

Design rule (enforced in code, covered by tests): Tavily results are PUBLIC
WEB CONTEXT ONLY. They are rendered in a separate memo section labelled
"informational only — NOT evidence for the verdict", and nothing in this
module is ever passed to ``matcher.evaluate``. A web page can never confirm
or deny a private payment; only the payment provider's API can.

Privacy guard: context search runs only when the claim names what looks
like a public business (no e-mail address, no transaction id in the query).
Claims from private individuals are skipped, not searched.
"""

import json
import os
import re
import urllib.request
import urllib.error

TAVILY_URL = "https://api.tavily.com/search"
_EMAIL_RE = re.compile(r"[^@\s]+@[^@\s]+\.[^@\s]+")
_TXID_RE = re.compile(r"\b[A-Z0-9]{17}\b")

DISCLAIMER = ("Public web context only — informational, NOT used as "
              "evidence for the financial verdict.")


class TavilyContextClient:
    def __init__(self, api_key=None, timeout=20):
        self.api_key = api_key if api_key is not None else os.environ.get(
            "TAVILY_API_KEY", "")
        self.timeout = timeout

    @property
    def mode(self):
        return "live" if self.api_key else "offline_fixture"

    def public_context(self, payer_name, item=None):
        """Return {"status": searched|skipped|offline, "query", "results",
        "disclaimer"}. Basic search depth only (1 credit per call)."""
        base = {"disclaimer": DISCLAIMER, "mode": self.mode, "results": []}
        if not payer_name or _EMAIL_RE.search(payer_name) \
                or _TXID_RE.search(payer_name):
            return {**base, "status": "skipped",
                    "reason": "payer does not look like a public business "
                              "name — no web search performed"}
        query = f'"{payer_name}"' + (f" {item}" if item else " business")
        if self.mode == "offline_fixture":
            return {**base, "status": "offline", "query": query,
                    "reason": "offline fixture mode: no web results "
                              "invented; connect a Tavily key for live "
                              "public context"}
        payload = {"query": query, "search_depth": "basic",
                   "max_results": 3, "include_answer": False}
        req = urllib.request.Request(
            TAVILY_URL, data=json.dumps(payload).encode(),
            headers={"Content-Type": "application/json",
                     "Authorization": "Bearer " + self.api_key})
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                data = json.loads(resp.read())
        except (urllib.error.URLError, TimeoutError, ValueError) as exc:
            return {**base, "status": "error", "query": query,
                    "reason": f"Tavily call failed: {exc}"}
        results = [{"title": r.get("title", ""), "url": r.get("url", ""),
                    "snippet": (r.get("content") or "")[:280]}
                   for r in data.get("results", [])]
        return {**base, "status": "searched", "query": query,
                "results": results}
