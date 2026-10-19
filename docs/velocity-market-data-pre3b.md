# VELOCITY-MARKET-DATA-PRE3B — Safe grouped-response diagnostics

Starting checkpoint: `f75f84e088898fed83b72ca3e7270f8354f19e4c`.
The user reported PRE3A Windows focused validation (382 tests, 4 skipped) and
successful preceding full Windows validation (8,647 tests, 18 skipped).

## Finding and scope

The user's October 19, 2026 manual request for October 16 failed closed. The old
`INVALID_RESPONSE` response erased the failed validation stage, so it cannot
establish which production field was rejected. No private response, API key or
real provider request was accessed during this diagnosis. The actual rejection
cause remains **unproven** until a later user-run manual load reports a fixed code.
No independently proven schema defect was found and no schema relaxation was made.

The [official Daily Market Summary documentation](https://massive.com/docs/rest/stocks/aggregates/daily-market-summary)
was checked directly on October 19, 2026. Its declared root/row fields agree with
the implemented allowlists. It includes Basic EOD access and documents `t` as
the aggregate-window end, not a session-completeness certificate. Its illustrative
`request_id` object conflicts with its declared string type; that illustration
does not establish the shape of a real response. No guessed alternative was added.
An offline test checks the documented OHLCV rows and timestamp against the declared
string metadata type. Documentation examples are not exhaustive production proof.

## Fixed diagnostic contract

The existing public `reason: INVALID_RESPONSE`, unavailable state and **60-second
manual-only retry backoff** remain. A separate `diagnostic_code` is selected from
a closed private enum. Only a validated enum value reaches JSON; arbitrary error
arguments/messages or substituted categories fall back to `VALIDATION_FAILED`.
The browser independently allowlists the codes and uses text content only.

| Code | First failed check |
| --- | --- |
| `HTTP_CONTENT_TYPE_INVALID` | Successful HTTP response is not JSON content type |
| `HTTP_CONTENT_ENCODING_INVALID` | Unsupported content encoding |
| `HTTP_CONTENT_LENGTH_INVALID` | Invalid declared length or received-length mismatch |
| `JSON_SYNTAX_INVALID` | Invalid UTF-8, JSON grammar, nonfinite literal, trailing content or truncation |
| `JSON_STRUCTURE_INVALID` | Preallocation depth/token/shape checks, duplicate object keys, integer framing or root type |
| `ROOT_FIELDS_UNEXPECTED` | Root contains an unrecognized field |
| `ROOT_STATUS_INVALID` | Root status is not the required OK value |
| `ROOT_ADJUSTMENT_INVALID` | Root adjustment is not exactly true |
| `ROOT_METADATA_INVALID` | Invalid bounded optional root metadata |
| `RESULT_COUNTS_INVALID` | Invalid count type/range or count/array mismatch |
| `ROW_FIELDS_UNEXPECTED` | Row contains an unrecognized field |
| `ROW_IDENTITY_INVALID` | Missing or malformed bounded source identity |
| `ROW_IDENTITY_DUPLICATE` | Repeated source ticker identity |
| `ROW_OHLCV_INVALID` | Missing/invalid price or volume, or inconsistent OHLC range |
| `ROW_TIMESTAMP_INVALID` | Invalid timestamp or incompatible requested-date/future relationship |
| `ROW_METADATA_INVALID` | Invalid optional VWAP, transaction count or OTC flag |
| `VALIDATION_FAILED` | Unexpected validation failure without a recognized stage |

Checks run in the existing deterministic order. Only the first encountered failed
stage is identified. A structural/preallocation failure can precede a more specific
field check; a code does not assert which exact row/value was wrong. It includes
no ticker, request ID, row index, raw data, headers, credentials or exception text.
No new logging or raw-response diagnostic endpoint exists.

The separate `RESPONSE_LIMIT_EXCEEDED`, timeout, network and HTTP failure outcomes
remain unchanged. Structurally valid but unsupported ticker formats still follow
the reviewed exclusion/count policy; they are not converted, adopted or silently
treated as malformed rows. All other validation still precedes any exclusion.

## Preserved boundaries

There is no partial publication or malformed-row skipping. Strict JSON, adjustment,
timestamp, date, numeric, identity and metadata rules are unchanged. The grouped
8 MiB/30,000-row bounds, 50-row pages, twelve-second timeout and per-ticker 256 KiB
policy remain. The shared four-attempt rolling-minute budget, minimum fifteen-second
interval, cache and backoff are unchanged. No automatic requests/retries, polling,
new endpoints, persistence, frontend resources or dependencies were added.

DEMO stays the offline default. Broker/backtest/strategy/account/journal/risk code
is unchanged. PAPER only; trading BLOCKED; Gateway Read-Only REQUIRED; restart
quarantine MANDATORY; AccountMode UNKNOWN; snapshot mode None; LIVE UNSUPPORTED.
First real PAPER order remains BLOCKED. No PRE4 or broker execution is authorized.

## Qualification and later manual diagnosis

Offline tests cover every code, malformed/malicious inputs, exact error publication,
credential/request-ID non-disclosure, no partial results, unchanged backoff/shared
quota, LF/CRLF fixtures and dashboard display/unknown-code rejection. HTTP/browser
tests use only a loopback server and synthetic provider doubles. PRE3B still needs
independent review and actual Windows qualification; cloud results are not Windows
qualification.

Cloud Python 3.12.14 results: 74 dedicated diagnostic tests; 186 combined diagnostic/
discovery tests; 529 combined dashboard/market-data regressions, including 72
installed-browser checks; full offline discovery **8,794 tests OK, skipped=4**.
All 156 final Python files compiled. Both diff checks and exact scope, original
validation-predicate, quota/per-ticker transport, credential/account/environment,
public-export, external-resource, broker and durability scans passed. No dependency
installation, real provider API calls, API-key access, IBKR activity or Gateway
change occurred. Official public documentation was the only Massive web access.

After updating and verifying the new reviewed commit, run in Windows PowerShell:

```powershell
$env:IBKR_RUN_READ_ONLY_TESTS = "0"
py -3.12 -m unittest tests.test_velocity_market_discovery_diagnostics tests.test_velocity_market_discovery tests.test_velocity_eod_research tests.test_velocity_marketdata tests.test_velocity_dashboard tests.test_velocity_scanner -v
py -3.12 -m unittest discover -v
git ls-files '*.py' | ForEach-Object { py -3.12 -m py_compile $_; if ($LASTEXITCODE -ne 0) { throw "Compilation failed: $_" } }
git diff --check
git diff --cached --check
git status --short
```

Only afterward, using the existing personally configured preview, the user may
repeat the deliberate grouped load. Report **only the fixed diagnostic code** if
it still fails. Do not share the key, headers, response body, request identifiers
or a raw traceback. A code identifies the next validation stage to investigate;
it does not authorize weaker parsing or a follow-on milestone.
