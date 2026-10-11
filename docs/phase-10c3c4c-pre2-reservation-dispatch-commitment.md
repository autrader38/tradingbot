# Phase 10C3C4C-PRE2 — private reservation-bound dispatch commitment

PRE2 supplies the arrival/commitment prerequisite for later write-ahead dispatch
integration. It is exercised with offline SDK doubles only. C3C4C is still not
implemented: no journal/store is created, opened, imported or appended by dispatch.
Callback durability remains separate C3C4D work. Gateway Read-Only remains required;
the first real PAPER order remains blocked. PRE2 must be independently reviewed
before C3C4C is re-qualified. Windows runtime validation must be performed on the
actual Windows/Python environment; Linux validation does not establish it.

## Reservation and ownership

Only the synchronized existing allocator supplies the numeric broker order ID.
Its lifetime floor is advanced, or maximum-ID exhaustion permanently established,
before an immutable `_DispatchReservation` is issued. Failure, interruption,
disconnect and reconnect never release that ID or reduce the floor. There is no
REQ_IDS request and no caller-selected numeric ID.

The frozen, slotted, opaque reservation copies these exact primitives:

| Field | Meaning |
| --- | --- |
| `generation` | Exact placing write generation; separate from read generations |
| `order_id` | Actual allocator-reserved numeric broker ID |
| `client_id` | Exact positive placing client ID |
| `con_id` | Intended verified contract identity |
| `side` | BUY |
| `quantity` | Exact independently copied Decimal quantity |
| `order_type`, `tif` | MKT, DAY |
| `order_ref` | Exact consumed client order reference |
| `arrival_marker` | Transport-lifetime arrival marker at reservation |
| `callback_watermark` | Transport-lifetime sanitized evidence sequence |
| `order_id_floor` | Floor immediately after this ID's permanent consumption |
| `allocator_exhausted` | Exact lifetime exhaustion state after reservation |

The transport retains the exact issued object plus an independent primitive
snapshot for the active invocation only. Provenance is established by object
identity in that transport's registry, with its permanently bound coordinator.
Equal reconstruction, foreign issuer/owner, substitution, changed primitives,
stale generation and reuse fail. The reservation retains no transport/coordinator,
raw SDK object, account identifier, enrollment material or authorization capability.
Python private boundaries provide application isolation, not protection from
arbitrary hostile code modifying internals.

The captured coordinator preparation method now receives the actual reservation.
Preparation authenticates existing gates outside arrival synchronization and binds
the exact active `_Attempt` and exact reservation. Final validation requires those
same objects, exact intended contract/order/reference agreement and the transport's
issuer validation. Recursive dispatch cannot replace an active attempt.

## Arrival evidence and conservative rejection

`_arrival_sequence` is monotonically increasing for the transport lifetime. Under
`_arrival_lock`, current-generation error, connectionClosed, openOrder, orderStatus,
execDetails and nextValidId arrivals advance it **before** waiting for `_lock`.
Stale-generation callbacks return before inspecting dangerous SDK properties and
do not advance the marker. Read generations are never compared with write generations.

Open/status/execution arrivals retain the existing bounded sanitized primitive
capture and separate callback sequence. Their observed IDs advance the lifetime
allocator floor immediately. nextValidId's floor update is also secured on the
arrival side. Existing exact duplicate spans and callback cursor semantics remain.
No raw account/error/SDK/protobuf object is retained as reservation authority.

Final proof conservatively rejects **any** covered current-generation arrival since
reservation, including an apparently unrelated lower ID. A matching-looking callback
for the reserved ID cannot belong to this future invocation: it always blocks.
Wrong orderRef, economic/execution evidence, higher-ID floor advancement, malformed
evidence and deferred error/closed arrivals likewise cannot escape proof. An arrival
during allocation cannot be absorbed into a fresh reservation watermark.

Earlier unrelated historical evidence may precede reservation. Its IDs already
contribute to the floor and its sequence is captured in the commitment watermark;
it cannot confirm the later pending invocation. A collision for the reserved numeric
ID, including previously captured evidence, blocks proof. Earlier matching references
alone retain the existing callback attribution semantics and do not establish ownership
of a newly allocated numeric ID.

Current-generation error/closed/malformed arrivals also mark local arrival state
unusable. Before any invocation this inhibits bound NEW dispatch without claiming an
SDK call occurred. A new generation resets that generation-local condition only;
the permanent reconciliation latch, consumed IDs, arrival and evidence sequences
are never cleared. Once invocation may have occurred, error/closed arrivals secure
the permanent reconciliation latch before deferred bookkeeping acquires `_lock`.

## Final commitment and no-gap boundary

Lock direction remains:

`coordinator → control → read lifecycle → read condition → write dispatch → arrival`

Callbacks release arrival synchronization before acquiring write bookkeeping. They
never wait for bookkeeping while holding arrival. No reverse lock path or callback
wait is added. Authenticated enrollment inspection stays outside arrival; final
coordinator validation uses the freshly authenticated binding under the already-held
read locks. Existing current-state, dual-clock, authorization, risk/order/contract,
account, connectivity, generation and safety checks still execute at final proof.

Under arrival synchronization, the transport stages one exact `_InvocationCommitment`
bound to the reservation and its secured watermarks. SDK Contract/Order primitives
and the private specification must still exactly agree with the reservation. The
reviewed coordinator final validator then runs and returns the exact fresh local
challenge. Reservation identity, generation, marker, evidence, known-ID collisions,
allocator floor/boundary/exhaustion and reconciliation/readiness are rechecked.

Still under arrival, one-shot consumption marks the irreversible invocation boundary,
sets lifetime invocation-start state, records the known ID and captures the commitment
callback watermark. The registry requires the exact commitment object; equal copies,
substitution and second consumption fail. Final validation and boundary publication
have no unsynchronized interval in which a callback arrival can go unnoticed.

Arrival is then released and the SDK NEW call occurs at most once. Eligibility is not
reopened after that boundary. An error/closed arrival after commitment inhibits future
writes and makes the outcome uncertain, even if the SDK call has not yet begun. Normal
open/status/execution callbacks remain sanitized queued evidence under the existing
pending-confirmation and broker-state barriers; they do not permit another entry.
The existing callback cursor is not advanced by the new handshake.

## Failure and interruption

Before commitment, an ordinary failure denies SDK invocation. KeyboardInterrupt,
SystemExit and GeneratorExit propagate as the exact original object; the numeric ID
and client reference remain consumed, and no SDK invocation is falsely claimed.
No failed reservation remains registered for reuse.

From the irreversible commitment boundary onward, any exception secures transport
uncertainty. The coordinator/control reconciliation barriers and authorization
invalidation are secured before propagating an interruption, including interruption
while publishing the pending record or bridge result. No optional audit replaces an
original BaseException. Normal SDK return remains pending confirmation, not broker
acceptance. SDK failure or post-commit uncertainty grants no retry.

Reservation/commitment registry entries are discarded on every dispatch exit.
Lifetime invocation, allocator/exhaustion and reconciliation facts persist. There is
no clear/reset, migration, account rebinding, restart adoption or recovery authority.

## Isolation and validation

Every coordinator-bound NEW path uses PRE2; the old reservation-unaware bound proof
path is removed. Existing unbound C1 fake-SDK transport qualification remains available
as an isolated offline foundation. **Future C3C4C must impose the durable universal
journal gate on every NEW path capable of invocation; PRE2 is memory-only and does
not satisfy that gate.** No public Broker placement API is added or enabled.

Focused offline tests cover exact/equal/foreign/tampered reservations, exact/equal/
reused commitments, recursive/concurrent attempts, SDK object substitution, allocator
floor/exhaustion, current/stale arrivals, deterministic blocked-bookkeeping races,
post-commit arrivals, all three BaseException types before/after commitment and during
publication, opaque primitive privacy and absence of journal/storage integration.
Existing transport, dispatch, callback, authorization, enrollment, reconciliation,
journal, PRE1 and frozen-strategy regressions remain required.

AccountMode remains UNKNOWN; public account snapshot mode remains None. Neither
reservation nor commitment attests PAPER, grants authorization or enables LIVE.
Production Broker APIs remain read-only. No real connection/write, Gateway change,
portfolio mutation, fill booking or barrier clearing is part of PRE2.

Linux validation: 98 new PRE2 tests passed; the selected relevant run passed 1,830
tests (1,732 prior tests plus PRE2). Full offline discovery ran 3,723 tests: 3,722
passed and the one existing opt-in real read-only integration test was skipped.
All 93 Python files compiled; whitespace, secret, account-identifier, `.env` and
protected-file scope checks passed. Existing assertions were retained; prior dispatch
test hooks were updated only for the explicit reservation argument. No Windows
runtime result is claimed for PRE2.
