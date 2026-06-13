"""Lightweight unit tests for pure-logic helpers (no network).

Run with: ``python container/tests/test_logic.py`` from the repo root.
"""

from __future__ import annotations

import sys
from pathlib import Path

# Make sibling modules importable when run as a script.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from bands import classify  # noqa: E402
from filters import (  # noqa: E402
    collect_constraints,
    describe_constraints,
    ratios_satisfy,
)
from formatting import format_market_cap, format_percent, symbol_for, truncate_summary  # noqa: E402


def test_classify_low_typical_high() -> None:
    assert classify("trailing_pe", 10) == "low"
    assert classify("trailing_pe", 20) == "typical"
    assert classify("trailing_pe", 40) == "high"
    # Boundary: value equal to high_lower is "high".
    assert classify("trailing_pe", 25) == "high"
    # Missing values / unknown metric.
    assert classify("trailing_pe", None) is None
    assert classify("nonexistent_metric", 1.0) is None


def test_classify_decimal_metrics() -> None:
    assert classify("roe", 0.05) == "low"
    assert classify("roe", 0.12) == "typical"
    assert classify("roe", 0.25) == "high"
    assert classify("dividend_yield", 0.005) == "low"
    assert classify("dividend_yield", 0.02) == "typical"
    assert classify("dividend_yield", 0.06) == "high"


def test_format_market_cap_usd() -> None:
    assert format_market_cap(2_900_000_000_000, "USD") == "$2.9T"
    assert format_market_cap(450_000_000_000, "EUR") == "€450.0B"
    assert format_market_cap(12_500_000, "USD") == "$12.5M"
    assert format_market_cap(None, "USD") is None
    assert format_market_cap(0, "USD") is None


def test_format_market_cap_inr_uses_crore() -> None:
    # 1,84,000 Cr == 1.84e12 INR
    out = format_market_cap(1_840_000_000_000, "INR")
    assert out is not None and out.endswith(" Cr") and "184,000" in out


def test_format_percent_and_truncate() -> None:
    assert format_percent(0.0123) == "1.23%"
    assert format_percent(None) is None
    long = "word " * 100
    truncated = truncate_summary(long, max_words=60)
    assert truncated is not None
    assert truncated.endswith("…")
    assert len(truncated.split()) <= 61  # 60 words + ellipsis token


def test_symbol_for_known_and_unknown() -> None:
    assert symbol_for("USD") == "$"
    assert symbol_for("INR") == "₹"
    assert symbol_for("CHF") == "CHF"  # trailing space stripped
    assert symbol_for("XYZ") is None
    assert symbol_for(None) is None
    assert symbol_for("") is None


def test_pick_ticker_deterministic_and_unknown_country() -> None:
    from selection import UnknownCountryError, pick_ticker

    a = pick_ticker("US", day="2026-01-15")
    b = pick_ticker("US", day="2026-01-15")
    assert a == b, "Same (country, day) must yield same pick"

    c = pick_ticker("US", day="2026-01-15")[1]
    d = pick_ticker("US", day="2026-06-30")[1]
    # Across very different dates the chance of collision is ~1/N; accept
    # either outcome but at minimum verify both are strings.
    assert isinstance(c, str) and isinstance(d, str)

    # Unknown country must raise, not silently fall back.
    try:
        pick_ticker("ZZ", day="2026-01-15")
    except UnknownCountryError:
        pass
    else:
        raise AssertionError("Unknown country should raise UnknownCountryError")


def test_collect_constraints_drops_empty() -> None:
    # Both bounds None -> dropped entirely.
    assert collect_constraints({"trailing_pe": (None, None)}) == {}
    # At least one bound present -> kept.
    assert collect_constraints({"trailing_pe": (20.0, None)}) == {
        "trailing_pe": (20.0, None)
    }
    out = collect_constraints({
        "trailing_pe": (20.0, 40.0),
        "roe": (None, None),
        "price_to_book": (None, 3.0),
    })
    assert out == {"trailing_pe": (20.0, 40.0), "price_to_book": (None, 3.0)}


def _ratios(**values: float) -> dict:
    return {key: {"value": val} for key, val in values.items()}


def test_ratios_satisfy_bounds() -> None:
    ratios = _ratios(trailing_pe=25.0, roe=0.12)
    # Within bounds.
    assert ratios_satisfy(ratios, {"trailing_pe": (20.0, 40.0)}) is True
    # Below min / above max.
    assert ratios_satisfy(ratios, {"trailing_pe": (30.0, None)}) is False
    assert ratios_satisfy(ratios, {"trailing_pe": (None, 20.0)}) is False
    # Multiple constraints must all hold.
    assert ratios_satisfy(ratios, {"trailing_pe": (20.0, 40.0), "roe": (0.1, None)}) is True
    assert ratios_satisfy(ratios, {"trailing_pe": (20.0, 40.0), "roe": (0.2, None)}) is False
    # Empty constraints match anything.
    assert ratios_satisfy(ratios, {}) is True


def test_ratios_satisfy_missing_value_fails() -> None:
    # A None / absent ratio fails any constraint placed on it.
    assert ratios_satisfy(_ratios(trailing_pe=25.0), {"price_to_book": (None, 3.0)}) is False
    assert ratios_satisfy({"trailing_pe": {"value": None}}, {"trailing_pe": (None, 40.0)}) is False
    # Boundary values are inclusive.
    assert ratios_satisfy(_ratios(trailing_pe=20.0), {"trailing_pe": (20.0, 40.0)}) is True
    assert ratios_satisfy(_ratios(trailing_pe=40.0), {"trailing_pe": (20.0, 40.0)}) is True


def test_describe_constraints_echo() -> None:
    assert describe_constraints({"trailing_pe": (20.0, 40.0), "roe": (0.1, None)}) == {
        "trailing_pe": {"min": 20.0, "max": 40.0},
        "roe": {"min": 0.1, "max": None},
    }


def test_pick_ticker_random_and_fallback_order() -> None:
    from selection import (
        UnknownCountryError,
        fallback_order,
        pick_ticker_random,
    )
    from universe import COUNTRY_TICKERS

    # Random pick must come from the country's universe.
    # The implementation is now time-based for better distribution,
    # so we just verify the ticker is valid.
    for _ in range(5):
        _, t = pick_ticker_random("IN")
        assert t in COUNTRY_TICKERS["IN"]

    # Verify that exclude parameter works when it doesn't exclude the entire list.
    # Patch the universe for determinism in this test (restore afterwards).
    original_in = COUNTRY_TICKERS["IN"]
    COUNTRY_TICKERS["IN"] = ["AAA.NS", "BBB.NS", "CCC.NS"]
    try:
        _, t = pick_ticker_random("IN", exclude={"AAA.NS"})
        assert t in {"BBB.NS", "CCC.NS"}
    finally:
        COUNTRY_TICKERS["IN"] = original_in

    # Unknown country still raises.
    try:
        pick_ticker_random("ZZ")
    except UnknownCountryError:
        pass
    else:
        raise AssertionError("Unknown country should raise UnknownCountryError")

    # fallback_order places the requested ticker first and keeps the rest.
    order = fallback_order("IN", "INFY.NS")
    assert order[0] == "INFY.NS"
    assert set(order) == set(COUNTRY_TICKERS["IN"])
    assert len(order) == len(COUNTRY_TICKERS["IN"])  # no duplicates


def test_api_filtering_precedence_and_limits() -> None:
    try:
        from unittest.mock import patch
        from fastapi.testclient import TestClient
        from app import app
        from universe import COUNTRY_TICKERS
    except ImportError:
        print("Skipping API/FastAPI tests because dependencies are not installed.")
        return

    client = TestClient(app)

    # 1. Test 404 precedence when successful_builds > 0 even if some throw errors.
    original_us = COUNTRY_TICKERS.get("US")
    COUNTRY_TICKERS["US"] = ["ERR", "OK_BUT_NO_MATCH"]

    def mock_build_payload(country: str, ticker: str) -> dict:
        if ticker == "ERR":
            raise ValueError("yfinance failed on ERR")
        return {
            "price": {"latest": 100.0},
            "market_cap": {"value": 1000000},
            "ratios": {
                "trailing_pe": {"value": 10.0}
            }
        }

    with patch("app.build_payload", side_effect=mock_build_payload):
        # We request min_pe=20.
        # ERR raises an exception (last_error set).
        # OK_BUT_NO_MATCH succeeds (successful_builds = 1) but fails constraints (pe 10 < 20).
        # Since successful_builds > 0, it should return 404 "no_match", NOT 502!
        response = client.get("/stock-of-the-day?country=US&min_pe=20")
        assert response.status_code == 404, f"Expected 404, got {response.status_code}: {response.json()}"
        assert response.json()["error"] == "no_match"
        assert response.json()["tickers_tried"] == 2

    # 2. Test 502 precedence when successful_builds == 0 and there is a last_error.
    COUNTRY_TICKERS["US"] = ["ERR1", "ERR2"]

    def mock_build_payload_all_err(country: str, ticker: str) -> dict:
        raise ValueError("yfinance failed on " + ticker)

    with patch("app.build_payload", side_effect=mock_build_payload_all_err):
        response = client.get("/stock-of-the-day?country=US&min_pe=20")
        assert response.status_code == 502, f"Expected 502, got {response.status_code}"
        assert response.json()["error"] == "data_source_failed"

    # 3. Test MAX_CANDIDATES_TRIED limit (30).
    # Create 40 mock tickers to exceed the MAX_CANDIDATES_TRIED limit of 30.
    COUNTRY_TICKERS["US"] = [f"T{i}" for i in range(40)]

    def mock_build_payload_all_no_match(country: str, ticker: str) -> dict:
        return {
            "price": {"latest": 100.0},
            "market_cap": {"value": 1000000},
            "ratios": {
                "trailing_pe": {"value": 10.0}
            }
        }

    with patch("app.build_payload", side_effect=mock_build_payload_all_no_match):
        # We request min_pe=20. All tickers will succeed building but fail the filter.
        # Since the limit is 30, it should stop scanning after 30.
        response = client.get("/stock-of-the-day?country=US&min_pe=20")
        assert response.status_code == 404
        assert response.json()["error"] == "no_match"
        assert response.json()["tickers_tried"] == 30

    # Restore COUNTRY_TICKERS
    if original_us is not None:
        COUNTRY_TICKERS["US"] = original_us
    else:
        COUNTRY_TICKERS.pop("US", None)


if __name__ == "__main__":
    test_classify_low_typical_high()
    test_classify_decimal_metrics()
    test_format_market_cap_usd()
    test_format_market_cap_inr_uses_crore()
    test_format_percent_and_truncate()
    test_symbol_for_known_and_unknown()
    test_collect_constraints_drops_empty()
    test_ratios_satisfy_bounds()
    test_ratios_satisfy_missing_value_fails()
    test_describe_constraints_echo()
    test_pick_ticker_deterministic_and_unknown_country()
    test_pick_ticker_random_and_fallback_order()
    test_api_filtering_precedence_and_limits()
    print("All tests passed.")
