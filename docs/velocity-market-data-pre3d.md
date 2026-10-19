# VELOCITY-MARKET-DATA-PRE3D — Validated grouped result count

Starting checkpoint: `26c949de5626f98d516d3ebc0fc356f3bf2b78f9`.

## Confirmed compatibility issue

The user manually loaded Massive's October 16, 2026 grouped daily response.
PRE3C reported `INVALID_RESPONSE / ROOT_FIELDS_UNEXPECTED / COUNT (1)`, identifying
the exact extra root name `count`. An anonymous offline response with eight rows,
`resultsCount=8` and `count=8` reproduced that rejection before this repair.
No private provider response, credential or real provider API request was accessed.

The [current official grouped documentation](https://massive.com/docs/rest/stocks/aggregates/daily-market-summary)
was checked again and omits this field. The user reports historical grouped-aggregate
documentation defines `count` as the number of returned results. Legacy documentation
and additional public SDK source URLs were unavailable in this environment, so no
independent historical verification is claimed. No conflicting meaning was established.
This phase implements the explicitly requested conservative returned-results policy:
`count` is redundant corroboration only and cannot replace the existing count proof.

## Exact policy

The root allowlist gains only the exact, optional name `count`.
When supplied, it must be an exact decoded JSON integer (not bool, decimal,
exponent-form number, string or null), nonnegative and at most **30,000**.
It must equal `resultsCount`. Independently, the existing received-array check
requires `len(results) == resultsCount`; therefore all three must agree.
Omitted `results` continues to mean an empty array only under the existing rules.

Invalid optional count types, bounds or disagreements use `RESULT_COUNTS_INVALID`
under the existing public `INVALID_RESPONSE` outcome and **60-second manual-only
retry backoff**. Earlier byte/container/JSON structure failures retain their existing
precedence. Duplicate keys, including escaped duplicates, still fail closed.

`queryCount` keeps its existing separate validation; no equality between it and
returned-row counts is invented. An absent `count` preserves original successful
behavior. Zero with zero rows is a validated empty response, not proof of a trading
session or no trading. The count includes every received row, including subsequently
excluded OTC/unsupported identities; it is not the supported-row or candidate count.

The optional field is checked and discarded. `_GroupedSummary`, dashboard count
publication and market observations remain unchanged. No optional field can override
received counts, repair invalid OHLCV/timestamps or authorize truncated publication.
No new public count field is added and no partial results are published.

## Preserved diagnostics and safety

`COUNT` is removed from PRE3C's unexpected-field categories because exact `count`
is now recognized. The remaining fixed categories are `TICKER`, `NEXT_URL`,
`REQUEST_ID_CAMEL`, `PAGINATION`, `MESSAGE`, `ERROR` and `OTHER`. The browser cap
is seven categories; the existing twelve-key structural/count limit remains.
Other extra fields still reject the whole response, even alongside a valid count.
No raw names, values, credentials, request IDs, exception text or provider response
bodies are disclosed. PRE3B/PRE3C stage diagnostics and sanitized fallback remain.

Grouped 8 MiB/30,000-row limits, 50-row pages, timeouts, the shared four-attempt
rolling-minute quota, minimum fifteen-second spacing, five-minute cache and provider
backoff are unchanged. No new requests, automatic retry, polling or background load.
Default DEMO and PRE2 per-ticker research remain unchanged. No dependencies added.

Broker/backtest/strategy/risk/account/order-management/execution/journal/Gateway
production files are unchanged. PAPER only; trading BLOCKED; Gateway Read-Only
REQUIRED; restart quarantine MANDATORY; AccountMode UNKNOWN; public snapshot mode
None; LIVE UNSUPPORTED. The first real PAPER order remains BLOCKED. No PRE4.

## Offline and Windows qualification

Anonymous offline fixtures cover absent/valid/zero/max count; type, range and
three-way equality failures; independent queryCount; other unknown fields;
malformed JSON/duplicates; no partial publication or disclosure; unchanged quota,
cache and manual backoff. Installed browser checks use only fixture-fed loopback
HTTP, including successful valid-count loading and safe mismatch reporting.
All source-text test reads retain explicit UTF-8.

PRE3D still requires independent review, actual Windows qualification and later
user-operated provider compatibility confirmation. Cloud tests are not Windows
qualification and do not establish that the real response passes every other check.

Cloud Python 3.12.14 results: **127 diagnostic tests** and **587 combined focused
tests** passed, including 76 installed-browser checks. Full offline discovery:
**8,852 tests OK, skipped=4**. All **156 tracked Python files** compiled. Both
diff checks and exact scope, credential/account/environment, public-export,
external-resource, broker/durability and parser/limit/quota preservation scans
passed. Additional offline checks rejected bool counts even when numerically
equal to zero/one received rows, and rejected matching oversized metadata.
No dependencies, real provider API requests, API-key access, IBKR activity or
Gateway changes occurred. Only public documentation/source URLs were consulted.

```powershell
git pull --ff-only
git rev-parse HEAD
$env:IBKR_RUN_READ_ONLY_TESTS = "0"
py -3.12 -m unittest tests.test_velocity_market_discovery_diagnostics tests.test_velocity_market_discovery tests.test_velocity_eod_research tests.test_velocity_marketdata tests.test_velocity_dashboard tests.test_velocity_scanner -v
py -3.12 -m unittest discover -v
git ls-files '*.py' | ForEach-Object { py -3.12 -m py_compile $_; if ($LASTEXITCODE -ne 0) { throw "Compilation failed: $_" } }
git diff --check
git diff --cached --check
git status --short
```
