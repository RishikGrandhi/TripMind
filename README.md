# TripMind — Agentic Constraint-Aware Travel Planner

TripMind is a college demonstration of reliable travel planning under hard constraints. A user writes a natural-language request; TripMind extracts a typed request, retrieves deterministic local travel options, builds an itinerary, validates every hard constraint, performs bounded targeted repairs, scores soft preferences only after feasibility, and explains the outcome from recorded facts.

The key contribution is guarded hybrid planning. In opt-in Groq mode, Groq performs language understanding and state-aware action selection over registered TripMind tools; deterministic Python still authorizes actions, owns state and exact costs, validates every hard constraint, and executes bounded repairs. TripMind never lets an LLM declare feasibility, change constraints, calculate the final budget, or bypass retry/tool limits.

> **Default demo data:** flights, hotels, activities, routes, prices, and availability are simulated local records. Optional live adapters are opt-in and do not provide booking.

## Core pipeline

```text
natural-language request
→ validated hard constraints + soft preferences
→ explicit TripState
→ registered travel tools (local by default)
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

See [architecture](docs/architecture.md), [dataset](docs/dataset.md), [demo script](docs/demo-script.md), [deterministic evaluation](docs/evaluation-results.md), and [mocked agentic evaluation](docs/agentic-evaluation-results.md).

## Technology and ₹0 design

- Backend: Python 3.11+, FastAPI, Pydantic v2, SQLAlchemy 2, SQLite, pytest
- Frontend: React 19, TypeScript, Vite, Tailwind CSS
- AI control: deterministic offline fallback by default; optional Groq primary agent or local Ollama extraction
- Travel providers: deterministic versioned JSON by default; optional SerpApi, StayingAPI, Geoapify, and OpenWeather adapters
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
  app/tools/           registered local tools and opt-in live adapters
  data/                simulated versioned travel records
  evaluation/          deterministic and mocked-agent scenario definitions
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
| `APP_MODE` | `demo` | `demo` for local tools; `live` only with explicitly enabled external providers |
| `LLM_PROVIDER` | `fallback` | Offline deterministic mode; optionally `groq` or `ollama` |
| `GROQ_API_KEY` | unset | Required only when `LLM_PROVIDER=groq`; never sent to the frontend or persisted |
| `GROQ_MODEL` | `llama-3.3-70b-versatile` | Configurable Groq model; not an architectural dependency |
| `GROQ_TIMEOUT_SECONDS` | `12` | Bounded Groq request timeout |
| `DATABASE_URL` | `sqlite:///./tripmind.db` | Planning history database |
| `MAX_REPLANNING_ATTEMPTS` | `3` | Hard retry bound |
| `MAX_TOOL_CALLS` | `20` | Hard tool-use bound |
| `MAX_AGENT_STEPS` | `12` | Hard Groq planning-loop bound |
| `DEMO_REFERENCE_DATE` | `2027-01-15` | Date assumption for duration-only queries |
| `EXTERNAL_PROVIDERS_ENABLED` | `false` | Select external travel tools only when `APP_MODE=live` |
| `LIVE_TOOL_FALLBACK_TO_LOCAL` | `false` | Explicitly permit labeled local fallback after a live tool failure |
| `SERPAPI_API_KEY` | unset | SerpApi Google Flights credential |
| `STAYING_API_KEY` | unset | StayingAPI credential |
| `GEOAPIFY_PLACES_API_KEY` | unset | Geoapify Places credential |
| `GEOAPIFY_ROUTING_API_KEY` | unset | Geoapify Routing credential |
| `OPENWEATHER_API_KEY` | unset | OpenWeather forecast credential |
| `LIVE_ROUTE_COST_PER_KM` | `12.00` | Deterministic INR estimate policy; never described as a provider fare |
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

### Optional Groq agent with local travel tools

Groq is opt-in and does not enable live travel providers. Set the key only in the backend process or an ignored `backend/.env` file:

```powershell
cd backend
$env:APP_MODE = "demo"
$env:LLM_PROVIDER = "groq"
$env:GROQ_API_KEY = "<your key>"
$env:GROQ_MODEL = "<a Groq chat model available to your account>"
$env:EXTERNAL_PROVIDERS_ENABLED = "false"
.\.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

In this mode Groq separately performs typed extraction, observes serialized `TripState`, selects and parameterizes registered local searches, observes normalized results, and proposes corrective actions after deterministic validation. Pydantic schemas and deterministic policy checks reject unknown tools, malformed parameters, and illegal repairs. Provider failure is recorded and falls back to the existing deterministic planner.

This is the preferred agentic review mode: Groq controls the adaptive action sequence while stable local data isolates that behavior from live-provider variability. The completed response and restored history view both render the factual **AI agent / tool trace**. Weather is available only when a weather implementation is registered; it is optional advisory context and never a hard-feasibility rule.

### Optional Groq agent with live tools

Live mode is a separate, opt-in configuration. Copy `backend/.env.live.example` to the ignored `backend/.env`, fill credentials locally, and keep `LIVE_TOOL_FALLBACK_TO_LOCAL=false` unless a visibly labeled fallback is desired:

```powershell
cd backend
$env:APP_MODE = "live"
$env:LLM_PROVIDER = "groq"
$env:EXTERNAL_PROVIDERS_ENABLED = "true"
$env:LIVE_TOOL_FALLBACK_TO_LOCAL = "false"
.\.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

The registry selects provider adapters; the agent still sees only generic TripMind actions. Flight and hotel records enter cost validation only with provider prices in INR. Geoapify Places records have unknown price and duration, so they remain advisory candidates and are not silently costed as ₹0. Geoapify route costs use the configured per-kilometre estimate and are marked `estimated`. Weather is advisory and returns `outside_forecast_horizon` when a requested date is not covered. Live results vary, and a provider failure never becomes apparently live local data.

Run one minimal provider smoke at a time from `backend` (keys are read from settings and never printed):

```powershell
.\.venv\Scripts\python.exe scripts/smoke_live_provider.py groq
.\.venv\Scripts\python.exe scripts/smoke_live_provider.py serpapi
.\.venv\Scripts\python.exe scripts/smoke_live_provider.py stayingapi
.\.venv\Scripts\python.exe scripts/smoke_live_provider.py places
.\.venv\Scripts\python.exe scripts/smoke_live_provider.py routing
.\.venv\Scripts\python.exe scripts/smoke_live_provider.py weather
```

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
.\.venv\Scripts\python.exe -m app.evaluation.agentic_runner

cd ..\frontend
npm run lint
npm run build
```

The deterministic runner executes SC-001 through SC-007 against local data. The separate agentic runner executes reproducible mocked SC-008 through SC-013 for weather adaptation, flight/hotel repair, multi-tool dependency, provider failure, and iteration bounds. Neither runner calls a live network provider, and both record factual scenario outcomes rather than an “AI accuracy” claim.

## Optional Ollama

Set `LLM_PROVIDER=ollama` to use a local model for extraction. Ollama is an enhancement, not an architectural dependency: unavailable, timed-out, malformed, or schema-invalid output visibly falls back to deterministic extraction. Only localhost HTTP URLs are accepted; no cloud model is contacted.

## Current limitations

- Safe review mode uses simulated local inventory and fixed demonstration dates; optional live quotes vary and require provider credentials
- Real Groq and live-provider compatibility cannot be claimed until the corresponding credentialed smoke command succeeds
- Geoapify Places has no authoritative admission price/duration; route cost is an explicitly labeled estimate
- Weather is advisory and unavailable outside the provider forecast horizon
- No booking, payments, maps, authentication, or cloud deployment
- SQLite is appropriate for the single-user demo, not concurrent production traffic
- Rule-based extraction covers the supported demo language, not arbitrary conversation
- Frontend assurance is ESLint, TypeScript/production build, and manual scenarios; no dedicated browser-test framework

## Future work

After academic review, optional work could add schema migrations, broader datasets, richer natural-language coverage, and browser-level tests. None is required for the complete offline demonstration.

## Three operating modes

Use safe review mode for the most reliable viva, Groq + local mode to demonstrate adaptive agent decisions without travel-provider variability, and live mode only for separately credentialed integration checks.

```powershell
# Safe review mode
$env:APP_MODE = "demo"
$env:LLM_PROVIDER = "fallback"
$env:EXTERNAL_PROVIDERS_ENABLED = "false"

# Groq agent + local tools
$env:APP_MODE = "demo"
$env:LLM_PROVIDER = "groq"
$env:EXTERNAL_PROVIDERS_ENABLED = "false"

# Groq agent + live tools
$env:APP_MODE = "live"
$env:LLM_PROVIDER = "groq"
$env:EXTERNAL_PROVIDERS_ENABLED = "true"
$env:LIVE_TOOL_FALLBACK_TO_LOCAL = "false"
```
