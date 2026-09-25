from __future__ import annotations

from datetime import timedelta
from hashlib import sha256

from pydantic import ValidationError

from app.core.config import LLMProviderName, Settings, get_settings
from app.domain.models import SoftPreferences, TransportMode, TravelConstraints, TravelRequest
from app.extraction.models import (
    ExtractedTravelIntent,
    ExtractionMetadata,
    NaturalLanguagePlanResponse,
)
from app.llm import (
    DeterministicFallbackProvider,
    ExtractionContext,
    LLMProvider,
    LLMProviderError,
    OllamaProvider,
)
from app.planning.coordinator import PlanningCoordinator, create_planning_coordinator
from app.tools.local_data import LocalDataCatalog, load_catalog
from app.tools.registry import DEFAULT_DATA_DIR


class ConstraintExtractionError(ValueError):
    def __init__(
        self,
        message: str,
        *,
        missing_fields: list[str] | None = None,
        warnings: list[str] | None = None,
        requested_provider: str = "fallback",
        provider_used: str = "fallback",
        fallback_used: bool = False,
        fallback_reason: str | None = None,
    ) -> None:
        super().__init__(message)
        self.missing_fields = missing_fields or []
        self.warnings = warnings or []
        self.requested_provider = requested_provider
        self.provider_used = provider_used
        self.fallback_used = fallback_used
        self.fallback_reason = fallback_reason

    def as_detail(self) -> dict[str, object]:
        return {
            "code": "clarification_required",
            "message": str(self),
            "missing_fields": self.missing_fields,
            "warnings": self.warnings,
            "requested_provider": self.requested_provider,
            "provider_used": self.provider_used,
            "fallback_used": self.fallback_used,
            "fallback_reason": self.fallback_reason,
        }


class NaturalLanguagePlanningService:
    def __init__(
        self,
        *,
        settings: Settings,
        catalog: LocalDataCatalog,
        coordinator: PlanningCoordinator,
        primary_provider: LLMProvider | None = None,
        fallback_provider: LLMProvider | None = None,
    ) -> None:
        self._settings = settings
        self._catalog = catalog
        self._coordinator = coordinator
        self._fallback = fallback_provider or DeterministicFallbackProvider()
        self._context = ExtractionContext(
            city_name_to_id={city.name.casefold(): city.id for city in catalog.cities},
            known_airlines=tuple(sorted({flight.airline for flight in catalog.flights})),
            demo_reference_date=settings.demo_reference_date,
        )
        self._primary = primary_provider or self._create_primary_provider()

    def plan(self, query: str) -> NaturalLanguagePlanResponse:
        requested = self._settings.llm_provider.value
        fallback_used = False
        fallback_reason: str | None = None
        provider_used = self._primary.name

        try:
            intent = self._primary.extract_travel_request(query, self._context)
            intent = self._normalize_intent(intent)
            travel_request = self._to_travel_request(query, intent)
        except (LLMProviderError, ConstraintExtractionError, ValidationError) as exc:
            if self._settings.llm_provider != LLMProviderName.OLLAMA:
                if isinstance(exc, ConstraintExtractionError):
                    raise
                raise ConstraintExtractionError(
                    "The deterministic extractor could not validate this request.",
                    warnings=[str(exc)],
                ) from exc
            fallback_used = True
            fallback_reason = _provider_failure_reason(exc)
            provider_used = self._fallback.name
            try:
                intent = self._fallback.extract_travel_request(query, self._context)
                intent = self._normalize_intent(intent)
                travel_request = self._to_travel_request(query, intent)
            except (LLMProviderError, ConstraintExtractionError, ValidationError) as fallback_exc:
                if isinstance(fallback_exc, ConstraintExtractionError):
                    raise ConstraintExtractionError(
                        str(fallback_exc),
                        missing_fields=fallback_exc.missing_fields,
                        warnings=[*fallback_exc.warnings, f"Ollama fallback reason: {fallback_reason}"],
                        requested_provider=requested,
                        provider_used=provider_used,
                        fallback_used=True,
                        fallback_reason=fallback_reason,
                    ) from fallback_exc
                raise ConstraintExtractionError(
                    "Neither Ollama nor the deterministic fallback produced a valid request.",
                    warnings=[str(fallback_exc)],
                    requested_provider=requested,
                    provider_used=provider_used,
                    fallback_used=True,
                    fallback_reason=fallback_reason,
                ) from fallback_exc

        warnings = list(intent.warnings)
        if fallback_used:
            warnings.append(f"Ollama extraction failed; deterministic fallback used ({fallback_reason}).")
        state = self._coordinator.plan(travel_request)
        return NaturalLanguagePlanResponse(
            original_query=query,
            extracted_request=travel_request,
            extraction=ExtractionMetadata(
                requested_provider=requested,
                provider_used=provider_used,
                fallback_used=fallback_used,
                fallback_reason=fallback_reason,
                assumptions=intent.assumptions,
                warnings=warnings,
            ),
            result=state,
        )

    def extract(self, query: str) -> tuple[TravelRequest, ExtractedTravelIntent]:
        """Deterministic helper used by tests and non-HTTP callers in fallback mode."""
        intent = self._fallback.extract_travel_request(query, self._context)
        intent = self._normalize_intent(intent)
        return self._to_travel_request(query, intent), intent

    def _normalize_intent(self, intent: ExtractedTravelIntent) -> ExtractedTravelIntent:
        normalized = intent.model_copy(deep=True)
        if (
            normalized.start_date is None
            and normalized.end_date is None
            and normalized.duration_days is not None
        ):
            normalized.start_date = self._settings.demo_reference_date
            normalized.end_date = normalized.start_date + timedelta(
                days=normalized.duration_days - 1
            )
            _append_unique(
                normalized.assumptions,
                "No start date was provided; used configured DEMO_REFERENCE_DATE "
                f"{self._settings.demo_reference_date.isoformat()}.",
            )
        elif (
            normalized.start_date is not None
            and normalized.end_date is None
            and normalized.duration_days is not None
        ):
            normalized.end_date = normalized.start_date + timedelta(
                days=normalized.duration_days - 1
            )
            _append_unique(
                normalized.assumptions,
                "Derived end date from the explicit start date and duration.",
            )
        if not normalized.allowed_transport_modes:
            normalized.allowed_transport_modes = [TransportMode.FLIGHT]
            _append_unique(
                normalized.assumptions,
                "No transport mode was provided; used flight for the demo search.",
            )
        if (
            normalized.prefer_direct_flights
            and TransportMode.FLIGHT not in normalized.preferred_transport_modes
        ):
            normalized.preferred_transport_modes.insert(0, TransportMode.FLIGHT)
        if "travelers" not in intent.model_fields_set:
            _append_unique(
                normalized.assumptions,
                "No traveler count was provided; assumed 1 traveler.",
            )
        return normalized

    def _create_primary_provider(self) -> LLMProvider:
        if self._settings.llm_provider == LLMProviderName.FALLBACK:
            return self._fallback
        return OllamaProvider(
            base_url=self._settings.ollama_base_url,
            model=self._settings.ollama_model,
            timeout_seconds=self._settings.ollama_timeout_seconds,
        )

    def _to_travel_request(
        self, query: str, intent: ExtractedTravelIntent
    ) -> TravelRequest:
        missing = list(intent.missing_required_fields)
        if not intent.origin_city_id:
            missing.append("origin")
        if not intent.destination_city_ids:
            missing.append("destination")
        if not intent.start_date or not intent.end_date:
            missing.append("travel_dates_or_duration")
        if intent.total_budget is None:
            missing.append("total_budget")
        missing = list(dict.fromkeys(missing))
        known_ids = {city.id for city in self._catalog.cities}
        requested_ids = [
            item
            for item in [intent.origin_city_id, *intent.destination_city_ids]
            if item is not None
        ]
        unknown = sorted(set(requested_ids) - known_ids)
        if unknown:
            missing.append("supported_city")
        if missing:
            supported = ", ".join(city.name for city in self._catalog.cities)
            warnings = list(intent.warnings)
            if unknown:
                warnings.append(f"Unknown city IDs: {', '.join(unknown)}.")
            raise ConstraintExtractionError(
                "Please clarify the missing or unsupported travel details. "
                f"Supported demo cities: {supported}.",
                missing_fields=list(dict.fromkeys(missing)),
                warnings=warnings,
                requested_provider=self._settings.llm_provider.value,
                provider_used=self._primary.name,
            )
        try:
            constraints = TravelConstraints(
                origin_city_id=intent.origin_city_id,
                destination_city_id=intent.destination_city_ids[0],
                additional_destination_city_ids=intent.destination_city_ids[1:],
                start_date=intent.start_date,
                end_date=intent.end_date,
                travelers=intent.travelers,
                total_budget=intent.total_budget,
                max_flight_price=intent.max_flight_price,
                max_hotel_price_per_night=intent.max_hotel_price_per_night,
                max_travel_duration_minutes=intent.max_travel_duration_minutes,
                allowed_transport_modes=intent.allowed_transport_modes,
            )
            preferences = SoftPreferences(
                preferred_transport_modes=intent.preferred_transport_modes,
                preferred_airlines=intent.preferred_airlines,
                preferred_hotel_amenities=intent.preferred_hotel_amenities,
                preferred_activity_categories=intent.preferred_activity_categories,
                prefer_direct_flights=intent.prefer_direct_flights,
                pace=intent.pace,
                notes=intent.notes,
            )
            return TravelRequest(
                request_id=f"nl-{sha256(query.encode('utf-8')).hexdigest()[:12]}",
                natural_language_request=query,
                constraints=constraints,
                preferences=preferences,
            )
        except ValidationError as exc:
            raise ConstraintExtractionError(
                "The extracted values conflict with the planning request schema.",
                warnings=[*intent.warnings, str(exc)],
                requested_provider=self._settings.llm_provider.value,
                provider_used=self._primary.name,
            ) from exc


def create_natural_language_planning_service(
    settings: Settings | None = None,
    *,
    catalog: LocalDataCatalog | None = None,
    coordinator: PlanningCoordinator | None = None,
    primary_provider: LLMProvider | None = None,
) -> NaturalLanguagePlanningService:
    configured = settings or get_settings()
    local_catalog = catalog or load_catalog(DEFAULT_DATA_DIR)
    planning_coordinator = coordinator or create_planning_coordinator(
        configured, catalog=local_catalog
    )
    return NaturalLanguagePlanningService(
        settings=configured,
        catalog=local_catalog,
        coordinator=planning_coordinator,
        primary_provider=primary_provider,
    )


def _provider_failure_reason(exc: Exception) -> str:
    if isinstance(exc, LLMProviderError):
        return exc.code
    if isinstance(exc, ConstraintExtractionError):
        return "invalid_extraction"
    return "invalid_schema"


def _append_unique(items: list[str], value: str) -> None:
    if value not in items:
        items.append(value)
