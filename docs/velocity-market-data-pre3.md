# VELOCITY-MARKET-DATA-PRE3 — Market-wide EOD discovery foundation

PRE2A independent review, Windows qualification and successful personal-use AAPL
history/calculation/date-scoped-reference retrieval were reported complete at
`6521e5ecf4f1ca48809f6ed6a0f43c27ee78d1fe` before PRE3. No private response or real
credential is needed here. PRE3 itself requires independent review and actual
Windows qualification; cloud validation does not establish Windows qualification.

## Official source and license

Official documentation reviewed on October 19, 2026:

- [Daily Market Summary](https://massive.com/docs/rest/stocks/aggregates/daily-market-summary)
  lists Stocks Basic access, end-of-day recency and two years of history.
- [Stocks plans](https://massive.com/stocks) lists free Basic with five API calls
  per minute. Individual plans are personal/non-professional; customer-facing or
  commercial redistribution needs separate rights.
- [All Tickers](https://massive.com/docs/rest/stocks/tickers/all-tickers) documents
  type, primary exchange, active/date filters and pagination. PRE3 does **not**
  request it, traverse cursors or claim common-stock/exchange/active classification.

The sole new provider request is fixed internally:

```text
GET https://api.massive.com/v2/aggs/grouped/locale/us/market/stocks/YYYY-MM-DD?adjusted=true&include_otc=false
```

No full-market snapshots, intraday quotes, streams, ticker-list pagination or
automatic per-ticker follow-ups. API authorization uses the existing server-only
`MASSIVE_API_KEY` Bearer header. No real provider API requests or credential access
are part of implementation/tests. Reading public official documentation is separate.
Actual account entitlement/response compatibility still needs the user's later
personal-use verification; 401/403 remain unavailable, never an entitlement bypass.

## Launch and manual workflow

Default stays offline, including all 24 DEMO scanner symbols and chart timeframes:

```powershell
py -3.12 -m tradingbot_dashboard --open
```

After securely configuring your own environment variable as described in the
[PRE1 guide](velocity-market-data-pre1.md), explicitly enable the optional surface:

```powershell
py -3.12 -m tradingbot_dashboard --market-data-preview --watchlist "AAPL,MSFT" --open
```

Neither command automatically makes a provider request. In **REAL EOD MARKET
DISCOVERY**, choose a past Eastern weekday and click **Load EOD Market Summary**.
One action attempts at most one uncached grouped request. Weekends are rejected
before issuance. A weekday is **not** proof of an exchange session: holiday/empty
responses are shown as no supported observations, with no invented session data.
The bounded date range is the preceding 730 calendar days, excluding the current
Eastern date. The suggested previous weekday is not a holiday-calendar assertion.

All previous/history/reference/grouped calls share the same process-wide gate:
at most four attempts per rolling 60 seconds and at least 15 seconds between
attempts, including separate preview instances. Existing provider backoff remains
shared. Grouped timeout/network failures additionally establish 15-second backoff;
malformed/over-limit responses establish 60 seconds. No automatic retries, timers,
polling or scheduled scans. Status messages show required manual-retry delays.

One validated grouped response is cached in memory for five minutes under its
exact requested date. Repeating that date manually can use the cache without quota;
changing date does not reuse unrelated data. The grouped cache retains at most
one date. Failed new provider attempts publish no partial/old summary as the new
date; old manually selected symbols can still be investigated separately.

## Source contract and resource limits

The separate grouped transport preserves fixed-host verified HTTPS, no redirects,
no URL supplied by a browser, no error-body disclosure, and a 12-second socket
timeout. Existing per-ticker transports remain **256 KiB / 8 seconds**.
Grouped policy is **8 MiB**, read with a single bounded `limit + 1` call, at most
**30,000 response rows**, and at most 30,002 JSON containers (root, results array,
flat rows). Content length, when supplied, must match the received body and fit
the cap. Unsupported content encoding is rejected; compressed expansion is not
performed. Over-limit input reports `RESPONSE_LIMIT_EXCEEDED`, never success with
a truncated prefix. The socket timeout bounds each blocking operation, not a
guaranteed total wall-clock deadline for an adversarial slow stream.

Before JSON allocation, lexical checks bound depth to three, strings to 256 raw
characters, non-string tokens to 64, and containers to the declared limit. Result
array elements must be flat objects, so huge primitive arrays are rejected before
allocation. Object keys are closed/unique, objects have at most twelve keys,
integer tokens at most 28 characters. UTF-8, grammar, trailing bytes, nonfinite
numbers, nested/unexpected fields and future/paginated metadata fail closed.
Canonical qualified tickers reuse the existing per-ticker validator; unknown
formats are excluded, never truncated, uppercased or rewritten into another asset.

Root status must be `OK`, adjustment must be exact `true`, and exact integer
`resultsCount` must equal all received rows. Optional `queryCount` is bounded
aggregate-count metadata, not a U.S.-market universe count. The documented request
ID, when supplied, must be a bounded string and is not published or retained.
Every row needs valid ticker-source text, timestamp and complete positive OHLC,
nonnegative volume, valid high/low relationships and valid optional VWAP/trade
count/OTC fields. Exact Decimal evidence is retained; bool is not an integer.

**Malformed row, count mismatch or duplicate ticker:** invalidate the entire
summary; no latest-wins or partially successful prefix. **Structurally valid OTC
or unsupported-format row:** exclude and count separately. Explicit OTC `true`
is never accepted as a candidate; the docs state absent `otc` means false, and the
request always fixes `include_otc=false`. This is source-qualified exclusion,
not independent exchange/common-stock classification. Malformed exclusions are
zero in a successful summary because any malformed row invalidates it.

## Timestamps, dates and freshness

Grouped documentation calls `t` the **end of the aggregate window**, unlike the
custom daily bar start contract. PRE3 does not reuse the midnight-only historical
parser or relabel grouped timestamps as starts, closing prints or receipts.
Original milliseconds, UTC instant, Eastern timestamp date and explicit requested
date remain separate. Accepted timestamps lie on the requested Eastern date or
exactly its following Eastern midnight boundary, and cannot be future instants.
This deliberately supports midnight examples and a following-window boundary
without inferring economic-event time or completed-session finality. Other date
relationships are unavailable pending separate qualification.

The date must already be in the past in Eastern time before a provider request.
The post-2007 U.S. Eastern DST rules reuse existing dependency-free validation.
Age greater than four calendar days is labeled **STALE**. This is an age policy,
not a market calendar or latest-session guarantee. Empty responses are unavailable.
`market_session_completeness` is always `NOT_ESTABLISHED`. Provider `DELAYED`,
current-day and incompatible responses do not supply completed-EOD candidates.

## Preliminary rules, local pages and honest coverage

All results retain **PRELIMINARY EOD CANDIDATES — NOT FULLY QUALIFIED**.

| Control | Rule |
| --- | --- |
| EOD close | Strictly below maximum, default $5. $4.99 passes; $5 does not. |
| Same-day daily volume | Default zero disables this heuristic and includes supplied zero. Positive thresholds use strictly above; at 1M, 1M fails and 1,000,001 passes. |
| Search | Case-insensitive ticker substring, AND with price/volume. |
| Sort | Volume descending by default; price ascending/descending or daily range descending. Ticker alphabetic tie-break. |
| Daily range % | Exact `100 × (high − low) / open`, using only that row's valid OHLC. **Not** close-to-close daily change or volatility qualification. |

Filtering and sorting use exact Decimals/Fractions independent of ambient Decimal
precision. Display rounding alone uses the reviewed exact half-even formatter.
No price/volume values are fabricated for missing fields. Reset restores price
$5, disabled volume threshold, empty search and volume sorting. These controls
cannot change PRE2 settings, DEMO filters, frozen strategy rules or risk.

Local GET queries filter/sort the retained validated summary on the loopback
server; **none call Massive**. Output is one page of at most **50** rows. Page
indices are bounded, with deterministic clamping after filters shrink results.
The browser also renders at most 50 rows. Tests verify large-source response pages
fit under 64 KiB; there is no unbounded full-universe transfer or DOM rendering.

Coverage separates provider-reported/received counts, supported validated rows,
OTC/format exclusions, preliminary candidates before search, matches after search,
displayed count and candidates awaiting deeper research. Receiving all declared
response rows does **not** prove complete U.S. market coverage, common stocks only,
exchange listing, activity today or strategy eligibility. All candidate metrics
refer to the requested date, not an asserted latest quote.

No close-to-close percentage, previous ten-bar average, daily relative volume,
market cap, High/Very High volatility, ABCD score or intraday momentum can be
established by this single response. They remain explicitly unverified. The
same-day volume heuristic is not the original previous-ten-day-average filter.

## Session-only shortlist and PRE2 integration

Select up to five distinct validated discovered tickers. This explicit local
selection does not call any provider and does not alter the configured watchlist.
PRE2's active research selector visibly changes to the session shortlist; the
original PRE1 preview selector still shows only the original configured symbols.
Each selection retains its actual discovery date, including selections retained
across later summaries. Remove symbols or **Use configured watchlist** to restore
the original research view. All tabs sharing one running local server share its
in-memory selection; there are no cookies or per-user sessions.

The server accepts only its validated discovered identities or retained selected
identities, never unchecked arbitrary browser strings. In-flight provider work
blocks replacement, and issuance revalidates membership under the existing lock.
Removing a nonconfigured symbol evicts its loaded/cache data. Loaded records are
bounded to at most twenty history/reference entries and thirty per-ticker cache
entries across five configured plus five selected symbols. Restart loses all
summary, selection and loaded/cache state; nothing is saved to disk or a database.

For a shortlisted ticker, PRE2 still requires **two distinct manual actions**:

1. Load ticker daily history to establish eligible close-to-close change, previous
   ten available volume average and daily relative volume.
2. Separately load a dated reference for market cap, using PRE2's reviewed source
   alignment and freshness requirements.

Selection never copies grouped OHLCV into PRE2 history, grants research matches,
or chains those requests. PRE2 history may cover a different/latest date; its
displayed dates govern its separate evaluation, not the discovery date. No
synchronous cross-source snapshot or historical-date alignment is invented.
`FULL STRATEGY QUALIFICATION: NOT AVAILABLE` remains visible even for supported
EOD criteria met. No intraday breakout, entry or trade signal is added.

## Local security, validation and Windows review

Loopback `127.0.0.1`, fixed routes, Host checks, CSP and cross-origin protections
remain. Read-only metadata and local pages allow GET/HEAD. Provider loads and
session selection require explicit same-origin GET with the reviewed custom
header; HEAD and all write-like methods are rejected. The selection only changes
in-memory presentation scope, never broker/configuration/strategy state. No public
hosting, credential forms, external frontend resources, arbitrary URLs, raw error
bodies, SQLite, filesystem data persistence or dependency installation.

Focused fixtures are synthetic, including the large generated market response.
External DNS/sockets are forbidden; HTTP/browser checks allow only their ephemeral
loopback server. Optional installed Chromium/Playwright covers manual loading,
pagination, filters, date/cache/cooldown, asynchronous result stability, selection
capacity, PRE2 manual research and responsive layout. Browser packages are not
required or installed. All source-text test reads specify UTF-8 explicitly.

Cloud Python 3.12.14 validation passed: **108 PRE3 tests** and **451 combined
focused tests**, including 69 installed-browser checks across the five dashboard/
market-data suites. The final visual/static recheck passed another 21 existing
checks after the sort-control styling adjustment. Full offline discovery passed:
**8,716 tests OK, skipped=4** (three optional installed-IBAPI checks unavailable
in cloud and the disabled real Gateway test). All **155 final Python files**
compiled. Desktop 1440-pixel and narrow 600-pixel fixture-fed visual checks found
no document overflow, browser errors or external requests; their browser/server
were stopped. Both diff checks and scope/credential/account/environment/public
export/broker/storage/external-resource/UTF-8 scans passed. Exactly fourteen
intended files changed. Protected production, PRE1A source models, PRE2 calculation
and reference modules, per-ticker transport and shared quota remain unchanged.
No dependencies, real provider API calls, API-key access, IBKR requests or Gateway
changes were involved. These results are not independent review or Windows
qualification and do not certify compatibility with an actual grouped response.

After updating the Windows checkout, without real Gateway tests:

```powershell
$env:IBKR_RUN_READ_ONLY_TESTS = "0"
py -3.12 -m unittest tests.test_velocity_market_discovery tests.test_velocity_eod_research tests.test_velocity_marketdata tests.test_velocity_dashboard tests.test_velocity_scanner -v
py -3.12 -m unittest discover -v
git ls-files '*.py' | ForEach-Object { py -3.12 -m py_compile $_; if ($LASTEXITCODE -ne 0) { throw "Compilation failed: $_" } }
git diff --check
git diff --cached --check
git status --short
```

## Preserved authority and next review checkpoint

Broker/backtest/strategy/account/order-management/journal/risk production files
remain unchanged. PAPER only; trading BLOCKED; Gateway Read-Only REQUIRED;
restart quarantine MANDATORY; AccountMode UNKNOWN; public snapshot mode None;
LIVE UNSUPPORTED. No IBKR requests or orders. First real PAPER order stays BLOCKED.

PRE3 needs independent review, actual Windows qualification and later user-run
provider compatibility checks before further work. Multi-session screening needs
separately reviewed date/source/calendar coverage, licensed reference/instrument
classification and explicit quota design. Intraday scanning needs appropriate
entitlements, current source/corporate-action/session evidence and independently
qualified strategy logic. None is supplied by this EOD discovery phase. Do not
advance to PRE4, execution or website publication under PRE3 authorization.
