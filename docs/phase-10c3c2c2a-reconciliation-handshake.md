# Phase 10C3C2C2A — private fresh reconciliation collection handshake

This is an offline provenance layer. It does not evaluate order lifecycle, reconcile
broker/control state, clear safety barriers, re-arm authorization, update a portfolio,
book fills, or enable another order. No real IBKR connection or broker write occurred.
Gateway Read-Only remains unchanged. AccountMode is UNKNOWN; public account mode is None.
LIVE remains unsupported. No dependency, public lifecycle, CLI or strategy route is added.

The guarantee of a coordinator-issued receipt/bundle is only:

> This exact private request, issued after a retained unresolved pending-order state was
> secured, was bound to this later read collection, and this immutable terminal read
> evidence/public snapshot came from that collection.

A direct private read request proves collection provenance alone. The coordinator
attempt additionally proves that retained pending identity and an existing safety
barrier preceded issuance. Neither form proves acceptance, a full fill, cancellation,
rejection, zero fills, PAPER mode, portfolio synchronization, safe barrier clearing or
permission for another trade. The actual evaluator is a later separately reviewed phase.

## Exact one-shot read authority

`_ReconciliationReadRequest` is frozen, slotted and opaque. It contains a transport-owned
request ID, baseline read generation/start marker, issuance marker and issued monotonic
time. `_prepare_reconciliation_read_request()` uses only the read condition and samples
the same module `monotonic()` used by C3C2C1. It sends nothing and does not connect, refresh
or clear state. The transport retains the exact issued object; equal reconstructed
dataclasses and requests from another transport are not authority.
Issuance rejects clock regression relative to the existing collection timing.

There is one outstanding request slot, covering unbound, bound/in-progress and terminal
but unconsumed requests. Request IDs increase for the transport lifetime and never reset.
There is no growing registry or public issuance/reset/consumption API. An incomplete
consumption leaves the request active. A wrong or consumed request is rejected.

Issuance cannot attach existing cached data. An ordinary connected `connect()` return
leaves the request unbound. Disconnect alone also leaves it unbound, including multiple
disconnects in the normal broker refresh path.

## First later collection and freshness

The first later `_reconciliation_begin()` with a read generation beyond the baseline
and start marker beyond the issuance marker binds the request once. Read generation
may advance by more than one. Same-generation reset cannot satisfy the request.
Read/write generation numbers are never compared.

Logical marker advancement proves ordered initiation; collection-start monotonic time
must not precede issuance. Equal monotonic samples are allowed because clock resolution
can hide a real ordered transition. A regressing clock fails the bound collection.
No wall-clock comparison or injected authorization clock establishes this proof.

Once bound, subsequent resets/refreshes cannot rebind the request. Losing the bound
generation before completion creates failed evidence rather than adopting a later
successful collection. Captures retain C3C2C1 presence, bounds and completeness rules.

## Terminal receipt and exact publication pair

`_ReconciliationReadReceipt` contains request baseline/issuance provenance, bound read
generation/start marker, collection timing/completion marker, outcome, the exact frozen
C3C2C1 private snapshot and the corresponding public `ReadOnlySnapshot` or None.

For COLLECTED authority, receipt construction occurs at the existing public/private
publication boundary under the read condition. The public snapshot must be the exact
object published by that bound collection. A standalone private finalization without
public publication cannot manufacture COLLECTED receipt authority. Public models receive
no new generation field. Broker `_observation` and timestamp similarity are not pairing
proofs.
A collection-local publication marker is cleared on reset and set only by the normal
publisher. Cached public objects and equal reconstructed copies cannot satisfy it.

Failed, timed-out, malformed, interrupted, overflow and generation-lost captures retain
a terminal failed receipt when construction is possible. Its public snapshot is None;
cached older public data is never substituted. Constructor/reporting failure secures
capture failure before propagation and may leave receipt access incomplete rather than
claiming successful publication.

`_consume_reconciliation_read_receipt(request)` requires exact issued-object identity
and returns a terminal successful or failed receipt once. It then retires only request,
binding and receipt bookkeeping. It performs no connection, SDK call or barrier change.
The retained terminal receipt survives later ordinary read collections before consumption;
caller-held consumed receipts remain immutable history afterward.

## Coordinator attempt and frozen local views

`_BrokerReconciliationAttempt` contains a coordinator-owned lifetime attempt ID, initial
`_BrokerReconciliationLocalState` and exact read request. The local view preserves:

- all 14 immutable `_PendingOrder` identity/provenance fields;
- pending confirmation, callback cursor and observation state;
- broker observation and strong openOrder provenance;
- broker-state/general coordinator reconciliation, last status and filled/remaining;
- economic observation, processed execution identities and total;
- control and write reconciliation flags, current write generation/closed state;
- write callback watermark, unread batch upper bound/events and authorization status.

Processed execution identities are immutable primitive tuples, excluding the model-class
tag used internally by C3C2B. Unread events are existing frozen sanitized write evidence.
The owner-bound `_evidence_since(owner, cursor)` supplies a bounded page of up to 128
events; its through-sequence may exceed the page end for coalesced duplicate spans.
The view records both facts honestly. This phase does not acknowledge events, advance
the cursor or claim that the entire unread ledger has been processed.

`_begin_broker_reconciliation_attempt()` requires the exact bound coordinator, exact
retained pending record and an existing broker-state, coordinator, control or write
reconciliation barrier. Pending confirmation alone is not a general refresh permission.
One coordinator attempt is active at a time. It freezes local state and issues the
read request while that state is secured. No callback synchronization, poll, refresh,
SDK request or network wait occurs. Failed attempt construction rolls back only its
new unbound request slot, leaving lifetime counters and all safety state intact.

No retained pending identity yields `PAPER_RECONCILIATION_IDENTITY_UNAVAILABLE` without
issuing a request. Immediate OUTCOME_UNKNOWN, post-invocation interruption and process
restart/lost memory must not synthesize identity from allocator state, callback history,
orderRef guesses, positions or economics. Persistence/restart recovery is deferred.

## Separate collection and final bundle

The intended private flow is:

1. Begin the coordinator attempt, then release the outer locks.
2. Separately perform a normal read-only refresh/new collection using offline doubles.
3. Let the request bind automatically and reach terminal capture.
4. Consume the coordinator attempt afterward.

No public orchestration method is added. Existing broker refresh/disconnect may invalidate
authorization; this is expected and does not invalidate handshake provenance. No ARMED
requirement or re-arming is introduced at consumption.

`_consume_broker_reconciliation_attempt(attempt)` checks exact active identity and requires
the original pending object to remain retained. Disappearance/replacement is a controlled
local-state inconsistency; it does not consume the receipt. It captures a final local view
and constructs `_BrokerReconciliationBundle(attempt, read_receipt, final_local)` before
retiring either authority. Constructor interruption leaves a valid terminal receipt
available for a subsequent consumption. Incomplete capture retains the active attempt;
terminal failed capture can return a bundle once.

Callbacks during collection appear in the final watermark/unread page. They are not
processed or rejected merely for arriving. A later evaluator must interpret changes,
page coverage and local barriers. No FILLED/OPEN/TERMINAL classification or success boolean
is produced here.

## Lock order, privacy and safety

Begin/consume use the reviewed order: coordinator → broker → read lifecycle → read
condition → paper dispatch → paper arrival. Existing locks are re-entered only when
already held. Read issuance/receipt access uses read-local synchronization. Callback
threads never acquire earlier coordinator/control locks. SDK collection waits occur
outside these coordinator methods, following the existing transport lifecycle discipline.

All new models have opaque representations. They contain validated primitive/frozen
evidence only, with no account number, raw SDK/protobuf objects, socket/client, capability,
enrollment secret or arbitrary broker error text. Public models and package exports are
unchanged. Python-private boundaries remain application isolation, not a hostile-code
sandbox.

Request/attempt retirement is bookkeeping only. Write reconciliation, coordinator
reconciliation, broker-state barriers, control reconciliation, authorization invalidation,
pending identity and duplicate history remain untouched. Receipt/bundle production cannot
authorize a second entry or mutate holdings, cash, equity, stops or protective orders.

## Validation

Offline tests exercise identity/replay, cached data, logical/equal-clock ordering,
generation jumps, exact publication pairing, failure/interruption, concurrent ownership,
callback/refresh races, local-state capture, privacy and zero recovery effects. All prior
C3C2C1/C3C2B/C3C2A/C1/enrollment/authorization and frozen strategy checks remain required.
Real integration stays opt-in and is skipped during development.

Validation: 118 new handshake tests pass. The complete offline suite runs 2,509 tests:
2,508 pass and one existing real integration test is skipped. All 218 C3C2C1, 231 C3C2B,
202 C3C2A, 239 C1 tests and all 64 canonical defaults pass unchanged.
