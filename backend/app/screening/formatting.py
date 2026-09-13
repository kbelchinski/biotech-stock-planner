"""Human-readable values for criterion explanations."""

from __future__ import annotations

from datetime import date


def usd(value: float | None) -> str:
    if value is None:
        return "—"
    magnitude = abs(value)
    for threshold, suffix in ((1e9, "B"), (1e6, "M"), (1e3, "K")):
        if magnitude >= threshold:
            return f"${value / threshold:,.2f}{suffix}"
    return f"${value:,.2f}"


def price(value: float | None) -> str:
    return "—" if value is None else f"${value:,.2f}"


def day(value: date | None) -> str:
    return "—" if value is None else value.strftime("%b %d, %Y").replace(" 0", " ")


def months(value: float) -> str:
    return f"{value:.1f} months"
