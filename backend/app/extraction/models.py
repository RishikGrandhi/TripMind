from datetime import date
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.domain.models import TransportMode, TravelRequest, TripState


class ExtractionModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ExtractedTravelIntent(ExtractionModel):
    origin_city_id: str | None = None
    destination_city_ids: list[str] = Field(default_factory=list)
    start_date: date | None = None
    end_date: date | None = None
    duration_days: int | None = Field(default=None, ge=2, le=30)
    travelers: int = Field(default=1, ge=1, le=20)
    total_budget: Decimal | None = Field(default=None, gt=0)
    max_flight_price: Decimal | None = Field(default=None, gt=0)
    max_hotel_price_per_night: Decimal | None = Field(default=None, gt=0)
    max_travel_duration_minutes: int | None = Field(default=None, gt=0)
    allowed_transport_modes: list[TransportMode] = Field(default_factory=list)
    preferred_transport_modes: list[TransportMode] = Field(default_factory=list)
    preferred_airlines: list[str] = Field(default_factory=list)
    preferred_hotel_amenities: list[str] = Field(default_factory=list)
    preferred_activity_categories: list[str] = Field(default_factory=list)
    prefer_direct_flights: bool = False
    pace: str | None = Field(default=None, pattern="^(relaxed|balanced|packed)$")
    notes: str | None = None
    assumptions: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    missing_required_fields: list[str] = Field(default_factory=list)


class NaturalLanguagePlanRequest(ExtractionModel):
    query: str = Field(min_length=1, max_length=4000)

    @field_validator("query")
    @classmethod
    def non_blank_query(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("query must not be empty")
        return normalized


class ExtractionMetadata(ExtractionModel):
    requested_provider: str
    provider_used: str
    fallback_used: bool
    fallback_reason: str | None = None
    assumptions: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


class PlanningSourceMetadata(ExtractionModel):
    planning: str = "deterministic_coordinator"
    travel_source: str = "local_demo_dataset"
    validation: str = "deterministic"
    replanning: str = "deterministic_policy"


class NaturalLanguagePlanResponse(ExtractionModel):
    original_query: str
    extracted_request: TravelRequest
    extraction: ExtractionMetadata
    sources: PlanningSourceMetadata = Field(default_factory=PlanningSourceMetadata)
    result: TripState
