"""Formatting helpers: market cap, percentages, and business summaries."""

from __future__ import annotations

from typing import Optional

# Per-currency symbol used in formatted market cap strings.
CURRENCY_SYMBOLS: dict[str, str] = {
    "USD": "$",
    "EUR": "€",
    "GBP": "£",
    "GBp": "£",  # London pence-quoted
    "JPY": "¥",
    "CNY": "¥",
    "HKD": "HK$",
    "INR": "₹",
    "KRW": "₩",
    "BRL": "R$",
    "CAD": "C$",
    "AUD": "A$",
    "CHF": "CHF ",
}


def symbol_for(currency: Optional[str]) -> Optional[str]:
    """Return the symbol for a currency, or ``None`` when unknown.

    The lookup is strict: callers that want a sensible default for
    formatting (e.g. ``"$"``) should use :func:`_symbol_for_formatting`.
    """
    if not currency:
        return None
    sym = CURRENCY_SYMBOLS.get(currency)
    if sym is None:
        return None
    # Trim trailing space used purely for prefix-formatting (e.g. "CHF ").
    return sym.rstrip()


def _symbol_for_formatting(currency: Optional[str]) -> str:
    """Symbol used as a prefix when formatting amounts.

    Falls back to ``"$"`` for unknown/missing currencies and to
    ``"<CODE> "`` (with a trailing space) for known ISO codes we don't
    have a glyph for, matching the original formatting behaviour.
    """
    if not currency:
        return "$"
    return CURRENCY_SYMBOLS.get(currency, f"{currency} ")


def format_market_cap(value: Optional[float], currency: Optional[str]) -> Optional[str]:
    """Return a short human-readable market-cap string.

    - For INR, uses the Indian Crore convention: ``₹18,400 Cr``.
    - For everything else, falls back to ``T / B / M / K`` suffixes,
      e.g. ``$2.9T``, ``€450.0B``.
    """
    if value is None or value <= 0:
        return None

    symbol = _symbol_for_formatting(currency)

    if currency == "INR":
        crore = value / 1_00_00_000  # 1 Cr = 10,000,000
        if crore >= 1000:
            return f"{symbol}{crore:,.0f} Cr"
        return f"{symbol}{crore:,.1f} Cr"

    for suffix, threshold in (("T", 1e12), ("B", 1e9), ("M", 1e6), ("K", 1e3)):
        if value >= threshold:
            scaled = value / threshold
            # Use 1 decimal place for compact readability.
            return f"{symbol}{scaled:.1f}{suffix}"
    return f"{symbol}{value:,.0f}"


def format_percent(value: Optional[float], digits: int = 2) -> Optional[str]:
    """Format a decimal (0.0123) as a percent string (``"1.23%"``)."""
    if value is None:
        return None
    return f"{value * 100:.{digits}f}%"


def truncate_summary(text: Optional[str], max_words: int = 60) -> Optional[str]:
    """Truncate a long business description to roughly ``max_words`` words."""
    if not text:
        return None
    words = text.split()
    if len(words) <= max_words:
        return text.strip()
    return " ".join(words[:max_words]).rstrip(",.;:") + "…"
