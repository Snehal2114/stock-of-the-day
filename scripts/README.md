# Stock Ticker Universe Fetcher

This script fetches and populates stock ticker universes for each country supported by the TRMNL Stock of the Day plugin.

## Requirements

- **Target**: 300-1000 tickers per country
- **Format**: Yahoo Finance compatible tickers with appropriate suffixes

## Automated Updates (GitHub Actions)

The repository includes a GitHub Actions workflow (`.github/workflows/update-stock-tickers.yml`) that automatically runs this script:

- **Schedule**: Weekly on Monday at 00:00 UTC
- **Manual Trigger**: Can be triggered manually from the Actions tab
- **Auto-commit**: Automatically commits and pushes changes if tickers are updated

The workflow uses the latest versions of all GitHub Actions:
- `actions/checkout@v4`
- `actions/setup-python@v5`

## Manual Usage

### Installation

```bash
pip install -r scripts/requirements.txt
```

Or install dependencies individually:

```bash
pip install yfinance pandas requests beautifulsoup4 lxml
```

### Running the Script

Run from the repository root:

```bash
python scripts/fetch_universe_tickers.py
```

The script will:
1. Fetch tickers from Wikipedia index pages
2. Attempt to fetch from Yahoo Finance indices
3. Add popular/large-cap stocks as fallback
4. Write to `container/universes/{COUNTRY}.txt`

## Country Coverage

| Country | Code | Suffix | Indices | Target |
|---------|------|--------|---------|--------|
| Australia | AU | .AX | ASX 300, All Ordinaries | 300 |
| Brazil | BR | .SA | Bovespa | 300 |
| Canada | CA | .TO | TSX Composite | 300 |
| Switzerland | CH | .SW | SMI, SPI | 300 |
| China | CN | .SS/.SZ | CSI 300 | 300 |
| Germany | DE | .DE | DAX, MDAX, SDAX | 300 |
| France | FR | .PA | CAC 40, SBF 120 | 300 |
| United Kingdom | GB | .L | FTSE 100, FTSE 250 | 350 |
| Hong Kong | HK | .HK | Hang Seng | 300 |
| Japan | JP | .T | Nikkei 225 | 300 |
| South Korea | KR | .KS/.KQ | KOSPI, KOSDAQ | 300 |
| Netherlands | NL | .AS | AEX | 300 |

## Troubleshooting

If the script doesn't fetch enough tickers (< 300):

1. **Check network connectivity** - Ensure you can access Wikipedia and Yahoo Finance
2. **Use financial data APIs** - Consider Alpha Vantage, IEX Cloud, or similar services
3. **Manual supplementation** - Download ticker lists from exchange websites:
   - ASX: https://www.asx.com.au/
   - TSX: https://www.tsx.com/
   - LSE: https://www.londonstockexchange.com/
   - Deutsche Börse: https://www.deutsche-boerse.com/
   - Euronext: https://www.euronext.com/

4. **Use TopForeignStocks.com** - Provides downloadable Excel lists for many indices

## Data Sources

The script attempts to fetch from multiple sources in order:
1. Wikipedia tables for major indices
2. Yahoo Finance index components (via yfinance)
3. Hardcoded popular/large-cap stocks as fallback

## Output Format

Each country file contains:
```
# Country Name (CODE)
# Count: NNN
TICKER1.SUFFIX
TICKER2.SUFFIX
...
```

## Notes

- US and IN already have 500+ tickers and are skipped
- Minimum target is 300 tickers per country
- Maximum is capped at 1000 tickers per country
- The script includes rate limiting to avoid overwhelming servers
