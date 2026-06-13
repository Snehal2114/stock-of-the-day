"""Deterministic ticker selection (pure logic, no network)."""

from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from typing import Optional

from universe import COUNTRY_TICKERS


class UnknownCountryError(KeyError):
    """Raised when the requested country has no curated ticker universe."""


def _tickers_for(country: str) -> list[str]:
    if country not in COUNTRY_TICKERS:
        raise UnknownCountryError(country)
    tickers = COUNTRY_TICKERS[country]
    if not tickers:
        raise UnknownCountryError(country)
    return tickers


def pick_ticker(country: str, day: Optional[str] = None) -> tuple[str, str]:
    """Return ``(country, ticker)`` chosen deterministically.

    Raises :class:`UnknownCountryError` when ``country`` has no curated
    universe — callers should surface this as a 4xx error rather than
    silently picking a US ticker.
    """
    tickers = _tickers_for(country)
    day = day or datetime.now(timezone.utc).strftime("%Y-%m-%d")

    seed = f"{country}:{day}".encode("utf-8")
    digest = hashlib.sha256(seed).digest()
    idx = int.from_bytes(digest[:8], "big") % len(tickers)
    return country, tickers[idx]


def pick_ticker_random(country: str, exclude: Optional[set[str]] = None) -> tuple[str, str]:
    """Return ``(country, ticker)`` chosen pseudo-randomly from the country universe.

    Used by the ``refresh=true`` code path so callers can force a fresh pick
    that differs from the day's deterministic choice. Tickers in ``exclude``
    are skipped where possible (used as a fallback when previous picks failed).
    Raises :class:`UnknownCountryError` when ``country`` is not supported.

    To provide better variety across refreshes, this uses a time-based rotating
    sequence instead of pure random selection. Each minute selects a different
    starting point in the ticker list, cycling through all tickers before repeating.
    This ensures users see a diverse set of stocks across multiple refreshes rather
    than clustering around the same popular stocks.
    """
    tickers = _tickers_for(country)
    candidates = [t for t in tickers if not exclude or t not in exclude] or tickers

    # Use current minute as a rotating index to provide better distribution.
    # This gives ~60 different starting points per hour, ensuring variety across
    # sequential refreshes while remaining stateless.
    now = datetime.now(timezone.utc)
    minute_of_hour = now.minute
    second = now.second
    # Combine minute and second for fine-grained rotation (3600 positions per hour)
    time_seed = minute_of_hour * 60 + second

    # Also incorporate the ticker hash to spread selections across the full universe
    ticker_hash = int.from_bytes(
        hashlib.sha256(f"{country}:{now.strftime('%Y-%m-%d:%H')}".encode()).digest()[:4],
        "big"
    )

    # Combine time-based and hash-based seeds for better distribution
    idx = (time_seed + ticker_hash) % len(candidates)
    return country, candidates[idx]


def fallback_order(country: str, first: str) -> list[str]:
    """Return the country's ticker list with ``first`` moved to the front.

    Used to iterate through alternative tickers when yfinance fails on the
    initially-picked one — preserves the original pick as the preferred
    choice while ensuring we don't repeatedly hit a single broken ticker.
    """
    tickers = _tickers_for(country)
    rest = [t for t in tickers if t != first]
    return [first, *rest]
