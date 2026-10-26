"""One-shot live smoke test for the Builder keys (run once, by hand).

Prints: (1) the Nemotron model ids the Token Factory catalog actually
offers this account, (2) one tiny chat completion with real token usage,
(3) one basic Tavily search. Total cost: well under $0.01. Requires
NEBIUS_API_KEY and/or TAVILY_API_KEY in the environment. Never prints keys.
"""

import json
import os
import sys
import urllib.request

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

BASE = os.environ.get("NEBIUS_BASE_URL",
                      "https://api.tokenfactory.nebius.com/v1").rstrip("/")


def get(url, key):
    req = urllib.request.Request(url, headers={
        "Authorization": "Bearer " + key})
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read())


def main():
    nkey = os.environ.get("NEBIUS_API_KEY", "")
    tkey = os.environ.get("TAVILY_API_KEY", "")
    if nkey:
        models = get(BASE + "/models", nkey)
        nemos = [m["id"] for m in models.get("data", [])
                 if "nemotron" in m["id"].lower()]
        print("Nemotron model ids available:")
        for m in nemos:
            print("  -", m)
        from nemotron_client import NemotronClient
        client = NemotronClient(api_key=nkey,
                                model=nemos[0] if nemos else None)
        out = client.explain_verdict(
            "PARTIAL",
            "status=PARTIAL\nflags=amount_mismatch, "
            "possible_digit_transposition\nclaimed 180 USD, actual 108 USD")
        print("explain_verdict mode:", out["mode"])
        print("explanation:", out["explanation"][:300])
        print("anomaly_flags:", out["anomaly_flags"])
    else:
        print("NEBIUS_API_KEY not set — Token Factory smoke skipped.")
    if tkey:
        from tavily_context import TavilyContextClient
        out = TavilyContextClient(api_key=tkey).public_context(
            "Nebius", "AI cloud")
        print("Tavily status:", out["status"],
              "| results:", len(out["results"]))
    else:
        print("TAVILY_API_KEY not set — Tavily smoke skipped.")


if __name__ == "__main__":
    main()
