# VELOCITY-MARKET-DATA-PRE2 — Real EOD watchlist scanner

Starting checkpoint: `ae02d443681999ec82c74d76e972bb4377470d94`.
The user reported MARKET-DATA-PRE1A independent review and actual Windows testing:
215 focused tests OK, skipped=2; 8,480 full tests OK, skipped=16; no failures and
the correct Windows Git checkpoint. The user also reported successful personal-use
Massive previous-day and historical AAPL retrieval after the timestamp repair.
No personal response or API key was accessed during PRE2 development.

## Purpose and launch

This is a personal-use, read-only EOD research workspace for **one to five
configured tickers**. It does not scan the full U.S. market. The existing 24-symbol
DEMO scanner, charts, example trades and safety panels remain independent.

Default offline DEMO launch is unchanged:

```powershell
py -3.12 -m tradingbot_dashboard --open
```

The existing optional preview flag also enables manual EOD scanner loads:

```powershell
py -3.12 -m tradingbot_dashboard --market-data-preview --watchlist "AAPL,MSFT,NVDA" --open
```

Use your own server-side `MASSIVE_API_KEY` through the existing secure environment
workflow in the [PRE1 guide](velocity-market-data-pre1.md). Never put it in chat,
a browser field, URL, frontend source, a committed file or `.env` in this project.
The server remains fixed to `127.0.0.1:8765`. No provider requests occur on import,
startup, filter/sort changes or ticker selection. No key is needed for tests.

## Official access checked

Public official documentation was retrieved during implementation, without an API
request or credential:

- [Stocks](https://massive.com/stocks): free tier, five requests per minute.
- [Custom Bars](https://massive.com/docs/rest/stocks/aggregates/custom-bars):
  Stocks Basic included, end-of-day, two years of historical access.
- [Ticker Overview](https://massive.com/docs/rest/stocks/tickers/ticker-overview):
  included in all Stocks plans; Basic updated daily and two-year history. Supports
  a `date` parameter for point-in-time reference information and optional
  `market_cap`, defined by the provider as most recent close times weighted shares.

Individual-plan personal/non-business restrictions remain. This is not a public
website, redistribution service, commercial data license or entitlement guarantee.
Actual 401/403 responses keep fields unavailable; no alternative source is invented.

## Exact calculations and qualification

The pure `tradingbot_marketdata/research.py` layer accepts existing validated
`_DailySeries` history and optional validated `_TickerReference` values. Previous-day
observations are not substituted for historical series. Existing PRE1 whole-series
validation is preserved: all supplied bars must satisfy source/presence/OHLCV rules,
adjustment must explicitly be true, the source must not be marked delayed, and
the latest historical date must precede today's Eastern date and be at most four
calendar days old. Partial, delayed, current-day or stale history cannot supply
usable research metrics. No fallback to synthetic values or older eligible tails.

| Metric | Exact rule |
| --- | --- |
| Latest EOD price | Latest eligible validated historical daily close. Never a live quote. |
| EOD close-to-close change | `100 * (latest close / previous available close - 1)`. Requires two positive valid closes and one consistent explicitly split-adjusted series. |
| Latest daily volume | Volume supplied by that latest daily aggregate. Zero remains supplied zero. |
| Previous ten-bar average volume | Exact sum of the ten available volume observations immediately preceding the latest bar, divided by ten. Requires at least eleven bars in total; excludes the latest bar. |
| EOD relative volume | Latest daily volume divided by that previous ten-bar average. Unavailable with insufficient lookback or a zero baseline. This is not intraday RVOL. |
| Reference market cap | Provider-supplied field only, from a separate explicitly dated Ticker Overview request. No fabricated share count or derived cap. |

Calculations and qualification use exact `Fraction` values constructed from
validated source Decimals. Ambient Decimal precision/traps do not influence
results. JSON retains exact numerator/denominator strings. Display strings use
explicit half-even rounding only; browser qualification and sorting use `BigInt`
rational comparisons, never the rounded display or binary floating-point values.
Original Decimal shape remains in the exact retained source and PRE1 observations.

The ten observations are **available bars**, not a certified consecutive market
calendar. No holidays, omitted sessions, early closes or missing bars are inferred
or interpolated. Close dates and all ten baseline dates are available per row.
Timestamp handling is unchanged from PRE1A: historical daily bars require exact
weekday ET midnight; previous-day bars retain their independent source policy.
Source UTC instants, Eastern session dates and split-adjustment metadata survive.

## Dated market-cap provenance

First load eligible history. A separate **Load dated reference** action internally
selects its latest source date and requests only:

`GET /v3/reference/tickers/{ticker}?date=YYYY-MM-DD`

The caller cannot supply a provider URL or reference date. Response ticker must
match exactly; U.S. stocks and USD context must be supplied before cap qualifies.
Missing optional cap/context remains unavailable. Invalid identity, numeric bounds,
counts, UTF-8, JSON, pagination or context fail closed with sanitized errors.
Qualified cap must be positive and at most $1 quadrillion under the bounded
numeric policy; zero or unsupported values are not promoted to usable reference.

The request date scopes provider reference information; it does **not** prove that
the cap's price/share-count constituents were measured simultaneously with OHLCV.
The API does not provide a qualified market-cap source timestamp. Each row retains
`requested_date`, `retrieved_at`, provider/endpoint and **`source_as_of: null`**.
The UI explicitly shows cap as-of UNAVAILABLE and no synchronous snapshot claim.
The cap criterion means comparison with the provider's date-scoped reference field,
not synchronized capitalization at the bar's instant or historical strategy proof.
The documented reference-date policy can use SEC period-of-report dates rather
than filing submission dates. This is not a filing-availability or lookahead-free
historical backtest contract; no backtest integration is added.

Cap is usable for this research subset only when reference context is complete,
the request date equals the retained latest eligible bar date, and freshness is
valid. A history-date change invalidates alignment; an old-date cache is not reused
for a new reference date. Reference age uses the same conservative four-calendar-day
limit. Future retrieval times invalidate usability after a clock regression.

## Filters, outcomes and missing requirements

Default supported research settings are separate from Strategy Spec v1.0, risk
rules and DEMO controls:

| Filter | Default | Comparison |
| --- | --- | --- |
| Maximum EOD price | $5 | Strictly below |
| Minimum EOD close-to-close change | +3% | Greater than or equal |
| Minimum date-scoped reference cap | $50 million | Greater than or equal; required by default |
| Minimum previous ten-bar average volume | 1,000,000 shares | Strictly above |
| Optional EOD relative volume | No minimum | Greater than or equal when enabled |

All enabled supported criteria use AND logic. A valid failed criterion takes
precedence over missing criteria: **EOD CRITERIA NOT MET**. With no valid failure
but any required unavailable metric: **INSUFFICIENT DATA**. Only all enabled
supported criteria passing yields **EOD CRITERIA MET**. Missing never passes.
The cap requirement can be explicitly excluded from this limited research subset;
its unavailability and provenance remain visible.

High/Very High volatility, ABCD setup scores/entry qualification and intraday
momentum remain **UNVERIFIED**. No ATR formula or volatility categories are added.
Every EOD outcome retains **FULL STRATEGY QUALIFICATION: NOT AVAILABLE**. No result
is a buy, entry-ready, breakout, auto-trade or live-signal classification.

Filters update assessments immediately with no HTTP request. Search is local and
case-insensitive. Sorting uses exact metric values, missing last, ticker alphabetic
tie-breaking. Sort modes: EOD change descending, price ascending/descending,
relative volume, daily volume and cap descending. All coverage rows display by
default; **Show EOD criteria met only** and search narrow the visible rows.
Coverage totals always describe the full configured watchlist under current
research filters. Unloaded tickers remain incomplete and never count as eligible
history or qualified reference. Empty filtered views explain missing coverage.

Reset restores defaults, required cap, empty search, all rows and change sorting.
Loosen doubles maximum price, subtracts one percentage point from minimum change,
and halves minimum cap, average volume and any active relative-volume minimum,
within declared bounds. All new values appear in the controls. It changes no
source data, broker behavior, risk rule, strategy setting or DEMO filter.

## Manual loading and API budget

1. Select one configured ticker; selection itself does not request data.
2. Click **Load ticker daily history**: at most one uncached historical API call.
3. Wait for quota availability, then click **Load dated reference** separately:
   at most one reference API call. History is never loaded as a hidden prerequisite.
4. Evaluate/filter/sort already loaded data locally. Existing PRE1 history loads
   also update the new coverage panel through local metadata only.

All endpoints share the existing process-wide four-attempt rolling-minute budget,
at least fifteen seconds between attempts and conservative provider backoff.
Cache reuse is five minutes and consumes no provider budget. Failure/cooldown
messages are predefined and sanitized. No automatic second request, retries,
polling, background watchlist scan or refresh loop is added.

Source records remain in bounded memory: at most ten loaded history/reference
records and fifteen cache entries for five tickers. Failed requests retain the
last validated evidence with its original retrieval date and an explicit failure
message. Freshness is evaluated at the displayed local evaluation time. The
**Re-evaluate loaded data** button updates local availability with no provider call;
there is no claim that an idle page's earlier evaluation is current now.

## Security and validation

The fixed-host TLS transport, Bearer authentication, body/timeout limits,
loopback/Host checks, CSP and cross-origin protections remain. Provider actions
still require explicit local GET plus the reviewed custom header; HEAD and
write-like methods are rejected without provider work. The new fixed metadata
route `/api/market-data/research` allows GET/HEAD and performs no provider request.
No raw provider body, exception, credential, arbitrary object serialization,
external frontend resource, cookies, localStorage or disk data persistence.

Tests use invented offline provider fixtures. External sockets/DNS are forbidden;
browser traffic is constrained to ephemeral loopback servers. Browser tests use
only an already installed optional Chromium/Playwright and injected quota grants;
separate adapter tests exercise the actual shared rate gate. No dependency install,
real provider API request, API-key access or IBKR operation is required.

Cloud Python 3.12.14 validation passed: 343 focused tests (88 new EOD research
tests plus 255 PRE1/PRE1A and Dashboard PRE1/PRE2 regressions), including 58
installed-browser checks. Full offline discovery passed: 8,608 tests OK,
skipped=4 (three optional installed-IBAPI checks unavailable in cloud and the
disabled real Gateway integration opt-in). All 153 tracked/planned Python files
compiled. Fixture-fed desktop/narrow visual inspection found no page overflow,
browser errors or external requests; its browser and loopback server were stopped.
Both diff checks and credential/account/environment, external-resource, public
export, read-only security and protected-file scope scans passed. All 64
broker/backtest/strategy files, PRE1A source model, TLS transport, rate gate and
CSP remain unchanged. Only the fifteen intended market-data/dashboard/test/docs
files changed. No provider API call or actual API-key access was used.

Windows verification, without enabling real Gateway tests:

```powershell
$env:IBKR_RUN_READ_ONLY_TESTS = "0"
py -3.12 -m unittest tests.test_velocity_eod_research tests.test_velocity_marketdata tests.test_velocity_dashboard tests.test_velocity_scanner -v
py -3.12 -m unittest discover -v
git ls-files '*.py' | ForEach-Object { py -3.12 -m py_compile $_; if ($LASTEXITCODE -ne 0) { throw "Compilation failed" } }
git diff --check
git diff --cached --check
git status --short
```

PRE2 requires independent review and actual Windows qualification. Optional browser
availability can change counts; cloud checks are not Windows qualification.

## Preserved barriers and future work

No broker/backtest/strategy production file, journal/SQLite schema, broker request,
execution authorization, risk rule, order management or Gateway setting changes.
PAPER; trading BLOCKED; Gateway Read-Only REQUIRED; restart quarantine MANDATORY;
AccountMode UNKNOWN; public snapshot mode None; LIVE UNSUPPORTED. The first real
PAPER order remains BLOCKED.

True full-market intraday scanning requires separately authorized entitlements,
licensed universe coverage and throughput, timely intraday source/corporate-action/
calendar semantics, qualified reference/fundamental coverage, validated volatility
and ABCD evidence, and independently reviewed signal integration. None is supplied
by a five-ticker EOD research view. Execution and public hosting remain out of scope.
