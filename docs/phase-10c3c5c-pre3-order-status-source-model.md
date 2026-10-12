# Phase 10C3C5C-PRE3 — private order-status source model and pure adapter

This phase adds immutable source values only. No production callback is captured,
no read/write request is made, and no observation is fed into reconciliation,
accounting, historical lifecycle or recovery. Gateway Read-Only remains required;
AccountMode remains UNKNOWN, public snapshot mode remains None, LIVE remains
unsupported, and the first real PAPER order remains blocked.

The starting checkpoint is `5678c0e71a6ec9506ff6d19e971cfb118151cfb0`.
Independent review and Windows validation of C3C5C-PRE1 were reported complete
before this phase. PRE2 source qualification used supplied Windows inspection
output from Python 3.12.10, ibapi 10.50.2 and protobuf 5.29.5. No broker connection
or SDK request occurred during that inspection.

## Inspected source contract

The SDK normalized callback is:

```text
orderStatus(orderId, status, filled, remaining, avgFillPrice,
            permId, parentId, lastFillPrice, clientId, whyHeld, mktCapPrice)
```

The nine relevant arguments are exact int IDs, a str status, Decimal filled and
remaining quantities, and float average/last prices. The SDK docstring's old
`filled:int` wording does not override its actual annotations and Decimal decoder.
whyHeld and mktCapPrice are deliberately excluded from these adapters and values.

The inspected raw message is `protobuf.OrderStatus` in `OrderStatus.proto`:

| SDK property | Field number | Protobuf scalar | Normalized SDK value |
|---|---:|---|---|
| orderId | 1 | INT32 | int |
| status | 2 | STRING | str |
| filled | 3 | STRING | Decimal |
| remaining | 4 | STRING | Decimal |
| avgFillPrice | 5 | DOUBLE | float |
| permId | 6 | INT64 | int |
| parentId | 7 | INT32 | int |
| lastFillPrice | 8 | DOUBLE | float |
| clientId | 9 | INT32 | int |

Each of these nine fields has explicit presence through its synthetic optional
oneof. HasField distinguishes absence from supplied zero or an empty scalar;
this survives serialization. The decoder invokes orderStatusProtoBuf(raw) before
constructing the normalized orderStatus callback. Future capture must qualify
that pairing itself; PRE3 supplies no callback issuer, sequence or generation.

## Private value and presence models

`ibkr_order_status_evidence.py` adds `_OrderStatusObservation` and its immutable
`_OrderStatusPresence` value. Both are frozen, slotted and opaque in repr. The
observation holds only protocol, the nine relevant normalized fields, and presence.
Decimal values are independent exact copies; mutable messages, descriptors,
argument tuples and raw status text are not retained.

`_OrderStatusProtocol` is LEGACY or PROTOBUF. Per-field quality is:

- SUPPLIED: an available value whose raw protobuf presence was checked.
- LEGACY_DECODED: a usable normalized legacy value, without a wire-presence claim.
- LEGACY_AMBIGUOUS: legacy zero ID/price, whose original token could have been zero
  or empty. The decoded zero is retained.
- UNAVAILABLE: no usable value; the observation field is None.

`presence.wire_supplied` is None for LEGACY. For PROTOBUF it is a frozenset of the
model field names whose raw HasField result was True. This independently preserves
raw supply even when a supplied reserved numeric sentinel is unavailable. Thus
absent quantity and supplied-unavailable quantity are distinguishable; neither
becomes a real quantity. Metadata cannot silently promote an unavailable value.

The constructors check exact protocol/presence types and value-quality agreement.
These checks establish data shape, not authenticated SDK origin, order ownership
or authority. An equal reconstructed observation is still data only.

## LEGACY adapter

`_adapt_legacy_order_status` receives the nine relevant primitives in the same
order as the SDK callback. It accepts no broker object, whyHeld, mktCapPrice,
runtime generation, arrival sequence, request ID or timestamp.

Legacy decode maps empty integer/float tokens to zero. PRE3 cannot recover raw
tokens from normalized arguments. Zero IDs/prices are retained with
LEGACY_AMBIGUOUS quality; positive decoded values remain LEGACY_DECODED and do
not gain protobuf-style presence. Valid decoded Decimal zero remains an available
quantity with LEGACY_DECODED quality.

Legacy missing/sentinel quantity tokens can become the finite 39-digit
`UNSET_DECIMAL = 170141183460469231731687303715884105727`. The adapter explicitly
recognizes its numeric value, including equivalent Decimal representations, and
returns None/UNAVAILABLE. Ordinary finite/bounds checks alone would accept it.
The qualified UNSET_DOUBLE price is likewise unavailable. No finite reserved
quantity or price is published as a usable economic value.

All IDs require exact ints, excluding bool. orderId, clientId and parentId use
0 through 2147483647; permId uses 0 through 9223372036854775807. Legacy zero stays
ambiguous. Supported positive ID maxima are not redefined as absent: the legacy
integer decoder does not use them as its empty-token fallback.

## PROTOBUF adapter

`_adapt_protobuf_order_status(raw, normalized=None)` checks the supplied local
message's qualified descriptor name/file, nine field numbers/scalar types,
presence support and synthetic oneof names. It reads HasField before any field
value; an absent field's default getter is never inspected or promoted.

Absent fields are None/UNAVAILABLE. Supplied numeric zero is retained with SUPPLIED
quality. Supplied empty quantity strings are malformed, not zero. Quantity text
uses a bounded ASCII decimal grammar, at most 160 characters, without whitespace
or underscores. Decimal construction preserves its exact coefficient/exponent;
no numeric normalization, rounding or float quantity conversion occurs.

An optional normalized tuple contains exactly the nine relevant SDK arguments.
Supplied raw IDs/status must agree with the normalized values; status comparison
precedes UNKNOWN normalization. Quantity comparison is exact numeric equality,
while the output retains the raw text's Decimal representation. Float prices must
agree with the decoder's direct raw-double forwarding, including signed zero.

For absent fields, a supplied normalized tuple must contain the qualified decoder
fallback: UNSET_INTEGER for the three INT32 IDs, UNSET_LONG for permId, empty
status, UNSET_DECIMAL quantities and UNSET_DOUBLE prices. Fallbacks cannot create
presence. Contradictions fail closed; neither source wins automatically.

## Numeric, status and exception policy

Available quantities require exact finite nonnegative Decimal values with at most
64 coefficient digits and exponents from -64 through 64. Fractional values and
signed zero are preserved. Bool, float quantities and unsupported primitive
subclasses are rejected.

Prices require exact finite nonnegative floats, excluding bool. Conversion uses
Decimal(str(value)) and the same representation bounds. It preserves the SDK
float's text value without claiming recovery of lost binary precision. No
qualified negative price case is introduced.

Malformed numeric text is parsed only under an independent local Decimal context.
Valid conversions, invalid conversions and comparisons preserve the caller's
precision, traps and flags. No ambient arithmetic or normalization is performed.

The existing eight status names and UNKNOWN are supported. Bounded printable ASCII
unrecognized/empty status maps to UNKNOWN without retaining arbitrary source text.
An absent protobuf status remains None. Unknown codes do not create authority.

Malformed data raises the fixed `INVALID_IBKR_ORDER_STATUS_EVIDENCE` ReadOnlyError,
without source repr or exception text. KeyboardInterrupt, SystemExit and
GeneratorExit propagate as the original objects. No partial authority is published.

## Same-event and authority limits

Each adapter call groups one input event's status, quantities, prices and available
IDs. It stores no history, merges nothing, and applies no latest-wins projection.
Repeated calls return independent equal values. The caller must supply one event's
material; PRE3 does not authenticate caller provenance or concurrent raw-message
ownership. Future capture must enforce exclusive message ownership and exact
raw/decoded generation/thread/sequence pairing.

No requested quantity is supplied here, so the adapters do not infer requested Q,
calculate Q-E, repair status pairs, resolve status regression or reconcile fills.
An otherwise valid same-event pair is retained for later qualified interpretation.

These are broker-reported cumulative filled/remaining/status-price observations.
Average/last prices, especially with filled zero, may be placeholders and are never
execution prices, acquisition lots, weighted execution averages or priced coverage.

orderStatus supplies no orderRef, contract, symbol, execId, request ID or broker
economic timestamp. No such field is added. IDs alone prove no bot ownership,
session membership, parent/child protection, OCO or bracket relationship.

There is no independent finite orderStatus collection completion. Observation
during an open/completed/execution request does not prove that request caused it.
Receipt ordering is not economic ordering. Filled/Cancelled/ApiCancelled/Inactive/
Expired do not establish current terminality, definitely canceled quantity, actual
zero-fill finality, no late fill, current inventory or exact current working remainder.
remaining means only reported remaining in that observation.

## Protected boundaries and subsequent work

Only a new production module is added. No production callback path, refresh or
reconnect behavior changes. Reconciliation/PRE1, journal/replay, SQLite, C3C4D
persistence, C3C5A acquisition accounting, C3C5B historical lifecycle, recovery,
public Broker exports/interfaces, read/write wire policies, strategy and backtest
behavior are unchanged. There are no files, clocks, locks, SDK requests, storage,
background workers or coordinator/transport accesses in the adapter logic.

Callback capture integration remains a separately qualified later phase. Session/
generation continuity remains deferred: public refresh still reconnects, creates a
new read generation and invalidates the original authenticated lifetime association.
There is no binding adoption, session migration, restart recovery, authorization
re-arm, pending retirement, barrier clear, cancellation, protection, exit or trading
permission. Gateway Read-Only remains required; the first PAPER order remains blocked.

## Validation

Focused tests use an independent qualified schema fixture with optional-scalar
protobuf wire encoding. When ibapi is installed, valid-message/presence tests also
exercise the actual decoder's OrderStatusProto class without creating a client.
The cloud has no installed ibapi/protobuf; that actual-SDK branch is not claimed
as executed here. No new platform skip or dependency is introduced.

```text
python -m unittest -q tests.test_ibkr_order_status_evidence
python -m unittest discover -s tests -q
python -m compileall -q tradingbot_backtest tradingbot_broker tests
git diff --check
```

Keep the real Gateway integration opt-in disabled. Independent review and actual
Windows/ibapi 10.50.2 focused/full validation remain mandatory before callback
capture integration. Linux results are not Windows qualification.

Linux/Python 3.12 validation completed: 104 new focused tests and 1,608 selected
existing regression tests passed. The full offline suite ran 4,628 tests with
4,627 passing and the existing opt-in real Gateway integration test skipped.
All 105 Python files compiled. Whitespace, secret/account identifier, tracked
`.env`, private-import and protected-file scope checks passed. Exactly the new
module, focused tests, this document and minimal README/readiness updates changed.
