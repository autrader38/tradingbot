# Phase 1 — Backtester foundation

The user authorized Phase 1 after the frozen documentation checkpoint
`cd3edd5189557258cc95ea249c8fcd876de5e157`. The [canonical specification](strategy-spec-v1.0.md), [defaults](strategy-parameters-v1.0.md) and [history](strategy-decision-history.md) remain unchanged and authoritative.

This phase is only a Python foundation. It has no ABCD detector, transition engine, entry/exit execution, sizing, portfolio simulation, universe calculations, higher-timeframe filters, broker adapter or network client.

## Running the offline checks

Use Python **3.11 or newer** (required for `StrEnum`) from the repository root:

```sh
python -m unittest discover -s tests -v
python -m compileall -q tradingbot_backtest tests
git diff --check
```

No installation, package download or third-party dependency is required. The root-level Python package is importable from the checkout. The host must supply the standard IANA timezone database used by `zoneinfo`; America/New_York availability is checked by the offline tests. Calendar fixtures are explicit test inputs, not evidence that a real provider/calendar has been verified.

## Modules

| Module | Foundation responsibility |
|---|---|
| `tradingbot_backtest/config.py` | Frozen typed dataclass defaults for all 64 table entries, with the total-attempt limit derived from allowed failures. Decimal fractions versus percentage-point ADR/room values are explicit. |
| `tradingbot_backtest/market.py` | Four minute classifications, immutable interval records, strict OHLCV validation and per-security chronological/duplicate checks. |
| `tradingbot_backtest/sessions.py` | Verified session-boundary record, New York regular/premarket/outside classification and a provider-neutral calendar protocol. |
| `tradingbot_backtest/states.py` | Requested lifecycle enumeration only. `AB_IMPULSE` is the implementation name for the specification's conceptual A/B impulse phase. |
| `tradingbot_backtest/codes.py` | Approved reason/status names, context/history classifications and setup termination labels; no behavior or new trading rules. |
| `tradingbot_backtest/numerics.py` | Exact decimal construction, addition/subtraction/multiplication, exact rational division and caller-supplied uniform-tick floor/ceiling primitives. |
| `tradingbot_backtest/audit.py` | Immutable provenance/identity/timestamp/state/reason/detail record, not a full reporting system. |

Create an independently labeled research configuration with `dataclasses.replace(FROZEN_V1, ...)`. The frozen instance stays immutable. Configuration validates exact types/finite decimals; it does not invent additional parameter ranges or implement a research strategy. A changed configuration must later be identified in results. Nonzero execution-cost/slippage behavior is not implemented merely because its configuration field exists.

## Data and timestamp conventions

- Minute `timestamp` labels the interval start and must be aware, valid and minute-aligned. `end` represents the following minute's boundary. Aware UTC inputs are supported; New York conversion is used for session classification and DST.
- `TRADED` requires finite Decimal OHLCV, positive prices, nonnegative volume and valid OHLC relationships. Minimum volume validation does not silently infer a data-provider classification.
- `NO_TRADE` requires explicit provider verification, Decimal zero volume and no OHLC. Zero volume alone does not establish no trades.
- `MISSING`/`INVALID` remain separate and contain no trusted numeric payload. Their explanatory reason is required; raw corrupt/provider evidence can be retained as audit strings/details. Bad input raises an exception, never becomes synthetic NO_TRADE.
- Sequence validation rejects duplicate/conflicting or reverse timestamps per stable security ID, including equivalent instants in different zones. It does not sort, resolve corrections or infer holidays/gaps. A future calendar-aware ingestion adapter must represent expected unavailable minutes explicitly; absent records are not automatically no-trade.
- The calendar protocol returns `None` only for verified non-sessions. Unknown/unavailable boundaries must raise an error. Session open/close are supplied, never inferred from weekdays or hard-coded to 16:00. No concrete provider has been selected or connected.
- Audit times distinguish record time, modeled time and containing-interval boundaries. No exact unknown intraminute time is invented. Details are immutable, named, finite exact scalar values; floats and duplicate keys are rejected. Event-type strings identify records, not a new execution policy.

## Numerical scope

Use Decimal from text/integers, never binary floats. Arithmetic primitives return exact results independently of ambient Decimal precision/rounding. Nonterminating division stays an exact Fraction. A zero denominator raises an error; later RVOL code must apply the approved zero-baseline failure rule before division.

Tick helpers use an explicitly supplied positive uniform increment; they do not supply a tick size, tick schedule, price-tier resolver or broker convention. They are arithmetic building blocks only, not stop/target calculations. Numerical display/tolerance and real tick-provider applicability are not guessed here.

## Pending and verification limits

Provider selection and all point-in-time historical-data capabilities remain pending. Actual pattern/state transitions, prices, risk/account rules, daily lockouts and reporting are deferred to later authorized phases. Passing Phase 1 tests establishes foundation behavior only; it does not validate any historical trade, realistic execution or deployable performance.
