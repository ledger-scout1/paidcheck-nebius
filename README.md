# PaidCheck — Nebius edition: "I paid you" claim verification, with Nemotron understanding

> **Nebius x NVIDIA Global AI Hackathon build.** This edition adds a real
> runtime integration with **NVIDIA Nemotron models on Nebius Token
> Factory** (claim understanding + verdict explanation) and a **Tavily**
> public-context layer — under one strict rule, enforced in code and
> proven by tests: **the financial verdict comes only from the payment
> provider's API.** Nemotron may understand and explain; Tavily may add
> public context; neither may decide, and neither can change a verdict.
> See `UPDATE_EXPLANATION.md` for the significant-update statement,
> `CREDIT_BUDGET.md` for the free-credit budget, and
> `tests/test_nebius_pipeline.py` for the trust-boundary tests
> (59 tests, all passing).

**Run the demo:** `python3 web_demo.py` (stdlib only, port 7860) or
`python3 demo_run.py` for three seeded scenarios in the terminal.
**With keys:** set `NEBIUS_API_KEY` / `TAVILY_API_KEY` (see `.env.example`)
and run `python3 tools/smoke_test.py` once to confirm the Nemotron model
ids your account can use. Without keys everything still works in a
clearly-labelled offline-fixture mode.

**Where Nemotron is used:** `nemotron_client.py` (suggestions for parser
gaps + plain-language verdict explanations with anomaly flags, via Token
Factory's OpenAI-compatible API). **Where Tavily is used:**
`tavily_context.py` (public business context, basic search, privacy-guarded).
**Where the verdict is decided:** `matcher.py` — unchanged from the
original build, fed only by payment-API evidence (`pipeline.py` composes
the layers around it).

---

# PaidCheck — "I paid you" claim verification agent (original build)

**PaidCheck checks a natural-language payment claim against real PayPal
transactions and issues an evidence-backed verdict memo.**

Sellers and freelancers constantly receive "I paid you" claims — pasted
emails, chat messages, screenshots — and verifying them by hand means
digging through a PayPal dashboard, squinting at amounts and dates. PaidCheck
automates the check: paste the claim, and the agent parses it, searches the
seller's PayPal account, and answers with one of five verdicts:

| Verdict | Meaning |
|---|---|
| **MATCHED** | A PayPal transaction agrees with the claim (amount, currency, payer). |
| **PARTIAL** | A close transaction exists but a field disagrees — amount, currency or payer. The memo names the differences and flags anomalies (e.g. a possible digit transposition: claimed 108, actual 180). |
| **NOT FOUND** | Nothing in the searched window resembles the claim. The claim is *not verified* — a screenshot is not proof of payment. |
| **DUPLICATE** | The transaction matches, but a different claim was already verified against the same payment. One payment cannot settle two claims. |
| **UNCONFIRMED** | The PayPal API call itself failed (auth error, HTTP 4xx/5xx, network). No search could be completed, so the claim is neither verified nor disproven — the memo records the exact API error. Deliberately distinct from NOT FOUND, which means the search *succeeded* and found nothing. |

The output is a shareable **verification memo** (rendered HTML + Markdown
export) quoting the exact PayPal transaction fields as evidence.

> Built for the **PayPal AI Hackathon** (Devpost). Everything runs against
> the **PayPal sandbox** (`api-m.sandbox.paypal.com`) with test money; the
> verification logic is identical to production.

## How it works

```
claim text ──► claim_parser ──► PayPalClient.search_transactions
 (pasted)      (deterministic      (Transaction Search, windowed)
                rules/regex)                 │
                                             ▼
 memo (HTML + Markdown) ◄── matcher.evaluate ◄── normalized transactions
        ▲                        │
        └──── claims ledger (SQLite) ── DUPLICATE detection
```

* `claim_parser.py` — extracts amount, currency, payer name/email, claimed
  date and search window, item description, and an explicit transaction
  reference if the claim includes one. Handles US (`1,200.50`) and European
  (`1.200,50`) number formats, currency symbols/words/ISO codes, absolute
  and relative dates ("yesterday", "3 days ago").
* `paypal_client.py` — OAuth client-credentials with token caching
  (two caches: the general token, and a separately scoped token for
  Transaction Search — see the scope note below), Orders v2
  (create/get/capture), Transaction Search on the **v1** endpoint with
  automatic 31-day window chunking and pagination, Payouts (seeding).
  PayPal errors surface verbatim (status, error name, debug id) —
  nothing is swallowed.
* `matcher.py` — the verdict engine, anomaly flags, the local SQLite
  claims ledger (duplicate detection), and the memo renderers.
* `app.py` — local demo UI + CLI, built on the Python standard library
  (see the module docstring for why not Flask).
* `seed_demo.py` — seeds the sandbox (orders + a payout) and logs, in
  `SEEDING.md`, exactly which server-side routes worked and which step
  needs a browser-approved checkout.

## Setup

Requirements: Python 3.10+.

```bash
pip install -r requirements.txt
```

Create a **sandbox** app to get credentials:

1. Sign in at <https://developer.paypal.com> (a free PayPal account works).
2. Dashboard → **My Apps & Credentials** → **Sandbox** tab → **Create App**.
3. Copy the **Client ID** and **Secret**.
4. Under **Sandbox test accounts**, note the generated **business** and
   **personal** test account emails (you will use the business account as
   "the seller" whose transactions PaidCheck searches).

Configure:

```bash
cp .env.example .env   # then paste your sandbox Client ID / Secret
export PAYPAL_CLIENT_ID=<your sandbox client id>
export PAYPAL_CLIENT_SECRET=<your sandbox secret>
```

## Run it

Seed the sandbox first (creates demo orders, attempts a seed payout, and
lists what Transaction Search can see):

```bash
python3 seed_demo.py
# read SEEDING.md for exactly what happened in your sandbox
```

Web UI:

```bash
python3 app.py            # http://127.0.0.1:8000
```

CLI (prints the Markdown memo):

```bash
python3 app.py --cli "Paid $180 for the logo design yesterday via PayPal. Transaction from John Smith."
```

### A note on the Transaction Search scope (important, verified live)

Transaction Search requires the OAuth scope
`https://uri.paypal.com/services/reporting/search/read`. Two things must
both be true to get it, and the second one is the trap:

1. The app owner enables the **Transaction Search** feature on the app in
   the Developer Dashboard (My Apps & Credentials → your app → features).
2. **The token request must explicitly ask for the scope.** PayPal's
   token endpoint answers a plain `client_credentials` request with a
   *cached general token* (26 scopes, reporting not among them) — even
   after the feature is enabled. This looks exactly like "the dashboard
   change hasn't propagated", but it is not propagation: requesting a
   token with `scope=https://uri.paypal.com/services/reporting/search/read`
   returns a *different, fresh* token that carries the reporting scope.
   Verified directly against the sandbox on 2026-10-22 (the two tokens
   have different fingerprints; the scoped one comes back with a full
   32400s expiry). `paypal_client.py` therefore keeps the two tokens in
   separate caches and uses the scoped one only for Transaction Search.

PaidCheck searches on the **v1** endpoint
(`GET /v1/reporting/transactions`): HTTP 200 with the scoped token. The
**v2** endpoint returns HTTP 404 with an empty body in this sandbox even
with a correctly scoped token, so it is not used.

PaidCheck also has a secondary evidence route: when a claim quotes a
**capture id**, `GET /v2/payments/captures/{id}` (Payments v2, no
reporting scope needed) returns the amount, currency and status, and the
matcher scores the claim against it (captures do not expose payer
identity, so the payer field is not scored on that route).

### A note on sandbox evidence

**A created order is not a completed payment.** The seeded demo orders
sit in status `CREATED` with no payments attached: an Orders v2 order
only becomes a payment after a payer approves it in a checkout flow and
the order is captured. That approval is a browser step a server script
cannot perform, so `seed_demo.py` logs PayPal's real
`ORDER_NOT_APPROVED` answer instead of pretending. Demonstrating a
*completed* payment therefore requires one browser-approved checkout +
capture (planned for the demo recording). The sandbox Payout, by
contrast, moves test money immediately and produces a genuine ledger
movement on the sender account. Transaction Search indexing can lag a
few minutes behind new activity.

## Tests

```bash
python3 -m unittest discover -s tests
```

50 unit tests, no network: parser coverage (amounts, currencies, dates,
payer, item, transaction refs), all five verdicts, anomaly flags
(transposition, currency/payer mismatch, stale window, future date),
duplicate detection against a temporary ledger, memo rendering, and the
PayPal client against a fake HTTP layer (token caching, error mapping,
normalization — including the scoped-token request used for search and
v1 empty-result handling). A dedicated test group pins the failure
semantics: a simulated API 403/500 during verification yields
UNCONFIRMED, while a successful-but-empty search yields NOT FOUND.
Mocks live **only** in tests — the app path always calls the real
sandbox API.

## AI tools disclosure

PaidCheck's claim understanding is **deterministic by design**: a
rules/regex parser (`claim_parser.py`) plus a rule-based verdict engine
(`matcher.py`). This is deliberate — the demo must run identically for any
judge, with **no paid API key**. An optional LLM gap-filler exists behind
the `PAIDCHECK_LLM_PARSER=1` environment flag (OpenAI API; it only fills
fields the rules could not find and falls back silently on any error); it
is **off by default** and not required for any documented flow.

This project was developed with the assistance of an AI coding agent
(Muse, by Meta) acting as the team's builder; the human team member
directed the product decisions and performed all account sign-ins.
Transaction evidence shown in the demo comes from the PayPal sandbox API,
not from generated content.

## Roadmap

* PayPal **Disputes API** integration (auto-attach a PaidCheck memo as
  dispute evidence).
* Invoicing API cross-check (claim vs. issued invoice).
* Hosted multi-seller deployment with per-seller OAuth (production).

## License

MIT — see [LICENSE](LICENSE).
