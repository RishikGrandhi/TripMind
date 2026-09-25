# TripMind mocked agentic evaluation results

Generated: 2026-09-25T06:50:46.882699+00:00

These are fixed, network-independent mocked-agent scenarios. They measure factual execution behavior, not universal AI accuracy or live-provider quality.

| Scenario | Steps | Ordered actions | Tools | Rejected | Failures | Replans | Final | Feasible | Replay |
|---|---:|---|---|---:|---:|---:|---|---|---|
| SC-008 — Weather-aware activity adaptation | 6 | get_weather → search_activities → search_flights → search_hotels → build_candidate → validate | WeatherTool.get_forecast, ActivitySearchTool.search_activities, FlightSearchTool.search_flights, HotelSearchTool.search_hotels | 0 | 0 | 0 | completed | yes | consistent |
| SC-009 — Flight alternative selection | 6 | search_flights → search_hotels → search_activities → build_candidate → validate → search_cheaper_flight → search_cheaper_hotel → propose_corrective_action | FlightSearchTool.search_flights, HotelSearchTool.search_hotels, ActivitySearchTool.search_activities | 0 | 0 | 2 | completed | yes | consistent |
| SC-010 — Hotel alternative selection | 5 | search_hotels → search_flights → build_candidate → validate → search_cheaper_hotel → propose_corrective_action | HotelSearchTool.search_hotels, FlightSearchTool.search_flights | 0 | 0 | 1 | completed | yes | consistent |
| SC-011 — Multi-tool dependency | 5 | get_route → search_hotels → search_activities → build_candidate → validate | RouteTool.calculate_route, HotelSearchTool.search_hotels, ActivitySearchTool.search_activities | 0 | 0 | 0 | completed | yes | consistent |
| SC-012 — External tool failure | 1 | get_weather → deterministic_planning_fallback | WeatherTool.get_forecast | 0 | 1 | 0 | completed | yes | consistent |
| SC-013 — Agent iteration limit | 1 | search_flights → agent_loop → deterministic_planning_fallback | FlightSearchTool.search_flights | 0 | 0 | 0 | completed | yes | consistent |

## Factual aggregate

- Scenarios meeting expected terminal status: 6/6
- Deterministic replay matches: 6/6
- Live network calls: 0
