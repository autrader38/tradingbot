# Phase 10C3C4C — private durable write-ahead dispatch integration

C3C4C connects the existing journal contract/storage to coordinator-bound NEW
entry dispatch. The exact bound store must commit and verify `_InvocationMayOccur`
before PRE2 can consume its invocation commitment or call the fake SDK in offline
qualification. There is no optional journaling flag or bound memory-only fallback.

This phase requires independent review and actual Windows/Python 3.12 validation.
Gateway Read-Only remains required. The first real PAPER order remains blocked.
AccountMode remains UNKNOWN, public snapshot mode can remain None, and LIVE remains
unsupported. Journal/account identity does not attest PAPER mode.

## Exact lifetime ownership and explicit setup

The private `_create_paper_dispatch_journal_owner(coordinator, path)` factory runs
outside dispatch on the designated dispatch thread. It issues and authenticates
the existing exact PRE1 account receipt, creates a fresh header and explicit SQLite
store, validates empty history, and commits a fresh `_SessionRegistered`. It
revalidates that same receipt after storage latency and publishes the owner only
after successful registration. IDs use independently generated `journal-` and
`session-` namespaces with 32 lowercase hexadecimal characters.

`_PaperDispatchJournalOwner` permanently retains the exact coordinator, control
broker, read transport/issuer, write transport, receipt, header, store, session,
and `threading.current_thread()` object. Independent primitive snapshots and
identity checks reject substitutions, including equal reconstructed receipts,
headers, sessions and invocation proofs. The coordinator permits one setup
attempt and one successful claim. Failed setup consumes that opportunity, secures
reconciliation, and deterministically discards any connection it created.

Only fresh creation is supported here. Existing paths are rejected by C3C4B;
there is no open/adopt fallback, default path, environment path, journal discovery,
store switching, replacement, account rebinding, reconnect migration, or second
journal after failure. An existing journal must not become runtime authority.
Reconnect/read-generation change, write-generation change, receipt invalidation,
or account/enrollment replacement invalidates the association. No new session is
automatically registered to restore it.

SQLite's default same-thread protection remains enabled. Setup, activation,
dispatch journal work, load, append and close require the exact creator thread.
Wrong-thread dispatch consumes the caller reference in memory, inhibits the
runtime, performs no SQLite access, and invokes no SDK. There is no production
queue or connection per thread. Store methods are captured from the reviewed
class during setup, not selected from mutable per-instance hooks.

## Internal context and durable event ordering

The private coordinator accepts `approval` and `position` as keyword inputs in
addition to the existing order, risk permission and capability. It internally
calls PRE1 with these exact sources, its selected verified contract and an explicit
validation timestamp. Caller-supplied `_DispatchJournalContext` is not accepted as
authority. PRE1's frozen configuration, witness, BOD equity, risk budget and tick
provenance rules are unchanged. Reference prices, stops, targets and simulated
Phase 6 fills are not broker execution facts.

| Boundary | Required facts |
| --- | --- |
| Setup | Verified durable `_SessionRegistered`, exact placing client ID and write generation. |
| Attempt begins | Existing one-shot in-memory client reference consumption; preliminary gates and internal PRE1 context. |
| PRE2 preparation | Allocator-backed permanent numeric reservation; coordinator receives its exact issued reservation. |
| First append | Complete `_AttemptConsumed` with exact session, order, contract, strategy and risk projections. |
| Second append | `_NumericOrderIdReserved` containing that actual reserved numeric ID. |
| Third append | `_InvocationMayOccur` for the exact consumed reference. |
| After successful durable marker | Fresh authentication/context/gate checks; exact marker proof; PRE2 arrival-aware final commitment. |
| SDK normal return | Durable `_SdkReturnedNormally` before clean transport result construction/publication. |

The owner itself invokes its captured exact store append. It checks returned
record type, event identity, header, sequence and history; independent loads before
and after append detect unexpected history and fabricated append results. C3C4B
commit verification remains unchanged. Returned records cannot be supplied by the
caller as dispatch permission. The final proof retains exact attempt, reservation,
context, event and committed-record objects plus independent primitive snapshots.
Equal reconstruction or replay is insufficient.

No floor observation is inserted ahead of an already-reserved ID. The existing
allocator remains exclusive: no REQ_IDS, caller-selected ID, release, reuse, floor
reduction or exhaustion reset. Reserving MAX_ORDER_ID still permits that one
dispatch while permanently exhausting future allocation. A failure after numeric
consumption but before durable history catches up inhibits further runtime writes.

A denied attempt without safe complete context may record identifier-only
`_AttemptConsumed` and `_AttemptClosedWithoutInvocation`. It cannot later be
upgraded/reused. Closure is never appended for an attempt whose invocation marker
may have committed. A later different denied reference can still be recorded while
the prior clean pending attempt remains protected. Reentrant attempts never start
nested SQLite work during SDK invocation; they consume the new reference and
permanently inhibit the owner because durable history cannot keep pace.

## Fresh revalidation and final commitment

After storage latency, the coordinator authenticates the SAME issuer-owned PRE1
receipt, checks current account/enrollment/read generation, reprojects the same
immutable source context, and repeats the existing dispatch gates. These cover
capability identity/scope and wall/monotonic deadlines, authorization, order/risk
expiry and agreement, fresh verified account/read health, write generation/client
and readiness, contract availability/validity/mapping, ownership, pending state,
emergency/pause controls and all reconciliation barriers.

PRE2 then checks the exact attempt/reservation/proof and current arrival,
callback/economic evidence and allocator state under arrival synchronization.
Publishing/consuming its one-shot commitment and marking the irreversible
invocation boundary are atomic with final proof. There is no SQLite or enrollment
I/O between that commitment and the SDK call. PRE2's reservation/arrival algorithm
is unchanged; only narrow captured SDK outcome hooks are added.

Lock direction remains coordinator → control → read lifecycle → read condition
→ write dispatch → arrival. Synchronous SQLite work may run under the earlier
coordination locks, never under arrival. No database transaction spans an SDK call,
and production storage work never waits for callbacks. Callback threads announce
arrival, release arrival synchronization, and later perform bookkeeping; they
never access the store. Reserved-ID evidence, errors, closed arrivals and floor
changes during append latency prevent clean commitment, including arrivals whose
bookkeeping is blocked behind the dispatch lock.

## Failure, uncertainty and interruptions

BUSY, STORAGE_FAILURE, POISONED, QUARANTINED, COMMIT_OUTCOME_UNKNOWN, closed stores,
corruption, invalid events/history/format, wrong header/session/store/thread and
interruptions cannot reach invocation commitment or the SDK. No retry, alternate
store, journal repair or memory-only continuation is permitted. C3C4B's persistent
semantic quarantine retains the offending evidence; no rejected prefix is adopted.

The owner conservatively records that a marker may exist as soon as its append
begins. Unknown commit outcomes and interruptions cannot be treated as clean
pre-invocation closure. After a successful marker, any final denial secures
transport/coordinator/control reconciliation, authorization invalidation and owner
inhibition, reports OUTCOME_UNKNOWN, and may best-effort append the existing
`_ReconciliationRequired` event. This does not claim an SDK call actually occurred.

An SDK exception secures safety before optional diagnostic persistence. Normal SDK
return is not broker receipt, acceptance, working status, fill or account-mode
attestation. Failure to persist `_SdkReturnedNormally` produces OUTCOME_UNKNOWN
with permanent inhibition; the SDK is not called again. Diagnostic persistence
failure never masks broker uncertainty or weakens safety.

KeyboardInterrupt, SystemExit and GeneratorExit propagate as the exact original
object. Before a marker, consumed references/IDs remain consumed and no invocation
is invented; an interrupted attempt inhibits incomplete runtime history. After a
marker/commitment, safety is secured before propagation. Best-effort diagnostics
cannot replace the original interruption.

## Remaining boundaries and qualification

Synchronous callbacks during placeOrder remain possible and are captured in memory.
C3C4C writes no callback, execution or commission events. C3C4D durable callback
capture remains separate; this journal does not reconstruct durable fills or
production accounting. Public Broker APIs and read-only write blocking, wire
allowlists, C3C4A schema/replay, C3C4B storage semantics, PRE1 and Strategy v1.0
remain unchanged. The isolated unbound C1 fake-SDK foundation remains solely for
offline tests.

A valid journal after restart remains evidence, with restart quarantine required.
No ownership adoption, resume, retry authority, pending retirement, transport
migration, barrier clearing, portfolio reconstruction, authorization re-arm or
recovery execution is added. Further prerequisites include durable callbacks,
production exposure/accounting and position runtime, partial/remainder management,
protective exits and cancel/replace lifecycle, strategy management/recovery,
session retirement/migration, restart authority and reviewed one-shot recovery/audit.

Focused qualification uses real temporary SQLite stores and fake SDKs, exercises
storage failure/unknown commits, exact ownership/thread/proof rejection, fresh
post-DB gates, deferred callback races, permanent IDs, outcome publication and exact
BaseException propagation. Existing coordinator fixtures use authentic Phase 5/6
reference evidence and explicit temporary journals; their qualified entry minute
is 10:15 New York. Former memory-only/pre-marker expectations are replaced with
required post-marker uncertainty. No coverage is skipped. Actual Windows runtime
retest remains mandatory; Linux validation does not satisfy that gate.
