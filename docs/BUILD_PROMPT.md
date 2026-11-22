# VINsight build spec

The spec this repo is built against. It is a revised version of the original prompt, with the
problems found in review fixed (see "Changes from the original" at the end).

## What it does

The user gives a car listing (pasted text or URL), a VIN, or talks to it hands-free. The agent:

1. Decodes the VIN into year / make / model / trim / engine, and validates it (check digit).
2. Retrieves recalls and complaints for that year/make/model, plus known failure points from the corpus.
3. Reads the listing and extracts price, mileage, title status and red-flag phrases
   ("rebuilt/salvage title", "as-is", "sold for parts", "won't always start"). Flags a VIN in the
   listing that doesn't match the decoded vehicle.
4. Estimates a fair price range from collected comps, labelled as an estimate.
5. Returns a verdict (BUY / CAUTION / AVOID), cited reasoning, a negotiation range, an inspection
   checklist and the questions to ask the seller.

## Agent design

- **Pipeline where the order is fixed, agent where there's a real decision.** The core path
  (decode → recalls + complaints → listing → price → synthesize) is predictable. The model decides:
  whether there's enough to start (no VIN → ask), whether to re-query retrieval when results are
  weak, whether a listing contradicts the decoded VIN, and how to answer follow-ups and comparisons.
  `docs/how-the-agent-decides.md` describes exactly which is which.
- **Tools are an MCP server** (`vinsight/mcp_server.py`), so they work with any MCP client. The
  agent is a hand-written tool-use loop on the Claude API that emits a trace event per step.
  - `decode_vin(vin)`
  - `get_recalls(make, model, year)`
  - `get_complaints(make, model, year)`
  - `search_known_issues(make, model, year, query)`: hybrid RAG over the corpus
  - `analyze_listing(text_or_url)`: structured fields + red flags
  - `estimate_price(make, model, year, mileage, condition)`
- **Verdict rubric is rules first.** Hard rules decide the floor (salvage/rebuilt title → AVOID
  unless the user accepts it; a recall with a park-it or park-outside advisory → at least CAUTION;
  price far below the comp band → CAUTION). The model explains the verdict and may raise it to a
  stricter level with stated reasons, never lower it.
- **RAG corpus: NHTSA complaint narratives and manufacturer communications (TSBs).** Public, free,
  large and messy enough that hybrid retrieval earns its keep. Hybrid BM25 + vectors, a reranker,
  and an agent-driven re-query when the top results score low. No scraped forum or paywalled content.
- **Memory:** cars the user evaluated plus budget/preferences, in SQLite. Single user, no auth.
- **Guardrails:** every factual claim carries a citation in a fixed format (`[recall:19V182000]`,
  `[complaint:11746949]`, `[complaints:make:model:year]`, `[vpic:VIN]`, later `[doc:…]`,
  `[listing]`, `[estimate]`). Cited IDs must come from tool results in the conversation; the UI and
  the evals both check this. Missing data is stated as "can't verify". Facts and opinion are in
  separate sections.
- **Recall scope is stated honestly.** NHTSA's public API gives recalls per year/make/model, not
  whether one is still open on a VIN. The agent never claims a recall is open or fixed; it tells
  the user how to check.

## Data (free / public)

- NHTSA vPIC for VIN decoding; NHTSA recalls and complaints APIs.
- Price: comps collected by hand (5–10 per test vehicle, committed with the date collected) plus a
  transparent depreciation + mileage heuristic for vehicles without comps. Always labelled an estimate.
- Listing input: pasted text is the main path. URL fetch is best-effort and fails gracefully.

## Voice

Hands-free mode for "I'm standing at the car": a cascaded pipeline (streaming STT → agent → TTS)
over LiveKit, with barge-in. Because multi-step tool runs take seconds, voice mode speaks short
progress updates ("pulling recalls…") instead of going silent. Text must work fully on its own.

## Evals

- **Test set:** 30–50 real listings, plus 5–10 adversarial ones (no VIN, VIN that doesn't match the
  listing, salvage title buried mid-text, a mileage typo). Ground truth per case: decoded vehicle,
  applicable recall campaign numbers (from a frozen NHTSA snapshot), hand-labelled red flags,
  and a price band from the committed comps.
- **Metrics:** VIN decode accuracy, recall retrieval recall/precision vs the snapshot, red-flag
  precision/recall, price error vs the comp band, citation validity (every cited ID exists in the
  tool results *and* in the snapshot), hallucination rate (claims with no valid citation), verdict
  agreement with the rubric, per-step latency, cost per run.
- **LLM-as-judge** for overall advice quality, with a written rubric, calibrated against about 10
  answers labelled by hand.
- **CI:** offline tests and a small eval smoke subset on recorded fixtures for every PR; the full
  eval (live model + judge) nightly or on manual trigger. Numbers go in `evals/REPORT.md` and the README.

## Stack

Python backend (FastAPI, MCP Python SDK, Anthropic SDK), Claude (`claude-opus-5-5`). Next.js +
Tailwind frontend; LiveKit (`@livekit/components-react` + LiveKit Agents) for voice. LanceDB or
Chroma for vectors. `docker compose up` runs everything.

## Definition of done

- README opens with a 60-second demo GIF/video.
- `docker compose up` runs the whole thing.
- Architecture diagram and a short "how the agent decides" write-up.
- Eval report with real numbers committed.
- Works end-to-end on at least one real listing you can show live.

## Build order

1. decode_vin + get_recalls (+ get_complaints) end-to-end, text output. **← done**
2. analyze_listing (pasted text) + the verdict rubric and synthesis step.
3. Known-issues corpus + hybrid retrieval + reranker.
4. Eval harness and first numbers.
5. Memory + price estimate.
6. Voice mode.

## Out of scope

Accounts/auth, payments, a mobile app, scrapers that fight anti-bot systems, real-time market pricing.

## Changes from the original

- "Actual open recalls" → "applicable recalls for the year/make/model" (no free per-VIN open-recall API).
- Price ground truth comes from committed comps, not from the heuristic being graded.
- RAG corpus named: NHTSA complaints + TSBs, instead of unspecified "reliability notes".
- Honest pipeline-vs-agent split instead of calling a fixed sequence "multi-hop agentic".
- Voice is a cascaded pipeline with progress speech (Claude has no native speech-to-speech).
- Verdict has an explicit rule-based rubric so it can be evaluated.
- CI runs on recorded fixtures; live evals run nightly. Cost per run is reported.
- LLM judge has a rubric and human calibration. Adversarial cases added.
