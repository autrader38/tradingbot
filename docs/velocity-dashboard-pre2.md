# VELOCITY-DASHBOARD-PRE2 — interactive momentum scanner

PRE2 extends the isolated read-only Velocity dashboard with a configurable local
scanner. It starts from `628331683ad1f499225181e5d73a4c30306a151f`, whose PRE1
independent review and Windows qualification the user reports complete (8,338
tests OK, 14 skipped, 144 tracked Python files compiled). The authorized cloud
working branch is `work`; the normal fast-forward deployment target is
`origin/main`. No branch renaming or history rewriting is required.

This is synthetic screening for visual exploration. It is not live screening,
entry qualification, a strategy change, a recommendation or trading authority.

## Open the dashboard on Windows

From the repository folder with Python 3.12 installed:

```powershell
py -3.12 -m tradingbot_dashboard --open
```

The system browser opens `http://127.0.0.1:8765`. Keep the console open; Ctrl+C
stops the server. Omit `--open` to start without opening a browser. No dependency,
Node/npm, frontend build, internet, account, credentials, provider or Gateway is
needed. The application still uses Python's standard library, bundled HTML/CSS
and vanilla JavaScript/Canvas only.

## Filters and comparison rules

All filters combine using AND, including search and selected volatility labels.
They update the rendered results immediately without page reload or HTTP calls.
The full-width filter panel can collapse without changing settings. These are
dashboard defaults, separate from frozen Strategy Spec v1.0 and active risk rules.

| Filter | Default | Matching rule | Accepted input |
| --- | --- | --- | --- |
| Maximum price | $5.00 | Price strictly **below** maximum | $0.01–$10,000, up to 2 decimals |
| Minimum change | +3.00% | Change **at or above** threshold | −100%–1,000%, up to 2 decimals |
| Minimum market cap | $50 million | Cap **at or above** threshold | $0–$1,000,000 million, up to 2 decimals in millions |
| Minimum average 10-day volume | 1,000,000 shares | Average volume strictly **greater** | 0–1,000,000,000,000 whole shares |
| Volatility | High + Very High | One of the selected synthetic labels | Either, both, or neither |
| Minimum relative volume | Blank: no minimum | Relative volume **at or above**, if supplied | 0–100, up to 2 decimals; blank permitted |
| Minimum setup score | 0 | Score **at or above** threshold | Whole number 0–100, numeric input and linked slider |

Neither volatility selected means zero matches with an explicit explanation.
The labels are fixtures, not a real volatility calculation. READY and scores are
synthetic setup labels, never authorization or a recommendation.

For example, $4.99 passes the default price filter and $5.00 does not; +2.99%
fails and +3.00% passes; $49,999,999 cap fails and $50,000,000 passes; average
volume 1,000,000 fails and 1,000,001 passes. Threshold fixtures isolate these
boundaries in tests.

Inputs reject nonnumeric values, NaN/Infinity, exponent notation, excessive decimal
precision and out-of-range values. Invalid input yields zero matches,
an accessible validation explanation and `aria-invalid`; stale valid results are
not retained. Correcting it immediately resumes screening. Loosen does not repair
invalid inputs silently; Reset remains available.

## Search, ordering and counts

Ticker search is case-insensitive local substring matching, trimmed, with a
12-character limit. It combines with every other filter. Clear Search removes
only search and restores focus to the search field. It never looks up a ticker.

Sort modes are change descending (default), price ascending/descending, relative
volume descending, volume descending and score descending. Equal keys use symbol
alphabetic order. Sorting operates on a new filtered array and never changes the
source fixtures or scores. The selected mode remains visible.

Counts show actual matches / 24 scanned and the number excluded by the combined
filter/search. Defaults produce **13 matches / 24 scanned**, with 11 excluded.
Empty results explain whether input, volatility, search or thresholds caused the
empty state; counts are never hardcoded.

## Reset and Loosen

Reset to Defaults restores every numeric default, both volatility labels, blank
search and change-descending sort. It updates the slider and displayed results.

Each Loosen action visibly applies:

- Maximum price +$2.50, capped at $10,000.
- Minimum change −1 percentage point, floored at −100%.
- Minimum cap −$10 million, floored at zero.
- Minimum average volume −250,000 shares, floored at zero.
- Active minimum relative volume −0.50, floored at zero; blank stays blank.
- Minimum score −10, floored at zero; both volatility categories selected.

Search and sort remain unchanged. An accessible summary reports the resulting
thresholds. This action affects presentation only, never risk or strategy state.

## Synthetic universe and chart continuity

Twenty-four bundled synthetic symbols retain ABCD, VELA, NEXA, QBIT and MTRX.
They vary price, change, cap, average volume, volatility, relative volume, score and
state. None is a claim about any real similarly named security. Every symbol has
deterministic 1m, 15m, 1D and 1W OHLCV arrays. Each timeframe's final candle close
equals its scanner price; the snapshot validates that relationship. No candle is
generated or changed by filter/sort/search interactions.

Click a matching symbol to load its existing chart. If a filter removes the viewed
symbol and matches remain, the chart switches to the first currently sorted match
with an announcement. If no matches remain, the chart stays stable and visibly
states it is outside filtered results. Timeframe switching still works. Clicking
an independent example trade may intentionally view a nonmatching symbol; the same
outside-results notice makes that distinction explicit. The next scanner change
reconciles selection again. No viewed symbol is falsely presented as a match.

The scanner keeps six primary fields readable and offers keyboard-accessible
Market Profile details for exact dollar cap, average shares, synthetic volatility,
volume and setup. Results scroll within the panel. Filters use six columns on
large screens, three on laptops and two on narrow screens; the existing responsive
chart/safety layout, navigation, VWAP, volume, hover, zoom, example levels/markers,
demo trades and equity curve remain available.

## Snapshot and security boundaries

Frozen/slotted `ScannerRow` adds validated integer `market_cap` and
`average_volume_10d` (0–1 trillion; bool rejected), plus a closed `volatility`
classification. Explicit allowlisted serialization includes only these reviewed
primitive values alongside existing scanner fields and `demo: true`. No generic
object dump, account field or arbitrary payload is accepted.

Browser settings are page-local; Reset to Defaults restores the initial controls.
There is no localStorage,
cookie, saved preference, mutation endpoint or server-side filter configuration.
The server and CLI are unchanged: IPv4 127.0.0.1 only, port 8765, exact fixed routes,
Host validation, CSP, traversal rejection, GET/HEAD only and 405 for write-like
methods. Static assets have no remote resource dependency, CDN, font, analytics
or provider fetch. `/api/snapshot` remains the sole fixed initial JSON request.

Broker, backtest and strategy production files are untouched. Journal V1/V2/V3,
SQLite, callbacks, execution policy, authentication, barriers and risk rules are
unchanged. All synthetic market panels explicitly say DEMO, including the
persistent **DEMO MARKET DATA — NO LIVE FEED** banner.

## Validation and qualification

Standard-library dashboard tests cover the model, fixture consistency, source
bounds, safety flags, semantic controls, fixed HTTP policy and isolation. Optional
browser tests run only with already installed Playwright and Chromium, otherwise
explicitly skip and require manual browser checking. There is no browser dependency
installation or requirement to run the dashboard. Browser checks use a disposable
headless browser with only an ephemeral loopback server; they never open the user's
browser. They exercise actual rendered filter logic, not a duplicate Python filter
implementation, and reject external/write requests.

Cloud implementation checks on Python 3.12.14:

- Focused dashboard/scanner suite: 132 passed, including 26 actual installed
  Chromium interaction tests; no skips.
- Full offline unittest discovery: 8,397 tests OK, skipped=4 (three optional
  installed-IBAPI checks and the explicitly disabled real Gateway opt-in test).
  Existing V1/V2/V3 compatibility regressions remain passing. The CSP-safe browser
  wait also passed 12 repeated checks of the previously affected setup scenario.
- All 145 Python files compiled (including the new focused test module).
- The real module entrypoint served root/assets/snapshot over loopback; POST was
  rejected with 405 and Ctrl+C stopped cleanly with exit zero.
- Rendered layouts at 1920×1080, 1440×900, 1024×768 and 390×844 had no horizontal
  page overflow, browser errors or external resource requests.
- Scope checks retain all 64 existing broker/backtest/strategy production files
  byte-for-byte; server, CLI and package exports remain unchanged. Secret/account,
  credential/.env, external-resource, broker-call, storage and export scans passed.

Before MARKET-DATA-PRE1, the user subsequently reported PRE2 independent review and
actual Windows qualification complete at `a961e625afb2cc01c8cc0467f2b1284c905164f2`:
8,371 tests OK, skipped=15, 145 Python files compiled, compile/diff exit zero,
clean tree/index and synchronized Windows main/origin/main. The count difference
reflects optional installed-browser tests. Cloud tests alone did not establish
that Windows qualification.

## Safety and remaining work

PAPER only; trading authority BLOCKED; Gateway Read-Only REQUIRED; restart quarantine
MANDATORY; AccountMode UNKNOWN; public snapshot mode None; LIVE UNSUPPORTED. No active
PRE9 runtime evidence is loaded. Current remainder and current position remain
UNKNOWN. No dependency is installed, no provider or IBKR connection is made, and no
broker request/order or Gateway change is part of this phase. The first real PAPER
order remains blocked.

Real market-data integration, authentic read-only runtime evidence adapters,
currentness, ownership/inventory, protection/order management, recovery and any
PAPER authorization remain separately reviewed future work. PRE2 does not begin
those phases.
