# Feedback section — draft (Devpost, Nebius x NVIDIA) — de completat final după smoke testul live

> Notă internă: textul de mai jos e pregătit pe baza documentației și a integrării offline.
> După rularea `tools/smoke_test.py` cu cheile Builder adăugăm 2–3 fraze cu observații reale
> (latență, modele găsite în catalog) — feedback-ul trebuie să fie despre experiența reală,
> conform jurizării separate a secțiunii (Most Valuable Feedback).

## Token Factory — what worked
- The OpenAI-compatible endpoint made integration a drop-in: our stdlib HTTP
  client needed only a base-URL and model change. JSON-object response mode
  worked as documented for structured extraction.
- The Builder Program credit model is genuinely hackathon-friendly: enough
  headroom that we could design for quality (a reasoning pass per verdict)
  instead of rationing calls.

## Token Factory — what would help
- Model-id discoverability: the catalog id format for Nemotron variants was
  the one integration detail we had to verify empirically via /models; a
  stable, documented "recommended id" per family on the model page would
  remove guesswork for new builders.
- A per-key daily spend/usage readout in the console (not just balance)
  would make credit budgeting for judging periods much easier — we built
  our own daily cap in the demo server for this reason.

## Nemotron models
- For short, structured tasks (field extraction, verdict explanation) the
  Super-class model was more than sufficient; a Nano-class model would cut
  our per-verification cost further. Publishing task-level guidance
  (extraction vs reasoning) per model would help builders pick correctly
  the first time.

## Tavily
- Basic search at 1 credit with content snippets was exactly the right
  granularity for "public context" use; the response shape dropped into
  our memo renderer with no transformation layer.

## AI Cloud (not used, by design)
- We deliberately kept the demo on Token Factory + free hosting to prove
  the free path end to end. Serverless Endpoints is the natural next step
  for a hosted production version.
