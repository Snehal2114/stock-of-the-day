#!/usr/bin/env python3
"""
Fetch and populate stock ticker universes for each country.
This script fetches tickers from major indices using yfinance and web scraping.

Usage:
    pip install yfinance pandas requests beautifulsoup4 lxml
    python fetch_universe_tickers.py

Requirements:
    - Minimum 300 tickers per country
    - Maximum 1000 tickers per country
    - Yahoo Finance compatible format

Note:
    This is a best-effort updater. Some markets won't reach the minimum
    without a dedicated listings data source.
"""

import re
import sys
import time
from pathlib import Path
import yfinance as yf
import pandas as pd
import requests
from bs4 import BeautifulSoup

# Configuration
BASE_DIR = Path(__file__).parent.parent
UNIVERSES_DIR = BASE_DIR / "container" / "universes"
MIN_TICKERS = 300
MAX_TICKERS = 1000

# Country configurations with their indices and ticker formats
COUNTRY_CONFIG = {
    'AU': {
        'name': 'Australia',
        'suffix': '.AX',
        'indices': ['^AXKO', '^AORD'],  # ASX 300, All Ordinaries
        'wiki_urls': ['https://en.wikipedia.org/wiki/S%26P/ASX_300'],
        'target': 300
    },
    'BR': {
        'name': 'Brazil',
        'suffix': '.SA',
        'indices': ['^BVSP'],  # Bovespa
        'wiki_urls': ['https://en.wikipedia.org/wiki/Ibovespa'],
        'target': 300
    },
    'CA': {
        'name': 'Canada',
        'suffix': '.TO',
        'indices': ['^GSPTSE'],  # TSX Composite
        'wiki_urls': ['https://en.wikipedia.org/wiki/S%26P/TSX_Composite_Index'],
        'target': 300
    },
    'CH': {
        'name': 'Switzerland',
        'suffix': '.SW',
        'indices': ['^SSMI', '^SXGE'],  # SMI, SPI
        'wiki_urls': ['https://en.wikipedia.org/wiki/Swiss_Market_Index'],
        'target': 300
    },
    'CN': {
        'name': 'China',
        'suffixes': ['.SS', '.SZ'],  # Shanghai, Shenzhen
        'indices': ['000300.SS'],  # CSI 300
        'wiki_urls': ['https://en.wikipedia.org/wiki/CSI_300_Index'],
        'target': 300
    },
    'DE': {
        'name': 'Germany',
        'suffix': '.DE',
        'indices': ['^GDAXI', '^MDAXI', '^SDAXI'],  # DAX, MDAX, SDAX
        'wiki_urls': [
            'https://en.wikipedia.org/wiki/DAX',
            'https://en.wikipedia.org/wiki/MDAX',
            'https://en.wikipedia.org/wiki/SDAX'
        ],
        'target': 300
    },
    'FR': {
        'name': 'France',
        'suffix': '.PA',
        'indices': ['^FCHI', '^SBF120'],  # CAC 40, SBF 120
        'wiki_urls': [
            'https://en.wikipedia.org/wiki/CAC_40',
            'https://en.wikipedia.org/wiki/SBF_120'
        ],
        'target': 300
    },
    'GB': {
        'name': 'United Kingdom',
        'suffix': '.L',
        'indices': ['^FTSE', '^FTMC'],  # FTSE 100, FTSE 250
        'wiki_urls': [
            'https://en.wikipedia.org/wiki/FTSE_100_Index',
            'https://en.wikipedia.org/wiki/FTSE_250_Index'
        ],
        'target': 350
    },
    'HK': {
        'name': 'Hong Kong',
        'suffix': '.HK',
        'indices': ['^HSI'],  # Hang Seng
        'wiki_urls': ['https://en.wikipedia.org/wiki/Hang_Seng_Index'],
        'target': 300
    },
    'JP': {
        'name': 'Japan',
        'suffix': '.T',
        'indices': ['^N225'],  # Nikkei 225
        'wiki_urls': ['https://en.wikipedia.org/wiki/Nikkei_225'],
        'target': 300
    },
    'KR': {
        'name': 'South Korea',
        'suffixes': ['.KS', '.KQ'],  # KOSPI, KOSDAQ
        'indices': ['^KS11', '^KQ11'],
        'wiki_urls': ['https://en.wikipedia.org/wiki/KOSPI'],
        'target': 300
    },
    'NL': {
        'name': 'Netherlands',
        'suffix': '.AS',
        'indices': ['^AEX'],  # AEX
        'wiki_urls': ['https://en.wikipedia.org/wiki/AEX_index'],
        'target': 300
    }
}

_TICKER_ALLOWED = re.compile(r"[^0-9A-Z.\\-]+")
_FOOTNOTE = re.compile(r"\\[[0-9]+\\]$")


def _clean_raw_ticker(raw: str) -> str:
    """Normalize common Wikipedia/table ticker formats to a bare symbol."""
    t = str(raw).strip().upper()
    if not t:
        return ""
    t = t.replace("\xa0", " ")
    t = t.split()[0]  # keep first token
    t = t.split("(")[0].strip()
    t = _FOOTNOTE.sub("", t).strip()
    t = _TICKER_ALLOWED.sub("", t)
    return t


def read_existing_universe(country_code: str) -> list[str]:
    """Read existing tickers so automation doesn't delete manual additions."""
    path = UNIVERSES_DIR / f"{country_code}.txt"
    if not path.exists():
        return []
    tickers: list[str] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        tickers.append(line)
    return tickers


def fetch_from_wikipedia(url):
    """Fetch tickers from Wikipedia table."""
    try:
        print(f"    Fetching from {url}")
        response = requests.get(url, timeout=15, headers={'User-Agent': 'Mozilla/5.0'})
        response.raise_for_status()

        # Try to parse tables with pandas
        dfs = pd.read_html(response.content)
        tickers = []

        for df in dfs:
            # Look for ticker/symbol columns
            for col in df.columns:
                col_lower = str(col).lower()
                if any(keyword in col_lower for keyword in ['ticker', 'symbol', 'code']):
                    tickers.extend(df[col].dropna().astype(str).tolist())

        cleaned: list[str] = []
        for t in tickers:
            t = _clean_raw_ticker(t)
            if not t:
                continue
            if len(t) > 15:
                continue
            if any(x in t.lower() for x in ['n/a', 'symbol', 'ticker', 'code']):
                continue
            cleaned.append(t)

        # De-duplicate while preserving order.
        return list(dict.fromkeys(cleaned))
    except Exception as e:
        print(f"    Error fetching from Wikipedia: {e}")
        return []


def fetch_from_yahoo_screener(country_code, suffix, count=500):
    """
    Fetch stocks using yfinance Screener for the country's exchange.
    This is a fallback method.
    """
    try:
        print(f"    Trying Yahoo Screener for {country_code}...")
        # Map country to exchange
        exchange_map = {
            'AU': 'ASX',
            'CA': 'TOR',
            'GB': 'LSE',
            'DE': 'FRA',
            'FR': 'PAR',
            'HK': 'HKG',
            'JP': 'JPX',
            'BR': 'SAO'
        }

        if country_code in exchange_map:
            # This is a placeholder - yfinance Screener API might not be available
            # You may need to use alternative methods
            pass
    except Exception as e:
        print(f"    Yahoo Screener error: {e}")

    return []


def fetch_from_index_components(index_symbol):
    """Try to fetch index components using yfinance."""
    try:
        ticker = yf.Ticker(index_symbol)

        # Some indices expose their holdings
        if hasattr(ticker, 'components'):
            components = ticker.components
            if components:
                return list(components)

        # Try getting info
        info = ticker.info
        if 'holdings' in info:
            return [h['symbol'] for h in info['holdings'] if 'symbol' in h]

    except Exception as e:
        print(f"    Could not fetch components for {index_symbol}: {e}")

    return []


def get_popular_stocks_for_country(country_code, suffix, count=100):
    """
    Get a list of popular/large-cap stocks as fallback.
    This uses a hardcoded list of well-known stocks.
    """
    popular_stocks = {
        'AU': ['BHP', 'CBA', 'CSL', 'NAB', 'WBC', 'ANZ', 'WES', 'WOW', 'MQG', 'RIO', 'FMG', 'GMG', 'TLS', 'WDS', 'COL'],
        'BR': ['PETR4', 'VALE3', 'ITUB4', 'BBDC4', 'ABEV3', 'BBAS3', 'B3SA3', 'RENT3', 'MGLU3', 'WEGE3'],
        'CA': ['RY', 'TD', 'ENB', 'CNR', 'SU', 'BMO', 'BNS', 'CP', 'CNQ', 'MFC'],
        'CH': ['NESN', 'ROG', 'NOVN', 'UBSG', 'ZURN', 'ABBN', 'SREN', 'CFR', 'HOLN', 'LONN'],
        'CN': ['600519', '601318', '600036', '600887', '601166', '600276', '601628', '601988', '600030'],
        'DE': ['SAP', 'SIE', 'BAYN', 'ALV', 'DTE', 'BMW', 'VOW3', 'MUV2', 'EOAN', 'BAS'],
        'FR': ['MC', 'OR', 'SAN', 'AI', 'SU', 'BNP', 'TTE', 'RMS', 'AIR', 'SAF'],
        'GB': ['HSBA', 'AZN', 'SHEL', 'BP', 'ULVR', 'RIO', 'DGE', 'GSK', 'BATS', 'NG'],
        'HK': ['00700', '00941', '00939', '00388', '00005', '01299', '00857', '00883', '00001', '00002'],
        'JP': ['7203', '6758', '9984', '6861', '9432', '8306', '8031', '7974', '6501', '6902'],
        'KR': ['005930', '000660', '035420', '051910', '005380', '068270', '035720', '207940', '005490'],
        'NL': ['ASML', 'PHIA', 'INGA', 'HEIA', 'KPN', 'MT', 'AD', 'UNA', 'AKZA', 'RAND']
    }

    if country_code in popular_stocks:
        stocks = popular_stocks[country_code]
        # Add suffix
        if 'suffixes' in COUNTRY_CONFIG[country_code]:
            # For countries with multiple suffixes, distribute evenly
            suffixes = COUNTRY_CONFIG[country_code]['suffixes']
            return [f"{s}{suffixes[i % len(suffixes)]}" for i, s in enumerate(stocks)]
        else:
            return [f"{s}{suffix}" for s in stocks]

    return []


def fetch_tickers_for_country(country_code, config):
    """Fetch all tickers for a country from multiple sources."""
    print(f"\nFetching tickers for {config['name']} ({country_code})...")

    all_tickers = set()
    all_tickers.update(read_existing_universe(country_code))
    suffix = config.get('suffix', '')
    suffixes = config.get('suffixes', [suffix] if suffix else [])

    # Method 1: Try Wikipedia
    for url in config.get('wiki_urls', []):
        wiki_tickers = fetch_from_wikipedia(url)
        for t in wiki_tickers:
            # Add appropriate suffix if not present
            if not any(t.endswith(s) for s in suffixes):
                if len(suffixes) == 1:
                    all_tickers.add(f"{t}{suffixes[0]}")
                else:
                    # For multi-suffix countries, try to infer or use first
                    all_tickers.add(f"{t}{suffixes[0]}")
            else:
                all_tickers.add(t)
        time.sleep(1)

    # Method 2: Try yfinance index components
    for index_symbol in config.get('indices', []):
        components = fetch_from_index_components(index_symbol)
        all_tickers.update(components)
        time.sleep(1)

    # Method 3: Add popular stocks as fallback
    popular = get_popular_stocks_for_country(country_code, suffix, 100)
    all_tickers.update(popular)

    # Clean and sort
    tickers_list = sorted(list(all_tickers))

    # Cap at the maximum; keep any existing manual additions.
    tickers_list = tickers_list[:MAX_TICKERS]

    print(f"  ✓ Collected {len(tickers_list)} tickers for {country_code}")

    # Ensure we meet minimum
    if len(tickers_list) < MIN_TICKERS:
        print(f"  ⚠ Warning: Only {len(tickers_list)} tickers found (minimum is {MIN_TICKERS})")
        print(f"  → You may need to manually supplement this list")

    return tickers_list


def write_universe_file(country_code, tickers):
    """Write tickers to universe file."""
    output_file = UNIVERSES_DIR / f"{country_code}.txt"

    # Keep output stable across runs: no timestamps, deterministic ordering.
    lines = [
        f"# {COUNTRY_CONFIG[country_code]['name']} ({country_code})",
        f"# Count: {len(tickers)}",
        *tickers,
        "",
    ]
    output_file.write_text("\n".join(lines), encoding="utf-8")

    print(f"  → Written to {output_file}")


def main():
    """Main function to fetch and update all country ticker universes."""
    print("=" * 70)
    print("Stock Ticker Universe Fetcher")
    print("=" * 70)
    print(f"Target: {MIN_TICKERS}-{MAX_TICKERS} tickers per country")
    print(f"Output directory: {UNIVERSES_DIR}")
    print()

    # Ensure output directory exists
    UNIVERSES_DIR.mkdir(parents=True, exist_ok=True)

    # Skip US and IN as they already have 500+ tickers
    skip_countries = ['US', 'IN']

    results = {}

    for country_code, config in COUNTRY_CONFIG.items():
        if country_code in skip_countries:
            print(f"\n{config['name']} ({country_code}): Skipping (already has sufficient tickers)")
            continue

        try:
            tickers = fetch_tickers_for_country(country_code, config)

            if tickers:
                write_universe_file(country_code, tickers)
                results[country_code] = {
                    'count': len(tickers),
                    'status': 'success' if len(tickers) >= MIN_TICKERS else 'warning'
                }
            else:
                results[country_code] = {
                    'count': 0,
                    'status': 'failed'
                }
        except Exception as e:
            print(f"  ✗ Error processing {country_code}: {e}")
            results[country_code] = {
                'count': 0,
                'status': 'error',
                'error': str(e)
            }

    # Print summary
    print("\n" + "=" * 70)
    print("SUMMARY")
    print("=" * 70)
    for country_code, result in results.items():
        status_icon = {
            'success': '✓',
            'warning': '⚠',
            'failed': '✗',
            'error': '✗'
        }.get(result['status'], '?')

        print(f"{status_icon} {country_code}: {result['count']} tickers ({result['status']})")

    print("\nDone!")
    print("\nNote: If any country has fewer than 300 tickers, you may need to:")
    print("  1. Manually add more tickers to the file")
    print("  2. Use a financial data provider API (e.g., Alpha Vantage, IEX Cloud)")
    print("  3. Scrape from financial websites with proper authentication")


if __name__ == "__main__":
    main()
