from decimal import Decimal
from typing import Iterable

from app.domain.models import CostBreakdown, ItemType, ItineraryItem


def calculate_cost_breakdown(items: Iterable[ItineraryItem]) -> CostBreakdown:
    totals = {
        "flights": Decimal("0"),
        "hotels": Decimal("0"),
        "activities": Decimal("0"),
        "local_transport": Decimal("0"),
        "other": Decimal("0"),
    }
    category_by_type = {
        ItemType.FLIGHT: "flights",
        ItemType.HOTEL: "hotels",
        ItemType.ACTIVITY: "activities",
        ItemType.ROUTE: "local_transport",
    }
    for item in items:
        totals[category_by_type[item.item_type]] += item.cost
    return CostBreakdown(**totals)
