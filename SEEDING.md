
## Seed run 2026-10-22T21:22:16.061960+00:00

- [oauth] OK — client-credentials token issued by sandbox
- [orders] created+fetched 7XC58817NM784843G: status=CREATED
- [orders] capture 7XC58817NM784843G: refused as expected: UNPROCESSABLE_ENTITY (checkout approval is a browser step)
- [orders] created+fetched 3J691670LW008135X: status=CREATED
- [orders] capture 3J691670LW008135X: refused as expected: UNPROCESSABLE_ENTITY (checkout approval is a browser step)
- [orders] created+fetched 2S992043GF101545N: status=CREATED
- [orders] capture 2S992043GF101545N: refused as expected: UNPROCESSABLE_ENTITY (checkout approval is a browser step)
- [payout] sent 12.34 USD to sb-6gsmq53469326@personal.example.com: batch_id=HT7NB24B2GPZJ status=PENDING
- [search] FAILED — GET /v2/reporting/transactions failed | HTTP 404

## Diagnosis (2026-10-22, after the first seed run) — CORRECTED below

- Transaction Search returns **HTTP 404 (empty body)** on
  `/v2/reporting/transactions` and **403 NOT_AUTHORIZED** on the v1 path,
  when called with the token the client originally used.
- Capture refusals confirmed in detail: HTTP 422 `UNPROCESSABLE_ENTITY`,
  issue `ORDER_NOT_APPROVED` (expected — checkout approval is a browser
  step).
- Payout batch HT7NB24B2GPZJ progressed PENDING -> PROCESSING
  (funding_source: BALANCE), so the payout route works end to end.
- Payout batch HT7NB24B2GPZJ final status: **SUCCESS** (re-checked after the seed run).

## Corrected diagnosis (2026-10-22 21:44Z, verified by direct API calls)

The first write-up of this diagnosis blamed a missing dashboard feature /
scope propagation. That was **wrong**. The verified mechanism:

- The app's **Transaction Search** feature WAS enabled in the Developer
  Dashboard and saved — that part is necessary, but it is not sufficient.
- PayPal's token endpoint answers a plain `client_credentials` request
  with a **cached general token** (26 scopes, reporting scope NOT among
  them) — before *and* after the dashboard change. Nothing was still
  "propagating".
- Requesting a token **with the parameter**
  `scope=https://uri.paypal.com/services/reporting/search/read` returns
  a **different, fresh token** (different fingerprint, full 32400s
  expiry) that **does** carry the reporting scope. The client now keeps
  two token caches and uses the scoped token only for Transaction
  Search (see `paypal_client.py`).
- With the scoped token: Transaction Search **v1**
  (`GET /v1/reporting/transactions`) answers **HTTP 200**
  (total_items 0 at 21:44Z; indexing lag, not an error). The **v2**
  endpoint still returns **HTTP 404 with an empty body** in this sandbox
  even with the scoped token, so the client uses v1.
- Post-fix live re-verification (21:49Z, through the fixed client):
  v1 search HTTP 200, `total_items = 1` — one account transaction
  (id 43672703D18258808, 5000.00 GBP, status S). End-to-end CLI with a
  $180 claim returns a **NOT FOUND** memo (search succeeded, no match);
  the same CLI with a deliberately wrong secret returns an
  **UNCONFIRMED** memo recording HTTP 401 `invalid_client`, exit code 0,
  no traceback.
- The seeded orders (7XC58817NM784843G $180 etc.) are status **CREATED
  with no payments object** — a created order is **not** a completed
  payment. Demonstrating a completed payment requires a payer-approved
  checkout + capture (browser step), still outstanding for the demo.
