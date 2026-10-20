# VELOCITY-MARKET-DATA-PRE4B — Bounded manual multi-day EOD collection

Starting checkpoint: `055d17298c5694e3ba004794f901323a87f859b5`.
The user reported all 64 PRE4A focused Windows tests passed. Its complete offline
suite passed during development. PRE4B requires independent review and actual
Windows qualification before PRE4C. No real provider data or credentials were
accessed during development; all fixtures and memory measurements are synthetic.

## Manual workflow and lifetime

Launch the dashboard with the existing optional preview flag and your separately
configured server-side `MASSIVE_API_KEY`. The normal command remains offline DEMO:

```powershell
py -3.12 -m tradingbot_dashboard --open
# Optional personal-use preview, only with credentials configured outside chat:
py -3.12 -m tradingbot_dashboard --market-data-preview --open
```

In **REAL EOD MARKET DISCOVERY**, choose one past Eastern weekday and click
**Load EOD Market Summary**. Repeat manually for each desired date, at most eleven.
Each new date uses at most one request to the existing fixed grouped daily endpoint,
with `adjusted=true&include_otc=false`. There are no new endpoints at Massive,
automatic requests, chained histories/references, timers, polling, retries or refresh.
Four requests per rolling minute and at least fifteen seconds between attempts
remain shared process-wide with every existing provider endpoint. Provider backoff
also survives collection clearing.

Successful loads retain the exact decoded `_GroupedSummary` and its canonical UTC
retrieval datetime in a frozen/slotted private `_RetainedSummary`. No raw body,
provider request ID, header or credential is stored in this collection. Retention
is session memory, distinct from the existing one-date five-minute HTTP cache.
Repeated dates reuse exact retained evidence even after that cache expires, without
quota use or timestamp renewal. Responses identify retention and HTTP-cache validity
separately; the legacy `cached` flag continues to mean reused local evidence.
There is no refresh operation in PRE4B.

The existing discovery table still shows one explicitly selected date and at most
fifty rows per page. As before, a failed provider load clears that active view;
previously retained dates remain available to select again without provider work.
Capacity rejection before a request leaves the current view and retained dates
unchanged. No failed response adds a date or publishes partial rows.
Quota-independent admission failures have fixed collection/date/memory reasons.
An allocation failure during a load reports the safe memory category with
sixty-second manual-retry backoff; raw exceptions are never displayed.

**Clear retained dates and discovery view** explicitly releases every grouped
reference: retained dates, current discovery view and grouped HTTP cache. It does
not change the configured watchlist, PRE2 shortlist or loaded per-ticker research.
It does not reset quota/backoff, change strategy or enable trading. Restart/shutdown
also clears collection state. `server_close()` closes the owner and releases its
references even if another Python reference to the preview survives. An in-flight
response cannot repopulate a closed owner; existing HTTPS timeouts remain bounded.

## Exact evidence and ordering

Dates can be loaded in any user-selected order. Status lists that actual load order,
each independently requested Eastern date, original retrieval time and received/
validated counts. A separate explicit date index supplies ascending dates to PRE4A;
it does not reorder timestamps within sources or repair incompatible evidence.

Before atomic retention, PRE4A's existing strict `_validate_sources` validates the
proposed dated set, including duplicate identities and cross-date timestamp rules.
Incompatibility rejects the proposed addition and preserves the prior collection.
PRE3D's decoder acceptance policy is unchanged. Empty valid weekday responses may
be retained with zero rows, without declaring a session or manufacturing bars.
Weekends/current dates/invalid requests remain rejected. Holidays and missing ticker
observations are not filled in. Date count never proves ten prior bars per ticker.

Split adjustment is the existing `_GroupedSummary` source contract, not proof that
independent requests share adjustment vintage, split factors or economic continuity.
No market calendar, full U.S. coverage, reference alignment, market-cap eligibility,
instrument eligibility, volatility category or ABCD qualification is inferred.
Retained historical evidence is not currentness. Personal/non-commercial use only;
no commercial/public redistribution of subscription data.

## Resource ownership and enforced budgets

One preview owns one locked collection. Preview operations acquire the existing
preview lock before the collection lock. No provider I/O or PRE4A calculation holds
either lock. The existing single in-flight guard excludes other provider requests,
shortlist changes, clear and internal evaluation. Collection status remains local;
source filtering is serialized to bound concurrent pagination workspace.

Limits per preview instance:

| Resource | Enforced bound |
| --- | --- |
| Retained dates | 11, no eviction/replacement |
| Source response | Existing 8 MiB body / 30,000 received rows / 12-second timeout |
| Retained received rows, including exclusions | 180,000 (narrower than PRE4A's 330,000) |
| Charged retained source memory | 384 MiB |
| Reserved grouped decode workspace | 192 MiB |
| Reserved single scoped PRE4A evaluation workspace | 128 MiB |
| Reserved pagination, PRE2 preview and owner overhead | 64 MiB |
| Total accounted working-memory envelope | 768 MiB |

Before spending quota, admit a maximum 30,000-row source and its conservative
memory charge alongside all reserves. A request may therefore be refused even if
an unknown smaller response would have fit. There is no speculative download at
capacity, silent truncation, eviction or row skipping. After decoding, independently
charge/recheck the actual source before committing it.

Memory charges include source/tuple/datetime headers via `sys.getsizeof`, and the
greater of 2,048 bytes or measured bar-plus-field object sizes for each accepted
row. Aliased/interned values are charged repeatedly, never deducted. Excluded source
rows also consume the 2,048-byte floor. Fixed header allowance covers date-index and
entry bookkeeping. Temporary body/text/JSON/model overlap uses the separate decode
reserve: existing byte/token/container/number/row caps remain mandatory. The reserve
allows 4,096 bytes per maximum decoded row plus body/text and bookkeeping headroom.
PRE4A's reviewed 128 MiB auxiliary ceiling is reserved separately. The old grouped
cache/view alias exact retained sources instead of copying them.

This is enforced conservative allocation accounting, not an operating-system RSS
limit: Python allocator/interpreter/browser overhead and independent application
instances are not a process-wide memory sandbox. The provider quota remains
process-wide. Supported CPython measurements must be requalified on Windows.
The representative test generates, decodes and retains eleven 12,630-row sources
(138,930 observations), then explicitly evaluates PRE4A under `tracemalloc`, checking
the working envelope. A 30,000-row source separately checks the admission charge.
Fault injection verifies every limit rejects safely and preserves prior evidence.

Initial CPython 3.12 Linux measurement for that synthetic eleven-date fixture:
112,253,052 bytes retained traced allocations, 119,475,394 bytes collection peak,
128,436,671 bytes peak including PRE4A evaluation, and a conservative retained-source
charge of 285,686,984 bytes. Measurements include generated response bodies, decoding
and source models; interpreter baseline/other processes are outside tracemalloc.
These are fixture-specific measurements, not a promise about personal provider data
or Windows allocator behavior. The fixed admission policy, rather than a measured
average, governs capacity.

## Bounded status and explicit internal handoff

`GET/HEAD /api/market-data/collection[?target=YYYY-MM-DD]` returns at most eleven
metadata entries, memory charges, in-flight state and target/input availability.
Ten earlier retained dates is explicitly **ticker history unverified**, not a match.
No per-ticker multi-day metrics or scanner result is sent to the browser.

`GET /api/market-data/collection/clear` requires the existing explicit local custom
header, valid Host and same-origin checks. HEAD and all write-like HTTP methods
are rejected for this action, matching existing session-selection conventions.
No other clear route/query is accepted. CSP, traversal protection, loopback-only
binding, no CORS and sanitized failures remain unchanged.

`_MarketDataPreview._collection_evaluation(target)` is an internal context manager,
not an HTTP route. It excludes other collection/provider operations while in scope,
selects only dates at/before the explicitly retained target and invokes the original
pure PRE4A evaluator. Exact source references and missing ticker histories survive.
Results are not stored on the owner; callers must release them at scope exit.
No runtime caller activates this path in PRE4B. PRE4C must separately qualify any
result retention, table/filter controls and bounded publication. No automatic
screening, ranking or reference enrichment is implemented.

## Safety and Windows qualification

No persistence, database, cookie, localStorage, external frontend dependency or
additional provider endpoint. No broker/backtest/strategy/risk/account/journal/
execution/Gateway changes. PAPER mode, trading BLOCKED, Read-Only REQUIRED, restart
quarantine MANDATORY, AccountMode UNKNOWN, snapshot mode None and LIVE UNSUPPORTED
remain unchanged. The first real PAPER order remains blocked.

```powershell
git pull --ff-only
git rev-parse HEAD
$env:IBKR_RUN_READ_ONLY_TESTS = "0"
py -3.12 -m unittest tests.test_velocity_grouped_collection -v
py -3.12 -m unittest tests.test_velocity_multiday tests.test_velocity_market_discovery tests.test_velocity_market_discovery_diagnostics tests.test_velocity_marketdata tests.test_velocity_eod_research tests.test_velocity_dashboard tests.test_velocity_scanner -v
py -3.12 -m unittest discover -v
git ls-files '*.py' | ForEach-Object { py -3.12 -m py_compile $_; if ($LASTEXITCODE -ne 0) { throw "Compile failed: $_" } }
git diff --check
git diff --cached --check
git status --short
```

All provider calls in these tests are mocked; no real API key is required. The
focused resource test prints only synthetic numeric memory measurements. Browser
checks use already installed Chromium/Playwright when available; no installation
is required. PRE4B stops before PRE4C or any trading milestone.

Cloud/Linux focused validation: 40 PRE4B tests passed (including three installed-
browser checks), 64 PRE4A tests passed, and 587 existing market-data/dashboard
regressions passed (including 76 browser checks). The allocation-failure correction
also passed a targeted 15-test failure/concurrency/browser run. All 160 final Python
files compiled. Real-entrypoint offline DEMO startup, loopback routes and normal
SIGINT shutdown passed. An earlier existing browser timing check missed its three-
second deadline during parallel stress tests, then passed both isolated and complete
focused reruns without changing its assertion. These are not Windows qualification.

Final complete offline discovery passed: 8,956 tests run, 4 skipped, zero failures
(1,071.494 seconds). Both Git diff checks, credential/account/environment-file,
private-export, manual-request/no-persistence and protected-scope scans passed.
The original shared quota/backoff definitions and PRE3D decoder, PRE4A evaluator,
transport policies, PRE2 calculations and safety snapshot production files are
unchanged. No dependencies were installed, real provider/IBKR calls made, credentials
used or Gateway settings changed. Independent review and Windows qualification
remain required before PRE4C.
