# TripMind demo dataset

TripMind uses local, simulated, version-controlled JSON data. It is designed to demonstrate orchestration, deterministic validation, and genuine repair—not to represent live travel inventory or prices.

## Coverage

| File | Records | Contents |
|---|---:|---|
| `cities.json` | 8 | Mumbai, Goa, Chennai, Delhi, Jaipur, Agra, Shimla, Hyderabad |
| `flights.json` | 17 | Dated flight alternatives, duration, stops, seats, exact INR price |
| `hotels.json` | 22 | Per-night price, rating, amenities, rooms |
| `routes.json` | 20 | Train, bus, car routes, time, distance, cost, feasibility |
| `activities.json` | 21 | City, category, duration, opening window, price |

Every record has a stable readable ID such as `flight-mum-goi-001`. Loaders validate records through Pydantic models and return predictable ordering. Monetary values enter the system as exact decimals, and malformed or internally inconsistent data fails early.

## Why alternatives exist

Important routes contain meaningful alternatives. Mumbai–Goa includes low-, medium-, and high-cost flights and hotels so an initially attractive ₹38,500 candidate can violate a ₹30,000 budget and be repaired to ₹10,400. Delhi–Shimla includes slower cheap bus travel and faster costlier car travel so a daily-time violation demonstrates an explicit time/cost trade-off. The Chennai–Delhi–Jaipur records support a multi-city itinerary.

Some routes are deliberately infeasible and some hard ceilings fall below every available option. These cases verify that TripMind returns an explicit bounded `INFEASIBLE` outcome instead of inventing inventory or relaxing constraints.

## Provenance and limitations

The dataset was authored specifically for the academic demo. Names containing “Demo” and all prices, schedules, availability, ratings, distances, and durations are illustrative. They are not scraped, live, bookable, or suitable for real travel decisions. External providers are disabled by default, and demo mode performs no hidden travel-data HTTP requests.

To extend the dataset, add coherent alternatives with stable IDs, keep references to known city IDs, preserve deterministic ordering, and run the full data/tool/planning tests plus the evaluation runner.
