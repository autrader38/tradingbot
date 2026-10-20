# VELOCITY-MARKET-DATA-PRE4A — Offline multi-day screening foundation

Starting checkpoint: `b43b7d632c67c1d9a88fba29d8e7d6bede43adda`.
The user reported successful personal-use validation of 12,630 grouped observations
and 2,080 preliminary sub-$5 candidates for October 16, 2026. No personal provider
data or credentials were accessed for this phase; all development fixtures are synthetic.

## Isolated input and validation contract

Private module `tradingbot_marketdata.multiday` exposes no public exports. Its
`_screen_grouped_history(summaries, target_date, filters=...)` accepts an exact tuple
of 1–11 exact PRE3D `_GroupedSummary` values and an explicit exact `date` target.
Dates must be unique and strictly increasing; the final summary must match the
target. Inputs are not reordered to repair provenance. Target output is ordered
alphabetically by validated ticker, independent of row ordering within a summary.

All source models, counts, numeric fields and identities are revalidated. Requested
dates must be supported modern Eastern weekdays; that is not a holiday/calendar
qualification. Each source timestamp must lie on its requested Eastern date or
exactly the next midnight boundary, preserving PRE3D's contract. Repeated ticker
identity within a summary fails. Across dates, the same ticker's source timestamp
must strictly increase, rejecting repeated source identity even across the allowed
midnight boundary. Validation includes unrelated historical rows, not only targets.

Any incompatible source makes the entire batch `SOURCE_INCOMPATIBLE`, with a fixed
reason enum, no rows, no partial coverage and no received-count claim. Arbitrary
error text and unvalidated source values are not returned. Invalid research filters
raise a fixed sanitized request error. BaseException interruptions propagate without
partial publication or safety-state mutation.

Only tickers actually present in the target summary receive a row. Historical-only
tickers cannot become candidates. No observations or missing sessions are synthesized.
The result retains exact sanitized target/history bar references and requested
source dates, plus validated per-date coverage counts. It does not retain unrelated
tickers' observations in a candidate row or expose raw provider objects.

## Exact calculations and research filters

`_MultiDayFilters` defaults are presentation-independent research values, not frozen
Strategy Spec v1.0 rules: max price $5, minimum observed change 3%, minimum average
volume 1,000,000. Decimal inputs use existing finite/bounded conventions. Computation
converts validated source Decimals to exact Fractions; no Decimal rounding or ambient
precision influences qualification. Exact source Decimal shapes remain in retained bars.

| Metric | Formula and source support | Default comparison |
| --- | --- | --- |
| Price | Target bar close | `< 5` |
| Observed close-to-close change | `100 * (target close - latest prior observed close) / latest prior observed close` | `>= 3` |
| Previous ten-observation average volume | Sum of exactly ten prior ticker volumes / 10, excluding target | `> 1,000,000` |
| EOD relative volume | Target volume / prior ten-observation average, only if positive | Descriptive; no new filter |

The previous close is the latest actual prior supplied observation for that ticker,
not a claim about the immediately preceding market session. Fewer than ten prior
ticker bars makes the average and relative volume unavailable. A zero average is
valid observed volume but cannot be a divisor; relative volume remains unavailable
with `ZERO_BASELINE_VOLUME`. Missing prior observations and missing ten-bar support
have separate fixed gap categories. A corrupted/missing close in a supplied bar is
incompatible source evidence, not a price to be guessed or silently skipped.

Every row retains its target, all available earlier observations (at most ten),
the exact previous supporting date and, when available, all ten baseline dates.
Price/change/volume source dates remain distinct from economic event time. There
is no automatic display rounding here; future displays must preserve exact comparisons.

## Two independent classification axes

Filter outcome precedence is deterministic:

1. Any available enabled criterion fails: `PRELIMINARY_NOT_MET`; missing criteria remain explicit.
2. Otherwise, a required metric lacks support: `INSUFFICIENT_HISTORY`.
3. Otherwise: `PRELIMINARY_MATCH`.

Separately, every usable batch/row remains `SESSION_CONTINUITY_UNVERIFIED`. This is
not overwritten by a preliminary match. Invalid batches instead carry the source
qualification `SOURCE_INCOMPATIBLE` and no usable row metrics. A match is only the
three supported observed-data comparisons, never full EOD strategy qualification.
An empty target yields zero candidate rows, not proof of market absence or no trading.

## Adjustment and coverage limits

PRE3D's qualified decoder accepts only split-adjusted grouped data; `_GroupedSummary`
therefore represents that source policy implicitly. It has no independent adjustment
flag, retrieval instant, split factors or adjustment-vintage field. PRE4A does not
invent such metadata, accept another policy-bearing representation or relax PRE3D.
Unadjusted decoder input and foreign/mixed source types fail. Matching type policy
does **not** establish that separately obtained dates share a synchronized corporate-
action adjustment basis. Split-like price changes are retained as observations;
no split correction, inference or economic continuity is manufactured.

All results explicitly retain these limitations: response coverage is not complete
U.S. market coverage; no verified consecutive sessions or atomic snapshot; adjustment
vintage/split-factor continuity unverified; market cap >=$50M unverified; instrument,
exchange and active-listing eligibility unverified; no synchronized reference data;
High/Very High volatility and ABCD setups unqualified; no intraday momentum/entry
signal, currentness or trading authority. Personal non-commercial research only.

## Resource and memory policy before PRE4B retention

Hard cardinality bounds are 11 summaries, 30,000 received rows per summary,
330,000 aggregate received rows, 30,000 target tickers and 330,000 retained dated
references at most. Existing bounded source numeric/string representations apply.
All collection bounds are checked before building an index. A validation-only
last-timestamp index has at most 330,000 distinct tickers and is released before
the target-history index is built. Only target tickers are indexed for calculation.
No global cache or retained owner state is introduced.

Validation is linear in supplied rows; indexing/calculation is linear in rows plus
alphabetical target sorting. At most eleven references are retained per target.
Source objects/Decimals are not copied or flattened. Two worst-case synthetic tests
exercise 330,000 rows with fully overlapping and fully disjoint ticker universes.
They enforce a **128 MiB CPython auxiliary-allocation test ceiling**, excluding
pre-existing input models. This is not a cross-interpreter RSS guarantee or a limit
for the source snapshots themselves.

PRE4B must separately review total retained-source/decoder/process memory, ownership,
explicit eviction and a concrete byte budget before activating retention. It must
preserve the above cardinality limits and all provider quotas. PRE4A defines and
tests bounded offline consumption; it does not authorize collection or retention.

## Inactivity and verification

No dashboard, provider adapter or runtime imports/calls this module. No provider
requests, API-key lookup, clocks, I/O, locks, timers, polling, retry, persistence,
public endpoint or subscription-data publication is added. PRE2 calculations,
PRE3D decoding, HTTP security, shared four-request/minute and fifteen-second gate
remain unchanged. No dependencies. No broker/backtest/strategy/risk/account/journal/
execution/Gateway changes. PAPER only; first real PAPER order BLOCKED; Gateway
Read-Only REQUIRED; restart quarantine MANDATORY; AccountMode UNKNOWN; public
snapshot mode None; LIVE UNSUPPORTED.

PRE4A requires independent review and actual Windows qualification. PRE4B collection
and PRE4C dashboard activation require separate review/authorization; neither is
implemented. No live scanning, execution or public website is authorized.

Cloud/Linux validation: 64 focused PRE4A tests and 587 existing dashboard/market-data
regressions passed (651 combined, including 76 installed-browser checks). Full offline
discovery ran 8,916 tests, with 4 skipped and no failures. All 158 final Python files
compiled. Both diff checks and secret/account, environment-file, public-export,
network/I/O, inactivity and protected-file scope scans passed. These results do not
constitute actual Windows qualification.

Windows qualification, after updating and verifying the reviewed final commit:

```powershell
git pull --ff-only
git rev-parse HEAD
$env:IBKR_RUN_READ_ONLY_TESTS = "0"
py -3.12 -m unittest tests.test_velocity_multiday -v
py -3.12 -m unittest tests.test_velocity_market_discovery_diagnostics tests.test_velocity_market_discovery tests.test_velocity_eod_research tests.test_velocity_marketdata tests.test_velocity_dashboard tests.test_velocity_scanner -v
py -3.12 -m unittest discover -v
git ls-files '*.py' | ForEach-Object { py -3.12 -m py_compile $_; if ($LASTEXITCODE -ne 0) { throw "Compilation failed: $_" } }
git diff --check
git diff --cached --check
git status --short
```
