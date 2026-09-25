from pydantic import Field

from app.domain.models import (
    ActivityOption,
    DomainModel,
    FlightOption,
    HotelOption,
    Itinerary,
    ItemType,
    RouteInfo,
    TravelConstraints,
)
from app.tools.local_data import LocalDataCatalog

TransportOption = FlightOption | RouteInfo


class CandidateSelectionOverrides(DomainModel):
    transport_by_segment: dict[int, TransportOption] = Field(default_factory=dict)
    hotel_by_segment: dict[int, HotelOption] = Field(default_factory=dict)
    activities_by_segment: dict[int, list[ActivityOption]] = Field(default_factory=dict)


def selection_overrides_from_itinerary(
    itinerary: Itinerary,
    constraints: TravelConstraints,
    catalog: LocalDataCatalog,
) -> CandidateSelectionOverrides:
    flights = {item.id: item for item in catalog.flights}
    routes = {item.id: item for item in catalog.routes}
    hotels = {item.id: item for item in catalog.hotels}
    activities = {item.id: item for item in catalog.activities}
    items = [item for day in itinerary.days for item in day.items]
    transport_items = sorted(
        (
            item
            for item in items
            if item.item_type in {ItemType.FLIGHT, ItemType.ROUTE}
        ),
        key=lambda item: (item.starts_at, item.id),
    )
    if len(transport_items) != len(constraints.destination_city_ids):
        raise ValueError("Cannot derive one transport selection per destination segment")

    transport_by_segment: dict[int, TransportOption] = {}
    hotel_by_segment: dict[int, HotelOption] = {}
    activities_by_segment: dict[int, list[ActivityOption]] = {}
    for index, destination in enumerate(constraints.destination_city_ids):
        transport_item = transport_items[index]
        option = (
            flights.get(transport_item.option_id)
            if transport_item.item_type == ItemType.FLIGHT
            else routes.get(transport_item.option_id)
        )
        if option is None:
            raise ValueError(f"Unknown transport option {transport_item.option_id}")
        transport_by_segment[index] = option

        hotel_ids = list(
            dict.fromkeys(
                item.option_id
                for item in items
                if item.item_type == ItemType.HOTEL and item.city_id == destination
            )
        )
        if len(hotel_ids) != 1 or hotel_ids[0] not in hotels:
            raise ValueError(f"Cannot derive one hotel selection for {destination}")
        hotel_by_segment[index] = hotels[hotel_ids[0]]

        activity_ids = list(
            dict.fromkeys(
                item.option_id
                for item in items
                if item.item_type == ItemType.ACTIVITY and item.city_id == destination
            )
        )
        try:
            activities_by_segment[index] = [activities[item_id] for item_id in activity_ids]
        except KeyError as exc:
            raise ValueError(f"Unknown activity option {exc.args[0]}") from exc

    return CandidateSelectionOverrides(
        transport_by_segment=transport_by_segment,
        hotel_by_segment=hotel_by_segment,
        activities_by_segment=activities_by_segment,
    )
