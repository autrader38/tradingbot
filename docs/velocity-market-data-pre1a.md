# VELOCITY-MARKET-DATA-PRE1A — Provider response compatibility

Starting checkpoint: `d78821ee25172f3683836980dc1599af802ed409`.
The user reported PRE1 independent review and actual Windows qualification:
191 focused tests OK, skipped=2; 8,456 full-suite tests OK, skipped=16;
150 Python files compiled, zero exits and clean synchronized GitHub main.

## Confirmed incompatibility and limits of diagnosis

The official [Previous Day Bar example](https://massive.com/docs/rest/stocks/aggregates/previous-day-bar)
contains source timestamp `1605042000000`: `2020-11-10T21:00:00+00:00`, or
`2020-11-10T16:00:00-05:00` Eastern. The original shared daily-bar validator
required ET midnight for both endpoints. An offline test using the public
documented response failed at that exact check before the repair. Its fixture
retains documented OHLCV/timestamp values and omits the unrelated request ID.
The documentation was read from an existing local cache; no provider request was
made in this phase. No personal API response or credential was accessed.

This is a confirmed parser incompatibility with that documented example. It does
not establish that the timestamp was the sole cause of the user's personal
INVALID_RESPONSE. Other fields may still fail validation. No credential-backed
request or assertion of personal-request success is part of this repair.

## Endpoint-specific timestamp contract

| Source | Accepted timestamp, on an ET weekday | Interpretation |
| --- | --- | --- |
| Previous Day Bar | Exact midnight or exact 16:00 ET | Unchanged provider previous-day timestamp; no assertion that it is aggregate start, a trade, session close or receipt time. Midnight preserves existing compatibility. |
| [Custom daily bars](https://massive.com/docs/rest/stocks/aggregates/custom-bars) | Exact midnight ET only | Daily aggregate window start, as before. The previous-day exception cannot apply here. |

A closed private source enum is required on each immutable daily bar and series.
Decoded endpoint identity selects the policy; series cannot mix or relabel source
kinds. No arbitrary intraday time is accepted. Minutes, seconds and milliseconds
must all be zero; numeric type/range, future-instant and weekday checks remain.
Post-2007 Eastern DST conversion is unchanged. No holiday or early-close rule,
session-completeness guarantee or economic-event-time inference is added.

Every bar exposes its unchanged `source_timestamp_ms` and equivalent UTC
`source_at`. `session_date` remains the Eastern date of that exact instant.
For previous-day bars, `window_start_at` is null and the quality/semantics text
identifies a provider previous-day timestamp. For custom daily bars,
`window_start_at` remains the exact midnight source instant. The dashboard shows
**Source timestamp**, plus endpoint-specific semantics, rather than falsely
labeling every timestamp as window start. Nothing is shifted to midnight.

Ticker, OHLCV, finite numeric bounds and Decimal representation, presence,
adjustment, response counts, monotonic history, bounded body/history and future
timestamp validation remain in force. Existing sanitized INVALID_RESPONSE
handling and retry policy remain unchanged; raw responses/exceptions and sensitive
request metadata are never published. No diagnostic logging was added.

## Offline verification

Regressions cover the official example, prior midnight compatibility, historical
midnight isolation, spring/fall DST weekdays, exact source timestamp and date,
malformed/near-boundary times, weekends, future instants, OHLCV/ticker/count/
adjustment failures, source-model integrity and hostile Decimal contexts.
Fixture-fed adapter, loopback HTTP and optional installed-browser tests verify
accurate display, cache behavior, sanitized failures and unchanged DEMO state.
External sockets/DNS are blocked in model/adapter tests; provider reads are mocked
and only ephemeral loopback traffic is permitted for UI tests. Test credentials
are synthetic. No real API key is needed or read.

Cloud validation on Python 3.12.14 passed: 123 market-data tests and 132
Dashboard PRE1/PRE2 regressions (255 focused tests, no skips), including 40
installed-browser checks. Full offline discovery passed: 8,520 tests OK,
skipped=4 (three unavailable optional installed-IBAPI checks and the disabled
real Gateway opt-in). All 150 tracked Python files compiled. Both diff checks
and secret/account, environment, public-export, network, endpoint/security,
protected-file and scope scans passed. All 64 broker/backtest/strategy files,
the provider transport, credential/rate/cache logic, HTTP server and demo core
remain byte-for-byte unchanged from the checkpoint.

Windows verification from the repository folder, without provider/Gateway access:

```powershell
$env:IBKR_RUN_READ_ONLY_TESTS = "0"
py -3.12 -m unittest tests.test_velocity_marketdata tests.test_velocity_dashboard tests.test_velocity_scanner -v
py -3.12 -m unittest discover -v
git ls-files '*.py' | ForEach-Object { py -3.12 -m py_compile $_ }
git diff --check
git diff --cached --check
git status --short
```

PRE1A still requires independent review and actual Windows qualification.

## Unchanged boundaries

Default startup remains offline DEMO, with no key lookup or automatic provider
request. Existing explicit preview opt-in, manual loads, fixed host/endpoints,
Bearer authentication, response limits, rate/backoff/cache and local HTTP security
are unchanged. No external dependencies, broker/backtest/strategy files, journal,
SQLite, execution, risk, authorization or Gateway setting changes.

Trading BLOCKED; first real PAPER order BLOCKED; Gateway Read-Only REQUIRED;
restart quarantine MANDATORY; AccountMode UNKNOWN; public snapshot mode None;
LIVE UNSUPPORTED. The repair adds no currentness, signal or trading authority
and begins no later milestone.
