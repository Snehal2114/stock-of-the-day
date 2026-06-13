# trmnl-stock-of-the-day

A Cloudflare **Container Worker** that returns a deterministic "stock of the
day" for a given country, with data sourced from
[`yfinance`](https://pypi.org/project/yfinance/) (unofficial Yahoo Finance).

The pick is stable for a given `(country, UTC date)` pair: every call on the
same day returns the same company, so it's safe to use for dashboards.

---

Currently Hosted endpoint

---

https://randomstock.pluginapi.xyz/stock-of-the-day?country=IN

## Architecture

```
┌─────────────────┐   HTTP    ┌──────────────────────────┐   HTTP    ┌────────────────────┐
│  Client / TRMNL │ ────────▶ │  Cloudflare Worker (TS)  │ ────────▶ │  Container (Python) │
│                 │           │   src/index.ts           │           │   container/app.py  │
└─────────────────┘           │  validates country,      │           │  FastAPI + yfinance │
                              │  routes to container,    │           │  port 8080          │
                              │  adds caching + CORS     │           └────────────────────┘
                              └──────────────────────────┘
```

- **Worker** (`src/index.ts`) — validates the `country` query parameter,
  forwards the request to the container via a Durable Object binding, adds
  CORS + `Cache-Control: public, max-age=3600`.
- **Container** (`Dockerfile` + `container/`) — a small FastAPI service that
  picks a ticker deterministically, fetches data from yfinance, formats
  the response, and classifies each ratio as `low` / `typical` / `high`.

## Endpoints

| Method | Path                              | Notes                                             |
| ------ | --------------------------------- | ------------------------------------------------- |
| `GET`  | `/`                               | API description, version, and supported countries.|
| `GET`  | `/stock-of-the-day?country=US`    | Returns the stock-of-the-day payload.             |
| `GET`  | `/?country=US`                    | Convenience alias for `/stock-of-the-day`.        |
| `GET`  | `/healthz`                        | Worker health check (does not touch container).   |

### Query parameters

- `country` — required for the stock endpoint. 2-letter ISO code.
- `refresh` — optional. When `true` (also accepts `1`, `yes`, `on`), the
  worker bypasses the edge cache (`Cache-Control: no-store`) and the
  container picks a different ticker (excluding the day's deterministic
  pick when possible) from the country's universe using a time-based
  rotation algorithm instead of the deterministic daily pick.
  This ensures good distribution across sequential refreshes, making each
  refresh show a different stock within short time windows. Useful for
  "give me another one" buttons in dashboards.
- Ratio filters — all optional and opt-in. When omitted, selection is
  unchanged. When supplied, the service scans the country's universe (starting
  from the day's pick) and returns the first company whose ratios fall within
  the given bounds, echoing them back under `filters_applied`. If nothing
  matches, it responds `404` with `{"error": "no_match"}`.

  | Parameter | Ratio | Notes |
  | --------- | ----- | ----- |
  | `min_pe` / `max_pe` | Trailing P/E | plain multiple |
  | `min_pb` / `max_pb` | Price-to-book | plain multiple |
  | `min_roe` / `max_roe` | Return on equity | decimal, `0.1` == 10% |
  | `min_dividend_yield` / `max_dividend_yield` | Dividend yield | decimal, `0.03` == 3% |
  | `min_profit_margin` / `max_profit_margin` | Profit margin | decimal, `0.1` == 10% |

  A company whose value for a filtered ratio is missing is skipped, so filtered
  results only ever include companies that could be verified against the bounds.
  Example: `?country=US&min_pe=20&max_pe=40`.

If the deterministically-picked ticker is unavailable on Yahoo Finance
(this happens occasionally for non-US listings such as some `.NS` tickers
for India), the container automatically falls back to the next ticker in
the country's universe and adds a `"fallback_from"` field to the
response so callers can see which ticker was originally chosen.

`country` must be a 2-letter ISO code. Curated universes are defined in
[`container/universe.py`](container/universe.py) for: **US, IN, GB, JP, DE,
FR, CA, AU, BR, CN, HK, KR, NL, CH**. Requesting an unsupported country
returns **`404 unsupported_country`** with the list of supported codes
in the response body.

## Example response

```jsonc
{
  "as_of": "2026-05-23",
  "country_requested": "US",
  "company": {
    "name": "Apple Inc.",
    "ticker": "AAPL",
    "exchange": "NMS",
    "sector": "Technology",
    "industry": "Consumer Electronics",
    "country": "United States",
    "currency": "USD",
    "currency_symbol": "$",
    "summary": "Apple Inc. designs, manufactures, and markets smartphones..."
  },
  "market_cap": { "value": 2900000000000, "currency": "USD", "currency_symbol": "$", "formatted": "$2.9T" },
  "price":      { "latest": 189.43, "currency": "USD", "currency_symbol": "$",
                  "change_percent": 0.0123, "change_percent_formatted": "1.23%",
                  "fifty_two_week_high": 199.62, "fifty_two_week_low": 164.08,
                  "history_30d": [
                    { "date": "2026-04-24", "close": 182.11 },
                    { "date": "2026-04-25", "close": 183.42 }
                    // … up to 30 most recent trading-day closes
                  ] },
  "ratios": {
    "trailing_pe":    { "value": 28.4,  "formatted": "28.40",  "context": "high"    },
    "forward_pe":     { "value": 24.1,  "formatted": "24.10",  "context": "high"    },
    "price_to_book":  { "value": 45.2,  "formatted": "45.20",  "context": "high"    },
    "roe":            { "value": 1.47,  "formatted": "147.00%","context": "high"    },
    "debt_to_equity": { "value": 1.50,  "formatted": "1.50",   "context": "high"    },
    "dividend_yield": { "value": 0.005, "formatted": "0.50%",  "context": "low"     },
    "profit_margin":  { "value": 0.25,  "formatted": "25.00%", "context": "high"    },
    "revenue_growth": { "value": 0.08,  "formatted": "8.00%",  "context": "typical" }
  },
  "source": "yfinance"
}
```

Every numeric field can be `null` if yfinance fails to return it — the
service degrades gracefully rather than erroring out. When a value is
`null`, its `context` tag is also `null`.

## Reference bands for the `context` tags

Defined in [`container/bands.py`](container/bands.py) as
`(low_upper, high_lower)`. Values `< low_upper` are `"low"`, values
`>= high_lower` are `"high"`, everything in between is `"typical"`.

| Metric            | Low      | Typical       | High     |
| ----------------- | -------- | ------------- | -------- |
| `trailing_pe`     | < 15     | 15 – 25       | ≥ 25     |
| `forward_pe`      | < 13     | 13 – 22       | ≥ 22     |
| `price_to_book`   | < 1      | 1 – 3         | ≥ 3      |
| `roe`             | < 8%     | 8% – 18%      | ≥ 18%    |
| `profit_margin`   | < 5%     | 5% – 15%      | ≥ 15%    |
| `debt_to_equity`  | < 0.5x   | 0.5x – 1.5x   | ≥ 1.5x   |
| `dividend_yield`  | < 1%     | 1% – 4%       | ≥ 4%     |
| `revenue_growth`  | < 3%     | 3% – 15%      | ≥ 15%    |

These are deliberately rough, sector-agnostic defaults — the goal is to
make each number legible to a learner, not to be precise.

## Local development

```bash
# 1. Install Node + Python deps
npm install
pip install -r container/requirements.txt

# 2. Run the pure-logic unit tests (no network)
python container/tests/test_logic.py

# 3. Run the container directly (optional, for curl testing)
uvicorn app:app --app-dir container --port 8080
curl 'http://localhost:8080/stock-of-the-day?country=IN'

# 4. Run the full Worker + Container stack (requires Docker)
npm run dev
curl 'http://localhost:8787/stock-of-the-day?country=US'
```

## Deploy

```bash
npm run deploy
```

Requires a Cloudflare account with **Workers Paid** + **Containers**
enabled. See the
[Cloudflare Containers docs](https://developers.cloudflare.com/containers/)
for current onboarding requirements.

## Project layout

```
.
├── Dockerfile                  # Python 3.14 image for the container
├── wrangler.jsonc              # Worker + Container + Durable Object config
├── package.json                # Worker tooling (wrangler, typescript)
├── tsconfig.json
├── src/
│   └── index.ts                # Cloudflare Worker entrypoint
└── container/
    ├── app.py                  # FastAPI service (stock-of-the-day logic)
    ├── universe.py             # Per-country ticker lists
    ├── universes/              # Per-country ticker files (editable)
    ├── bands.py                # Reference bands + classify()
    ├── formatting.py           # Market-cap / percent / summary helpers
    ├── requirements.txt
    └── tests/
        └── test_logic.py       # Pure-logic unit tests (no network)
```

## Notes & limitations

- yfinance is an **unofficial** Yahoo Finance scraper. Fields can be
  missing, stale, or briefly unavailable — the service treats every
  metric as optional. Expect occasional `null`s in production.
- The curated ticker universes are intentionally small and biased toward
  well-known large caps so yfinance has a high chance of returning rich
  data. The lists are stored as per-country text files in
  `container/universes/` (one ticker per line) so you can update them
  manually.
- The day-of-pick uses **UTC**, not market-local time.
