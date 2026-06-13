"""FastAPI service: deterministic 'stock of the day' per country.

The pick is stable for a given (country, UTC date) combination so that the
same call within a day always returns the same company. Data is fetched
through yfinance, which can occasionally fail or return missing fields —
every field we surface is therefore wrapped in defensive accessors.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Optional

from fastapi import FastAPI, Query
from fastapi.responses import JSONResponse

from bands import classify
from filters import (
    FILTERABLE_RATIOS,
    collect_constraints,
    describe_constraints,
    ratios_satisfy,
)
from formatting import format_market_cap, format_percent, symbol_for
from selection import (
    UnknownCountryError,
    fallback_order,
    pick_ticker,
    pick_ticker_random,
)
from universe import COUNTRY_TICKERS

logger = logging.getLogger("stock-of-the-day")
logging.basicConfig(level=logging.INFO)

# To balance between finding matches and preventing request/gateway timeouts over
# large universes (e.g., US with 3400+ tickers), limit sequential candidate scanning.
MAX_CANDIDATES_TRIED = 30

app = FastAPI(title="Stock of the Day", version="0.1.0")


# ---------------------------------------------------------------------------
# Safe accessors around yfinance
# ---------------------------------------------------------------------------

def _safe_float(value: Any) -> Optional[float]:
    """Coerce a yfinance value to ``float`` or return ``None``."""
    try:
        if value is None:
            return None
        f = float(value)
    except (TypeError, ValueError):
        return None
    # yfinance sometimes returns NaN as a float.
    if f != f:  # NaN check
        return None
    return f


def _info(ticker: Any) -> dict[str, Any]:
    """Fetch ``ticker.info`` defensively."""
    try:
        info = ticker.info or {}
        return info if isinstance(info, dict) else {}
    except Exception as exc:  # yfinance raises a grab-bag of exceptions
        logger.warning("ticker.info failed: %s", exc)
        return {}


def _latest_price_and_change(ticker: Any, info: dict[str, Any]) -> tuple[Optional[float], Optional[float]]:
    """Best-effort latest price + day percent change (as a decimal)."""
    price: Optional[float] = None
    prev_close: Optional[float] = None

    # 1) Prefer fast_info — it's cheaper and usually fresher.
    try:
        fast = ticker.fast_info
        price = _safe_float(getattr(fast, "last_price", None))
        prev_close = _safe_float(getattr(fast, "previous_close", None))
    except Exception as exc:
        logger.debug("fast_info failed: %s", exc)

    # 2) Fall back to fields on info.
    if price is None:
        price = _safe_float(info.get("currentPrice") or info.get("regularMarketPrice"))
    if prev_close is None:
        prev_close = _safe_float(info.get("previousClose") or info.get("regularMarketPreviousClose"))

    # 3) Fall back to history if needed.
    if price is None or prev_close is None:
        try:
            hist = ticker.history(period="5d", auto_adjust=False)
            if not hist.empty:
                closes = hist["Close"].dropna()
                if len(closes) >= 1 and price is None:
                    price = _safe_float(closes.iloc[-1])
                if len(closes) >= 2 and prev_close is None:
                    prev_close = _safe_float(closes.iloc[-2])
        except Exception as exc:
            logger.debug("history fallback failed: %s", exc)

    change_pct: Optional[float] = None
    if price is not None and prev_close not in (None, 0):
        change_pct = (price - prev_close) / prev_close

    return price, change_pct


def _recent_closes(ticker: Any, days: int = 30) -> list[dict[str, Any]]:
    """Return the most recent ``days`` trading-day closing prices.

    Each entry is ``{"date": "YYYY-MM-DD", "close": <float>}``. Returns
    an empty list when history is unavailable — never raises.
    """
    try:
        # Pull a bit extra so weekends/holidays don't shrink the window
        # below ``days`` trading sessions.
        hist = ticker.history(period="3mo", auto_adjust=False)
    except Exception as exc:  # yfinance raises a grab-bag of exceptions
        logger.debug("history(3mo) failed: %s", exc)
        return []

    if hist is None or hist.empty or "Close" not in hist.columns:
        return []

    closes = hist["Close"].dropna().tail(days)
    out: list[dict[str, Any]] = []
    for ts, value in closes.items():
        close = _safe_float(value)
        if close is None:
            continue
        try:
            date_str = ts.strftime("%Y-%m-%d")
        except Exception:
            date_str = str(ts)[:10]
        out.append({"date": date_str, "close": close})
    return out


def _ratio_block(metric: str, raw: Optional[float], *, formatted: Optional[str] = None) -> dict[str, Any]:
    """Build the ``{value, formatted, context}`` block for a single ratio."""
    return {
        "value": raw,
        "formatted": formatted,
        "context": classify(metric, raw),
    }


def _round_numbers(obj: Any) -> Any:
    """Recursively round every float in ``obj`` to 2 decimal places.

    Existing ints are left as ints; booleans, strings, ``None``, and other
    types are returned unchanged. Non-finite floats (``NaN``/``inf``) become
    ``None``.
    """
    # bool is a subclass of int — preserve it as-is.
    if isinstance(obj, bool):
        return obj
    if isinstance(obj, float):
        if obj != obj or obj in (float("inf"), float("-inf")):
            return None
        return round(obj, 2)
    if isinstance(obj, dict):
        return {k: _round_numbers(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_round_numbers(v) for v in obj]
    if isinstance(obj, tuple):
        return tuple(_round_numbers(v) for v in obj)
    return obj


# ---------------------------------------------------------------------------
# Assembly
# ---------------------------------------------------------------------------

def build_payload(country: str, ticker_symbol: str) -> dict[str, Any]:
    import yfinance as yf

    ticker = yf.Ticker(ticker_symbol)
    info = _info(ticker)

    currency = info.get("currency") or info.get("financialCurrency")
    currency_symbol = symbol_for(currency)
    market_cap = _safe_float(info.get("marketCap"))

    # 52-week range.
    week52_high = _safe_float(info.get("fiftyTwoWeekHigh"))
    week52_low = _safe_float(info.get("fiftyTwoWeekLow"))

    # Raw ratios from yfinance.
    trailing_pe   = _safe_float(info.get("trailingPE"))
    forward_pe    = _safe_float(info.get("forwardPE"))
    price_to_book = _safe_float(info.get("priceToBook"))
    roe           = _safe_float(info.get("returnOnEquity"))
    profit_margin = _safe_float(info.get("profitMargins"))
    dividend_yld  = _safe_float(info.get("dividendYield"))
    revenue_grow  = _safe_float(info.get("revenueGrowth"))

    # yfinance returns debt/equity in percent-style (e.g. 75.0 == 0.75x);
    # normalise to a plain ratio before classifying.
    debt_to_equity_raw = _safe_float(info.get("debtToEquity"))
    debt_to_equity = debt_to_equity_raw / 100 if debt_to_equity_raw is not None else None

    price, change_pct = _latest_price_and_change(ticker, info)
    closing_prices = _recent_closes(ticker, days=30)

    ratios = {
        "trailing_pe":    _ratio_block("trailing_pe",    trailing_pe,   formatted=f"{trailing_pe:.2f}" if trailing_pe is not None else None),
        "forward_pe":     _ratio_block("forward_pe",     forward_pe,    formatted=f"{forward_pe:.2f}" if forward_pe is not None else None),
        "price_to_book":  _ratio_block("price_to_book",  price_to_book, formatted=f"{price_to_book:.2f}" if price_to_book is not None else None),
        "roe":            _ratio_block("roe",            roe,            formatted=format_percent(roe)),
        "debt_to_equity": _ratio_block("debt_to_equity", debt_to_equity, formatted=f"{debt_to_equity:.2f}" if debt_to_equity is not None else None),
        "dividend_yield": _ratio_block("dividend_yield", dividend_yld,   formatted=format_percent(dividend_yld)),
        "profit_margin":  _ratio_block("profit_margin",  profit_margin,  formatted=format_percent(profit_margin)),
        "revenue_growth": _ratio_block("revenue_growth", revenue_grow,   formatted=format_percent(revenue_grow)),
    }

    return _round_numbers({
        "as_of": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
        "country_requested": country,
        "company": {
            "name":     info.get("longName") or info.get("shortName") or ticker_symbol,
            "ticker":   ticker_symbol,
            "exchange": info.get("exchange") or info.get("fullExchangeName"),
            "sector":   info.get("sector"),
            "industry": info.get("industry"),
            "country":  info.get("country"),
            "currency": currency,
            "currency_symbol": currency_symbol,
            "summary":  (info.get("longBusinessSummary") or None),
        },
        "market_cap": {
            "value":     market_cap,
            "currency":  currency,
            "currency_symbol": currency_symbol,
            "formatted": format_market_cap(market_cap, currency),
        },
        "price": {
            "latest":            price,
            "currency":          currency,
            "currency_symbol":   currency_symbol,
            "change_percent":    change_pct,
            "change_percent_formatted": format_percent(change_pct),
            "fifty_two_week_high": week52_high,
            "fifty_two_week_low":  week52_low,
            "history_30d":       closing_prices,
        },
        "ratios": ratios,
        "source": "yfinance",
    })


def _payload_is_empty(payload: dict[str, Any]) -> bool:
    """Return True when yfinance returned essentially no useful data.

    Used to drive ticker fallback: if the picked ticker yields a payload
    with no price, no market cap, and no ratios, we try the next ticker
    in the country's universe rather than serving an empty response.
    """
    price = payload.get("price") or {}
    market_cap = payload.get("market_cap") or {}
    ratios = payload.get("ratios") or {}
    has_price = price.get("latest") is not None
    has_market_cap = market_cap.get("value") is not None
    has_any_ratio = any(
        (r or {}).get("value") is not None for r in ratios.values()
    )
    return not (has_price or has_market_cap or has_any_ratio)


# ---------------------------------------------------------------------------
# HTTP routes
# ---------------------------------------------------------------------------

@app.get("/healthz")
def healthz() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/")
def root() -> JSONResponse:
    """Return API metadata and usage details."""
    return JSONResponse(
        {
            "name": "Stock of the Day",
            "version": app.version,
            "description": (
                "Returns a deterministic 'stock of the day' for a given "
                "country, sourced from yfinance. The pick is stable for "
                "the whole UTC day."
            ),
            "endpoints": {
                "GET /": "This API description.",
                "GET /healthz": "Health check.",
                "GET /stock-of-the-day?country=<ISO2>": (
                    "Returns the stock-of-the-day payload for the given "
                    "2-letter country code. Add &refresh=true to bypass the "
                    "deterministic daily pick and choose a fresh random ticker. "
                    "Optionally narrow the pick with ratio filters (all opt-in): "
                    "min_pe, max_pe, min_pb, max_pb, min_roe, max_roe, "
                    "min_dividend_yield, max_dividend_yield, min_profit_margin, "
                    "max_profit_margin. ROE, dividend yield and profit margin are "
                    "decimals (0.1 == 10%)."
                ),
            },
            "supported_countries": sorted(COUNTRY_TICKERS.keys()),
            "source": "yfinance",
            "documentation": "https://github.com/sd416/trmnl-stock-of-the-day",
        }
    )


@app.get("/stock-of-the-day")
def stock_of_the_day(
    country: str = Query("US", min_length=2, max_length=2, pattern="^[A-Za-z]{2}$"),
    refresh: bool = Query(
        False,
        description=(
            "When true, bypass the deterministic daily pick and choose a fresh "
            "random ticker from the country's universe."
        ),
    ),
    # ------------------------------------------------------------------
    # Optional ratio filters (opt-in). When omitted, selection is
    # unchanged. When supplied, the picked company must fall within the
    # given bounds; otherwise the next ticker in the universe is tried.
    # ROE / dividend yield / profit margin are decimals (0.1 == 10%),
    # matching the ``value`` fields in the response's ``ratios`` block.
    # ------------------------------------------------------------------
    min_pe: Optional[float] = Query(None, description="Minimum trailing P/E ratio."),
    max_pe: Optional[float] = Query(None, description="Maximum trailing P/E ratio."),
    min_pb: Optional[float] = Query(None, description="Minimum price-to-book ratio."),
    max_pb: Optional[float] = Query(None, description="Maximum price-to-book ratio."),
    min_roe: Optional[float] = Query(None, description="Minimum return on equity (decimal, 0.1 == 10%)."),
    max_roe: Optional[float] = Query(None, description="Maximum return on equity (decimal, 0.1 == 10%)."),
    min_dividend_yield: Optional[float] = Query(None, description="Minimum dividend yield (decimal, 0.03 == 3%)."),
    max_dividend_yield: Optional[float] = Query(None, description="Maximum dividend yield (decimal, 0.03 == 3%)."),
    min_profit_margin: Optional[float] = Query(None, description="Minimum profit margin (decimal, 0.1 == 10%)."),
    max_profit_margin: Optional[float] = Query(None, description="Maximum profit margin (decimal, 0.1 == 10%)."),
) -> JSONResponse:
    country = country.upper()

    # Build the (optional) ratio constraints. Keys are payload ratio keys.
    constraints = collect_constraints({
        FILTERABLE_RATIOS["pe"]:             (min_pe, max_pe),
        FILTERABLE_RATIOS["pb"]:             (min_pb, max_pb),
        FILTERABLE_RATIOS["roe"]:            (min_roe, max_roe),
        FILTERABLE_RATIOS["dividend_yield"]: (min_dividend_yield, max_dividend_yield),
        FILTERABLE_RATIOS["profit_margin"]:  (min_profit_margin, max_profit_margin),
    })

    try:
        if refresh:
            # `refresh=true` should show a different ticker than the day's
            # deterministic pick where possible.
            _, daily_ticker = pick_ticker(country)
            _, ticker_symbol = pick_ticker_random(country, exclude={daily_ticker})
            pick_source = "random"
        else:
            _, ticker_symbol = pick_ticker(country)
            pick_source = "daily"
    except UnknownCountryError:
        logger.info("unsupported_country country=%s", country)
        return JSONResponse(
            status_code=404,
            content={
                "error": "unsupported_country",
                "message": (
                    f"No curated stock universe for country '{country}'. "
                    "See GET / for the list of supported countries."
                ),
                "country": country,
                "supported_countries": sorted(COUNTRY_TICKERS.keys()),
            },
        )

    logger.info(
        "stock_of_the_day country=%s ticker=%s pick=%s refresh=%s",
        country, ticker_symbol, pick_source, refresh,
    )

    # Try the picked ticker first; if yfinance fails or returns an empty
    # payload (common for some non-US listings on Yahoo), fall back to the
    # other tickers in the country's universe. This keeps endpoints like
    # `?country=IN` working even when a specific NSE listing is unavailable.
    last_error: Optional[Exception] = None
    tried: list[str] = []
    successful_builds = 0

    for candidate in fallback_order(country, ticker_symbol):
        if len(tried) >= MAX_CANDIDATES_TRIED:
            logger.warning(
                "max_candidates_reached country=%s tried=%d limit=%d",
                country, len(tried), MAX_CANDIDATES_TRIED,
            )
            break

        tried.append(candidate)
        try:
            payload = build_payload(country, candidate)
        except Exception as exc:  # noqa: BLE001 - yfinance raises many things
            last_error = exc
            logger.warning(
                "build_payload_failed country=%s ticker=%s error=%s",
                country, candidate, exc,
            )
            continue

        if _payload_is_empty(payload):
            logger.warning(
                "build_payload_empty country=%s ticker=%s",
                country, candidate,
            )
            continue

        successful_builds += 1

        # Optional ratio filtering: when constraints are supplied, keep
        # scanning the universe until a company satisfies all of them.
        if constraints and not ratios_satisfy(payload.get("ratios") or {}, constraints):
            logger.info(
                "filter_skip country=%s ticker=%s",
                country, candidate,
            )
            continue

        if candidate != ticker_symbol:
            logger.info(
                "fallback_used country=%s original=%s served=%s tried=%d",
                country, ticker_symbol, candidate, len(tried),
            )
            payload["fallback_from"] = ticker_symbol
        if constraints:
            payload["filters_applied"] = describe_constraints(constraints)
        return JSONResponse(payload)

    # When filters were supplied and we had some successful fetches but nothing matched,
    # or if there was no network/parsing error at all, surface it as a 404 with the filters echoed back.
    if constraints and (successful_builds > 0 or last_error is None):
        logger.info(
            "no_filter_match country=%s tried=%d successful_builds=%d",
            country, len(tried), successful_builds,
        )
        return JSONResponse(
            status_code=404,
            content={
                "error": "no_match",
                "message": (
                    "No stock in the "
                    f"'{country}' universe matched the requested ratio filters."
                ),
                "country": country,
                "filters_applied": describe_constraints(constraints),
                "tickers_tried": len(tried),
            },
        )

    logger.error(
        "all_tickers_failed country=%s tried=%d last_error=%s",
        country, len(tried), last_error,
    )
    return JSONResponse(
        status_code=502,
        content={
            "error": "data_source_failed",
            "message": (
                "Failed to fetch market data from yfinance for any ticker "
                f"in the '{country}' universe."
            ),
            "country": country,
            "ticker": ticker_symbol,
            "tickers_tried": len(tried),
        },
    )
