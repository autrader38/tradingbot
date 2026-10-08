# Phase 6 — Trade construction and simulated opening entry

This phase follows checkpoint `8fca29ee1f9cc13ef3f545a2804863abfb28b36c`.
The frozen [specification](strategy-spec-v1.0.md), [defaults](strategy-parameters-v1.0.md)
and [decision history](strategy-decision-history.md) are unchanged and authoritative.

**RESEARCH BACKTEST MODEL — V1.0**

**ZERO-FRICTION BASELINE**

**FULL-FILL RESEARCH ASSUMPTION — MARKET DEPTH AND PARTIAL FILLS NOT MODELED**

TRANSACTION COSTS AND SLIPPAGE NOT YET MODELED.

This implementation establishes deterministic research behavior, not historical
liquidity, realistic live execution, profitability or suitable dataset availability.

## Input contract and ownership

`TradeConstructor(run_id=..., data_version=...).construct(approval, account, ticks,
trade_id=...)` accepts an immutable Phase 5 `EntryApproval`, supplied
`PortfolioSnapshot` and provider-neutral `TickSource`. Trade IDs must be unique.
Each constructor instance permits one construction opportunity per security/setup
and trade ID. A rejection consumes the opportunity; it cannot retry a later minute.
A wrong input type or reused opportunity raises an ownership/contract error.

The supplied approval preserves A/B/C/D, scheduled reference OPEN, context and
normalization/source evidence. Phase 6 checks its disposition and time contract;
it does not rerun context filters. It reads no scheduled HIGH/LOW/CLOSE/volume.
The modeled entry timestamp is the scheduled interval start, with its containing
one-minute interval retained in audit records.

`TradeConstructionResult` returns either an immutable `InitialPosition` with its
before/after account snapshots, or an explicit consumed rejection. Input records
are never mutated. The owner applies the resulting snapshot exactly once and
applies frozen fresh-setup rules after consumption. This is not a multi-ticker loop.

## Tick metadata

`TickQuery` identifies security, ticker, share basis, historical timestamp, order
purpose and raw price. Stop and target are queried independently because their
applicable increments may differ. The Phase 5 opaque reference remains preserved
in the approval, but it is not itself sufficient numeric tick evidence.

The source supplies `TickRule`: explicit increment and grid origin, verified
security/basis/order purpose, price band, validity interval, availability timestamp,
execution context and source identifier. There is no default tick or implemented
historical tick schedule. Validity starts inclusively and ends exclusively;
availability exactly at modeled OPEN is permitted. Unavailable metadata contributes
only unavailable diagnostics, not substantive future values.

Each rule applies only inside its inclusive-lower/exclusive-upper price band.
`TickSource.resolve(query)` supplies the initial applicable rule. When that band
has no valid grid price in the requested direction, the constructor calls
`resolve_adjacent(query, boundary, direction)`, retaining the **original** raw
price and query identity. `TickDirection.DOWN` requests the immediately preceding
band whose exclusive maximum equals the boundary; `UP` requests the next band
whose inclusive minimum equals it. Sources return a unique `TickRule`, `None`
when unavailable, or a tuple containing all conflicting matches. They must not
choose a favorable rule from conflicting metadata.

Normalization searches DOWN for stops and UP for targets, consulting each
contiguous required band until the nearest valid directional price is established.
A downward search excludes the previous band's upper boundary; an upward search
includes its lower boundary. A band with no qualifying grid price still requires
verified metadata before proceeding to the next band. The destination band may
have a different increment or grid origin. For example, a raw target of 1.23 with
.02 increments below 1.24 and .01 increments at/above 1.24 normalizes to 1.24.
Exact valid raw prices, including inclusive band boundaries, remain unchanged.

Every consulted rule must be available at/before modeled OPEN, effective then,
verified and compatible with security, share basis and order purpose. Metadata
availability is checked before substantive fields enter the decision/audit.
Gaps, overlaps, conflicts and unavailable intermediate/destination rules reject;
no rule is extrapolated beyond its band, no band is skipped, and no default tick
or historical schedule is invented. Existing single-band adapters without
`resolve_adjacent` still work inside their band; a crossing rejects until the
adapter supplies the required metadata. Missing, ambiguous, unverified,
incompatible, obsolete or uncovered stop metadata rejects with
`ENTRY_TICK_SIZE_UNAVAILABLE`; corresponding target failure uses
`ENTRY_TARGET_TICK_SIZE_UNAVAILABLE`. Supplied quality causes remain in audits.
The position retains the final stop/target rules; the audit preserves all consulted
bands, increments, origins, availability/verification, transitions, direction,
raw price and final executable price.

## Executable levels

With approved historical OPEN E and locked C:

```text
raw_stop = C * 0.995
executable_stop = floor_to_supplied_valid_grid(raw_stop)
risk_per_share = E - executable_stop
raw_target = E + 2 * risk_per_share
executable_target = ceil_to_supplied_valid_grid(raw_target)
effective_R = (executable_target - E) / risk_per_share
```

Exact grid levels remain unchanged. Stop rounds down and target rounds up. Reject
nonpositive executable stop or risk; require effective R >=2 under frozen defaults.
Initial normalization is not a later stop widening. The immutable initial stop and
fixed target are preserved for future management, never executed in this phase.
Target rounding does not alter quantity. The actual OPEN retains its precision.
Calculations use exact Decimal helpers and Fraction ratios, independent of ambient
Decimal precision; no floating comparisons or numerical tolerance is invented.

## Supplied account and sizing

The snapshot represents the account after already-required opening exits and
before this entry. Beginning-of-day equity is separately supplied; it is never
inferred from cash or current equity. For held positions, supply quantities and
trustworthy eligible current OPEN marks at this exact modeled timestamp, with
identity, share basis, source and availability. A carried mark, absent/stale/future
OPEN, explicit quality failure, duplicate position or inconsistent valuation
cannot fund a new entry.

Validate `exposure = sum(quantity * current_open)` and
`current_equity = available_cash + exposure`. Account metadata must be available
by OPEN. No margin or negative cash is permitted. The owner supplies explicit
daily entry permission; false rejects without a fill. Full daily lockout/counter
calculation remains outside Phase 6.

```text
risk_budget = BOD_equity * 0.01
risk_qty = floor(risk_budget / risk_per_share)
share_cap = 1000
value_qty = floor(current_equity * 0.20 / E)
cash_qty = floor(available_cash / E)
exposure_qty = floor((current_equity * 0.60 - existing_exposure) / E)
quantity = min(risk_qty, share_cap, value_qty, cash_qty, exposure_qty)
```

Require fewer than three existing positions and none in the same ticker or stable
security ID. Already at/above 60% exposure prohibits entry. Exactly 20% position
value and 60% post-entry exposure are permitted. Quantity is whole shares, floored;
below two rejects, exactly two passes. No additions or averaging are implemented.

Cash is checked explicitly. In a consistent cash-plus-exposure account, remaining
60% exposure capacity is smaller than available cash by 40% of equity, so cash
cannot independently be the final limiting cap. Its exact flooring boundaries are
tested independently; valid-account tests enforce cash safety and consistency.
This does not remove the frozen cash constraint.

## Failed gates and primary-reason convention

The frozen specification requires retaining all failed gates, but does not prescribe
their complete primary-reason precedence. The following is an **IMPLEMENTATION
CONVENTION affecting reason labeling only**; it preserves Phase 6's existing
primary rejection order and cannot enable a fill or change accepted quantity:

1. Invalid Phase 5 approval; unavailable account snapshot; wrong snapshot timestamp.
2. Supplied daily entry lockout.
3. Unavailable/invalid held-position valuation or duplicate positions; inconsistent
   cash/equity/exposure valuation.
4. Same ticker/security already open; maximum simultaneous positions; existing
   exposure at/above the cap, in that order.
5. Unavailable stop tick; invalid executable stop/risk.
6. Unavailable target tick; invalid executable target.
7. Final quantity below the minimum.

After required account validation, collect every independently evaluable failed
portfolio gate and capital capacity. With valid stop/risk metadata, also calculate
and audit risk quantity and all sizing limits even when another portfolio gate
already prohibits entry. Earlier portfolio reasons remain primary if later tick
or sizing checks fail. Target availability retains priority over final minimum
quantity, while already-known risk/capital failures remain in the audit.

Each rejection's `failed_gates` field retains the complete evaluated failure
collection alongside the primary description/canonical code, timestamp and IDs.
Capacity labels identify a cap permitting fewer than the required two shares:
`RISK_QUANTITY_BELOW_MINIMUM`, `SHARE_CAP_BELOW_MINIMUM`,
`POSITION_VALUE_CAPACITY_BELOW_MINIMUM`, `CASH_CAPACITY_BELOW_MINIMUM`, and
`EXPOSURE_CAPACITY_BELOW_MINIMUM`. `ENTRY_QUANTITY_BELOW_MINIMUM` records the
resulting size failure. These labels report existing constraints; they add no rule.
A cap merely reducing size to a valid quantity is **not** a failed entry gate.
Every cap's quantity remains individually auditable, including equal limiting caps.

Unavailable required inputs never fabricate dependent failures: absent stop/risk
metadata prevents risk-quantity evaluation. Independently known capital capacities
remain recorded. A supplied daily lockout retains its original no-tick-lookup
short circuit; other valid account gates/capacities are collected, and an
`ENTRY_UNEVALUATED_GATES` record identifies the skipped tick/risk checks. All
rejections consume the opportunity without a fill or retry.

## Fill and account effect

All approved shares fill at reference OPEN, with zero commission, fees and
slippage. No minute-volume size limit, partial fill, spread, queue, depth or impact
adjustment is applied. Nonzero cost configuration is rejected as unsupported by
this phase rather than silently ignored.

Cash decreases by E times quantity; exposure increases by the same amount; one
position is added; current equity remains unchanged. Purchase cost is not a
realized loss. The result supplies `accepted_entry_delta = 1` on success, zero on
rejection, and audits zero completed-trade/P&L deltas. No completed trade or realized
P&L is generated. The owner must update accepted-entry counters and recompute
permission before another entry, including the frozen fifth-entry restriction;
the returned snapshot does not run a daily lockout engine.

## Records, audit and next-phase handoff

`InitialPosition` retains trade/run/data IDs, configuration, the entire approval,
quantity/value, raw/executable stop and target, risk/budget/effective R, both tick
rules, every sizing quantity, before/after snapshots and zero-cost/full-fill flags.
Setup/security/ticker, entry time and price, BOD/current equity, cash/exposure and
historical evidence remain available through the immutable nested records.

Audits preserve initial/adjacent tick lookups, verified bands/transitions and
availability/quality causes, directional normalization, held-position marks,
account valuation, all caps/final quantity and failed gates, entry price/time,
cash/exposure/equity/count effects, research disclosures and consumed rejection
causes. Canonical tick codes are retained. Conditions without a frozen exact code
use explicit descriptions such as `INVALID_INITIAL_STOP_OR_RISK`,
`PORTFOLIO_VALUATION_UNAVAILABLE`, `SAME_TICKER_POSITION_ALREADY_OPEN`,
`MAXIMUM_SIMULTANEOUS_POSITIONS`, `ENTRY_QUANTITY_BELOW_MINIMUM`, and
`SUPPLIED_DAILY_ENTRY_LOCKOUT`; these are implementation labels, not new rules.

Future Phase 7 can consume the position's fixed initial levels and approval
evidence. Stop/target execution, partial sales, breakeven/trailing changes, EOD
liquidation, realized P&L, consecutive losses, full daily lockouts, ranking/event
loops, scanners and broker/TradingView/live integrations are not implemented.

Required provider work remains historical security/date/price/order-specific ticks
and grid applicability, verified current eligible opening marks, compatible share
units and point-in-time account/session state. No provider or API is selected or
connected, and no dependency is added.

Run offline tests with `python -m unittest discover -s tests`; validate all package
and test sources and run `git diff --check`. Existing Phase 1–5 tests stay unchanged.
