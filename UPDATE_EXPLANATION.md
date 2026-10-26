# Significant-update explanation (Devpost submission text — Nebius x NVIDIA)

**What existed before:** PaidCheck was first built on 22 Oct 2026 for the
PayPal AI Hackathon (public repo, MIT): a deterministic claim parser, a
PayPal API client (OAuth, Orders v2, Transaction Search), a verdict engine
(MATCHED / PARTIAL / NOT FOUND / DUPLICATE / UNCONFIRMED) with a SQLite
duplicate ledger, and an evidence memo. That submission remains live and
untouched; this Nebius entry is a separate repository.

**What was significantly updated for this hackathon (Oct 2026, inside the
submission period):**

1. **Nemotron understanding layer (Nebius Token Factory).** A new runtime
   integration with NVIDIA Nemotron models served through Nebius Token
   Factory does two real jobs in the verification flow: (a) it reads messy,
   free-text payment claims and suggests values for fields the deterministic
   parser could not extract — suggestions may only fill empty fields, never
   overwrite parsed facts; (b) it explains each decided verdict in plain
   language and flags anomaly patterns (digit transpositions, amount and
   payer mismatches). A code-level guardrail discards any model statement
   that contradicts the decided verdict.
2. **Tavily public-context layer.** A new runtime integration with the
   Tavily search API attaches public-web context about the payer's business
   to the memo, in a separate section labelled "informational only — NOT
   evidence for the verdict". Web content can never confirm or deny a
   private payment; the verdict continues to come only from the payment
   provider's API. A privacy guard skips search entirely when the payer
   looks like a private individual.
3. **Pipeline + hosted demo.** A new pipeline module composes parser →
   (Nemotron suggestions) → original verdict engine → Nemotron explanation
   → Tavily context, plus a stdlib web demo with a daily live-call cap that
   protects the free Builder credits through the judging period, degrading
   transparently to offline-fixture mode instead of spending.
4. **Tests for the trust boundary.** Nine new tests (59 total, all passing)
   prove the architectural claim: verdicts are byte-identical with the AI
   and web layers off, offline, live-shaped — and adversarial (a model
   stub that tries to flip the verdict, a web stub that falsely "confirms"
   the payment). Neither can move the verdict.

**Why this is not a rebrand:** the required sponsor technology sits in the
critical path of a new capability — understanding unstructured claims and
explaining evidence-backed verdicts — with an explicit, tested separation
between what AI may do (understand, explain, contextualize) and what it may
never do (decide whether money moved).
