"""Documented business rules used by generation and validation."""

from __future__ import annotations

from datetime import date
from decimal import Decimal, ROUND_HALF_UP

RETURN_WINDOWS_DAYS = {
    "apparel": 45,
    "electronics": 30,
    "home": 30,
    "final_sale": 0,
}
EXCLUDED_CATEGORIES = {"final_sale"}
RETURN_REASONS = {"no_longer_needed", "damaged", "wrong_item", "exchange"}
INSPECTION_CONDITIONS = {"sellable", "minor_damage", "missing_parts", "unsafe"}
DISPOSITIONS = {"restock", "refurbish", "quarantine", "scrap"}
REFUND_STATUSES = {"paid", "blocked_policy", "blocked_duplicate"}


def is_eligible(
    category: str, order_date: date, requested_at: date, manager_override: bool
) -> bool:
    if category in EXCLUDED_CATEGORIES:
        return False
    age_days = (requested_at - order_date).days
    if age_days < 0:
        return False
    return age_days <= RETURN_WINDOWS_DAYS[category] or manager_override


def disposition_for(condition: str) -> str:
    return {
        "sellable": "restock",
        "minor_damage": "refurbish",
        "missing_parts": "quarantine",
        "unsafe": "scrap",
    }[condition]


def to_cents(value: Decimal | str | int) -> int:
    amount = Decimal(value)
    return int((amount * 100).quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def calculate_refund_cents(
    paid_cents: int, approved_quantity: int, purchased_quantity: int, reason: str
) -> int:
    """Return a prorated refund after a fixed return-shipping fee."""
    if approved_quantity <= 0 or purchased_quantity <= 0:
        return 0
    prorated = (paid_cents * approved_quantity + purchased_quantity // 2) // purchased_quantity
    fee = 0 if reason == "damaged" else min(499, prorated)
    return max(0, prorated - fee)
