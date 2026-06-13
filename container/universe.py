"""Curated per-country ticker universes for the 'stock of the day' picker.

Ticker lists live in ``container/universes/<COUNTRY>.txt`` (one ticker per
line) so they can be updated manually without touching selection logic.
Tickers use the Yahoo Finance suffix convention (e.g. ``.NS`` for NSE India,
``.L`` for LSE, etc.).
"""

from __future__ import annotations

from pathlib import Path


_UNIVERSES_DIR = Path(__file__).resolve().parent / "universes"


def _load_universe(country: str) -> list[str]:
    path = _UNIVERSES_DIR / f"{country}.txt"
    if not path.exists():
        return []
    tickers = [line.strip() for line in path.read_text(encoding="utf-8").splitlines()]
    tickers = [t for t in tickers if t and not t.startswith("#")]

    # De-duplicate while preserving order (avoids skew in deterministic picks).
    seen: set[str] = set()
    out: list[str] = []
    for t in tickers:
        if t in seen:
            continue
        seen.add(t)
        out.append(t)
    return out


_SUPPORTED_COUNTRIES = [
    "AU",
    "BR",
    "CA",
    "CH",
    "CN",
    "DE",
    "FR",
    "GB",
    "HK",
    "IN",
    "JP",
    "KR",
    "NL",
    "US",
]


COUNTRY_TICKERS: dict[str, list[str]] = {c: _load_universe(c) for c in _SUPPORTED_COUNTRIES}

# Used when the requested country isn't in the dict above.
DEFAULT_COUNTRY = "US"
