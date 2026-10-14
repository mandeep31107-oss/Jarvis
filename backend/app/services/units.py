"""Unit conversion helpers (canonical internal unit = metre / square metre)."""
from __future__ import annotations

from typing import Literal

LENGTH_TO_M = {
    "m": 1.0,
    "cm": 0.01,
    "ft": 0.3048,
    "in": 0.0254,
    "yd": 0.9144,
}

AREA_TO_M2 = {
    "sqm": 1.0,
    "sqft": 0.09290304,
    "sqyd": 0.83612736,
    "gaj": 0.83612736,
    "acre": 4046.8564224,
    "cent": 40.468564224,
}

SQFT_PER_SQM = 10.7639104
SQYD_PER_SQM = 1.19599005


def to_metres(value: float, unit: str) -> float:
    if unit not in LENGTH_TO_M:
        raise ValueError(f"Unsupported length unit: {unit}")
    return float(value) * LENGTH_TO_M[unit]


def from_metres(value_m: float, unit: str) -> float:
    if unit not in LENGTH_TO_M:
        raise ValueError(f"Unsupported length unit: {unit}")
    return float(value_m) / LENGTH_TO_M[unit]


def to_sqm(value: float, unit: str) -> float:
    if unit not in AREA_TO_M2:
        raise ValueError(f"Unsupported area unit: {unit}")
    return float(value) * AREA_TO_M2[unit]


def from_sqm(value_m2: float, unit: str) -> float:
    if unit not in AREA_TO_M2:
        raise ValueError(f"Unsupported area unit: {unit}")
    return float(value_m2) / AREA_TO_M2[unit]


def convert_area(value: float, from_unit: str, to_unit: str) -> float:
    return from_sqm(to_sqm(value, from_unit), to_unit)


def m2_to_sqft(value_m2: float) -> float:
    return value_m2 * SQFT_PER_SQM


def sqft_to_m2(value_sqft: float) -> float:
    return value_sqft / SQFT_PER_SQM


def format_length(value_m: float, unit: Literal["m", "ft"] = "ft", precision: int = 2) -> str:
    """Human readable length. Imperial is rendered as feet + inches."""
    if unit == "m":
        return f"{value_m:.{precision}f} m"
    total_inches = value_m / 0.0254
    feet = int(total_inches // 12)
    inches = total_inches - feet * 12
    if inches >= 11.75:
        feet += 1
        inches = 0.0
    return f"{feet}'-{inches:.0f}\""


def format_area(value_m2: float, unit: str = "sqft", precision: int = 0) -> str:
    converted = from_sqm(value_m2, unit)
    labels = {"sqft": "sq.ft", "sqm": "sq.m", "sqyd": "sq.yd", "gaj": "gaj", "acre": "acre", "cent": "cent"}
    return f"{converted:,.{precision}f} {labels.get(unit, unit)}"


def parse_dimension(text: str) -> float | None:
    """Parse '30', '30ft', "12'6\\\"", '3.5 m' -> metres."""
    t = (text or "").strip().lower().replace("’", "'").replace("”", '"')
    if not t:
        return None
    if "'" in t:
        parts = t.split("'")
        try:
            feet = float(parts[0].strip())
        except ValueError:
            return None
        rest = parts[1].replace('"', "").strip() if len(parts) > 1 else ""
        inches = 0.0
        if rest:
            try:
                inches = float(rest)
            except ValueError:
                return None
        return to_metres(feet, "ft") + to_metres(inches, "in")
    number = ""
    unit = "m"
    for ch in t:
        if ch.isdigit() or ch == ".":
            number += ch
        elif ch.isalpha():
            unit_candidate = t[t.index(ch):].strip()
            unit = {"ft": "ft", "feet": "ft", "foot": "ft", "f": "ft", "m": "m", "meter": "m", "metre": "m",
                    "meters": "m", "metres": "m", "in": "in", "inch": "in", "inches": "in", "yd": "yd",
                    "yard": "yd", "yards": "yd", "cm": "cm"}.get(unit_candidate, "m")
            break
    try:
        return to_metres(float(number), unit)
    except (ValueError, KeyError):
        return None


def rate_m2_to_sqft(rate_per_sqm: float) -> float:
    """Convert a rate expressed per m² into the same currency per sq ft.

    A *rate* divides where an *area* multiplies: ₹18,000/m² is about ₹1,672/sq ft.
    """
    return rate_per_sqm / SQFT_PER_SQM


def rate_sqft_to_m2(rate_per_sqft: float) -> float:
    return rate_per_sqft * SQFT_PER_SQM
