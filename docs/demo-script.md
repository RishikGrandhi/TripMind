# TripMind 5–8 minute professor demo

## 0. Preflight and safe mode — before the review

Use `APP_MODE=demo`, `LLM_PROVIDER=fallback`, and `EXTERNAL_PROVIDERS_ENABLED=false`. Start the backend with a new timestamped SQLite filename as shown in `README.md`; this keeps old history recoverable while preventing stale sessions from appearing during the demonstration. Keep the standard ports 8000 and 5173. Before starting, check whether either is already occupied:

```powershell
Get-NetTCPConnection -State Listen -LocalPort 8000,5173 -ErrorAction SilentlyContinue
```

If this prints a listener, close the intended old development process before continuing. Confirm <http://127.0.0.1:8000/api/v1/health> reports `mode: demo`, `llm_provider: fallback`, and `external_providers_enabled: false`, then open <http://localhost:5173>.

## 1. Problem and contribution — 45 seconds

Say: “Travel requests mix hard rules such as budget with softer wishes such as beaches or direct flights. A language model can understand the sentence, but it should not be trusted to decide feasibility. TripMind combines optional AI extraction with deterministic planning, validation, and bounded repair.”

Point out the **DEMO / LOCAL MODE** badge and state that all data is simulated, local, free, and usable offline.

## 2. Run the flagship request — 60 seconds

Paste or select:

> I want to travel from Mumbai to Goa for 4 days with a maximum budget of ₹30,000. I prefer beaches, direct flights and a comfortable hotel.

Click **PLAN TRIP**.

Say: “The extractor turns prose into typed hard constraints and soft preferences. If Ollama is unavailable, deterministic fallback still works; neither path controls feasibility.”

## 3. Inspect the pipeline — 90 seconds

Show the extracted origin, destination, four-day window, ₹30,000 budget, preferences, and the explicit date assumption.

Move to the pipeline and validation panels. Say: “The first candidate costs ₹38,500, so deterministic validation emits `budget_exceeded`. The planner does not ask an LLM to try again; it applies legal targeted actions.”

Show the replanning timeline:

- cheaper flight selected;
- cheaper compliant hotel selected;
- final cost ₹10,400;
- all hard constraints pass.

Say: “Every before/after component, tool call, cost effect, and validation outcome is recorded in `TripState`. Hard constraints never change.”

## 4. Final plan and trade-offs — 45 seconds

Show the day-wise itinerary, cost breakdown, 83.07/100 preference score, and deterministic explanation.

Say: “Preference scoring ranks only a feasible plan. It cannot compensate for a hard violation. The explanation is rendered from structured facts, not hidden chain-of-thought.”

## 5. Persistence/history — 45 seconds

Scroll to **Recent plans**, open the new Mumbai → Goa record, and show that the same result reappears.

Say: “The API saves terminal feasible and infeasible sessions in SQLite. Pydantic `TripState` remains authoritative; SQLite stores summary columns and validated JSON rather than duplicating the whole domain across dozens of tables.”

## 6. Impossible case — 60 seconds

Run:

> Plan a 4-day trip from Mumbai to Goa with a budget of ₹30,000 and flight price under ₹4,000.

Show the bounded attempts and remaining flight-ceiling violation.

Say: “The cheapest available flight is still above ₹4,000. TripMind stops after bounded targeted attempts and reports `INFEASIBLE`; it does not silently relax the ceiling or present an invalid itinerary.”

## 7. Architecture, tests, and evaluation — 60 seconds

Open `docs/architecture.md` and `docs/evaluation-results.md`.

Say: “The architecture keeps extraction, planning tools, validation, replanning, scoring, and persistence separate. The suite covers domain/data/tool logic, limits, fallback, API, deterministic replay, and persistence. Seven real-pipeline evaluation scenarios cover normal, repairable, multi-city, simultaneous-violation, and impossible outcomes.”

Finish: “TripMind’s academic contribution is inspectable hybrid planning: language assistance at the boundary, deterministic authority for correctness, bounded repair, and an honest infeasible result.”
