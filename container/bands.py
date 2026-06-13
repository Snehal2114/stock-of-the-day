"""Reference bands for classifying ratios as 'low' / 'typical' / 'high'.

These are deliberately rough, sector-agnostic defaults — the goal is to make
each number legible to a learner, not to be precise. Each band is
``(low_upper_bound, high_lower_bound)``: values strictly below ``low_upper``
are ``"low"``; values at or above ``high_lower`` are ``"high"``; anything in
between is ``"typical"``.

All ratios that are naturally expressed as decimals (e.g. 0.18 == 18%) are
kept as decimals here for consistency with what yfinance returns.
"""

from __future__ import annotations

from typing import Optional

# Band definition: (low_upper_bound, high_lower_bound)
Band = tuple[float, float]

REFERENCE_BANDS: dict[str, Band] = {
    # Valuation multiples
    "trailing_pe":    (15.0, 25.0),
    "forward_pe":     (13.0, 22.0),
    "price_to_book":  (1.0,  3.0),
    # Profitability / returns (decimals: 0.18 == 18%)
    "roe":            (0.08, 0.18),
    "profit_margin":  (0.05, 0.15),
    # Leverage — yfinance returns debt/equity as a percent-style number
    # (e.g. 75.0 means 0.75x). We normalise to a ratio before tagging.
    "debt_to_equity": (0.5,  1.5),
    # Income / growth (decimals)
    "dividend_yield": (0.01, 0.04),
    "revenue_growth": (0.03, 0.15),
}


def classify(metric: str, value: Optional[float]) -> Optional[str]:
    """Return ``"low"``, ``"typical"``, ``"high"`` or ``None``.

    ``None`` is returned when the value is missing or the metric has no
    configured reference band.
    """
    if value is None:
        return None
    band = REFERENCE_BANDS.get(metric)
    if band is None:
        return None
    low_upper, high_lower = band
    if value < low_upper:
        return "low"
    if value >= high_lower:
        return "high"
    return "typical"
