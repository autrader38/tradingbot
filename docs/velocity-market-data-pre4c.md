# VELOCITY-MARKET-DATA-PRE4C — Real multi-day EOD screener dashboard

Starting checkpoint: `ee067cac639ee332d019af913854f59a2c48b35b`.
The user reported successfully retaining eleven historical dates and PRE4B Windows
focused qualification: 101 tests run, one skipped, zero failures. All development
data in PRE4C is synthetic; no real provider credentials, Massive calls or IBKR
connections were used. PRE4C requires independent review and actual Windows
qualification before any subsequent milestone.

## Run and use after restarting

Retained dates and screening results are memory-only. Restarting requires opening
the dashboard again and manually reloading source dates; no disk adoption occurs.
Keep your personal `MASSIVE_API_KEY` configured only in the server environment,
outside chat, source files and browser code. With that separately configured key:

```powershell
py -3.12 -m tradingbot_dashboard --market-data-preview --open
```

1. In **REAL EOD MARKET DISCOVERY**, manually load the target Eastern date and up
   to ten earlier dated summaries. Each click still permits at most one provider
   request. Respect the shared four-per-minute / fifteen-second gate and manual
   backoff feedback. Do not assume weekdays establish a market calendar.
2. In **REAL MULTI-DAY EOD SCREENER**, select a retained target date. **Refresh
   retained dates** reads local metadata only; it never collects or evaluates.
3. Adjust research thresholds, then click **Evaluate selected date**. Only this
   explicit action invokes PRE4A through PRE4B's original internal handoff.
4. Search, sort, choose an outcome view and page through the published result.
   These operations use the compact local projection, with zero provider calls
   and no PRE4A recalculation. Each response contains at most fifty rows.
5. Target/threshold edits clear the displayed view and require another explicit
   evaluation. **Reset research filters** restores defaults without evaluating.
   Every evaluation visibly resets search, sorting and outcome view to their
   defaults. These three view controls are disabled until evaluation finishes;
   afterwards they filter the retained result locally. Original DEMO/PRE2 controls
   and frozen strategy settings are unchanged.

The default command remains fully offline DEMO, with no collection or evaluation:

```powershell
py -3.12 -m tradingbot_dashboard --open
```

## Exact research and interpretation

PRE4A's production evaluator and formulas are unchanged. PRE4B's scoped handoff
accepts the validated PRE4A filter value and supplies exact retained summaries in
explicit ascending date order, excluding dates later than the selected target.
No caller-supplied source rows or browser-invented calculations enter this path.

| Research filter | Default | Comparison |
| --- | --- | --- |
| Target adjusted EOD close | $5 | Strictly below |
| Observed close-to-close gain | +3% | Greater than or equal |
| Previous ten-observation average volume | 1,000,000 shares | Strictly above |
| Optional EOD relative volume | Disabled | Greater than or equal when enabled |

Gain remains `100 * (target close - previous observed close) / previous observed
close`. The prior close must actually exist for the same validated ticker; it is
not declared the immediately preceding trading session. Average volume uses
exactly ten prior observed volumes, excludes the target and is unavailable with
fewer observations. RVOL is target volume / that average only when the denominator
is positive. It is a historical daily ratio, not intraday RVOL.

PRE4A calculates and assesses the first three criteria using exact Fractions.
PRE4C's optional RVOL criterion compares the returned exact Fraction to the validated
threshold. No quantity/price/change/average/RVOL formula is reimplemented in JavaScript.
Decimal input/source representations are retained where supplied; qualification
does not depend on Decimal context or rounded displays. JSON retains exact ratio
numerator/denominator strings and rounded display strings. Hovering ratio cells
shows the exact fraction; the original target close text is also preserved.

Classification precedence remains conservative:

- Any established criterion failure: `PRELIMINARY_NOT_MET`, retaining missing facts.
- Otherwise, a required unsupported metric: `INSUFFICIENT_HISTORY`.
- Otherwise: `PRELIMINARY_MATCH` for only these enabled historical research filters.

Eleven dated market responses do not prove ten prior observations for each ticker.
The table preserves missing metrics, specific unavailable reasons (including zero
baseline volume), the actual prior close date, each supporting prior observation
date, the ten-volume baseline dates when available, and the verbatim target source
timestamp. Metadata lists each independently requested date and original retrieval
time. No missing candle/session is synthesized or borrowed from DEMO/PRE2.

All rows retain unverified session continuity and adjustment vintage. Independently
retrieved split-adjusted summaries do not prove common adjustment vintage, split
factor continuity, an atomic snapshot or complete U.S. coverage. No instrument,
listing, volatility category, ABCD setup or intraday momentum is qualified.
**Market cap >= $50M remains UNVERIFIED**; dated reference research stays separate
in PRE2 and is neither fetched nor incorporated by the multi-day screener.
Full strategy qualification remains unavailable. Preliminary matches are not entry
signals, protection/recovery authority or trading permission.

## Compact publication, lifetime and resource policy

`tradingbot_marketdata/screener.py` privately projects PRE4A results into frozen,
slotted scalar rows: validated ticker, source close/timestamp, exact metric Fractions,
support dates and closed assessment/reason values. It retains no source bars,
history wrappers, complete PRE4A result, credential or raw provider body.

One preview retains at most one projection, with at most 30,000 target rows and a
**64 MiB charged projection limit**. Charges include row/field/Fraction integer/date
objects, deliberately double-count aliases, use a 2,048-byte per-row minimum, and
allow construction pointers plus fixed metadata overhead. Additions are checked
while building; capacity failure publishes no prefix. No per-query/evaluation cache
accumulates. Projection charge/limits are reported in bounded result metadata.

The extra 64 MiB reservation raises the PRE4B **allocation-accounting envelope to
832 MiB** per preview: unchanged 384 MiB source / 192 MiB decoder / 128 MiB PRE4A /
64 MiB preview reserves, plus 64 MiB projection. PRE4B source caps (eleven dates,
180,000 received rows), provider response limits and quota admission are unchanged.
This remains conservative allocation accounting, not an operating-system RSS cap.

During evaluation, PRE4A's temporary result may coexist with the prior compact
projection. The prior projection is released before building the replacement;
old and new full projections never coexist under the owner. Publication happens
only after the first bounded page is prepared. The full PRE4A result is released
at scoped handoff exit. Pagination holds the preview lock through local projection
filtering/sorting and produces only page dictionaries, preventing readers from
pinning an entire superseded projection across replacement. Workspace fits the
existing preview reserve. Sorting copies bounded row references, not source models.

Fresh collection additions, explicit collection clear and shutdown invalidate the
projection. Retained-date reuse preserves it. A positive local evaluation counter
rejects pages from superseded evaluations; it grants no source/trading authority.
Collection metadata revisions and browser request revisions reject late responses
for stale collections, searches or edited targets/thresholds. Invalid requests may
leave a previous valid server projection intact, but the UI shows no result for the
invalid draft. Memory/source/internal failures have fixed sanitized categories.
Interruptions preserve their exact objects and release the original in-flight guard.

Search is case-insensitive and combines with the outcome view using AND logic.
Sorts use exact server values, missing last, then alphabetical ticker ties: observed
gain, price ascending/descending, prior average volume, RVOL, target volume, ticker.
Whole-target match/failure/insufficiency counts remain separate from search/view
counts. Empty views do not establish market absence. Responses are also byte-capped
at **256 KiB**, with no export/download endpoint or unbounded browser rendering.

## Local HTTP and protected scope

- `GET/HEAD /api/market-data/multiday`: bounded status only, never evaluates.
- `GET /api/market-data/multiday/evaluate`: explicit target and finite bounded
  threshold query, existing local custom header and origin/fetch-site checks.
  HEAD is rejected; this action performs zero provider requests/quota reservations.
- `GET/HEAD /api/market-data/multiday/page`: bounded evaluation ID, search/sort,
  outcome and page; never reevaluates or collects.

Host validation, fixed route/asset allowlists, CSP (without unsafe-eval), loopback
binding, no CORS and GET/HEAD-only policy remain. All write-like methods return 405.
There is no polling, automatic evaluation, provider enrichment, external frontend
resource/dependency, persistence, cookie, localStorage or subscription-data export.
Original single-date discovery, configured watchlist, PRE2 shortlist/reference
workflow, DEMO chart/scanner and safety snapshot are preserved.

Broker/backtest/strategy production, authorization, routing, risk, account, journal,
Gateway and wire-policy files are unchanged. PAPER only, trading BLOCKED, Gateway
Read-Only REQUIRED, restart quarantine MANDATORY, AccountMode UNKNOWN, snapshot
mode None and LIVE UNSUPPORTED remain. The first real PAPER order remains blocked.
No Resistance Squeeze, Level 2, pattern recognition, execution simulation or broker
execution is implemented. Personal/non-commercial local research only.

## PRE4C review repairs

Review of checkpoint `ce52ce4a131d6088c433f14bd89bfccf37eb0aa1` identified two
display defects and an unsafe PRE4B test shutdown:

- Decimal's default string conversion changed small accepted control values such
  as `0.000000000001` into `1E-12`. The browser correctly rejected the differing
  applied-settings text, leaving no displayed result. Filter metadata now uses
  fixed-point Decimal formatting without rounding, preserving scale and signed
  zero. Exact calculations, threshold bounds and source observations are unchanged.
- Search/outcome edits while evaluation was pending could relabel default-view
  rows. Search, sort and outcome controls now remain disabled during evaluation
  and are restored on both success and failure. Rendering also checks all three
  returned view settings against the controls, including programmatic edits.
  Target/threshold changes still invalidate pending results; no automatic
  reevaluation, provider call or additional request capability was added.
- The PRE4B server-close test called `server_close()` while another thread still
  ran `serve_forever()` and selected the listening socket. It now waits for
  `shutdown()` to finish that loop before closing the socket, following the
  standard-library lifecycle. All retained-evidence cleanup assertions remain,
  with checks that shutdown leaves the socket open and close releases it. Other
  threaded dashboard fixtures already use shutdown before close. The production
  entrypoint closes only after its serving loop exits and is unchanged.

The unsafe test ordering is confirmed by inspection. The reported Windows native
exit `0xc0000008` has not been reproduced on this Linux environment, so its exact
native cause and resolution remain **unverified Windows qualification blockers**
until the repaired test and full Windows suite complete without that exit. Linux
success does not establish Windows approval. No broker, quota, decoder, security,
calculation, safety-state or production server-lifecycle change is included.

Repair verification on Linux CPython 3.12.14 passed: 48 PRE4C tests (including
twelve installed-browser checks), 104 PRE4A/PRE4B tests and 587 existing dashboard /
market-data regressions, totaling 739 selected tests. A complete offline discovery
run passed **9,004 tests, 4 skipped, zero failures** (1,212.680 seconds). All 162
tracked Python files compiled. Both diff checks and secret/account/environment,
external-resource, private-export, broker-operation, persistence and protected-scope
scans passed. No dependencies, real provider calls, API-key access, IBKR requests
or Gateway changes were involved. These results do not provide Windows clearance.

## Windows qualification

```powershell
git pull --ff-only
git rev-parse HEAD
$env:IBKR_RUN_READ_ONLY_TESTS = "0"
py -3.12 -X faulthandler -m unittest tests.test_velocity_grouped_collection.HTTPTests.test_server_close_clears_retention_even_if_preview_still_referenced -v
if ($LASTEXITCODE -ne 0) { throw "Server-close qualification failed: $LASTEXITCODE" }
py -3.12 -X faulthandler -m unittest tests.test_velocity_multiday_screener -v
py -3.12 -m unittest tests.test_velocity_multiday tests.test_velocity_grouped_collection -v
py -3.12 -m unittest tests.test_velocity_market_discovery tests.test_velocity_market_discovery_diagnostics tests.test_velocity_marketdata tests.test_velocity_eod_research tests.test_velocity_dashboard tests.test_velocity_scanner -v
py -3.12 -X faulthandler -m unittest discover -v
git ls-files '*.py' | ForEach-Object { py -3.12 -m py_compile $_; if ($LASTEXITCODE -ne 0) { throw "Compile failed: $_" } }
git diff --check
git diff --cached --check
git status --short
```

Tests use only synthetic retained summaries and mocked providers; no API key is
needed. Installed Chromium/Playwright checks remain optional. Source text reads
explicitly use UTF-8. Focused resource fixtures cover maximum 30,000-row publication
and eleven-date / 12,630-ticker complete history, including extra projection memory.
Windows review/qualification must report actual platform counts and skips.

Initial CPython 3.12 Linux allocation measurements for the synthetic fixtures:
30,000 target rows used a 61,928,192-byte projection charge and approximately
24.4 MB peak extra traced allocations. Eleven dates / 12,630 complete-history
tickers used a 26,076,512-byte projection charge, approximately 8.3 MB retained
extra allocations and 19.4 MB peak extra allocations. Tracing starts after source
models exist; PRE4B separately qualifies source/decoder allocation. These are
fixture-specific measurements, not Windows qualification or an RSS guarantee.

Focused cloud validation passed: 43 PRE4C tests (including eight installed-browser
checks), 104 PRE4A/PRE4B regressions, and 587 existing dashboard/market-data tests.
The UI was also visually inspected using only synthetic loopback fixtures, with
narrow-width keyboard/overflow checks. All 162 final Python files compiled, and
the real module entrypoint passed offline DEMO startup, local route/method checks
and clean SIGINT shutdown. No dependencies were installed. Existing dashboard
guards were extended only for the exact new table, script, inputs and private
projection import; engine access guards now inspect actual imports/calls rather
than treating the new URL word as an engine import. CSP remains unchanged.

Complete offline discovery passed: **8,999 tests run, 4 skipped, zero failures**
(1,289.967 seconds). Both diff checks, secret/account/environment-file scans,
private-export, external-resource, request/persistence and protected-scope checks
passed. PRE4A production calculations, PRE3D decoding, provider transport, shared
quota/backoff, PRE2 calculation/reference models and safety snapshot production
remain unchanged. No real provider/IBKR calls, credentials, dependency installation
or Gateway changes occurred. Independent review and Windows qualification remain
required; this cloud validation does not provide either clearance.
