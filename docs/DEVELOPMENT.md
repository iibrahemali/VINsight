# Developing VINsight

Everything needed to run, configure, test and extend the project. For what VINsight is, see the
[README](../README.md).

## Requirements

- Python 3.11+
- Node 22+ and pnpm
- A free [Groq API key](https://console.groq.com/keys). A [Gemini key](https://aistudio.google.com/apikey) is optional.

## Run it locally

```bash
cp .env.example .env            # add GROQ_API_KEY (free); GEMINI_API_KEY is optional

# backend (terminal 1)
cd backend
python3 -m venv .venv && .venv/bin/pip install -e ".[dev]"
.venv/bin/uvicorn vinsight.api:app --reload --port 8000

# frontend (terminal 2)
cd frontend
pnpm install && pnpm dev        # http://localhost:3000
```

Or with Docker: `docker compose up --build`.

**No server keys?** Visitors can paste their own free keys under **API keys** in the sidebar. They
stay in the browser, are sent with each request as a header (`X-Groq-Key`, `X-Gemini-Key`), and
are never stored or logged by the server.

**Natural voice for the Advisor call:** accept the Orpheus model terms once in the
[Groq console](https://console.groq.com/playground?model=canopylabs%2Forpheus-v1-english). Until
then (or when its free limit of about 10 requests a minute is reached) the call uses Kokoro, an
open voice model that runs in the browser.

## Configuration

All settings live in `.env` (see [`.env.example`](../.env.example)):

| Setting | Default | What it does |
| --- | --- | --- |
| `GROQ_API_KEY` | | Chat (GPT-OSS, Qwen), voice (Whisper, Orpheus), the note-taker |
| `GEMINI_API_KEY` | | Adds Gemini to the model picker and as a backup |
| `VINSIGHT_PROVIDER` | `gpt-oss-120b` | Starting model: `gpt-oss-120b`, `gpt-oss-20b`, `qwen3.8-27b`, `gemini`, `claude` |
| `VINSIGHT_BACKUP_PROVIDER` | `gemini` | Takes over if the active model fails; `none` disables it |
| `GROQ_TTS_VOICE` | `daniel` | Orpheus voice: `austin`, `daniel`, `troy`, `autumn`, `diana`, `hannah` |

## Command line

```bash
cd backend
.venv/bin/vinsight -p inspector "Is 1HGCM82633A004352 worth a look?"
.venv/bin/vinsight -p coach            # interactive chat with a persona
.venv/bin/vinsight-mcp                 # the tool server on its own (MCP over stdio)
```

## Tests

```bash
cd backend && .venv/bin/pytest -q
cd frontend && npx tsc --noEmit && pnpm lint
```

Tests run offline against recorded NHTSA responses (`backend/tests/fixtures`). The agent-loop tests
drive the real loop and the real MCP server with a scripted stand-in for the model, covering
multi-step tool use, parallel tool calls, tool errors, refusals, stopping mid-turn, provider
fallback and rate-limit handling.

## Recording the demo

The README GIF is recorded from the running app with Playwright, one scene at a time, with
unrecorded pauses so the free-tier rate limits reset between scenes:

```bash
cd demo && pnpm install && npx playwright install chromium
pnpm record                    # all scenes; needs the app on :3000 / :8000
node record.mjs scout advisor  # or only some scenes
./make-gif.sh                  # writes docs/demo.gif and docs/demo.mp4
```

Per-scene playback speeds are at the top of `make-gif.sh`.

## Project layout

```
backend/vinsight/
  api.py              HTTP + server-sent events, sessions, attachments, keys, stop
  agent/loop.py       the agent loop: streams trace events, runs tools, prefetches fixed steps
  agent/prompts.py    personas = instructions + tool subset
  llm/                provider adapters (Groq, Gemini, Claude) and the router (switching, fallback)
  mcp_server.py       tools over MCP: decode_vin, get_recalls, get_complaints,
                      compare_model_years, analyze_listing, read_link
  nhtsa.py            NHTSA API client
  listing.py          Listing X-ray rules and the verdict floor
  attachments.py      links (with SSRF guard), PDFs, text files
  voice.py            Whisper speech-to-text, Orpheus text-to-speech
  notetaker.py        the Advisor's note-taking agent
  shortlist_check.py  checks recommended model years against NHTSA data
frontend/src/
  app/page.tsx        the app
  components/         turn view, answer, listing X-ray, voice call, sidebar, settings
  lib/agent.ts        API client, citation parsing and verification
  lib/voice.ts        mic + voice detection, speech output, echo handling
  lib/kokoro.worker.ts  in-browser fallback voice
demo/                 Playwright demo recorder
docs/BUILD_PROMPT.md  the original build spec
```
