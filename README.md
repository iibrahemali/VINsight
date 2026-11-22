# VINsight

A used-car buying copilot, built for people who have never bought a car. Three personas share one
conversation:

- **Coach** teaches how buying a used car works: the real budget, titles, financing, inspections, scams.
- **Scout** turns your needs into a shortlist of models, and uses NHTSA data to say which model years to avoid.
- **Inspector** checks a specific car from its VIN or listing: a rule-based **Listing X-ray** (scam
  patterns, title problems, VIN-vs-listing mismatch), recalls, complaints, and a BUY / CAUTION / AVOID verdict.
- **Advisor** is a hands-free **voice call** with Sam, who learns your budget and needs and recommends
  models. A second agent takes notes into a live "car brief", and the app checks every recommended
  model year against NHTSA complaint data, feeding spikes back into the call.

Every fact about a vehicle carries a citation you can click, and the UI flags any citation that
doesn't trace back to a tool result.

> Demo GIF goes here once the verdict step lands.

## Status

| Step | What | State |
| --- | --- | --- |
| 1 | VIN decode, recalls, complaints, cited text answers | ✅ done |
| 1b | Personas (Coach / Scout / Inspector), model-year comparison, Gemini / Groq / Claude with fallback | ✅ done |
| 1c | Streaming reasoning + answers, model picker, stop, links / PDFs / files as evidence | ✅ done |
| 2 | Listing X-ray + rule-based verdict floor | ✅ done |
| 2b | Voice Advisor: VAD + barge-in, Whisper + GPT-OSS on Groq, note-taker agent, background data checks | ✅ done |
| 3 | Known-issues corpus with hybrid retrieval + reranker | planned |
| 4 | Eval harness and report | planned |
| 5 | Memory + price estimate | planned |

The full spec is in [docs/BUILD_PROMPT.md](docs/BUILD_PROMPT.md).

## How it works

```
Next.js UI ──SSE──▶ FastAPI ──▶ Agent loop ──────MCP/stdio──▶ VINsight MCP server ──▶ NHTSA APIs
   personas, chat,     /api/chat     persona = prompt + tools        decode_vin                 vPIC
   trace, citations                  Gemini → Groq backup            get_recalls                recalls
                                                                     get_complaints             complaints
                                                                     compare_model_years
```

- **Tools are an MCP server** (`backend/vinsight/mcp_server.py`). Any MCP client can use them,
  including Claude Desktop or `mcp dev`.
- **The agent loop is hand-written** (`backend/vinsight/agent/loop.py`) so every step emits a trace
  event with its latency. The CLI, the web UI and (soon) the eval harness all use the same trace.
- **Model-agnostic, with a backup.** The loop talks to a small `Conversation` interface
  (`backend/vinsight/llm/`) with Gemini, Groq and Claude adapters. Gemini is the default and falls
  through its model list when one is overloaded or out of quota; if all fail, the conversation is
  replayed into Groq (`gpt-oss-120b`) and continues there.
- **Tool results are sized for the budget.** Recall and complaint text is trimmed at the source
  (the full text is one click away on nhtsa.gov), and Groq gets an extra-compact view to fit its
  free tier's 8K tokens/minute.
- **Personas are a system prompt plus a tool subset** (`backend/vinsight/agent/prompts.py`). Coach
  can't decode VINs, and the loop enforces that even if the model tries.
- **Streams everything.** Reasoning and answers arrive token by token; the UI shows live thinking
  that folds to "Thought for Ns", tool steps with timings, and a Stop button that cancels the run
  server-side while keeping the conversation valid.
- **Links, PDFs and files are evidence.** Pasted links become preview cards (title, image, read
  status, any VIN found). Fetching is best-effort with an honest bot user agent and an SSRF guard;
  sites that block bots come back as a card that says "paste the text instead". Attached text is
  fenced as data in the prompt and cited as `[doc:id]`.
- **Every tool result carries a `source`** (recall campaign number, complaint ODI number, VIN). The
  system prompt requires a citation after each fact, and the UI marks each citation green
  (returned by a tool) or red (not found).
- **The voice call needs no LiveKit or paid service.** The browser detects speech itself (adaptive
  energy VAD in an AudioWorklet), Whisper on Groq transcribes, GPT-OSS answers, and replies are spoken
  sentence by sentence (Groq Orpheus once its terms are accepted, otherwise the browser's voice).
  Talking over Sam interrupts him. Sam has no tools, so turns stay under about a second; notes and
  data checks run alongside, in code.
- **Recall scope is stated honestly.** NHTSA's public API lists recalls per year/make/model, not
  whether one is still open on a specific VIN, so VINsight never claims a recall is open or fixed.

## Run it

You need Python 3.11+, Node 22+, pnpm, and a free Groq API key. A Gemini key is optional.

```bash
cp .env.example .env            # add GROQ_API_KEY (free); GEMINI_API_KEY is optional

# backend
cd backend
python3 -m venv .venv && .venv/bin/pip install -e ".[dev]"
.venv/bin/uvicorn vinsight.api:app --reload --port 8000

# frontend (second terminal)
cd frontend
pnpm install && pnpm dev        # http://localhost:3000
```

Or with Docker: `docker compose up --build`.

Command line, no UI:

```bash
cd backend
.venv/bin/vinsight -p inspector "Is 1HGCM82633A004352 worth a look?"
.venv/bin/vinsight -p coach      # interactive chat with a persona
```

## Tests

```bash
cd backend && .venv/bin/pytest
```

Tests run offline against recorded NHTSA responses (`backend/tests/fixtures`). The agent-loop
tests drive the real loop and the real MCP server with a scripted stand-in for the model. They
cover multi-step tool use, parallel tool calls, tool errors and refusals.
