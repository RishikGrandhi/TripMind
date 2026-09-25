# TripMind deterministic evaluation results

Generated: 2026-09-25T06:49:44.328980+00:00

These results come from executing the real local planning pipeline against the versioned demo dataset. They are not live-travel or ML benchmark claims.

| Scenario | Initial | Violations | Attempts | Tool calls | Final | Initial cost | Final cost | Score | Replay |
|---|---:|---:|---:|---:|---|---:|---:|---:|---|
| SC-001 — Normal feasible trip | feasible | 0 | 0 | 0 | completed | ₹38500.00 | ₹38500.00 | 74.60 | consistent |
| SC-002 — Budget violation repaired successfully | invalid | 1 | 2 | 2 | completed | ₹38500.00 | ₹10400.00 | 83.07 | consistent |
| SC-003 — Hotel ceiling conflict repaired | invalid | 1 | 1 | 1 | completed | ₹38500.00 | ₹18700.00 | 85.33 | consistent |
| SC-004 — Daily travel-time route conflict | invalid | 1 | 1 | 2 | completed | ₹30600.00 | ₹34900.00 | 71.04 | consistent |
| SC-005 — Multi-city planning | feasible | 0 | 0 | 0 | completed | ₹47950.00 | ₹47950.00 | 90.41 | consistent |
| SC-006 — Impossible flight ceiling | invalid | 2 | 3 | 3 | infeasible | ₹38500.00 | ₹10400.00 | — | consistent |
| SC-007 — Multiple simultaneous violations | invalid | 3 | 2 | 2 | completed | ₹38500.00 | ₹10400.00 | 88.44 | consistent |

## Aggregate metrics

- Hard-constraint satisfaction on expected-feasible scenarios: 100.00%
- Repair success rate for initially invalid expected-feasible scenarios: 100.00%
- Average replanning attempts: 1.29
- Deterministic replay consistency: 100.00%
- Correct impossible-case detection: yes

`SC-006` is intentionally infeasible; its unresolved hard violation is the correct outcome.

## Separate mocked agentic evaluation

SC-008 through SC-013 are reported in `docs/agentic-evaluation-results.md`. They use fixed mocked agent/provider behavior and never alter or replace this local deterministic baseline.
