# C3C5D-PRE4-PRE2 — sole qualified runtime decision-boundary producer

This phase activates fresh V3 runtime journals and one explicit private producer.
It preserves local decision-time callback-stream provenance only. It adds no
status-interval evaluator, combined collection evaluator, broker currentness,
ownership, lifecycle/remainder, accounting incorporation or trading authority.
The first real PAPER order remains blocked. Gateway Read-Only remains required,
restart quarantine remains mandatory, AccountMode remains UNKNOWN, public snapshot
mode remains None and LIVE remains unsupported.

Starting checkpoint: `d27e2abc4fb63e1574dcfe966b804772b89378a5`.
The user reported PRE4-PRE1 independently reviewed and actually Windows validated
on Python 3.12.10: 195 focused tests, 7,017 full-suite tests with 14 skips, 123 tracked
Python files compiled, clean diff checks/tree/index and synchronized main/origin/main.
PRE4-PRE2 requires its own independent review and actual Windows validation before
C3C5D-PRE4. Cloud validation is not Windows qualification.

## Missing proof and fresh activation

Original PRE4 could not place a decision datetime inside the PRE9 callback stream.
Callback sequence coverage, journal chronology and validated timestamps alone do
not prove datetime-to-frontier correspondence. PRE4-PRE1 added the initially inactive
four-field V3 boundary declaration and strict prefix/replay validation. This phase
implements the sole runtime path that may certify that declaration.

`_create_paper_dispatch_journal_owner` now creates only fresh schema-3 journals with
`paper-execution-journal-v3`. The owner's exact version/format checks require V3.
The existing fresh-only factory still rejects existing paths and has no adoption,
upgrade, rewrite or migration path. Default generic journal headers remain V1.
Explicit V1/V2 stores remain readable with their exact old contracts. V1/V2 event
unions, bytes, replay meanings and checksums are unchanged. PRE4-PRE1 V3 schema,
codec and replay rules are unchanged. SQLite DDL, application ID, user_version=1,
storage format/hash domain/framing, WAL/FULL and storage methods are unchanged.

## One owner, one entry point

`_PaperDispatchJournalOwner._certify_decision_boundary()` takes no parameters.
Callers cannot supply session, generation, datetime or callback frontier. It is
private, creator-thread-only, unavailable through public Broker/client views and
not invoked automatically by strategy, risk, dispatch, callbacks, timers or startup.
The fixture-constructible event dataclass remains a value contract, not runtime
certification. The generic owner append path rejects a boundary unless it is the
exact pending event of the active producer.

The existing owner retains exact coordinator/control, read/write transports,
issuer receipt, journal/header/store/session and creator thread. Producer-specific
immutable association captures the original account-association object, exact
read/write client views, configuration objects/values, authenticated snapshot and
read clock. Equal reconstructed objects cannot replace that lifetime provenance.
Original authentication/deadline, generations, client IDs, connection validity and
association are revalidated before capture and after append; no renewal, reconnect,
session migration or restart adoption occurs.

## Atomic capture and durable ordering

1. Hold coordinator then control locks; reject an active attempt, active drain or
   reentrant producer. Validate original authentication outside capture locks.
2. Perform exactly one existing bounded callback-drain attempt. Require CURRENT.
   PENDING, DEFERRED or FAILED cannot certify a boundary.
3. Guard the producer operation against reentrant drains/occurrence append. Recheck
   authentication and enter read lifecycle -> read condition -> write dispatch ->
   write arrival. Revalidate exact association and safe capture state.
4. Require the unchanged `_require_persistence_current` gate: no persistence failure,
   staged or covered-but-unpublished callback, usable arrival and persistence cursor
   equal to transport callback sequence. Independently validate truthful retained
   spans, no capture loss, no invalid callback and no in-flight status fence.
5. Under these locks, obtain `_decision_utc_now()` internally, validate the unchanged
   V3 exact fixed-UTC/fold-zero timestamp, and capture frontier N. Check the original
   authentication deadline and recheck state/frontier/arrival/history before release.
   No callback sequence may change across this serialized time/frontier capture.
6. Release all read/write/arrival capture locks. Recheck original authentication and
   session continuity. Load/preflight the exact captured durable prefix with the
   unchanged V3 replay rules before delegating to the existing owner append writer.
   Contiguous coverage, high-water and known progress suffix must already qualify N.
7. Append the boundary before any post-capture callback occurrence is persisted.
   The existing load/history, append/commit and subsequent load verification apply.
   No drain occurs between capture and this append. No SQLite or authentication I/O
   occurs under the arrival lock.
8. Revalidate authentication outside capture locks, then re-enter the same lock
   order. Require unchanged exact association/safety, sequence >= N, exact committed
   event/record and expected durable history. Only then publish the private receipt.

New callbacks may arrive in memory after lock release, including before SQLite
append completes. They do not change the historical `(D,N)` pair. A reentrant drain
returns DEFERRED during publication; callback N+1 is durably ordered after the
boundary. The producer does not require the callback sequence to remain N, but it
must not regress. Evidence loss, invalid capture, unusable arrival, an active fence
or broken session continuity prevents successful publication. A previously
committed boundary can remain valid historical evidence when later publication fails.

## Clock, failures and receipts

The private clock seam obtains `datetime.now(timezone.utc)` in production. There is
no public injection or historical assertion API. V3 requires exact datetime,
fixed UTC with the UTC name, fold zero and bounded exact integers. Each successfully
durable boundary's time is remembered. Equal or regressing times, malformed values
and ordinary clock exceptions are rejected before append as
`PAPER_DECISION_BOUNDARY_NOT_READY`. No microsecond is added, no value is edited and
a healthy store is not quarantined because the wall clock moved backward.

A callback racing drain-to-capture likewise yields no boundary/no success, with
no earlier timestamp reuse. A future attempt captures a new decision instant.
Other association/capture failures inhibit/reconcile under existing policy.
During/after append, unknown outcomes retain existing poisoning/inhibition rules;
there is no blind append retry. KeyboardInterrupt, SystemExit (including code) and
GeneratorExit propagate the original object after local safety is secured.
Cleanup errors cannot replace an append/producer interruption. In-flight state is
cleared, unpublished receipts are invalidated and no certified pair is reused.

`_DecisionBoundaryReceipt` is private, frozen, slotted and opaque. Its two fields are
the exact boundary event and exact `_CommittedRecord`. They expose internally only
the four boundary facts, committed journal sequence/envelope and digest; no account
number, credential, SDK object or authority is introduced. The owner retains only
its latest receipt, original event/committed references and independent primitive
snapshots. `_require_decision_receipt` requires exact owner/thread/object identity,
original references and untampered values. Equal reconstructed receipts or replaced
references are rejected. Prior records remain durable historical evidence even when
a later receipt replaces the bounded latest-publication slot. Receipt validation
does not consume collection receipts, mutate economic state or grant permission.

## Local-stream limitation and protected phases

Success proves only: at the internally captured decision time, while local arrival
was serialized for the authenticated source session/generation, the received
callback stream had frontier N and every callback through N was already durably
qualified. It cannot prove broker/exchange event time, absence of events in flight,
absence of delayed network/SDK callbacks, execution absence, no late fill, finality,
later broker currentness, order/attempt/position ownership, inventory or protection.

Callbacks remain bounded memory-only producers: no SQLite, filesystem, journal I/O,
network/request or persistence wait is added. Only the journal-owner thread drains,
captures and appends. PRE9 occurrence/progress semantics, PRE1/PRE2/PRE3 independent
receipts, accounting, historical lifecycle, reconciliation reasons, advisory recovery,
public APIs, wire policy, enrollment/authentication, strategy/backtest and risk rules
are unchanged. No barrier clearing, restoration, PAPER/LIVE authority or management
operation is added. Replay continues to require restart quarantine.

## Validation and remaining work

Focused tests use deterministic anonymous SDKs, blocked real network and temporary
SQLite only. They exercise fresh activation, zero/nonzero/mixed coverage, source
presence/Decimal preservation, serialized arrival capture, post-capture arrival
ordering, clock/association failures, exact receipt references and interruption/
cleanup seams. Old V1/V2 golden bytes/digests and database compatibility remain
covered by unchanged contract/storage regressions. Actual Windows validation on
Python 3.12.10 with ibapi 10.50.2 and protobuf 5.29.5 remains required, with real
Gateway opt-in disabled. No real broker connection is needed for those tests.

Cloud validation on Python 3.12.14: 179 new focused tests passed; selected
regressions ran 4,052 tests, OK with three optional installed-SDK skips, including
all 195 PRE4-PRE1 tests. Full offline discovery ran 7,196 tests, OK with four skips
(three installed-SDK tests and disabled real Gateway integration). All 124 Python
files compiled with caches outside the checkout. Diff/whitespace, secret/account,
`.env`/credential, public export/API, broker/network-operation, compatibility and
authority/scope scans passed. The eight intended files are the only changes;
all other 54 tracked broker/backtest files remain byte-for-byte unchanged. Three
prior test modules receive only narrow fresh-factory/inactivity expectation updates.
No dependency installation, real IBKR connection, broker operation or Gateway
setting change occurred. These results do not establish Windows qualification.

Next, after independent review and Windows clearance: **C3C5D-PRE4 — pure qualified
decision-time status interval evaluator** using qualified V3 boundaries. Later:
combined PRE1/PRE2/PRE3/PRE4 evidence, exact coverage reasoning/attempt attribution,
new execution incorporation, lifecycle/remainder, inventory/disposition, protective
ownership/resizing, cancel/replace/exits, session continuity/retirement and recovery/
risk/strategy restoration. First PAPER authorization remains separately gated by
reviewed prerequisites. This phase implements none of those consumers.
