"""Optional ratio-based filtering (pure logic, no network).

The ``/stock-of-the-day`` endpoint can optionally narrow its pick to
companies whose fundamental ratios fall within caller-supplied bounds.

Filtering is entirely **opt-in**: when no bounds are supplied the selection
behaves exactly as before. This module only contains pure helpers (no
FastAPI/yfinance imports) so the logic can be unit-tested in isolation, in
keeping with the rest of the container's selection code.
"""

from __future__ import annotations

from typing import Any, Optional

# Public filter name -> ratio key as it appears in a payload's ``ratios``
# block. Only these "important" ratios can be filtered on; the keys here are
# also what the endpoint echoes back under ``filters_applied`` so callers can
# see exactly which constraints were honoured.
FILTERABLE_RATIOS: dict[str, str] = {
    "pe": "trailing_pe",
    "pb": "price_to_book",
    "roe": "roe",
    "dividend_yield": "dividend_yield",
    "profit_margin": "profit_margin",
}

# A single ratio constraint: ``(min, max)``; either bound may be ``None`` to
# leave that side open.
Bound = tuple[Optional[float], Optional[float]]


def collect_constraints(args: dict[str, Bound]) -> dict[str, Bound]:
    """Drop entries that don't actually constrain anything.

    ``args`` maps a ratio key (e.g. ``"trailing_pe"``) to a ``(min, max)``
    tuple. Entries where *both* bounds are ``None`` are removed, so a request
    that supplies no usable bounds is treated as "no filter" and selection
    falls back to its normal behaviour.
    """
    return {
        key: (lo, hi)
        for key, (lo, hi) in args.items()
        if lo is not None or hi is not None
    }


def ratios_satisfy(ratios: dict[str, Any], constraints: dict[str, Bound]) -> bool:
    """Return ``True`` if ``ratios`` satisfies every constraint.

    ``constraints`` maps a ratio key (as found in the payload ``ratios``
    block, e.g. ``"trailing_pe"``) to a ``(min, max)`` tuple; either bound may
    be ``None`` to leave that side open.

    A ratio whose value is missing (``None``) fails any constraint placed on
    it, so filtering never returns a company we couldn't actually verify
    against the requested bounds. An empty ``constraints`` mapping matches
    everything.
    """
    for key, (lo, hi) in constraints.items():
        block = ratios.get(key) or {}
        value = block.get("value")
        if value is None:
            return False
        if lo is not None and value < lo:
            return False
        if hi is not None and value > hi:
            return False
    return True


def describe_constraints(constraints: dict[str, Bound]) -> dict[str, dict[str, Optional[float]]]:
    """Render constraints as a JSON-friendly ``{ratio: {min, max}}`` echo."""
    return {
        key: {"min": lo, "max": hi}
        for key, (lo, hi) in constraints.items()
    }
