# VELOCITY-MARKET-DATA-PRE3C — Unexpected root-field categories

Starting checkpoint: `fde56b1d70b8983a7c6b84fcc6f8df95890294a6`.
The user reported `INVALID_RESPONSE — ROOT_FIELDS_UNEXPECTED` from a manual
October 16, 2026 grouped daily request. This proves an extra decoded root key
was present, but does not identify its name. PRE3C adds bounded diagnostics only.

## Source review and unchanged acceptance

The [official grouped endpoint documentation](https://massive.com/docs/rest/stocks/aggregates/daily-market-summary)
still lists `adjusted`, `queryCount`, `resultsCount`, `status`, `request_id` and
`results`. Those remain the **only accepted root fields**. No optional field was
accepted, pagination ignored/followed, malformed row skipped or partial result
published by this change.

The official Python SDK's [grouped row model](https://github.com/massive-com/client-python/blob/master/massive/rest/models/aggs.py)
maps row field `T` to its `ticker` attribute. The [endpoint implementation](https://github.com/massive-com/client-python/blob/master/massive/rest/aggs.py)
requests `result_key="results"`; its [base decoder](https://github.com/massive-com/client-python/blob/master/massive/rest/base.py)
selects that array and discards the envelope. This does not certify extra root
fields or prove that `ticker` appears at the root. Sources were reviewed directly
on October 19, 2026; no SDK was installed or executed and no provider API was called.

## Closed diagnostic classification

At the existing root-field rejection stage, unexpected **names only** are mapped
case-sensitively into this closed diagnostic vocabulary:

| Exact input name | Published category |
| --- | --- |
| `ticker` | `TICKER` |
| `count` | `COUNT` |
| `next_url` | `NEXT_URL` |
| `requestId` | `REQUEST_ID_CAMEL` |
| `pagination` | `PAGINATION` |
| `message` | `MESSAGE` |
| `error` | `ERROR` |
| Any other decoded name | `OTHER` |

These are diagnostic labels, not additions to the root allowlist. Unknown names
are aggregated into `OTHER`; no raw name, value or fingerprint is retained in the
diagnostic summary. No request ID, ticker value, key/header, raw response, arbitrary
exception text or raw provider logging is added.

The public outcome remains `INVALID_RESPONSE` with `ROOT_FIELDS_UNEXPECTED` and
60-second manual-retry backoff. Only this stage may additionally publish
`unexpected_root_fields`, an array of `{category, count}` objects. Category order
is fixed as in the table, independent of source object ordering. There are at most
eight entries. Known-name counts are exactly one; `OTHER` is 1–12. Total count
cannot exceed the existing twelve-key JSON object limit. Duplicate JSON keys,
excessive key/token counts or unsupported nested structures still fail at their
earlier structural checks and cannot produce partial classification.

The private error retains only immutable enum/count pairs. Publication revalidates
types, counts, uniqueness and ordering, omitting malformed/substituted details.
The browser separately checks the category allowlist, integer/count/array bounds
and duplicates before displaying text such as:

```text
Diagnostic: ROOT_FIELDS_UNEXPECTED. Unexpected root categories: COUNT (1), OTHER (2).
```

No new HTTP routes, requests, background work, retries, cache behavior or resource
limits. Shared four-request/minute and fifteen-second spacing, existing backoff,
loopback-only security, CSP, GET/HEAD policy and offline DEMO remain unchanged.
No real provider/API-key access, broker/strategy/journal/risk changes or trading
authority. Gateway Read-Only remains REQUIRED, restart quarantine MANDATORY,
AccountMode UNKNOWN, public snapshot mode None, LIVE UNSUPPORTED, first PAPER
order BLOCKED. PRE4 is not authorized.

## Verification and next diagnostic checkpoint

Offline fixtures cover every category, malicious names/values, multiple unknowns,
the twelve-key cap, deterministic output, bounded counts, no leakage, no partial
results and unchanged manual backoff/shared quota. Installed-browser tests use
only synthetic provider doubles and a loopback server. Source-text reads specify
UTF-8 for Windows portability. PRE3C needs independent review and actual Windows
qualification; cloud results are not Windows qualification.

Cloud Python 3.12.14 verification: 105 diagnostic tests passed; 563 combined
dashboard/market-data regressions passed, including 74 installed-browser checks;
full offline discovery **8,828 tests OK, skipped=4**. All 156 tracked Python files
compiled. Both diff checks and exact scope, credential/account/environment,
public-export, external-resource, broker/durability and parser/quota preservation
scans passed. No dependencies, provider API requests, credential access, IBKR
activity or Gateway changes occurred. Only public documentation/SDK source was
retrieved for source review.

```powershell
$env:IBKR_RUN_READ_ONLY_TESTS = "0"
py -3.12 -m unittest tests.test_velocity_market_discovery_diagnostics tests.test_velocity_market_discovery tests.test_velocity_eod_research tests.test_velocity_marketdata tests.test_velocity_dashboard tests.test_velocity_scanner -v
py -3.12 -m unittest discover -v
git ls-files '*.py' | ForEach-Object { py -3.12 -m py_compile $_; if ($LASTEXITCODE -ne 0) { throw "Compilation failed: $_" } }
git diff --check
git diff --cached --check
git status --short
```

After qualification, the user may repeat the existing deliberate manual load and
report only the fixed categories/counts. The actual extra root name remains unknown
until that report. `OTHER` intentionally cannot identify a raw unknown name. No
accepted schema change is authorized before further source/compatibility review.
