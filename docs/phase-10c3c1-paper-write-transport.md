# Phase 10C3C1 — isolated IBKR paper write-transport foundation

**OFFLINE ONLY. NO REAL CONNECTION OR ORDER TRANSMISSION.**

This foundation is separate from the permanent read-only transport. It is not
installed in `ReadOnlyIBKRBroker` or the strategy/portfolio broker API. Gateway's
Read-Only API setting must remain enabled. No real SDK dependency was installed
and no Gateway session was opened during this phase.

## Qualified SDK contract

The user supplied local inspection of the official **IBKR TWS API 10.50.2**:

```text
placeOrder(orderId: int, contract: Contract, order: Order)
cancelOrder(orderId: int, orderCancel: OrderCancel)
reqGlobalCancel(orderCancel: OrderCancel)
```

The SDK chooses legacy or protobuf encoding internally. The transport calls
these named methods, never their protobuf helpers directly. Runtime qualification
requires the supplied positional parameter names/arity without defaults or
variadic arguments. Unexpected SDK contracts fail with sanitized fixed codes.
Official SDK compatibility is exercised with exact-signature offline doubles;
actual installation/runtime qualification of this new boundary remains deferred.

`OUT.PLACE_ORDER`, `OUT.CANCEL_ORDER` and `OUT.REQ_GLOBAL_CANCEL` must be exactly
3, 4 and 58. Plain integer SDK fixtures or matching members of the SDK's OUT enum
are normalized only from those named constants. No arbitrary coercion is used.
`PROTOBUF_MSG_ID` must be exact integer 200. Only the three qualified named
write translations produce 203, 204 and 258.

## Separate immutable policies

| Policy | Legacy IDs | Protobuf IDs |
|---|---|---|
| Permanent read-only | `{7,16,17,49,61,62,63,64,71,99}` | `{207,216,217,249,261,262,263,264,271,299}` |
| Isolated paper foundation | `{3,4,7,16,17,49,58,61,62,63,64,71,99}` | `{203,204,207,216,217,249,258,261,262,263,264,271,299}` |

Each paper set has exactly 13 IDs. Neither policy uses ranges or a runtime
write-enable switch. Read-only connections cannot be converted to writers.
`reqIds` is **excluded** (legacy 8 and protobuf 208), despite its known Python
signature. No additional read or write operation is qualified.

The paper client validates exact IDs and exact payload types before delegating to
official SDK framing. Its separate final socket guard validates bytes again,
including calls that bypass the client override through SDK base methods. The
guard supports the existing version handshake, ASCII/NUL legacy IDs and four-byte
unsigned big-endian IDs. Protobuf IDs require raw framing. Invalid lengths,
unknown IDs, concatenated frames and frames exceeding the existing 64 KiB body
limit fail closed. Parsing selects one encoding without retrying another after
failure. Payloads after the ID are opaque and cannot confer authorization.

Before the first byte, a full frame is validated. Partial sends may continue only
with the exact unsent suffix. Altered continuation or a replacement frame fails.
An exception after raw `send` or `sendall` begins, or an invalid raw return value,
permanently poisons that guarded socket. Every later send fails before touching
the sink; close/shutdown remain available. This also applies to pending
continuations. A valid zero-byte `send` retains the entire pending suffix without
an implicit retry loop. Rejections before raw invocation do not poison the socket.
Poison is committed first on any raw outbound exception. Ordinary `Exception` is
then sanitized to a fixed controlled failure. `KeyboardInterrupt`, `SystemExit`
and `GeneratorExit` instead re-raise the original interruption unchanged, including
its identity and exit value. During a named SDK write, the transport then latches
reconciliation before propagating that same interruption to the caller. These
interruptions are not converted to OUTCOME_UNKNOWN. No automatic retry or broker
acceptance/rejection is inferred; close/shutdown remain usable after poisoning.
No raw socket, descriptor, connection, SDK client or generic sender is exposed
on the transport's public surface or its restricted internal client view.
These are application boundaries, not a Python reflection sandbox.

## Configuration and phase boundary

`PaperTWSConfig` requires the exact `PaperTransportIntent.LOCAL_PAPER_FOUNDATION`
member. It accepts only literal localhost hosts, valid ports, positive nonzero
client IDs and integer timeouts from 1–60 seconds. There is no environment loader,
LIVE selector or automatic substitution for `TWSReadOnlyConfig`.

Port 4002, this configuration and SDK connectivity do not prove PAPER.
**AccountMode remains UNKNOWN.** Enrollment and authorization remain unchanged;
MATCHED alone remains insufficient and ARMED still leaves trading UNUSABLE.

`PaperTWSTransport` has **no public connect or write-dispatch API**. Construction
lazily imports/qualifies the official SDK but opens no socket. Private generation
and dispatch primitives are exercised with already-connected in-memory SDK
doubles. No application path establishes a real paper-write connection in this
phase. The minimal private adapter creates only stock/market SDK objects from
typed non-sensitive scalar data for offline contract testing. Production
`OrderRequest` translation, authorization and risk bridging are deferred.

## Lifetime order-ID non-reuse and generation-bound callbacks

The allocator requires a current-generation `nextValidId` callback with an exact
nonnegative signed int32: **0..2,147,483,647**, including valid zero. This bound
also applies to observed IDs, known cancellation targets and dispatch-result IDs.
Bool, negative, oversized and malformed IDs fail closed. It cannot allocate from observations
alone. Repeated `nextValidId(N)` raises the boundary to `max(boundary, N)`.
Every observed `openOrder` or `orderStatus` ID M raises it to at least M+1,
regardless of originating client. Observations received before `nextValidId`
are retained as a lower bound without making the allocator ready.
The effective boundary is at least the private transport-lifetime floor. Valid
broker lower-bound evidence never lowers that floor, and accepted current-generation
`openOrder`/`orderStatus` IDs raise it to at least M+1. Reservation raises it to
N+1 immediately, before later preflight or SDK invocation can fail. These accepted
lower bounds survive disconnect, reconnect and generation reset. For example,
reserving 10 makes a later `nextValidId(5)` or `nextValidId(10)` allocate at least 11.
The lifetime floor is only a non-reuse constraint, never connection permission.

Observing the maximum ID exhausts the allocator instead of producing MAX+1.
`nextValidId(MAX)` permits one final reservation; that reservation immediately
exhausts allocation for the transport instance's lifetime. Later callbacks,
generation resets and reconnect cannot revive it. No recovery/reset API exists.

Required initial `reqAllOpenOrders` synchronization must be ACTIVE before its
completion marker is accepted. New-order dispatch stays blocked until completion;
missing completion remains incomplete with no waiting or fabricated success.
Private allocation increments under the transport lock. New-order dispatch
accepts no caller-selected broker ID. Allocated IDs are never recycled, including
local failures after reservation and uncertain invocation. Reserving the final
valid ID leaves the allocator exhausted even if dispatch fails; an existing ID is
never reused to retry or modify a NEW order.

Disconnect invalidates generation readiness immediately while preserving lifetime
reservation/observation history and exhaustion. Reconnect never permits reuse of
a consumed NEW-order ID. A new generation requires fresh
`nextValidId` and open-order synchronization; stale callbacks cannot initialize,
complete or change the current generation. Malformed ID callbacks preserve the
previous numeric boundary but block writes for the affected generation.
Known existing IDs are addressed separately for private cancellation tests.

Callbacks cover `nextValidId`, `openOrder`, `openOrderEnd`, `orderStatus`, numeric
errors, execution/end markers, both commission callback names and
`connectionClosed`. Only ID bounds/known IDs, bounded numeric error codes and
sanitized callback categories are retained. Raw Contract/Order/Execution objects,
account identifiers and broker error strings are discarded. No fills, position
economics, acceptance or broker acknowledgements are manufactured.

## Internal dispatch outcomes

| Result | Meaning |
|---|---|
| NOT_DISPATCHED | Local preflight failed before SDK write invocation began. |
| DISPATCHED | The exact named SDK write method was invoked and returned normally. |
| OUTCOME_UNKNOWN | Invocation began and an exception left broker receipt uncertain. |

**DISPATCHED does not mean broker receipt, acceptance, acknowledgement, working
status or a fill.** Official SDK validation may report `wrapper.error` and return
normally without sending bytes. Safe numeric callback codes are retained for
future reconciliation; raw error text is not returned.

The transport's post-invocation `BaseException` handler surrounds only the exact
named SDK write invocation. It latches reconciliation before optional result creation
or propagation. Regular `Exception` retains sanitized OUTCOME_UNKNOWN behavior;
`KeyboardInterrupt`, `SystemExit` and `GeneratorExit` propagate as their original
interruptions after safety state is secured. They cannot bypass uncertainty
safety, and propagation is not broker rejection. No exception object/text is
persisted by the transport. Locks release and callbacks remain processable.

An interruption before SDK invocation does not claim dispatch uncertainty.
If an ID was already reserved, however, it remains permanently consumed. This
distinguishes ID non-reuse from broker receipt uncertainty. No interruption
authorizes an automatic retry, and reconnect cannot clear a latched barrier.

No write is automatically retried. OUTCOME_UNKNOWN latches a transport-instance
reconciliation barrier. Disconnect, reconnect, generation changes, fresh
`nextValidId` and completed open-order synchronization cannot clear it.
**Transport reconnect does not resolve an uncertain prior broker write.** Process
restart is also not proof of reconciliation; production recovery remains a
separately reviewed future requirement. There is no reconciliation/unfreeze API
in this phase. Blocked writes invoke no SDK write and reserve no new order ID.

After any write invocation has begun, a current-generation SDK error callback
conservatively latches this barrier as soon as it is processed, including delayed,
synchronous and malformed-code errors. No informational-code exemption or guessed
attribution is used. Old-generation callbacks cannot poison a clean current
generation or clear an existing barrier. Exact bounded numeric codes may be
retained; raw text is discarded. A normal SDK return still means DISPATCHED only,
and its result carries an explicit `reconciliation_required` signal when latched;
it never becomes proof of acceptance or rejection.

The transport lock serializes generations, allocation and SDK invocation. A
short arrival lock orders error revocation against final dispatch commitment.
Error intake releases the arrival lock before waiting for transport bookkeeping,
so a callback deferred by an in-flight SDK call cannot become a later attempt's
baseline. No SDK operation runs under the arrival lock. An error latched before
commitment prevents invocation; one arriving after commitment blocks every later
write. Socket sends and poison transitions are separately serialized.

Global cancel is distinct and never runs during ordinary cancel, disconnect,
failure handling or cleanup. No real order has been transmitted in this phase.

## Validation and next phase

Offline tests exercise exact signatures, all wire policies, client/socket bypasses,
malformed frames, pending suffixes, callback chronology, ID bounds, concurrent
allocation, dispatch uncertainty and continued armed read-only broker rejection.
The opt-in real read-only integration test remains skipped by default. Run:

```bash
python -m unittest tests.test_ibkr_paper_transport -q
python -m unittest discover -s tests -q
```

Phase 10C3C2 must separately review atomic authorization + risk + fresh account
evidence + dispatch, authenticated enrollment, connection-generation ownership,
unknown-dispatch reconciliation, broker callbacks and order-ID lifecycle,
cancel/replace semantics and PAPER-only operational qualification. No live routing,
broker PAPER attestation, strategy bridge or Gateway configuration change is
authorized by this foundation.
