from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal
from math import ceil

from app.domain.models import (
    ActivityOption,
    FlightOption,
    HotelOption,
    ItemType,
    Itinerary,
    ItineraryDay,
    ItineraryItem,
    RouteInfo,
    SoftPreferences,
    TransportMode,
    TravelConstraints,
    TravelRequest,
    TripState,
)
from app.planning.cost_engine import calculate_cost_breakdown
from app.planning.selection import CandidateSelectionOverrides, TransportOption
from app.tools.registry import ToolRegistry

INDIA_TIMEZONE = timezone(timedelta(hours=5, minutes=30), name="IST")
HOTEL_GUESTS_PER_ROOM = 2
ACTIVITY_LIMIT_BY_PACE = {"relaxed": 1, "balanced": 2, "packed": 3}


class CandidateBuildError(RuntimeError):
    """Raised when required resources cannot form a structurally coherent candidate."""


class CandidateBuilder:
    def __init__(self, tools: ToolRegistry) -> None:
        self._tools = tools

    def build(
        self,
        request: TravelRequest | TripState,
        overrides: CandidateSelectionOverrides | None = None,
    ) -> Itinerary:
        request_id, constraints, preferences = self._request_parts(request)
        destinations = constraints.destination_city_ids
        self._validate_structure(constraints, destinations)
        transition_dates = self._transition_dates(constraints, len(destinations))
        rooms = ceil(constraints.travelers / HOTEL_GUESTS_PER_ROOM)
        items: list[ItineraryItem] = []
        origin = constraints.origin_city_id

        for index, (destination, transition_date) in enumerate(
            zip(destinations, transition_dates, strict=True)
        ):
            transport_item, arrival = self._build_transport_item(
                index=index,
                origin=origin,
                destination=destination,
                travel_date=transition_date,
                constraints=constraints,
                preferences=preferences,
                selected=(overrides.transport_by_segment.get(index) if overrides else None),
            )
            items.append(transport_item)
            segment_end = (
                transition_dates[index + 1]
                if index + 1 < len(transition_dates)
                else constraints.end_date
            )
            hotel = self._select_hotel(
                destination,
                rooms,
                preferences,
                selected=(overrides.hotel_by_segment.get(index) if overrides else None),
            )
            items.extend(
                self._build_hotel_nights(
                    index=index,
                    city_id=destination,
                    hotel=hotel,
                    rooms=rooms,
                    first_night=transition_date,
                    segment_end=segment_end,
                )
            )
            items.extend(
                self._build_activities(
                    index=index,
                    city_id=destination,
                    arrival=arrival,
                    segment_end=segment_end,
                    is_final_destination=index == len(destinations) - 1,
                    travelers=constraints.travelers,
                    preferences=preferences,
                    existing_items=items,
                    selected=(overrides.activities_by_segment.get(index) if overrides else None),
                )
            )
            origin = destination

        days = self._build_days(constraints.start_date, constraints.end_date, items)
        costs = calculate_cost_breakdown(item for day in days for item in day.items)
        return Itinerary(id=f"candidate-{request_id}", days=days, costs=costs)

    @staticmethod
    def _request_parts(
        request: TravelRequest | TripState,
    ) -> tuple[str, TravelConstraints, SoftPreferences]:
        if isinstance(request, TripState):
            return request.trip_id, request.constraints, request.preferences
        return request.request_id, request.constraints, request.preferences

    def _validate_structure(
        self, constraints: TravelConstraints, destinations: list[str]
    ) -> None:
        requested_cities = {constraints.origin_city_id, *destinations}
        missing = sorted(requested_cities - self._tools.city_ids)
        if missing:
            raise CandidateBuildError(f"Unknown city IDs: {', '.join(missing)}")
        trip_nights = (constraints.end_date - constraints.start_date).days
        if trip_nights < len(destinations):
            raise CandidateBuildError(
                "Trip date range must provide at least one hotel night per destination"
            )

    @staticmethod
    def _transition_dates(constraints: TravelConstraints, count: int) -> list[date]:
        nights = (constraints.end_date - constraints.start_date).days
        return [
            constraints.start_date + timedelta(days=(index * nights) // count)
            for index in range(count)
        ]

    def _build_transport_item(
        self,
        *,
        index: int,
        origin: str,
        destination: str,
        travel_date: date,
        constraints: TravelConstraints,
        preferences: SoftPreferences,
        selected: TransportOption | None,
    ) -> tuple[ItineraryItem, datetime]:
        if selected is not None:
            return self._build_selected_transport(
                index=index,
                origin=origin,
                destination=destination,
                travel_date=travel_date,
                travelers=constraints.travelers,
                allowed_modes=constraints.allowed_transport_modes,
                selected=selected,
            )
        attempted: list[str] = []
        for mode in self._ordered_modes(constraints, preferences):
            if mode == TransportMode.FLIGHT:
                flights = self._tools.flights.search_flights(
                    origin,
                    destination,
                    travel_date=travel_date,
                    travelers=constraints.travelers,
                )
                if flights:
                    flight = self._select_flight(flights, preferences)
                    return self._flight_item(index, flight, constraints.travelers), flight.arrival
                attempted.append(f"flight on {travel_date.isoformat()}")
                continue

            result = self._tools.routes.calculate_route(origin, destination, mode)
            if result.feasible and result.route is not None:
                route = result.route
                starts_at = datetime.combine(travel_date, time(8, 0), INDIA_TIMEZONE)
                ends_at = starts_at + timedelta(minutes=route.duration_minutes)
                return self._route_item(index, route, starts_at, ends_at), ends_at
            attempted.append(f"{mode.value}: {result.reason}")

        attempted_text = "; ".join(attempted)
        raise CandidateBuildError(
            f"No transport option from {origin} to {destination} ({attempted_text})"
        )

    @staticmethod
    def _build_selected_transport(
        *,
        index: int,
        origin: str,
        destination: str,
        travel_date: date,
        travelers: int,
        allowed_modes: list[TransportMode],
        selected: TransportOption,
    ) -> tuple[ItineraryItem, datetime]:
        if selected.origin_city_id != origin or selected.destination_city_id != destination:
            raise CandidateBuildError(
                f"Selected transport {selected.id} does not match {origin} to {destination}"
            )
        if isinstance(selected, FlightOption):
            if TransportMode.FLIGHT not in allowed_modes:
                raise CandidateBuildError(f"Selected flight {selected.id} is not an allowed mode")
            if selected.departure.date() != travel_date:
                raise CandidateBuildError(
                    f"Selected flight {selected.id} does not depart on {travel_date}"
                )
            if selected.available_seats < travelers:
                raise CandidateBuildError(f"Selected flight {selected.id} lacks required seats")
            return CandidateBuilder._flight_item(index, selected, travelers), selected.arrival

        if selected.mode not in allowed_modes:
            raise CandidateBuildError(f"Selected route mode {selected.mode} is not allowed")
        if not selected.is_feasible:
            raise CandidateBuildError(f"Selected route {selected.id} is explicitly infeasible")
        starts_at = datetime.combine(travel_date, time(8, 0), INDIA_TIMEZONE)
        ends_at = starts_at + timedelta(minutes=selected.duration_minutes)
        return CandidateBuilder._route_item(index, selected, starts_at, ends_at), ends_at

    @staticmethod
    def _ordered_modes(
        constraints: TravelConstraints, preferences: SoftPreferences
    ) -> list[TransportMode]:
        allowed = constraints.allowed_transport_modes
        preferred = [mode for mode in preferences.preferred_transport_modes if mode in allowed]
        return list(dict.fromkeys([*preferred, *allowed]))

    @staticmethod
    def _select_flight(
        flights: list[FlightOption], preferences: SoftPreferences
    ) -> FlightOption:
        preferred_airlines = {
            airline.casefold(): index
            for index, airline in enumerate(preferences.preferred_airlines)
        }
        no_preference_rank = len(preferred_airlines) + 1
        return min(
            flights,
            key=lambda flight: (
                preferred_airlines.get(flight.airline.casefold(), no_preference_rank),
                flight.stops,
                flight.duration_minutes,
                flight.price,
                flight.departure,
                flight.id,
            ),
        )

    @staticmethod
    def _flight_item(index: int, flight: FlightOption, travelers: int) -> ItineraryItem:
        return ItineraryItem(
            id=f"item-transport-{index + 1:02d}",
            item_type=ItemType.FLIGHT,
            option_id=flight.id,
            title=f"{flight.airline} {flight.flight_number}",
            starts_at=flight.departure,
            ends_at=flight.arrival,
            cost=flight.price * travelers,
            origin_city_id=flight.origin_city_id,
            destination_city_id=flight.destination_city_id,
            duration_minutes=flight.duration_minutes,
            notes=f"{flight.stops} stop(s); fare for {travelers} traveler(s)",
        )

    @staticmethod
    def _route_item(
        index: int, route: RouteInfo, starts_at: datetime, ends_at: datetime
    ) -> ItineraryItem:
        return ItineraryItem(
            id=f"item-transport-{index + 1:02d}",
            item_type=ItemType.ROUTE,
            option_id=route.id,
            title=f"{route.mode.value.title()} from {route.origin_city_id} to {route.destination_city_id}",
            starts_at=starts_at,
            ends_at=ends_at,
            cost=route.estimated_cost,
            origin_city_id=route.origin_city_id,
            destination_city_id=route.destination_city_id,
            duration_minutes=route.duration_minutes,
            notes=f"{route.distance_km} km; stored local demo route",
        )

    def _select_hotel(
        self,
        city_id: str,
        rooms: int,
        preferences: SoftPreferences,
        selected: HotelOption | None,
    ) -> HotelOption:
        if selected is not None:
            if selected.city_id != city_id:
                raise CandidateBuildError(
                    f"Selected hotel {selected.id} is not located in {city_id}"
                )
            if selected.available_rooms < rooms:
                raise CandidateBuildError(f"Selected hotel {selected.id} lacks required rooms")
            return selected
        hotels = self._tools.hotels.search_hotels(city_id, rooms=rooms)
        if not hotels:
            raise CandidateBuildError(f"No usable hotel option in {city_id} for {rooms} room(s)")
        preferred = {amenity.casefold() for amenity in preferences.preferred_hotel_amenities}

        def rank(hotel: HotelOption) -> tuple[object, ...]:
            amenities = {amenity.casefold() for amenity in hotel.amenities}
            matches = len(preferred & amenities)
            all_match = bool(preferred) and preferred.issubset(amenities)
            return (-int(all_match), -matches, -hotel.rating, hotel.price_per_night, hotel.id)

        return min(hotels, key=rank)

    @staticmethod
    def _build_hotel_nights(
        *,
        index: int,
        city_id: str,
        hotel: HotelOption,
        rooms: int,
        first_night: date,
        segment_end: date,
    ) -> list[ItineraryItem]:
        nights: list[ItineraryItem] = []
        current = first_night
        while current < segment_end:
            starts_at = datetime.combine(current, time(22, 0), INDIA_TIMEZONE)
            ends_at = datetime.combine(current + timedelta(days=1), time(7, 0), INDIA_TIMEZONE)
            nights.append(
                ItineraryItem(
                    id=f"item-hotel-{index + 1:02d}-{current.isoformat()}",
                    item_type=ItemType.HOTEL,
                    option_id=hotel.id,
                    title=f"Overnight at {hotel.name}",
                    starts_at=starts_at,
                    ends_at=ends_at,
                    cost=hotel.price_per_night * rooms,
                    city_id=city_id,
                    duration_minutes=540,
                    notes=f"{rooms} room(s), one night; checkout at 07:00 in demo schedule",
                )
            )
            current += timedelta(days=1)
        return nights

    def _build_activities(
        self,
        *,
        index: int,
        city_id: str,
        arrival: datetime,
        segment_end: date,
        is_final_destination: bool,
        travelers: int,
        preferences: SoftPreferences,
        existing_items: list[ItineraryItem],
        selected: list[ActivityOption] | None,
    ) -> list[ItineraryItem]:
        if selected is not None and any(activity.city_id != city_id for activity in selected):
            raise CandidateBuildError(f"Selected activity is not located in {city_id}")
        options = list(selected) if selected is not None else self._tools.activities.search_activities(city_id)
        preferred_categories = {
            category.casefold(): rank
            for rank, category in enumerate(preferences.preferred_activity_categories)
        }
        fallback_rank = len(preferred_categories) + 1
        options.sort(
            key=lambda activity: (
                preferred_categories.get(activity.category.casefold(), fallback_rank),
                activity.id,
            )
        )
        limit = ACTIVITY_LIMIT_BY_PACE.get(preferences.pace or "balanced", 2)
        available_end = segment_end if is_final_destination else segment_end - timedelta(days=1)
        scheduled: list[ItineraryItem] = []
        for activity in options:
            if len(scheduled) >= limit:
                break
            item = self._schedule_activity(
                index=index,
                sequence=len(scheduled),
                city_id=city_id,
                activity=activity,
                first_date=arrival.date(),
                last_date=available_end,
                arrival=arrival,
                travelers=travelers,
                occupied=[*existing_items, *scheduled],
            )
            if item is not None:
                scheduled.append(item)
        return scheduled

    @staticmethod
    def _schedule_activity(
        *,
        index: int,
        sequence: int,
        city_id: str,
        activity: ActivityOption,
        first_date: date,
        last_date: date,
        arrival: datetime,
        travelers: int,
        occupied: list[ItineraryItem],
    ) -> ItineraryItem | None:
        current = first_date
        opening = time.fromisoformat(activity.opening_time or "10:00")
        closing = time.fromisoformat(activity.closing_time or "20:00")
        while current <= last_date:
            starts_at = datetime.combine(current, opening, INDIA_TIMEZONE)
            ends_at = starts_at + timedelta(minutes=activity.duration_minutes)
            closes_at = datetime.combine(current, closing, INDIA_TIMEZONE)
            if starts_at >= arrival and ends_at <= closes_at and not any(
                _overlaps(starts_at, ends_at, item.starts_at, item.ends_at)
                for item in occupied
            ):
                return ItineraryItem(
                    id=f"item-activity-{index + 1:02d}-{sequence + 1:02d}",
                    item_type=ItemType.ACTIVITY,
                    option_id=activity.id,
                    title=activity.name,
                    starts_at=starts_at,
                    ends_at=ends_at,
                    cost=activity.price * travelers,
                    city_id=city_id,
                    duration_minutes=activity.duration_minutes,
                    notes=f"{activity.category}; price for {travelers} traveler(s)",
                )
            current += timedelta(days=1)
        return None

    @staticmethod
    def _build_days(
        start_date: date, end_date: date, items: list[ItineraryItem]
    ) -> list[ItineraryDay]:
        by_date: dict[date, list[ItineraryItem]] = {}
        for item in items:
            by_date.setdefault(item.starts_at.date(), []).append(item)
        days: list[ItineraryDay] = []
        current = start_date
        while current <= end_date:
            day_items = sorted(by_date.get(current, []), key=lambda item: (item.starts_at, item.id))
            days.append(ItineraryDay(date=current, items=day_items))
            current += timedelta(days=1)
        return days


def _overlaps(
    first_start: datetime,
    first_end: datetime,
    second_start: datetime,
    second_end: datetime,
) -> bool:
    return first_start < second_end and second_start < first_end
