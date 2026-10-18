# VELOCITY-MARKET-DATA-PRE1 — real read-only EOD observations

Starting checkpoint: `a961e625afb2cc01c8cc0467f2b1284c905164f2`. The user reported
Dashboard PRE2 independent review and actual Windows qualification complete:
8,371 tests OK, 15 skipped, 145 Python files compiled, compilation/diff exit zero,
clean synchronized Windows main. Optional browser tests explain the cloud count
difference. The authorized cloud branch remains `work`; deployment is a normal
fast-forward to `origin/main`. No branch rename or history rewrite is needed.

This phase adds observation display only. It supplies no live feed, real scanner,
ABCD signal, broker evidence, account access, management or trading authority.

## Official provider contract checked on October 18, 2026

The official pages were read successfully from the cloud after documentation
network access became available; the user also independently verified the plan.

| Official source | Verified contract |
| --- | --- |
| [Stocks plans](https://massive.com/stocks) | Stocks Basic $0/month, end-of-day data, two years of history, five API calls/minute. No live feed/streaming/full-market snapshot entitlement assumed. |
| [REST quickstart](https://massive.com/docs/rest/quickstart) | HTTPS `api.massive.com`; `Authorization: Bearer` authentication supported. |
| [Previous Day Bar](https://massive.com/docs/rest/stocks/aggregates/previous-day-bar) | `/v2/aggs/ticker/{ticker}/prev`; Basic included; previous trading-day aggregate OHLCV; explicit split-adjustment status. |
| [Custom Bars](https://massive.com/docs/rest/stocks/aggregates/custom-bars) | Basic included with EOD/two-year limits; daily ET aggregate windows; qualifying trades can include extended hours; no qualifying trades can mean no bar. |
| [Terms index](https://massive.com/terms), [Individuals terms](https://massive.com/legal/individuals-terms-of-service) | Individual services are personal, non-commercial and non-business. Commercial/customer-facing/redistribution use requires separate rights. |

The free plan does **not** authorize a commercial Velocity customer display.
This implementation is a local personal-use preview, not a distribution service.
No account was registered, subscription purchased, credential requested in chat or
provider stock-data API called during development. Plan/account access can change;
a configured key is not proof of entitlement. 401/403 fail without tier escalation.

## Default DEMO stays unchanged

```powershell
py -3.12 -m tradingbot_dashboard --open
```

The existing demo scanner, filters, all 24 symbols/four timeframes, main Canvas
chart, VWAP, volume, example markers, trades, equity, navigation and safety panels
remain independent. Default startup does not read a provider key or make provider
requests, even if the environment already contains one. The new panel explains
that the preview is unavailable/disabled. Demo rows never receive EOD values.

## Optional secure local connection, later

Use your own authorized personal-use provider account/key. Do not paste the key
into Codex, source, command arguments, browser forms, `.env` files or Git. The app
reads only the server-side process environment variable `MASSIVE_API_KEY`; it does
not load dotenv, store credentials or accept a key through HTTP/CLI parameters.

On Windows PowerShell, masked entry avoids a literal key in command history:

```powershell
$marketSecret = Read-Host "Massive API key (hidden)" -AsSecureString
$marketPointer = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($marketSecret)
try {
    $env:MASSIVE_API_KEY = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($marketPointer)
} finally {
    [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($marketPointer)
    $marketSecret.Dispose()
}
try {
    py -3.12 -m tradingbot_dashboard --market-data-preview --watchlist "AAPL,MSFT" --open
} finally {
    Remove-Item Env:MASSIVE_API_KEY -ErrorAction SilentlyContinue
}
```

The local URL remains `http://127.0.0.1:8765`; Ctrl+C stops the server. Omit
`--open` if desired. The optional startup flag enables capability only: **no
provider call occurs on startup**. The page reads local configuration metadata,
then waits for the user to select one ticker and click **Load EOD bar** or
**Load daily history**. There is no batch scan, polling, automatic refresh or
automatic retry. Selecting a ticker alone does not request data.

The default watchlist is AAPL/MSFT/NVDA. `--watchlist` permits one to five distinct
uppercase symbols, optionally a single class suffix such as BRK.B. It requires
the preview flag. Unknown symbols, lowercase, URL/path/query characters, duplicate
symbols and larger lists are rejected. Only configured symbols may be queried.
The panel displays one selected symbol at a time; cached observations are bounded
by that watchlist. Missing/invalid key leaves DEMO usable and shows UNAVAILABLE.
Changing credentials requires restarting the server. No actual key was used to
qualify this implementation; a later user-controlled API check is separate.

## Isolated implementation

`tradingbot_marketdata/` contains private frozen/slotted source models, a bounded
decoder, the Massive preview/cache/rate owner and one fixed-host HTTPS transport.
It imports only Python's standard library and no broker/backtest/strategy modules.
It has no public exports. Imports and construction perform no provider requests.

The dashboard adds a separate `market-data.js` surface and fixed local read routes.
The existing demo snapshot schema, source fixtures and `app.js` remain unchanged.
The browser receives only explicit allowlisted primitive JSON; no Python object
dump, credential, authorization header, raw provider response or exception escapes.

### Fixed source requests and bounds

- HTTPS host is always `api.massive.com`, TLS certificate/hostname verification on.
  No supplied base URL, proxy, SDK object or arbitrary endpoint is accepted.
- Previous bar uses `/v2/aggs/ticker/{ticker}/prev?adjusted=true`.
- History uses `/v2/aggs/ticker/{ticker}/range/1/day/{from}/{to}` with fixed
  `adjusted=true&sort=asc&limit=64`. End is the prior ET calendar date; start is
  60 days earlier. This is a bounded daily subset well inside the two-year plan.
- No pagination or redirects are followed. Unexpected continuation fails closed.
- HTTPS socket timeout is eight seconds. OS DNS resolution follows platform
  behavior; the code does not claim a separate cancellable DNS deadline.
- Response body is at most 256 KiB; history at most 64 returned bars; previous
  query at most one bar. Cache at most ten entries (five symbols × two views).
- Numeric values are finite, exact Decimal source values, bounded to 28 digits
  and exponents −12..12; positive OHLC ≤ 1e12, volume ≥ 0 and ≤ 1e15. Bool is not a
  number. Decimal strings preserve source scale in browser JSON; Canvas rendering
  is visual only, with exact source values available in the table.

### Rate, cache and manual retry

All adapter instances share a process-wide locked rate gate: at most four attempts
per rolling minute and at least 15 seconds between attempts, below the five/minute
Basic allowance. Failed attempts consume budget too. Other applications sharing a
key can still consume provider quota; a 429 is respected independently.

Validated source observations cache in memory for five minutes. A cache hit does
not contact the provider or spend request budget. It retains the original source
timestamp and retrieval time, recalculating age at read time. Expiry does not
trigger refresh; another explicit load is required. No disk/journal/SQLite cache.

HTTP 401/403/404/429/5xx, redirects, timeout, network and malformed data become
fixed sanitized UNAVAILABLE reasons. 401/403 establish a five-minute backoff;
other HTTP failures generally one minute. 429/503 honor a valid Retry-After
seconds/date with at least a minute's backoff. Timeouts/network failures retain
the minimum spacing. Concurrent uncached loads are rejected without queueing.
There are no retry sleeps or background jobs; retry is a later explicit user load.
Locks protect cache/rate bookkeeping only, never held over HTTPS. No live broker
locks, callback ownership, persistence or SDK calls are involved.

## Source semantics and quality

Validated observations retain ticker, exact source Unix-millisecond timestamp,
source OHLCV, supplied-vs-missing fields and actual split-adjustment flag. UTC
window-start and ET session date derive only from the documented daily timestamp.
`retrieved_at` records local receipt/validation time separately. The provider's
timestamp means **aggregate window start**, not trade time, close time or current
intraday observation time. Daily aggregates may include extended-hours qualifying
trades; they are not asserted to be regular-session-only or an atomic snapshot.

ET interpretation uses explicit post-2007 US Eastern DST rules for modern dates,
including the UTC transition instants. It needs no external Windows tzdata package.
Only weekday ET-midnight daily windows are accepted. No exchange holiday calendar,
early-close inference, missing-bar interpolation or complete-history claim is made.

Source ticker must match exactly, results counts must match, timestamps must
increase, OHLC relationships must hold and future/malformed windows fail closed.
Duplicate JSON keys, invalid UTF-8, NaN/Infinity, oversized bodies/counts,
contradictory prices and unsupported status forms fail validation. Unknown provider
fields are ignored, never serialized. Missing prices/volume remain null and display
UNAVAILABLE; supplied zero volume remains zero. Missing adjustment is not inferred
from `adjusted=true` in the request; conflicting adjustment is unusable.

| Display state | Meaning |
| --- | --- |
| DEMO | Existing scanner/main chart/trades/performance only; never replaced by provider observations. |
| REAL END-OF-DAY | Validated complete source daily observation dated before the current ET day, within the age policy. No claim that it is the latest complete market session. |
| DELAYED | Provider explicitly marks delayed, or returns a current-session window whose completed EOD status cannot be established. No fixed intraday delay or live entitlement inferred. |
| STALE | Latest supplied daily bar is older than four calendar days. Conservative age policy, not a market-calendar assertion. |
| UNAVAILABLE | Disabled/unconfigured, missing source facts, no result, denied access, rate limit, failed validation or transport failure. No silent demo fallback. |

EOD status requires complete source OHLCV and confirmed requested adjustment.
Source quality explicitly states that recency is not session completeness.
No-result means no observation returned, not proof that no order/trading existed.
Full source dates, original provider status, adjustment, retrieval time, missing
fields and quality remain visible. Excessive age takes STALE precedence while
preserving any provider DELAYED flag. The separate real daily chart displays only
validated complete-source historical bars within the requested prior-day range,
with its actual EOD/DELAYED/STALE label; partial UNAVAILABLE data is never charted.
It has no demo VWAP, trade markers, entry/stop/target lines or signals. The original
main chart remains DEMO, even after a real historical observation is loaded.

The preview is **not a complete scanner row**. Market cap, 10-day average volume,
relative volume, volatility, setup score, intraday change and ABCD signals remain
explicitly missing/unqualified. No calculations fabricate those fields from bars.

## Loopback/read-only boundary

IPv4 `127.0.0.1`, fixed static routes, exact Host validation, no-store, nosniff,
referrer policy and strict CSP remain. The frontend contacts only its loopback
origin; its provider key is never in a URL or JavaScript. No CORS grant, remote
script/font, telemetry, credentials form, localStorage or persistent settings.

New routes: GET/HEAD `/api/market-data` reads safe configuration only; explicit GET
`/api/market-data/observation?ticker=...&view=previous|history` performs one allowed
read or cache lookup. Observation GET requires `X-Velocity-Market-Preview: 1` and
rejects foreign Origin/cross-site fetch metadata. Cross-site images/links lack
that header; custom cross-origin fetches require preflight, which is rejected.
HEAD on the observation route returns 405 (Allow GET) and never requests data.
Write-like methods remain 405; arbitrary paths/queries/hosts fail closed.
No upload, configuration mutation, command execution, broker or order endpoint.

Loopback is not authentication against other software on the same computer. A
local application can make these bounded reads and consume quota, but cannot read
the key through the API. This preview is not authorized for hosting/redistribution.

## Offline validation and Windows commands

Tests use anonymous invented prices in provider-shaped JSON fixtures, never real
API credentials. External sockets/DNS are blocked, transport reads mocked, and any
available installed browser is constrained to an ephemeral loopback server. Tests
cover source/presence/Decimal/date/DST/quality, missing/delayed/stale/corrupt data,
rate/backoff/cache/concurrency, all HTTP failures, credential non-disclosure,
explicit opt-in and user loads, read policy and unchanged demo/safety state.
Optional browser tests install nothing and skip explicitly if unavailable.

Cloud validation on Python 3.12.14 includes 230 focused tests passing: 98 new
market-data tests and 132 Dashboard PRE1/PRE2 regressions, with 39 installed-browser
checks across those suites. Full offline discovery passed: 8,495 tests OK,
skipped=4 (three optional installed-IBAPI checks and the disabled real Gateway
opt-in). Existing V1/V2/V3 compatibility regressions remain passing.
All 150 Python files compiled. A real default module
launch served page/assets/local configuration successfully, retained disabled
preview and BLOCKED authority, rejected observation POST/HEAD with 405 and stopped
cleanly on Ctrl+C. Fixture-fed visual inspection found no browser errors, external
requests or horizontal overflow at desktop/laptop/narrow widths. Scope/security
scans preserve all 64 broker/backtest/strategy files and the existing demo core,
with no credentials/.env, external assets, public broker API, storage or CSP change.

On Windows, with provider access and real Gateway integration disabled for tests:

```powershell
$env:IBKR_RUN_READ_ONLY_TESTS = "0"
py -3.12 -m unittest tests.test_velocity_marketdata tests.test_velocity_dashboard tests.test_velocity_scanner -v
py -3.12 -m unittest discover -v
git ls-files '*.py' | ForEach-Object { py -3.12 -m py_compile $_ }
git diff --check
git diff --cached --check
git status --short
```

No actual key or provider API request is required to pass. These commands do not
opt into market preview. Cloud validation does not establish Windows qualification;
this phase still requires independent review and actual Windows validation.

## Preserved authority boundaries and remaining work

No production file beneath broker/backtest/strategy changes. No journal event,
V1/V2/V3 meaning, SQLite schema/hash, broker API, risk/strategy rule, callback,
authentication, reconciliation or order-management behavior changes.

PAPER only; trading authority BLOCKED; first real PAPER order BLOCKED; Gateway
Read-Only REQUIRED; restart quarantine MANDATORY; AccountMode UNKNOWN; public
snapshot mode None; LIVE UNSUPPORTED. No active PRE9 runtime evidence is loaded;
current remainder and current position remain UNKNOWN.

Real scanner qualification, reference enrichment, point-in-time calculations,
licensed commercial display, real-time feeds, runtime broker evidence and any
signal/execution integration require separate review and authorization. None is
started by this phase.
