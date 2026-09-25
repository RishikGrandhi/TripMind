from __future__ import annotations

import re
from datetime import date, datetime
from decimal import Decimal

from app.domain.models import TransportMode
from app.extraction.models import ExtractedTravelIntent
from app.llm.base import ExtractionContext


class DeterministicFallbackProvider:
    name = "fallback"

    def extract_travel_request(
        self, text: str, context: ExtractionContext
    ) -> ExtractedTravelIntent:
        query = " ".join(text.strip().split())
        city_ids = _extract_cities(query, context)
        origin, destinations = _split_route(query, city_ids, context)
        dates = _extract_dates(query)
        duration = _first_int(
            query,
            r"\b(?:for\s+)?(\d{1,2})\s*(?:-|\s)?days?\b",
        )
        assumptions: list[str] = []
        warnings: list[str] = []

        start_date = dates[0] if dates else None
        end_date = dates[1] if len(dates) > 1 else None
        if not dates and duration is not None:
            start_date = context.demo_reference_date
            end_date = date.fromordinal(start_date.toordinal() + duration - 1)
            assumptions.append(
                "No start date was provided; used configured DEMO_REFERENCE_DATE "
                f"{context.demo_reference_date.isoformat()}."
            )
        elif len(dates) == 1 and duration is not None:
            end_date = date.fromordinal(start_date.toordinal() + duration - 1)
            assumptions.append("Derived end date from the explicit start date and duration.")
        elif len(dates) >= 2:
            inclusive_days = (end_date - start_date).days + 1
            if duration is not None and duration != inclusive_days:
                warnings.append(
                    f"Explicit dates imply {inclusive_days} days; ignored stated duration {duration}."
                )
            duration = inclusive_days

        travelers = _first_int(
            query,
            r"\b(\d{1,2})\s+(?:travell?ers?|people|persons?|adults?)\b",
        )
        if travelers is None:
            travelers = 1
            assumptions.append("No traveler count was provided; assumed 1 traveler.")

        money = _extract_money_constraints(query)
        max_travel = _extract_daily_travel_limit(query)
        mentioned_modes = _extract_modes(query)
        direct = bool(re.search(r"\bdirect\s+flights?\b", query, re.IGNORECASE))
        if direct and TransportMode.FLIGHT not in mentioned_modes:
            mentioned_modes.insert(0, TransportMode.FLIGHT)
        allowed_modes = list(mentioned_modes)
        if not allowed_modes:
            allowed_modes = [TransportMode.FLIGHT]
            assumptions.append("No transport mode was provided; used flight for the demo search.")

        hotel_amenities = _extract_hotel_amenities(query)
        if re.search(r"\b(?:good|comfortable)\s+hotel\b", query, re.IGNORECASE) and not hotel_amenities:
            warnings.append(
                "The qualitative hotel preference was retained as a note; no amenity was invented."
            )

        missing: list[str] = []
        if origin is None:
            missing.append("origin")
        if not destinations:
            missing.append("destination")
        if start_date is None or end_date is None:
            missing.append("travel_dates_or_duration")
        if money["total_budget"] is None:
            missing.append("total_budget")

        known_ids = set(context.city_name_to_id.values())
        unknown_ids = [item for item in [origin, *destinations] if item and item not in known_ids]
        if unknown_ids:
            warnings.append(f"Unsupported city identifiers: {', '.join(unknown_ids)}.")

        return ExtractedTravelIntent(
            origin_city_id=origin,
            destination_city_ids=destinations,
            start_date=start_date,
            end_date=end_date,
            duration_days=duration,
            travelers=travelers,
            total_budget=money["total_budget"],
            max_flight_price=money["max_flight_price"],
            max_hotel_price_per_night=money["max_hotel_price_per_night"],
            max_travel_duration_minutes=max_travel,
            allowed_transport_modes=allowed_modes,
            preferred_transport_modes=mentioned_modes,
            preferred_airlines=_extract_airlines(query, context),
            preferred_hotel_amenities=hotel_amenities,
            preferred_activity_categories=_extract_activity_categories(query),
            prefer_direct_flights=direct,
            pace=_extract_pace(query),
            notes=query if re.search(r"\b(?:good|comfortable)\s+hotel\b", query, re.IGNORECASE) else None,
            assumptions=assumptions,
            warnings=warnings,
            missing_required_fields=missing,
        )


def _extract_cities(query: str, context: ExtractionContext) -> list[tuple[int, str]]:
    matches: list[tuple[int, str]] = []
    for name, city_id in sorted(
        context.city_name_to_id.items(), key=lambda item: (-len(item[0]), item[0])
    ):
        for match in re.finditer(rf"\b{re.escape(name)}\b", query, re.IGNORECASE):
            matches.append((match.start(), city_id))
    ordered: list[tuple[int, str]] = []
    seen: set[str] = set()
    for position, city_id in sorted(matches):
        if city_id not in seen:
            ordered.append((position, city_id))
            seen.add(city_id)
    return ordered


def _split_route(
    query: str,
    city_matches: list[tuple[int, str]],
    context: ExtractionContext,
) -> tuple[str | None, list[str]]:
    if not city_matches:
        return None, []
    if len(city_matches) == 1:
        position, city_id = city_matches[0]
        city_name = next(
            name for name, known_id in context.city_name_to_id.items() if known_id == city_id
        )
        prefix = query[:position].casefold()
        suffix = query[position + len(city_name) :].casefold()
        if re.search(r"\bfrom\s*$", prefix) or re.match(r"\s+to\b", suffix):
            return city_id, []
        return None, [city_id]
    from_match = re.search(r"\bfrom\b", query, re.IGNORECASE)
    origin_pair = city_matches[0]
    if from_match:
        candidates = [item for item in city_matches if item[0] > from_match.end()]
        if candidates:
            origin_pair = candidates[0]
    destinations = [city_id for _, city_id in city_matches if city_id != origin_pair[1]]
    return origin_pair[1], destinations


def _extract_dates(query: str) -> list[date]:
    patterns = [
        (r"\b\d{4}-\d{1,2}-\d{1,2}\b", ("%Y-%m-%d",)),
        (r"\b\d{1,2}[/-]\d{1,2}[/-]\d{4}\b", ("%d/%m/%Y", "%d-%m-%Y")),
        (
            r"\b\d{1,2}\s+(?:Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|Jul(?:y)?|Aug(?:ust)?|Sep(?:tember)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)\s+\d{4}\b",
            ("%d %B %Y", "%d %b %Y"),
        ),
        (
            r"\b(?:Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|Jul(?:y)?|Aug(?:ust)?|Sep(?:tember)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)\s+\d{1,2},?\s+\d{4}\b",
            ("%B %d, %Y", "%B %d %Y", "%b %d, %Y", "%b %d %Y"),
        ),
    ]
    found: list[tuple[int, date]] = []
    occupied: list[tuple[int, int]] = []
    for pattern, formats in patterns:
        for match in re.finditer(pattern, query, re.IGNORECASE):
            if any(match.start() < end and start < match.end() for start, end in occupied):
                continue
            parsed = _parse_date(match.group(0), formats)
            if parsed is not None:
                found.append((match.start(), parsed))
                occupied.append(match.span())
    return [value for _, value in sorted(found)]


def _parse_date(value: str, formats: tuple[str, ...]) -> date | None:
    normalized = " ".join(value.split())
    for format_string in formats:
        try:
            return datetime.strptime(normalized, format_string).date()
        except ValueError:
            continue
    return None


def _extract_money_constraints(query: str) -> dict[str, Decimal | None]:
    values: dict[str, Decimal | None] = {
        "total_budget": None,
        "max_flight_price": None,
        "max_hotel_price_per_night": None,
    }
    pattern = re.compile(
        r"(?:₹\s*|\b(?:inr|rs\.?|rupees?)\s*)?"
        r"(?P<number>\d[\d,]*(?:\.\d+)?)\s*(?P<suffix>k)?"
        r"(?:\s*(?:inr|rs\.?|rupees?))?\b",
        re.IGNORECASE,
    )
    for match in pattern.finditer(query):
        token = match.group(0)
        number = match.group("number")
        suffix = match.group("suffix")
        has_currency = bool(re.search(r"₹|\binr\b|\brs\.?|rupees?", token, re.IGNORECASE))
        if not has_currency and not suffix:
            continue
        amount = Decimal(number.replace(",", ""))
        if suffix:
            amount *= Decimal("1000")
        before = query[max(0, match.start() - 65) : match.start()].casefold()
        after = query[match.end() : min(len(query), match.end() + 45)].casefold()
        context = f"{before} {after}"
        if "hotel" in before or "per night" in after:
            values["max_hotel_price_per_night"] = amount
        elif "flight" in before or "airfare" in before:
            values["max_flight_price"] = amount
        elif re.search(r"budget|under|within|up to|maximum|max(?:imum)? budget", context):
            values["total_budget"] = amount
    return values


def _extract_daily_travel_limit(query: str) -> int | None:
    match = re.search(
        r"(?:more than|over|maximum(?: of)?|max(?:imum)?|up to|under|within)\s+"
        r"(\d+(?:\.\d+)?)\s*(hours?|hrs?|minutes?|mins?)\s*(?:per|a)\s+day",
        query,
        re.IGNORECASE,
    )
    if not match:
        return None
    value = Decimal(match.group(1))
    if match.group(2).casefold().startswith(("hour", "hr")):
        value *= Decimal("60")
    return int(value)


def _extract_modes(query: str) -> list[TransportMode]:
    aliases = {
        TransportMode.FLIGHT: r"\b(?:flight|flights|flying|plane)\b",
        TransportMode.TRAIN: r"\b(?:train|trains|rail)\b",
        TransportMode.BUS: r"\b(?:bus|buses|coach)\b",
        TransportMode.CAR: r"\b(?:car|drive|driving)\b",
        TransportMode.WALK: r"\b(?:walk|walking)\b",
    }
    return [mode for mode, pattern in aliases.items() if re.search(pattern, query, re.IGNORECASE)]


def _extract_hotel_amenities(query: str) -> list[str]:
    aliases = {
        "wifi": r"\bwi-?fi\b",
        "breakfast": r"\bbreakfast\b",
        "pool": r"\b(?:pool|swimming pool)\b",
        "spa": r"\bspa\b",
        "parking": r"\bparking\b",
        "beach_access": r"\bbeach access\b",
        "sea_view": r"\b(?:sea|ocean) view\b",
        "heritage": r"\bheritage hotel\b",
    }
    return [name for name, pattern in aliases.items() if re.search(pattern, query, re.IGNORECASE)]


def _extract_activity_categories(query: str) -> list[str]:
    aliases = {
        "nature": r"\b(?:nature|beach|beaches|outdoor|sunset)\b",
        "culture": r"\b(?:culture|cultural|heritage|history|historical)\b",
        "food": r"\b(?:food|cuisine|culinary)\b",
        "museum": r"\b(?:museum|museums)\b",
        "leisure": r"\b(?:leisure|cruise|relaxation)\b",
    }
    return [name for name, pattern in aliases.items() if re.search(pattern, query, re.IGNORECASE)]


def _extract_airlines(query: str, context: ExtractionContext) -> list[str]:
    return [
        airline
        for airline in context.known_airlines
        if re.search(rf"\b{re.escape(airline)}\b", query, re.IGNORECASE)
    ]


def _extract_pace(query: str) -> str | None:
    for pace in ("relaxed", "balanced", "packed"):
        if re.search(rf"\b{pace}\b", query, re.IGNORECASE):
            return pace
    return None


def _first_int(query: str, pattern: str) -> int | None:
    match = re.search(pattern, query, re.IGNORECASE)
    return int(match.group(1)) if match else None
