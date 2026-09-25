# TripMind — Agentic Constraint-Aware Travel Planner

TripMind is a college demonstration of reliable travel planning under hard constraints. A user writes a natural-language request; TripMind extracts a typed request, retrieves deterministic local travel options, builds an itinerary, validates every hard constraint, performs bounded targeted repairs, scores soft preferences only after feasibility, and explains the outcome from recorded facts.

The key contribution is the separation of AI-assisted language understanding from authoritative deterministic planning. TripMind never lets an LLM declare feasibility, change constraints, calculate the final budget, or bypass retry/tool limits.

> **Demo data:** all flights, hotels, activities, routes, prices, and availability are simulated local records. TripMind does not provide live prices or booking.

## Core pipeline

```text
natural-language request
→ validated hard constraints + soft preferences
→ explicit TripState
→ registered local travel tools
→ candidate itinerary
→ deterministic validation
→ violation classification
→ targeted bounded replanning
→ re-validation
→ preference scoring (feasible plans only)
→ fact-based explanation
→ COMPLETED or explicit INFEASIBLE
→ SQLite planning history
```

See [architecture](docs/architecture.md), [dataset](docs/dataset.md), [demo script](docs/demo-script.md), and [generated evaluation results](docs/evaluation-results.md).

## Technology and ₹0 design

- Backend: Python 3.11+, FastAPI, Pydantic v2, SQLAlchemy 2, SQLite, pytest
- Frontend: React 19, TypeScript, Vite, Tailwind CSS
- AI extraction: deterministic offline fallback by default; optional local Ollama
- Travel providers: deterministic versioned JSON only
- Cost: no keys, paid APIs, cloud services, or internet connection required in default demo mode

## Repository structure

```text
backend/
  app/api/             FastAPI endpoints
  app/domain/          authoritative Pydantic models and TripState
  app/extraction/      language extraction and provider fallback
  app/llm/             LLMProvider, fallback, optional Ollama
  app/planning/        builder, validator, replanner, scorer, explanation
  app/persistence/     SQLAlchemy database, model, repository, API schemas
  app/tools/           registered local travel tools
  data/                simulated versioned travel records
  evaluation/          seven curated scenario definitions
  tests/               unit and integration suite
frontend/src/          completed planning and history UI
docs/                  architecture, dataset, demo, generated evaluation
```

## Setup and run

Prerequisites: Python 3.11 or newer, Node.js 20 or newer, and npm.

Backend (PowerShell):

```powershell
cd backend
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
.\.venv\Scripts\python.exe -m uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
```

Frontend (a second terminal):

```powershell
cd frontend
npm ci
npm run dev
```

Open <http://localhost:5173>. API docs are at <http://127.0.0.1:8000/docs> and health is at <http://127.0.0.1:8000/api/v1/health>.

These commands do not require PowerShell script activation. Vite is configured to stop with a clear error if port 5173 is occupied instead of silently selecting a different port that is not in the default CORS allowlist.

### Environment variables

| Variable | Default | Purpose |
|---|---|---|
| `APP_MODE` | `demo` | Local demo behavior |
| `LLM_PROVIDER` | `fallback` | Offline deterministic extraction; optionally `ollama` |
| `DATABASE_URL` | `sqlite:///./tripmind.db` | Planning history database |
| `MAX_REPLANNING_ATTEMPTS` | `3` | Hard retry bound |
| `MAX_TOOL_CALLS` | `20` | Hard tool-use bound |
| `DEMO_REFERENCE_DATE` | `2027-01-15` | Date assumption for duration-only queries |
| `EXTERNAL_PROVIDERS_ENABLED` | `false` | Remains off for the ₹0 demo |
| `OLLAMA_BASE_URL` | `http://127.0.0.1:11434` | Local Ollama only |
| `OLLAMA_MODEL` | `llama3.2:3b` | Optional local model name |
| `OLLAMA_TIMEOUT_SECONDS` | `8` | Bounded local provider timeout |
| `CORS_ORIGINS` | local Vite origins | Allowed frontend origins |
| `VITE_API_BASE_URL` | `http://127.0.0.1:8000` | Frontend API base |

The backend safely creates the `planning_sessions` table on first use; it never resets the database on startup.

### Safest professor-demo configuration

Use the offline fallback and a fresh, timestamped SQLite file in the backend terminal. This preserves existing history while ensuring the review starts with an empty **Recent plans** list:

```powershell
cd backend
$env:APP_MODE = "demo"
$env:LLM_PROVIDER = "fallback"
$env:EXTERNAL_PROVIDERS_ENABLED = "false"
$env:MAX_REPLANNING_ATTEMPTS = "3"
$env:MAX_TOOL_CALLS = "20"
$tripmindDemoDb = "tripmind-demo-$(Get-Date -Format 'yyyyMMdd-HHmmss').db"
$env:DATABASE_URL = "sqlite:///./$tripmindDemoDb"
.\.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

In the frontend terminal:

```powershell
cd frontend
$env:VITE_API_BASE_URL = "http://127.0.0.1:8000"
npm run dev
```

Ollama remains optional. Do not set `LLM_PROVIDER=ollama` for the primary review unless the local model has been tested immediately beforehand.

## Demo query and expected trace

Use:

```text
I want to travel from Mumbai to Goa for 4 days with a maximum budget of ₹30,000. I prefer beaches, direct flights and a comfortable hotel.
```

The deterministic flagship trace is:

```text
initial candidate ₹38,500
→ budget_exceeded
→ select cheaper flight
→ remaining budget violation
→ select compliant cheaper hotel
→ final cost ₹10,400
→ all hard constraints pass
→ preference score 83.07/100
```

The completed plan is stored in SQLite and can be reopened from **Recent plans**. Normal infeasible results are stored too; clarification/system failures are not stored as partial sessions.

## API

- `POST /api/v1/trips/plan-natural` — natural-language planning envelope
- `POST /api/v1/trips/plan` — structured `PlanRequest` → `TripState`
- `GET /api/v1/trips/history?limit=10` — compact recent summaries
- `GET /api/v1/trips/{session_id}` — restored request, `TripState`, and natural response when available

## Test and evaluation commands

```powershell
cd backend
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe -m compileall -q app tests
.\.venv\Scripts\python.exe -m app.evaluation.runner

cd ..\frontend
npm run lint
npm run build
```

The runner executes SC-001 through SC-007: initially feasible, budget repair, hotel-ceiling repair, travel-time repair, multi-city, correctly infeasible, and simultaneous-violation cases. It writes actual JSON and Markdown results under `docs/` and checks deterministic replay.

## Optional Ollama

Set `LLM_PROVIDER=ollama` to use a local model for extraction. Ollama is an enhancement, not an architectural dependency: unavailable, timed-out, malformed, or schema-invalid output visibly falls back to deterministic extraction. Only localhost HTTP URLs are accepted; no cloud model is contacted.

## Current limitations

- Simulated local inventory and fixed demonstration dates; no live availability or pricing
- No booking, payments, maps, authentication, or cloud deployment
- SQLite is appropriate for the single-user demo, not concurrent production traffic
- Rule-based extraction covers the supported demo language, not arbitrary conversation
- Frontend assurance is ESLint, TypeScript/production build, and manual scenarios; no dedicated browser-test framework

## Future work

After academic review, optional work could add free/live provider adapters behind the existing tool contracts, schema migrations, broader datasets, richer natural-language coverage, and browser-level tests. None is required for the complete offline demonstration.
