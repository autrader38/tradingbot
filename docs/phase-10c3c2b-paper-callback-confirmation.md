# Phase 10C3C2B — offline broker callback attribution and confirmation

**OFFLINE ONLY. No real IBKR connection or order has been transmitted. Gateway
Read-Only remains unchanged. BROKER_OBSERVED != BROKER ACCEPTED FOR STRATEGY PURPOSES.**

The single private C3C2A coordinator now synchronizes sanitized broker callback
evidence from its isolated paper transport. It remains outside the production Broker
protocol. There is no public connect lifecycle, CLI, environment activation, strategy
write route, cancel/replace/global-cancel integration or reconciliation-clear API.
AccountMode remains UNKNOWN; LIVE is unsupported. No callback supplies PAPER attestation,
authorization, scope or risk permission.

## Qualified order reference contract

The user directly inspected installed official Python API 10.50.2: Order.orderRef is
a string field; legacy placeOrder includes it, createOrderProto includes a nonempty
reference in the protobuf request, and both incoming openOrder decoders copy the
returned reference before wrapper.openOrder. Legacy and protobuf execution decoders
also support Execution.orderRef. This qualifies the field paths, **not a guarantee
that TWS echoes the reference unchanged in every circumstance**.

The coordinator translates the exact validated OrderRequest.client_order_id into
the private stock/market specification. C1 assigns that exact string to Order.orderRef
during SDK object preparation, before reservation and final authorization commitment.
The named SDK method remains placeOrder(orderId, Contract, Order); production code
does not invoke protobuf write helpers or invent serializers. The existing C1 fixture
may omit the optional reference; coordinator NEW entries always supply it.

No trimming, normalization, case folding, prefix/suffix or approximate comparison is
used. An empty/missing reference cannot confirm an open order. A different reference
for the pending numeric ID is a conflict. Malformed/unbounded references fail closed
after write commitment. Raw unexpected references are never logged or returned.

## Pending identity and primitive evidence

Before invoking the SDK, the coordinator copies expected identity from validated
inputs independently of caller-held models. Before returning clean pending dispatch,
it installs a frozen private record: client_order_id, reserved int32 order ID, write
generation/client ID, conId, security ID, symbol/currency, BUY/MKT/quantity/DAY,
dispatch time and the callback sequence at invocation commitment. There are no
account identifiers, capabilities, SDK objects or mutable contract/order references
in that record. C3C2A's permanent single-owner binding and duplicate history remain.

The paper callback boundary immediately copies qualified primitive fields into
frozen private evidence:

| Evidence | Copied fields beyond generation, sequence and int32 order ID |
|---|---|
| openOrder | Optional client ID/reference, conId, security type, optional exchange/symbol/currency, side, order type, exact Decimal quantity, TIF, fixed status category |
| orderStatus | Client ID, fixed status, exact filled/remaining Decimals, finite nonnegative average/last prices, parent order ID |
| execDetails | Optional client ID/reference, bounded execId, conId, BOT/SLD side evidence, positive Decimal shares, finite nonnegative price |

Unknown statuses become UNKNOWN, never raw strings. whyHeld, account/acctNumber,
raw callback objects, errorString and SDK repr are not retained. permId is omitted:
it is unnecessary for this attribution rule and is not incorrectly constrained as
an int32 API orderId. API order IDs remain exact ints in 0..2,147,483,647; bool is
rejected. Callback conIds use a separate conservative 64-bit evidence-storage bound.
Copied numeric evidence has at most 64 digits and exponent -64..64. Exact sums use
256-digit local arithmetic, independent of application Decimal rounding, so a tiny
execution overfill or impossible filled/remaining total cannot be rounded away.

## Sequence, storage and cursor

Each accepted relevant callback under a bound owner receives a unique increasing
transport-lifetime sequence, including duplicates. Generation reset, disconnect,
synchronization and coordinator interaction do not reset that sequence or ledger.
Evidence before invocation commitment cannot confirm a later order.

The private ledger holds at most 128 entries. Consecutive exact duplicate events
are coalesced while their sequence still advances. Interleaved repeats are retained
because Submitted → Cancelled → Submitted must be detected as a regression. A new
event that would exceed capacity permanently latches transport reconciliation before
any potentially required truth could be silently lost. Existing evidence is not
evicted. This intentionally conservative lifetime ledger has no recovery/reset API.

Only the exact strong coordinator owner can obtain an immutable tuple batch newer
than a validated private cursor. Bounded duplicate-span metadata preserves each exact
sequence; snapshots expand those spans into pages of at most 128 immutable events.
The batch upper bound is informational, never an acknowledgement. The coordinator
processes exact consecutive sequences one event at a time, secures that event's safety
effects, then advances its cursor to that completed event only. A conflict/processing
exception stops the page without acknowledging later evidence; reconciliation does
not advance the cursor. Sequence gaps require reconciliation. Repeated synchronization
may drain duplicate pages without changing lifecycle state. No public queue, event
injector, acknowledgement/clear operation or raw mutable collection is exposed.

## Attribution and lifecycle

Strong openOrder observation requires the expected current write generation and
reserved numeric ID, exact orderRef, matching client ID when supplied, exact conId,
STK, BUY, MKT, exact total quantity and DAY. Returned exchange must be SMART when
present; returned symbol/currency must match when present. Missing optional fields
are not fabricated. Missing orderRef leaves pending unresolved; same-ID conflicting
identity requires reconciliation. Another well-formed order ID cannot confirm this
entry, though observed IDs still raise C1's permanent lifetime non-reuse floor.

orderStatus is secondary numeric-ID evidence. It cannot establish strong identity
on its own. PendingSubmit, PreSubmitted and Submitted before strong openOrder identity
remain secondary correlation only. Terminal/economic same-ID evidence before that
identity (Cancelled, ApiCancelled, Inactive, Expired, Filled or any positive filled
quantity) immediately requires reconciliation and authorization invalidation without
claiming strong identity or broker acceptance. After strong identity it advances lifecycle. Only PendingSubmit, PreSubmitted,
Submitted, Filled, Cancelled, ApiCancelled, Inactive and Expired are recognized.
Unknown same-ID status, terminal-to-working regression, decreasing fill, increasing
remaining quantity or an impossible total requires reconciliation. Duplicate exact
status is idempotent. Inactive is recorded as terminal observation, without inventing
strategy rejection semantics.

Strong execution observation requires the expected generation/order ID, conId, BOT
side and matching supplied client ID/reference. If orderRef is supplied it must match
exactly. Without it, prior strong openOrder identity **and** supplied exact client ID
are required; otherwise attribution is ambiguous. A private monotonic
_strong_open_order_identity_established flag is set only by successfully validated
exact openOrder evidence. Prior execution observation, orderStatus or an observed
lifecycle enum cannot supply this provenance. An exact-reference execution can be
independently attributed, but does not establish openOrder provenance for another
execution that omits its reference. Ambiguous executions are not accumulated as
safely attributed shares. Positive shares are
deduplicated by bounded exact execId. Conflicting content under the same execId or
cumulative shares above expected quantity requires reconciliation. Neither execution
nor Filled/partial-fill status constructs a production Fill, updates holdings or
fabricates execution details not present in callbacks.

The private sync result separates primary safety state from historical observation:
NO_CHANGE, BROKER_OBSERVED, BROKER_TERMINAL, EXECUTION_OBSERVED and
RECONCILIATION_REQUIRED. The historical observed_state can retain execution evidence
while primary state reports reconciliation. Precedence is:

**RECONCILIATION_REQUIRED > execution/broker-state reconciliation > observed/terminal
> pending confirmation.**

A strong non-economic openOrder resolves pending confirmation into a permanent
BROKER_STATE_RECONCILIATION_REQUIRED entry barrier. Authorization may remain ARMED,
but a fresh capability, permission, refresh or reconstruction cannot permit another
entry. Callback identity answers whether the broker observed this order; it does not
prove local working-order/account/position/risk state is synchronized.

An execution, Filled/partial economic observation, conflict, evidence overflow or
lost pending write generation permanently escalates coordinator, paper-transport and
control-plane reconciliation and invalidates authorization. Later matching evidence
never clears that state. If transport uncertainty already exists, it outranks successful
evidence processing. Well-formed unrelated callbacks and identical duplicates do not
fabricate observation or a second lifecycle transition. Accepted observed MAX order ID
still exhausts the allocator for the transport lifetime.

## Synchronization, locks and privacy

Callbacks announce evidence/malformed-data reconciliation under paper arrival locking,
release it, then perform C1 dispatch/allocator bookkeeping. They never acquire earlier
coordinator or broker locks. Thus a callback deferred by dispatch locking cannot have
its evidence or fatal error silently absorbed into a later dispatch baseline.
Accepted observed IDs also raise lifetime non-reuse history under arrival locking
before deferred bookkeeping; a generation reset cannot discard that accepted safety
evidence or restore allocation after an accepted MAX ID. Readiness/known-ID bookkeeping
still requires the current generation under the dispatch lock.

The explicit private _sync_broker_callbacks operation uses the existing order:
coordinator → broker → read lifecycle → read condition → paper dispatch → paper
arrival. Every coordinator dispatch interaction synchronizes callbacks before duplicate,
pending or observed-barrier returns. Delayed errors escalate on that next interaction;
callbacks do not reverse lock order to update broker state asynchronously. No callback
wait, request, enrollment-file I/O, worker join or sleep is added to synchronization.
Current SDK inspection is local only. Current-generation callback primitive extraction
interrupted by KeyboardInterrupt, SystemExit or GeneratorExit permanently latches
transport reconciliation before re-raising the exact original interruption (including
SystemExit's code). No successful evidence is fabricated, no raw object is logged,
and callbacks acquire no broker/coordinator locks. The next coordinator interaction
escalates control-plane reconciliation and invalidates authorization. Ordinary malformed
extraction retains its fail-closed policy. Old-generation callbacks are ignored before
raw payload inspection, including attributes that would raise an interruption. Fresh callbacks cannot confirm an order dispatched in a lost older
generation; losing that generation requires reconciliation.

Existing fixed authorization-invalidated audit is used on escalation, after all safety
flags and revocation are committed. Audit failure cannot undo them. Observation sync
does not add raw payload audit records. Evidence/pending repr is opaque; sync results
contain only validated client IDs, numeric order IDs, sequence, typed states and flags.
No account ID, fingerprint, key/salt, capability_id, challenge/proof, SDK object,
socket, raw reference mismatch or exception text crosses these surfaces.

## Preserved boundary and validation

C3A enrollment, C3B authenticity/scopes/generation/deadlines/current SDK connectivity,
generic risk/safety, frozen strategy and permanent read-only sources are unchanged.
C1 order-ID lifetime floor/MAX exhaustion, socket poison, BaseException propagation,
delayed error latch and permanent reconciliation remain intact. Exact wire policies:

| Policy | Exact IDs |
|---|---|
| Paper legacy | {3,4,7,16,17,49,58,61,62,63,64,71,99} |
| Paper protobuf | {203,204,207,216,217,249,258,261,262,263,264,271,299} |
| Read-only legacy | {7,16,17,49,61,62,63,64,71,99} |
| Read-only protobuf | {207,216,217,249,261,262,263,264,271,299} |

REQ_IDS 8/208 remain excluded. All qualification uses offline SDK/socket/callback
doubles. Two C3C2A reconnect tests are intentionally strengthened: stale callbacks
remain ignored, but losing a clean pending order's generation now requires reconciliation;
their no-second-write assertions remain. No dependencies are installed or changed.

Future separately reviewed recovery must reconcile account/orders/positions and govern
barrier clearing. This phase supplies no such recovery, no second entry, no real paper
connection and no PAPER attestation. Gateway Read-Only must remain unchanged.
