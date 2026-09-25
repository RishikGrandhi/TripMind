from datetime import UTC, date, datetime
from decimal import Decimal
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, computed_field, field_validator, model_validator


class DomainModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


Money = Decimal
PositiveMoney = Decimal


class PlanningStatus(StrEnum):
    RECEIVED = "received"
    SEARCHING = "searching"
    CANDIDATE_READY = "candidate_ready"
    VALIDATING = "validating"
    REPLANNING = "replanning"
    COMPLETED = "completed"
    INFEASIBLE = "infeasible"
    FAILED = "failed"


class TransportMode(StrEnum):
    FLIGHT = "flight"
    TRAIN = "train"
    BUS = "bus"
    CAR = "car"
    WALK = "walk"


class ViolationCode(StrEnum):
    BUDGET_EXCEEDED = "budget_exceeded"
    DATE_MISMATCH = "date_mismatch"
    DURATION_EXCEEDED = "duration_exceeded"
    TRANSPORT_UNAVAILABLE = "transport_unavailable"
    CAPACITY_EXCEEDED = "capacity_exceeded"
    MISSING_REQUIRED_ITEM = "missing_required_item"
    DATE_WINDOW_VIOLATION = "date_window_violation"
    TRIP_DURATION_MISMATCH = "trip_duration_mismatch"
    MANDATORY_DESTINATION_MISSING = "mandatory_destination_missing"
    FLIGHT_COST_EXCEEDED = "flight_cost_exceeded"
    HOTEL_COST_EXCEEDED = "hotel_cost_exceeded"
    DAILY_TRAVEL_TIME_EXCEEDED = "daily_travel_time_exceeded"
    ROUTE_INFEASIBLE = "route_infeasible"
    SCHEDULE_INTEGRITY_ERROR = "schedule_integrity_error"


class ItemType(StrEnum):
    FLIGHT = "flight"
    HOTEL = "hotel"
    ACTIVITY = "activity"
    ROUTE = "route"


class ToolCallStatus(StrEnum):
    SUCCEEDED = "succeeded"
    FAILED = "failed"


class AgentTraceStatus(StrEnum):
    SUCCEEDED = "succeeded"
    APPROVED = "approved"
    REJECTED = "rejected"
    FALLBACK = "fallback"
    FAILED = "failed"


class CorrectiveActionType(StrEnum):
    SEARCH_CHEAPER_FLIGHT = "search_cheaper_flight"
    SEARCH_CHEAPER_HOTEL = "search_cheaper_hotel"
    SEARCH_CHEAPER_TRANSPORT = "search_cheaper_transport"
    SEARCH_FASTER_TRANSPORT = "search_faster_transport"
    SELECT_FEASIBLE_ROUTE = "select_feasible_route"
    REMOVE_OPTIONAL_ACTIVITY = "remove_optional_activity"


class ReplanningOutcome(StrEnum):
    FEASIBLE = "feasible"
    INFEASIBLE = "infeasible"
    FAILED = "failed"


class DataSource(StrEnum):
    LOCAL_DEMO = "local_demo"
    SERPAPI = "serpapi"
    STAYINGAPI = "stayingapi"
    GEOAPIFY = "geoapify"
    OPENWEATHER = "openweather"


class PriceSource(StrEnum):
    LIVE_QUOTE = "live_quote"
    LOCAL_DEMO = "local_demo"
    ESTIMATED = "estimated"
    UNKNOWN = "unknown"


class TravelConstraints(DomainModel):
    origin_city_id: str = Field(min_length=1)
    destination_city_id: str = Field(min_length=1)
    additional_destination_city_ids: list[str] = Field(default_factory=list)
    start_date: date
    end_date: date
    travelers: int = Field(default=1, ge=1, le=20)
    total_budget: PositiveMoney = Field(gt=0)
    max_flight_price: PositiveMoney | None = Field(default=None, gt=0)
    max_hotel_price_per_night: PositiveMoney | None = Field(default=None, gt=0)
    max_travel_duration_minutes: int | None = Field(default=None, gt=0)
    allowed_transport_modes: list[TransportMode] = Field(
        default_factory=lambda: [TransportMode.FLIGHT]
    )

    @model_validator(mode="after")
    def dates_and_route_are_valid(self) -> "TravelConstraints":
        if self.end_date <= self.start_date:
            raise ValueError("end_date must be after start_date")
        if self.origin_city_id == self.destination_city_id:
            raise ValueError("origin and destination must be different")
        destinations = [self.destination_city_id, *self.additional_destination_city_ids]
        if self.origin_city_id in destinations:
            raise ValueError("origin cannot appear in the destination sequence")
        if len(destinations) != len(set(destinations)):
            raise ValueError("destination sequence cannot contain duplicates")
        if not self.allowed_transport_modes:
            raise ValueError("at least one transport mode must be allowed")
        return self

    @property
    def destination_city_ids(self) -> list[str]:
        return [self.destination_city_id, *self.additional_destination_city_ids]


class SoftPreferences(DomainModel):
    preferred_transport_modes: list[TransportMode] = Field(default_factory=list)
    preferred_airlines: list[str] = Field(default_factory=list)
    preferred_hotel_amenities: list[str] = Field(default_factory=list)
    preferred_activity_categories: list[str] = Field(default_factory=list)
    prefer_direct_flights: bool = False
    pace: str | None = Field(default=None, pattern="^(relaxed|balanced|packed)$")
    notes: str | None = None


class TravelRequest(DomainModel):
    request_id: str = Field(min_length=1)
    natural_language_request: str = Field(min_length=1)
    constraints: TravelConstraints
    preferences: SoftPreferences = Field(default_factory=SoftPreferences)


class PlanRequest(TravelRequest):
    """API-facing request model; kept explicit for later endpoint evolution."""


class City(DomainModel):
    id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    country_code: str = Field(min_length=2, max_length=2)
    timezone: str = Field(min_length=1)
    airport_code: str | None = Field(default=None, min_length=3, max_length=3)
    latitude: Decimal | None = Field(default=None, ge=-90, le=90)
    longitude: Decimal | None = Field(default=None, ge=-180, le=180)


class FlightOption(DomainModel):
    id: str
    origin_city_id: str
    destination_city_id: str
    airline: str
    flight_number: str
    departure: datetime
    arrival: datetime
    duration_minutes: int = Field(gt=0)
    stops: int = Field(default=0, ge=0, le=4)
    price: PositiveMoney = Field(gt=0)
    available_seats: int | None = Field(default=None, ge=0)
    source: DataSource = DataSource.LOCAL_DEMO
    is_live: bool = False
    is_estimate: bool = False
    price_source: PriceSource = PriceSource.LOCAL_DEMO
    fallback_from: DataSource | None = None

    @model_validator(mode="after")
    def chronological_flight(self) -> "FlightOption":
        if self.arrival <= self.departure:
            raise ValueError("arrival must be after departure")
        return self


class HotelOption(DomainModel):
    id: str
    city_id: str
    name: str
    price_per_night: PositiveMoney = Field(gt=0)
    rating: Decimal | None = Field(default=None, ge=0, le=5)
    amenities: list[str] = Field(default_factory=list)
    available_rooms: int | None = Field(default=None, ge=0)
    source: DataSource = DataSource.LOCAL_DEMO
    is_live: bool = False
    is_estimate: bool = False
    price_source: PriceSource = PriceSource.LOCAL_DEMO
    fallback_from: DataSource | None = None


class ActivityOption(DomainModel):
    id: str
    city_id: str
    name: str
    category: str
    duration_minutes: int | None = Field(default=None, gt=0)
    price: Money | None = Field(default=None, ge=0)
    opening_time: str | None = None
    closing_time: str | None = None
    source: DataSource = DataSource.LOCAL_DEMO
    is_live: bool = False
    is_estimate: bool = False
    price_source: PriceSource = PriceSource.LOCAL_DEMO
    fallback_from: DataSource | None = None

    @model_validator(mode="after")
    def unknown_price_has_honest_provenance(self) -> "ActivityOption":
        if self.price is None and self.price_source != PriceSource.UNKNOWN:
            raise ValueError("an activity without a price requires price_source=unknown")
        return self


class RouteInfo(DomainModel):
    id: str
    origin_city_id: str
    destination_city_id: str
    mode: TransportMode
    duration_minutes: int = Field(gt=0)
    distance_km: Decimal = Field(gt=0)
    estimated_cost: Money = Field(ge=0)
    is_feasible: bool = True
    unavailable_reason: str | None = None
    source: DataSource = DataSource.LOCAL_DEMO
    is_live: bool = False
    is_estimate: bool = False
    price_source: PriceSource = PriceSource.LOCAL_DEMO
    fallback_from: DataSource | None = None

    @model_validator(mode="after")
    def feasibility_has_consistent_reason(self) -> "RouteInfo":
        if self.is_feasible and self.unavailable_reason is not None:
            raise ValueError("a feasible route cannot have an unavailable_reason")
        if not self.is_feasible and not self.unavailable_reason:
            raise ValueError("an infeasible route requires an unavailable_reason")
        return self


class RouteResult(DomainModel):
    origin_city_id: str
    destination_city_id: str
    mode: TransportMode
    feasible: bool
    route: RouteInfo | None = None
    reason: str | None = None
    source: DataSource = DataSource.LOCAL_DEMO
    is_live: bool = False
    fallback_from: DataSource | None = None

    @model_validator(mode="after")
    def result_matches_route(self) -> "RouteResult":
        if self.feasible and (self.route is None or not self.route.is_feasible):
            raise ValueError("a feasible route result requires a feasible route")
        if not self.feasible and not self.reason:
            raise ValueError("an infeasible route result requires a reason")
        return self


class ItineraryItem(DomainModel):
    id: str
    item_type: ItemType
    option_id: str
    title: str
    starts_at: datetime
    ends_at: datetime
    cost: Money = Field(ge=0)
    city_id: str | None = None
    origin_city_id: str | None = None
    destination_city_id: str | None = None
    duration_minutes: int | None = Field(default=None, gt=0)
    notes: str | None = None
    source: DataSource = DataSource.LOCAL_DEMO
    is_live: bool = False
    is_estimate: bool = False
    price_source: PriceSource = PriceSource.LOCAL_DEMO
    fallback_from: DataSource | None = None

    @model_validator(mode="after")
    def chronological_item(self) -> "ItineraryItem":
        if self.ends_at <= self.starts_at:
            raise ValueError("ends_at must be after starts_at")
        return self


class ItineraryDay(DomainModel):
    date: date
    items: list[ItineraryItem] = Field(default_factory=list)


class CostBreakdown(DomainModel):
    flights: Money = Field(default=Decimal("0"), ge=0)
    hotels: Money = Field(default=Decimal("0"), ge=0)
    activities: Money = Field(default=Decimal("0"), ge=0)
    local_transport: Money = Field(default=Decimal("0"), ge=0)
    other: Money = Field(default=Decimal("0"), ge=0)
    currency: str = Field(default="INR", min_length=3, max_length=3)

    @computed_field
    @property
    def total(self) -> Decimal:
        return self.flights + self.hotels + self.activities + self.local_transport + self.other


class WeatherForecast(DomainModel):
    forecast_at: datetime
    temperature_c: Decimal
    feels_like_c: Decimal | None = None
    condition: str
    precipitation_probability: Decimal | None = Field(default=None, ge=0, le=1)
    rain_mm: Decimal | None = Field(default=None, ge=0)
    humidity_percent: int | None = Field(default=None, ge=0, le=100)
    wind_speed_mps: Decimal | None = Field(default=None, ge=0)


class WeatherResult(DomainModel):
    city_id: str
    requested_date: date
    weather_available: bool
    reason: str | None = None
    forecasts: list[WeatherForecast] = Field(default_factory=list)
    source: DataSource = DataSource.OPENWEATHER
    is_live: bool = True

    @model_validator(mode="after")
    def availability_matches_forecasts(self) -> "WeatherResult":
        if self.weather_available and not self.forecasts:
            raise ValueError("available weather requires at least one forecast")
        if not self.weather_available and not self.reason:
            raise ValueError("unavailable weather requires a reason")
        return self


class Itinerary(DomainModel):
    id: str
    days: list[ItineraryDay]
    costs: CostBreakdown


class ConstraintCheck(DomainModel):
    constraint: str
    passed: bool
    expected: str
    actual: str
    violation_code: ViolationCode | None = None

    @model_validator(mode="after")
    def failed_check_has_code(self) -> "ConstraintCheck":
        if not self.passed and self.violation_code is None:
            raise ValueError("a failed constraint check requires a violation_code")
        if self.passed and self.violation_code is not None:
            raise ValueError("a passed constraint check cannot have a violation_code")
        return self


class ConstraintViolation(DomainModel):
    code: ViolationCode
    message: str
    item_id: str | None = None
    details: dict[str, Any] = Field(default_factory=dict)


class ValidationResult(DomainModel):
    is_valid: bool
    checks: list[ConstraintCheck] = Field(default_factory=list)
    violations: list[ConstraintViolation] = Field(default_factory=list)
    validated_at: datetime | None = None

    @computed_field
    @property
    def feasible(self) -> bool:
        return self.is_valid

    @model_validator(mode="after")
    def validity_matches_violations(self) -> "ValidationResult":
        if self.is_valid == bool(self.violations):
            raise ValueError("is_valid must be true exactly when violations is empty")
        return self


class CorrectiveAction(DomainModel):
    action: CorrectiveActionType
    reason: str
    target_id: str | None = None
    target_violation: ViolationCode
    expected_goal: str
    parameters: dict[str, Any] = Field(default_factory=dict)


class ReplanningAttempt(DomainModel):
    attempt_number: int = Field(ge=1)
    triggered_by: list[ViolationCode]
    actions: list[CorrectiveAction]
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    succeeded: bool | None = None
    before_component_id: str | None = None
    after_component_id: str | None = None
    before_value: Money | int | str | None = None
    after_value: Money | int | str | None = None
    cost_effect: Money | None = None
    duration_effect_minutes: int | None = None
    tool_name: str | None = None
    validation_result: ValidationResult | None = None
    outcome: str | None = None
    state_fingerprint: str | None = None


class PreferenceScore(DomainModel):
    total: Decimal = Field(ge=0, le=100)
    components: dict[str, Decimal] = Field(default_factory=dict)
    weights: dict[str, Decimal] = Field(default_factory=dict)
    explanation: list[str] = Field(default_factory=list)

    @field_validator("components")
    @classmethod
    def valid_component_scores(cls, value: dict[str, Decimal]) -> dict[str, Decimal]:
        if any(score < 0 or score > 100 for score in value.values()):
            raise ValueError("preference component scores must be between 0 and 100")
        return value

    @field_validator("weights")
    @classmethod
    def valid_weights(cls, value: dict[str, Decimal]) -> dict[str, Decimal]:
        if any(weight < 0 or weight > 1 for weight in value.values()):
            raise ValueError("preference weights must be between 0 and 1")
        if value and sum(value.values(), Decimal("0")) != Decimal("1"):
            raise ValueError("preference weights must sum to 1")
        return value


class ToolCallRecord(DomainModel):
    id: str
    tool_name: str
    arguments: dict[str, Any] = Field(default_factory=dict)
    status: ToolCallStatus
    result_count: int | None = Field(default=None, ge=0)
    started_at: datetime
    completed_at: datetime | None = None
    error: str | None = None


class AgentTraceRecord(DomainModel):
    step: int = Field(ge=1)
    provider: str = Field(min_length=1)
    action: str = Field(min_length=1)
    reason_code: str | None = None
    tool: str | None = None
    source: DataSource | None = None
    parameters: dict[str, Any] = Field(default_factory=dict)
    status: AgentTraceStatus
    result_summary: str | None = None
    violation: ViolationCode | None = None
    before_component_id: str | None = None
    after_component_id: str | None = None
    before_value: Money | int | str | None = None
    after_value: Money | int | str | None = None


class TripState(DomainModel):
    trip_id: str
    original_request: str
    constraints: TravelConstraints
    preferences: SoftPreferences
    status: PlanningStatus = PlanningStatus.RECEIVED
    flight_candidates: list[FlightOption] = Field(default_factory=list)
    hotel_candidates: list[HotelOption] = Field(default_factory=list)
    activity_candidates: list[ActivityOption] = Field(default_factory=list)
    route_candidates: list[RouteInfo] = Field(default_factory=list)
    weather_results: list[WeatherResult] = Field(default_factory=list)
    initial_itinerary: Itinerary | None = None
    current_itinerary: Itinerary | None = None
    current_validation: ValidationResult | None = None
    validation_history: list[ValidationResult] = Field(default_factory=list)
    replanning_attempts: list[ReplanningAttempt] = Field(default_factory=list)
    tool_call_history: list[ToolCallRecord] = Field(default_factory=list)
    agent_trace: list[AgentTraceRecord] = Field(default_factory=list)
    requested_provider: str = "fallback"
    provider_used: str = "fallback"
    fallback_used: bool = False
    fallback_reason: str | None = None
    agent_steps_used: int = Field(default=0, ge=0)
    preference_score: PreferenceScore | None = None
    final_explanation: str | None = None


class ReplanningResult(DomainModel):
    outcome: ReplanningOutcome
    state: TripState
    termination_reason: str
    attempts_used: int = Field(ge=0)
    tool_calls_used: int = Field(ge=0)
