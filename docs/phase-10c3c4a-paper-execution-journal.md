# Phase 10C3C4A — private execution journal contract and pure replay

**C3C4A IS NOT DURABLE STORAGE.** It defines immutable private journal facts and
deterministic complete-history replay. It writes no file, opens no SQLite database,
hooks no dispatch or callback, and cannot survive restart by itself.

No recovery application, barrier clearing, retry, position management, cancellation,
protection, transport replacement, authorization re-arm or PAPER execution is added.
Gateway Read-Only remains enabled. AccountMode remains UNKNOWN; AccountSnapshot.mode
remains None. LIVE and production write routes remain unsupported.

## Source basis and private scope

`tradingbot_broker/paper_execution_journal.py` has no package export or consumer.
It imports only standard-library value/validation tools, the existing identifier
validator, timezone validation and read-only error/account-mode types. Existing
broker, transport, reconciliation, planner, strategy and backtest sources are unchanged.

The schemas copy the following reviewed contracts, without retaining their objects:

- `_PendingOrder`: exact client reference, numeric ID, placing client, write
  generation, contract/economics and dispatch provenance.
- C3C2B sanitized openOrder, orderStatus and execution fields. Status quantities
  belong to their individual status observation, never a later openOrder status.
- `OrderRequest`: creation/expiry, security, quantity, currency, requested side,
  type/prices, mode and intent. Generic well-formed requests can be retained as
  consumed denials; invocation remains restricted to PAPER / ENTRY / BUY / MKT / DAY.
  Requested mode records intent and never attests the broker's account environment.
- `RiskPermission`: exact full order-payload binding, permission boolean, validity
  and source. Arbitrary source reason text and reusable authorization are omitted.
- `IBKRContract`: verified stock/SMART mapping and its availability/validity source.
- `EntryApproval` / `InitialPosition`: setup, A/B/C/D witness references, reference
  entry/stop/target, quantity, BOD equity, risk budget and tick provenance.
- C3C2C1 qualified optional execution cumulative quantity and timestamp. Current
  C3C2B write evidence does **not** supply those fields: future integration must
  preserve None unless a separately qualified source supplies them.
- `Commission`: signed received amount, currency, report/execution link and time.

These are data contracts, not adapters from current runtime objects. No values are
invented for absent context or absent optional callback fields.

## Header, version and identities

`_JournalHeader` contains schema version 1, fixed format
`paper-execution-journal-v1`, a `journal-<32 lowercase hex>` identity and an exact
`_JournalAccountBinding` carrying `binding-<32 lowercase hex>`. Mode is fixed
UNKNOWN and cannot be a constructor override. Bindings identify a logical account
namespace; they are neither authenticated capabilities nor PAPER attestation.

`_SessionRegistered` records `session-<32 lowercase hex>`, positive placing client
ID and positive write generation. The session identity names an exact write
session/generation within a transport lifetime. A reconnect/new generation needs
a distinct registration identity, even if its transport callback counter continues.
Different transport lifetimes cannot share a callback namespace accidentally.

Exact duplicate registration is idempotent; different facts under the same session
ID are invalid. Registration does not establish connection, readiness or ownership
transfer. No read-generation field or read/write-generation comparison exists.

Unknown schema/format, wrong types, unsupported events and mismatched journal/account
envelopes are invalid. There is no implicit coercion, patch or schema migration.

## Consumed attempt and recovery context

`_AttemptConsumed` retains the exact client_order_id independently from its optional
immutable order, contract, strategy and risk snapshots. A client_order_id starts at
most one attempt in the entire journal,
including across sessions and after denial, uncertainty or terminal observation.

The current coordinator consumes a valid client reference before full payload gates.
If an order cannot be safely sanitized, order=None retains that consumption; all
other contexts must then be absent. It cannot reserve an ID or acquire a marker.
This missing snapshot does not claim explicit denial or prove the SDK was not called.
Well-formed SELL/LMT/STP/LIVE/EXIT requests can likewise be retained as denied intent
without acquiring entry-invocation authority.

An attempted order may also be denied before contract/strategy/risk evidence exists.
Those contexts are optional at consumption; absence is retained. An invocation
marker requires all three contexts, a verified contract and a positive risk decision.
This requirement is structural and does not evaluate live freshness or authorization.

Order/contract security, symbol and currency must agree. Strategy security/symbol
and original quantity must agree with the order. Risk context carries its own exact
order snapshot and must match the entire consumed order payload. Optional current
equity, exposure and cash remain None when unavailable; they are not supplied by
RiskPermission itself and require separately sourced facts.

Strategy context preserves frozen spec version v1.0, configuration/setup identity,
session date, four ordered A/B/C/D witnesses with level/time/source, eligible minute,
reference entry, raw/tick-adjusted reference stop and target, original quantity,
risk budget, BOD equity and stop/target tick sources. It retains no full market bars.
The schema validates bounded fields and reference-level ordering, not full strategy
eligibility, formula/tick authority or sizing. The copied values remain explicitly
**reference** values. Actual-fill/slippage/partial-fill management is unqualified.

No later event can change consumed context. There is no generic patch event.

## Numeric IDs and allocator constraints

The copied reviewed order-ID bounds are inclusive **0..2,147,483,647**. Numeric IDs
are exact ints, never bools. Session clients are positive ints within the same int32
maximum; supplied callback client IDs may be zero, but target attribution requires
the registered placing client. Positive conId is bounded separately to 64 bits.

`_NumericOrderIdReserved` requires an existing, unclosed, uninvoked attempt; each
attempt reserves at most one ID, each ID belongs to at most one attempt, and it
cannot fall below the retained global floor. Duplicate reservation is invalid,
even under the same attempt. Denial does not free either client or numeric IDs.

`_OrderIdFloorObserved` raises the lower bound conservatively: older lower values
are retained as input facts but cannot reduce replay's floor. `_BrokerOrderIdObserved`
advances it above an observed ID. Observing or reserving MAX exhausts the allocator
and retains MAX without wraparound. `_AllocatorExhausted` explicitly preserves
permanent exhaustion and a fixed reconciliation reason. New reservations afterward
are invalid, including in newly registered sessions.

A valid reservation of MAX may still have its own invocation marker; exhaustion
prohibits a subsequent reservation, not the already-reserved attempt. These facts
are allocator constraints, not an allocator or readiness authority.

## Dispatch facts and legal transitions

Dispatch facts remain independent from broker/economic observations:

| Event | Prerequisite | Retained meaning |
|---|---|---|
| `_AttemptConsumed` | Registered session; unused client reference | Permanently consumed attempt and copied context |
| `_NumericOrderIdReserved` | Existing open attempt; unused ID above floor | Permanent numeric ownership |
| `_InvocationMayOccur` | Reservation, complete qualified-context shape, PAPER/ENTRY/BUY/MKT/DAY intent, no closure/reconciliation | SDK invocation may occur or may have occurred |
| `_SdkReturnedNormally` | Invocation marker; no previous return | Named invocation returned normally; not acceptance |
| `_AttemptClosedWithoutInvocation` | Consumed attempt; no marker/previous closure | Explicit local gate/preflight closure; IDs stay consumed |
| `_ReconciliationRequired` | Existing attempt and/or registered session | Monotonic fixed safety reason |

Invocation marker, SDK return and closure each occur at most once. A marker after
closure and closure after a marker are invalid. Callbacks after a marker but before
normal return are valid. Later normal return does not remove any reconciliation fact.

A marker without return yields `sdk_outcome_uncertain`, even if broker observations
arrived. A normal return resolves only that missing-return diagnostic; explicit
interrupted/exception uncertainty remains latched, and broker/control state remains
unresolved. Session-scoped reconciliation applies to owned attempts, including later
consumption in that session. Marker absence means only "no invocation marker supplied";
it does not prove the SDK was not called. An absent closure is not a denial event.

Future C3C4C must commit the marker **before** SDK invocation, outside the existing
final proof-to-commit window. A marker may exist even when final validation subsequently
prevents the call. This phase deliberately has no post-marker not-invoked shortcut.

There is no retry, retirement, migration or reconciliation-cleared event. A client
reference cannot be reused and a second marker is invalid. No replay field grants
permission to send a replacement attempt under another reference.

## Callback facts, attribution and sequence domains

`_CallbackSource` carries registered session, exact write generation and positive
source callback sequence. Target callback records require the original attempt's
session/generation and exact reserved numeric ID. A supplied client ID must agree.
New-session registration does not authorize callbacks to adopt an old order.

Journal-specific `_OpenOrderObserved`, `_OrderStatusObserved` and `_ExecutionObserved`
contain copied validated primitives. They retain no SDK or transport evidence objects.
Lifecycle is a fixed enum for the eight reviewed statuses plus sanitized UNKNOWN.
Unrecognized raw text is not accepted as a lifecycle string.

Open observations require matching contract/economics. Exact reference plus qualified
recognized lifecycle establishes strong-open provenance using the existing C3C2B
optional-client/optional-contract-text rules. Missing reference is secondary;
wrong or empty supplied reference is conflict. UNKNOWN requires reconciliation.
Status never establishes strong identity. A terminal/economic status before strong
open identity preserves secondary evidence and latches unresolved-lifecycle reasons.

Each status record preserves its own filled/remaining/prices/parent fields and
validates filled+remaining==Q. Filled status requires its own Q/0 totals. Positive
status quantities contribute an owned historical lower bound only after strong-open
identity. They never attach to another open observation's status. Replay does not
classify current terminal economics or infer chronology from record proximity.

These target events cannot silently accept unrelated orders. A foreign target ID,
client/reference/contract/economics is invalid. Unrelated numeric-ID observations
can use `_BrokerOrderIdObserved` solely to retain allocator constraints. Capturing
the whole broker universe remains the separate read-evidence plane.

Journal sequence is contiguous 1..N for the supplied **complete** journal. A sequence
gap, duplicate or regression is invalid. No replay-window/checkpoint protocol exists.

Source callbacks may be a selected subset and may be appended as historical facts.
Replay does not infer source coverage, economic chronology or a callback gap from
missing source numbers. Same session/sequence must contain the exact same callback;
repeating that exact source event is idempotent. Different contents are invalid.

Observed callback maxima are separate from `_CallbackProgress`, which declares the
source's actual processed cursor and fetched through-watermark. Both declared values
are monotonic and processed<=through. Callback observation alone advances neither.
The future producer must uphold the reviewed actually-processed cursor invariant;
pure replay cannot prove that producer executed a handler. A qualified gap is an
explicit `_ReconciliationRequired(CALLBACK_GAP)` fact.

## Executions, partial quantities and fees

Executions retain exact execId, numeric/client/reference/contract identity, BOT side,
positive shares, nonnegative price and source. Optional cumulative quantity/time
remain absent unless supplied. Exact reference can independently attribute execution;
missing reference requires a **prior strong openOrder** and supplied exact client.
A prior execution or status is not that anchor.

An execId is globally unique to its target/economic identity. A repeated observation
under another callback sequence is idempotent only when every retained field except
source is identical, including optional-field presence and execution timestamp.
Conflicting identity, quantity, price, cumulative value or timestamp invalidates the
journal. There is no latest-wins or optional-field enrichment/correction policy.

Distinct attributed shares sum exactly and cannot exceed Q. Cumulative quantity must
be at least its own shares and at most Q; no cumulative chronology is inferred from
capture order. Cumulative proof contributes a historical lower bound but cannot
invent missing execution IDs or prices. `observed_execution_quantity` is the unique
execution union, independent from that lower bound.

If distinct attributed execution shares exceed the requested order quantity Q,
replay rejects the entire journal with `INVALID_PAPER_EXECUTION_JOURNAL`. The
journal is invalid and unusable for authoritative replay. The system must remain
quarantined and reconciliation-blocked. The offending record MUST NOT be silently
dropped, skipped, repaired or ignored, and the journal MUST NOT be partially replayed
into a supposedly clean authoritative state. Automatic trading or recovery MUST NOT
continue from the rejected journal. Manual investigation and recovery are required
before any future authoritative state may be established.

This disposition is a requirement for future consumers; C3C4A adds no recovery
mechanism, barrier-clear path, mutation authority or trading permission.

Thirty-percent observation does not imply the broker currently has exactly seventy
percent working. Even observed union==Q establishes no current position, protection,
fee closure, strategy recovery or clearable barrier.

`_CommissionReceived` preserves a signed received amount (including rebates), exact
currency, report ID, execution ID and received timestamp. It requires an already
attributed target execution. This is an attribution prerequisite, not an assumption
that SDK reports always arrive in that order. Future integration must separately
qualify staging of reports whose execution ownership is not yet established.

Late first reports are supported. Repeated identical amount/currency/report/execution
facts are idempotent even with a later receipt timestamp; the first receipt is retained.
A differing report or financial value fails closed. Reviewed corrections/update
semantics are deferred. Missing fees are not zero; there is no commission-completeness
flag, accounting total or fee-inclusive basis calculation.

## Replay output, validation and privacy

`_replay_paper_execution_journal(header, records)` accepts exact private models and
an exact tuple. It revalidates models, including construction-bypassed nested values.
Ordinary invalid input raises fixed `INVALID_PAPER_EXECUTION_JOURNAL` without raw
repr or exception text. KeyboardInterrupt, SystemExit (including code 37), and
GeneratorExit propagate as the exact original objects.

The frozen/slotted/opaque result retains the header, last sequence, sessions,
consumed IDs, reserved-ID ownership, floor/exhaustion, independent attempt facts,
observations, exact execution union, historical economic lower bound, received fee
diagnostics, fixed reasons, source maxima and explicit cursors. Immutable tuples and
frozensets contain no mutable internal accumulator references.

`restart_quarantine_required` is always true. Invocation implies unresolved broker,
price-coverage, fee, protective and strategy obligations as applicable, even without
received fills. There are no can_trade, can_retry, safe_to_send, authorized, clear,
recovery-capability or apply properties/functions.

Input Decimal values are finite, at most 64 coefficient digits with exponent [-64,64].
Derived totals permit up to 256 digits and remain bounded by original Q. Exact sums
use a fresh explicit 256-digit context/traps independent from hostile ambient
precision, traps or exponent limits. IDs reject bool. Strings are bounded exact
identifiers or printable ASCII, preserving accepted reference case/content.
Timestamps follow project valid-aware-instant validation and retain only standard
timezone/ZoneInfo objects; eligible minutes are aligned. Execution-time text is a
bounded copied primitive, not parsed chronology. No current clock is read.

Models contain no raw account number, SDK/protobuf object, socket/client, capability,
secret, enrollment credential or arbitrary broker message. Reprs are opaque at every
nested model boundary. Opaque identity syntax is not cryptographic authenticity.

## Restart limits and future phases

Pure replay establishes consistency of supplied facts, not existence, completeness,
durability, authenticity or freshness of a file. Missing/corrupt journals must not be
treated as empty clean state by future startup. A restored-old valid journal cannot
be detected by this pure contract: anti-rollback is not implemented.

The qualified progression is:

**C3C4A contract/replay -> C3C4B transactional SQLite storage -> C3C4C write-ahead /
dispatch integration.**

Further exposure, lifecycle, protection, management, session, strategy/data and
accounting foundations remain mandatory before the first real PAPER write. No existing
barrier or authorization invariant is weakened by this module.

## Offline verification

Run `python -m unittest tests.test_paper_execution_journal -q` and the complete offline
suite with the existing real integration opt-in unset. Tests exercise field-specific
validation, legal transitions, ID safety, source replay, quantities/fees, Decimal
extremes, construction bypasses, opaque immutable state, BaseException and forbidden
I/O/clock/network/lock boundaries. They make no SDK call or real broker connection.
