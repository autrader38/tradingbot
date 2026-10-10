# Phase 10C3C2C1 — private read reconciliation evidence

This phase adds offline-qualified, private evidence capture to `ReadOnlyTWSTransport`.
It captures identity and collection provenance for a future C3C2C2 consumer. It does
not perform reconciliation, clear any barrier, confirm strategy acceptance or enable
orders. No real IBKR connection or order transmission occurred during development.
Gateway Read-Only remains unchanged. AccountMode remains UNKNOWN and public account
snapshots retain `mode=None`; LIVE remains unsupported.

## Private evidence and unchanged public projections

Frozen, slotted `_ReconciliationOrderEvidence` and
`_ReconciliationExecutionEvidence` retain validated primitive identity, contract and
economic fields only. `_ReconciliationPresence` retains supplied-field names and
explicit order ID, client ID, reference, permanent ID, top/nested order ID, lifecycle
structure and status flags.
`_ReconciliationCollectionState` describes each required order/execution source.
`_ReconciliationReadSnapshot` contains immutable tuples, generation, start/completion
markers, monotonic collection timestamps, last evidence sequence and capture outcome.
All private evidence representations are opaque.

`readonly_models.py`, `models.py`, and their public `ObservedOrder`, `Fill` and
`ReadOnlySnapshot` contracts are unchanged. Existing public projections continue to
operate independently. A stricter private capture failure cannot become a successful
private collection merely because the public projection succeeded. The additive layer
does not create production fills or mutate holdings, cash, equity, risk or strategy.

## Qualified legacy and protobuf identity

The user inspected the installed official IBKR Python API 10.50.2 source on Windows.
This implementation relies on that qualified callback ordering and field contract;
it does not claim a real Gateway qualification from this environment.

Legacy open orders retain callback/order numeric ID agreement, placing client ID,
exact reference, positive permanent ID, contract identity and order economics.
Legacy completed orders always retain `order_id=None` and `client_id=None`, even if
a Python object happens to contain defaults or attributes with those names. Their
reference, positive permanent ID and economics are corroborating evidence, not an
initial numeric-order/client attribution claim.

For protobuf, `openOrderProtoBuf`, `completedOrderProtoBuf` and
`executionDetailsProtoBuf` run before their decoded callbacks. `HasField` determines
presence; decoded zero/empty defaults are never evidence of supply. A supplied zero
order/client ID is distinguished from absence. A supplied empty reference is represented
by `order_ref=None` with its presence flag true; it cannot be an exact nonempty match.
Absent references have the flag false. No reference is trimmed, normalized or case-folded.

Top-level and nested open-order IDs are preserved separately in presence metadata.
If both are supplied they must agree. Top-level ID must also agree with the callback
argument and decoded order. Nested-only ID remains available with top presence false;
absence of both produces `None`, never an invented zero.

Protobuf completed-order IDs/reference/permanent ID are retained only when supplied.
Missing optional identity remains `None`. Required contract and economic fields must
be supplied and valid; missing required fields fail capture. Missing identity is
captured honestly and grants no attribution permission.

Both protobuf order sources require `HasField("orderState")` before lifecycle structure
is trusted. An open order with a present orderState but absent `HasField("status")`
retains `status=None`, with order-state presence true and status presence false. Private
capture does not inspect or promote the decoded status in that case; the existing public
projection still processes its decoded callback normally. Missing open-order orderState
fails the private collection. Completed orders require both orderState and a supplied,
nonempty status; missing structure, missing status or an empty supplied status fails.

A raw-present status must be exact bounded printable text and must equal decoded
`orderState.status` exactly. Conflict fails before a record is retained. Empty supplied
open-order status has presence true but stores `UNKNOWN`. Other bounded unrecognized
statuses map to the existing `UNKNOWN` category only after exact raw/decoded correlation.
Raw status text exists only in the temporary primitive envelope, never in the final
snapshot unless it is an existing approved status category. No status vocabulary is
expanded. Valid legacy status decoding remains unchanged.

Execution evidence retains optional order/client/permanent IDs and reference, exact
execution ID, contract identity, side, shares, price, optional cumulative quantity and
bounded execution timestamp text. The exchange field is the returned contract exchange.
The decoded execution request ID must match the active request exactly. No account
number, account ID, raw execution or raw contract is stored in this evidence.

## One-shot protobuf envelope

There is capacity for exactly one unmatched envelope across all three callback kinds.
The raw callback immediately copies only whitelisted validated primitives and presence
flags, plus generation, callback kind and decoder-thread identity. No raw protobuf
object survives the callback. The following decoded callback must consume that envelope
once on the same thread, generation and kind, and every supplied normalized value must
match. A second raw envelope, wrong decoded kind/thread/request, value conflict,
malformed protobuf, unmatched envelope at an end marker or generation loss fails capture.
The wrapper explicitly intercepts `orderStatus(orderId, status, filled, remaining,
avgFillPrice, permId, parentId, lastFillPrice, clientId, whyHeld, mktCapPrice)`. For a
current generation it invalidates any pending envelope under the read condition BEFORE
delegating to the inherited SDK handler, when that handler exists. Without an envelope
it preserves the inherited behavior and adds no evidence or failure. Status arguments
are never retained or interpreted as reconciliation evidence; the parent handler runs
after the boundary releases its local lock. Stale status callbacks cannot invalidate
a current-generation envelope. This extends the existing intervening-callback boundary
for callbacks handled by the read wrapper; it adds no catch-all SDK interception.

Encoding is fixed per source within a collection. A decoded callback without an envelope
after protobuf has been observed cannot silently downgrade to legacy evidence. Mixed
encoding in one source fails conservatively. Execution correlation uses the one active
execution request and exact decoded request ID in addition to envelope matching; it does
not invent an unqualified top-level protobuf request field. Order-state/status presence
comes from raw protobuf HasField. Raw-present status must match before conversion to the
existing fixed category; absent open-order status remains explicitly absent.

## Validation and privacy

Order/client IDs require exact `int`, excluding bool, and range 0..2,147,483,647.
Permanent IDs retain positive values only (zero becomes `None`), while conId must be
positive. These independent identifiers have a conservative signed-64 storage bound;
this is a local evidence bound, not an assertion that their SDK contract is signed int32.
Quantities are positive finite Decimal values. Price and cumulative quantity are finite
and nonnegative. Decimal size/exponent are bounded. Text is exact printable ASCII of at
most 100 characters; empty references carry no matching value. Status is one of the
existing fixed order-status categories or `UNKNOWN`; arbitrary status text is discarded.

Unrelated well-formed orders are retained for the existing selected account; evidence
is not filtered to the C3C2B pending order. Other security types, routes, actions and
order types remain exact bounded observations, never permission to trade them. A future
attribution consumer must enforce its qualified STK/SMART/BUY/MKT/DAY rules.
Account identifiers are used only transiently for existing account membership filtering.
No account identifiers, SDK objects, error strings, whyHeld, advanced reject JSON,
socket/client objects, credentials or capability IDs are added to private models/logs.

## Collection completeness, absence and freshness

Each read generation records requested/support/completion state separately for open
orders, completed orders and executions. End markers are `openOrderEnd`,
`completedOrdersEnd`, and `execDetailsEnd` with the exact integer execution request ID.
Completed-order support unavailable is `UNAVAILABLE`, not an empty completed response.
Not-requested, active, completed, unavailable, timed-out, malformed, interrupted, failed,
generation-lost and overflow outcomes are distinguishable. A successful `COLLECTED`
capture requires completed sources or explicitly unavailable completed-order support.
Normal publication also waits for the existing account/positions/time collection.

A terminal immutable snapshot is available only through private
`_reconciliation_snapshot()`. Before completion/failure it raises the controlled
`RECONCILIATION_READ_INCOMPLETE` code. It performs no connection, refresh, write,
barrier clear or portfolio operation. No public Broker method or snapshot field is added.
If terminal snapshot construction fails, the failure latch remains set and private
snapshot access remains unavailable; this cannot restore successful collection status.

The snapshot is a capture result, not a complete historical truth assertion.
`CURRENT_OPEN_ORDERS` identifies current-open coverage. Completed orders and executions
are explicitly `HISTORY_LIMITED`. `reqCompletedOrders(False)` removes the API-only
restriction without proving unlimited history. The default execution filter requests
available history; filter client zero is not placing-client evidence. Empty tuples alone
are never proof that an order or execution did not exist.

A future C3C2C2 reconciliation attempt must initiate a NEW post-dispatch read collection,
record its read-generation identity and start marker after the dispatch observation or
uncertainty, and consume that exact terminal snapshot. Read/write generations are separate
domains and must not be numerically compared. Cached `connect()` data retains its original
generation/markers; accessing it again does not create fresh reconciliation evidence.
Wall-clock equality alone cannot prove freshness. A held frozen prior snapshot survives
reset unchanged; new capture buffers belong only to the new read generation.

## Bounds, duplicates and interruption safety

The evidence sequence and collection markers increase for the transport lifetime and
never reset on reconnect. At most 1,024 combined order and distinct execution records
are retained per collection; overflow freezes a failed/incomplete capture with existing
records intact. There is no silent eviction. Identical execution IDs/evidence deduplicate
idempotently; conflicting contents for the same execution ID fail capture. Accepted
duplicates consume a sequence number without adding another retained record. This is
snapshot evidence metadata, not a claim that a consumer processed every sequence.

Current-generation extraction errors, including raw lifecycle structure/presence/value
extraction, mark capture malformed. KeyboardInterrupt,
SystemExit (including its exact code) and GeneratorExit first latch `INTERRUPTED` and
discard incomplete envelopes, then propagate the original interruption unchanged.
Stale-generation raw and decoded callbacks return before dangerous attribute access.
Capture runs under the existing read condition only; it never acquires coordinator,
write-transport or broker locks from callback threads. No reverse-lock propagation or
reconciliation clearing is introduced.

## Validation

Fake SDK, socket and protobuf doubles exercise field presence, exact identity, one-shot
pairing, conflicts, end markers, unavailability, timeout, generation isolation, lifetime
sequence, full-capacity overflow, duplicate executions, immutable/opaque evidence and
all three interruption types for raw/decoded order/execution callbacks. The original
159 C3C2C1 tests remain; 59 focused regressions add lifecycle presence/conflicts, missing
structure/status, original-interruption preservation and orderStatus interleaving/SDK
delegation. Full validation ran 2,391 tests: 2,390 passed and the one real Gateway
integration test was skipped. All 218 C3C2C1 tests and the prior 239 C1, 202 C3C2A
and 231 C3C2B tests passed, as did the C3A/C3B and frozen strategy suites.
The 64 canonical defaults, in-memory compilation, whitespace and secret/account/.env
change scans passed. No SDK installation or dependency change was made.
Broker/control-state reconciliation and recovery remain separately reviewed future work.
