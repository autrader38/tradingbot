# C3C5D-PRE5-PRE1 — prepared same-session collection evidence

This phase prepares exact PRE1/PRE2/PRE3 receipts before a later PRE4 decision
boundary. It adds private memory-only provenance, with no cross-source observation
correlation, decision-time binding, currentness, finality, lifecycle/remainder,
ownership, execution incorporation, accounting, inventory, protection, cancel/
replace/exit, recovery, barrier clearing, risk/strategy restoration or trading
authority. The first real PAPER order remains blocked. Gateway Read-Only remains
required, restart quarantine remains mandatory, AccountMode remains UNKNOWN,
public snapshot mode remains None and LIVE remains unsupported.

Starting checkpoint: `1817b6907d96917272134c53d526ac92bb1a67e4`.
The user reported PRE4 independently GitHub-reviewed and actually Windows-validated
on Python 3.12.10: 164 focused tests, 7,360 full-suite tests with 14 skips, 126 tracked
Python files compiled, clean tree/index and synchronized main/origin/main.
PRE5-PRE1 requires its own independent review and actual Windows validation before
PRE5. Cloud tests are not Windows qualification.

## Why preparation precedes the decision boundary

Each collection owner permits one exact receipt consumption while the original
authenticated live association and collection boundaries remain valid. PRE2/PRE3
also retain exact journal-history tuple identity. A decision-boundary append
changes that history and invalidates unconsumed receipts. Reconstructed equal
receipts cannot repair this ordering. Preparation consumes all three while their
original contracts still hold, then retains the exact issued receipt references
as already-consumed historical evidence.

`paper_fresh_collection_bundle._create_fresh_collection_preparation_owner(journal)`
accepts only the exact registered creator-thread journal owner. It retrieves the
three original registered collection owners from that owner's exact read transport;
callers cannot supply owners, receipts, accounts, client objects or request functions.
Collections must already have completed. No collection request is initiated here.

The private `_FreshCollectionPreparationOwner` is lazily registered in
`read._fresh_collection_preparation_owner`, once for that read-transport lifetime.
There is no replacement after a registered failure, success or interruption.
No existing transport/callback module is changed. Its association captures exact
journal/coordinator/control/read/write/issuer/header/store/session/thread objects,
registered source owners and original bound consumption methods, clients,
configuration, snapshot, clock, account association and journal-history prefix.
Equal object substitutions and changed generations cannot substitute for that
lifetime. There is no renewal, reconnect or restart adoption.

## Prevalidation and deterministic consumption

All three source receipts undergo their existing private `_receipt_values`
validation before any consumption. Those validators reconstruct only independent
checks; the original receipt references are retained and never replaced. Exact
type, expected scope, expected limitation tuple, COMPLETED outcome, normal SDK
return, explicit completion, valid completion marker, unchanged-boundary flag and
all immutable observation invariants are required.

Type-preserving canonical values must agree on opaque account binding, journal ID,
session ID, read/write generations, read/placing client IDs and original fixed
authentication deadline. These are shared provenance dimensions only. Local IDs,
markers, SDK execution request ID and observation watermarks are independent source
domains; they are never compared to infer source timing or one global sequence.

Safe non-mutating live prevalidation additionally checks all original receipt/value
references, unconsumed flags, authenticated association, source capture integrity,
sealed completion and each original unchanged collection boundary. It delegates
authentication and capability checks to the existing owners, preserving their
failure/invalidation behavior. Prevalidation is repeated at preparation, even if
factory validation already succeeded.

`owner._prepare()` has one attempt, without receipt parameters. It invokes the
captured original `_consume_receipt()` operations in fixed order:

1. PRE1 open orders.
2. PRE2 executions.
3. PRE3 completed orders.

Each must return its exact original receipt. After all three succeed, authentication,
source consumption flags, capture/boundary state and static values are checked
again. Final construction and validation precede publication under the established
capture locks. No consumption flag is edited directly by this phase.

## Token and exact issuer validation

`_PreparedFreshCollectionEvidence` is private, frozen, slotted and opaque. Its fields
are the exact `open_order_receipt`, `execution_receipt`, `completed_order_receipt`,
opaque `account_binding`, `journal_id`, `session_id`, `read_generation`,
`write_generation`, `read_client_id`, `placing_client_id`, original
`authentication_deadline` and fixed `limitations`. Each original observation tuple
and source limitation tuple remains independent. No flattening, economic aggregation,
order/perm/execution matching or cross-source contradiction policy is added.

`owner._require_prepared(token)` checks exact issuer/thread/token/reference identity,
independent canonical value snapshots, original receipt references, consumed source
owners and unchanged registered lifetime. Equal reconstructed, pickled/recreated,
foreign-issuer or tampered tokens cannot restore provenance. Constructor syntax and
structural equality alone are insufficient. Validation is repeatable evidence
checking, not another source-receipt consumption or later PRE5 token-use contract.

After publication, legitimate journal appends may replace the history tuple while
retaining its exact prepared prefix entries. Token validation permits that extension;
it neither reruns the now-stale source consumption boundaries nor calls PRE4.
Rewritten/replaced prefix entries, changed association/configuration/client/session/
generation, disconnect or closed original lifetime fail validation. The token's
deadline remains original historical provenance: validation never renews it or
certifies that a future decision occurred before it. PRE5 must separately prove the
decision-time/deadline and same-prepared-lifetime relationship.

The token has no journal event, codec, database format or replay representation.
Restart loses its issuer lifetime. Serialization/reconstruction does not produce
an accepted token, and restart quarantine is never cleared.

## Terminal failures and threading

A registered preparation owner becomes permanently failed on an unsuccessful
attempt. Static failure publishes nothing. Partial consumption is conservative
evidence destruction: earlier consumed flags remain set, later receipts may remain
unconsumed, no token is published, and there is no rollback, replacement or retry
from the middle. A failure after all consumes likewise produces no token. Calling
prepare again cannot reconsume and invalidates preparation publication. No safety
barrier or authority is cleared on any outcome.

Ordinary exceptions become the fixed sanitized
`FRESH_COLLECTION_PREPARATION_UNAVAILABLE` error. KeyboardInterrupt, SystemExit
including its code and GeneratorExit secure local terminal state and preserve the
original exception object. Cleanup uses direct local state assignments, without a
fallible notification hook that could replace the interruption. Existing source
owner interruption/security semantics remain in force.

Only the exact journal creator thread constructs, prevalidates, consumes and
publishes. Lock direction remains coordinator -> control -> read lifecycle -> read
condition -> write dispatch -> arrival. Capability inspection occurs outside these
locks. Existing read-local authentication/connection-state checks retain their
reviewed locking; no write/arrival lock is added around them. Final capture locks
contain bounded memory validation only. No callback is added or modified, and
no callback performs journal/SQLite/filesystem work or waits on this owner.

Preparation adds no SDK data request, order operation, SQLite I/O, journal append,
persistence/callback cursor advancement, PRE4 producer/evaluator call,
accounting/lifecycle mutation or authority restoration. Existing authentication
may read its reviewed local enrollment store under its original read-local locks;
this is preserved rather than bypassed, and never occurs under write/arrival locks.
The new module adds no filesystem access. PRE1/PRE2/PRE3/PRE4,
PRE4-PRE1/PRE2, PRE9, V1/V2/V3, SQLite schema/version/hash, public APIs, wire policy,
reconciliation/recovery, strategy/backtest and risk behavior are unchanged.

## Strongest claim and remaining workflow

Success means only: three independently completed collection receipts sharing the
declared authenticated same-session provenance were consumed exactly once by
their original live owners and retained in an exact-issuer memory-only token.
They are separate collection intervals, not an atomic broker snapshot. There is
no proof that nothing occurred between intervals or that no delayed callback or
late fill exists. Absence is non-observation, never universal order/execution
absence, finality or ownership. PRE2 execution history/default-filter limitations
and PRE3 completed-order history/scope/non-execution limitations remain explicit.

Intended later ordering is:

1. Existing PRE1/PRE2/PRE3 collections complete independently.
2. PRE5-PRE1 prevalidates and consumes their exact live receipts.
3. PRE5-PRE1 publishes its exact preparation token.
4. A separately invoked existing PRE4-PRE2 producer appends a qualified boundary.
5. Existing PRE4 evaluates durable qualified status through that boundary.
6. After this phase's independent review and Windows qualification, **C3C5D-PRE5**
   may combine the exact preparation token with exact durable PRE4 evidence.

This phase implements preparation only; it does not initiate step 1 or automate
steps 4–6. PRE5 must still preserve non-authority and cannot resolve cross-source
contradictions, attribute attempts or derive lifecycle/remainder. Later reviewed
coverage reasoning, attribution, execution incorporation, lifecycle/remainder,
inventory/disposition, protection/resizing, cancel/replace/exits, session continuity/
retirement and recovery/risk/strategy restoration remain required before separately
authorizing any first PAPER order.

## Validation

Focused tests use anonymous SDKs and deterministic callbacks with real network
blocked. They exercise exact live consumption, static/shared mismatches before the
first consume, original-owner failures, terminal partial consumption, BaseException
identity, bounded source occurrences, independent protocol/presence/Decimal facts,
issuer/reference tampering and no broker/durability/decision/authority side effects.
One offline test explicitly invokes the existing producer only **after** preparation
to prove later history extension leaves the historical token valid; producer/evaluator
calls are forbidden during preparation itself. Actual Windows validation remains
required on Python 3.12.10 with ibapi 10.50.2/protobuf 5.29.5 and real Gateway opt-in
disabled. No real broker connection is needed for that validation.

Cloud validation on Python 3.12.14: 207 focused PRE5-PRE1 tests passed. Selected
regressions ran 4,395 tests, OK with three optional installed-SDK skips, including
PRE1 180, PRE2 229, PRE3 284, PRE4 164, PRE4-PRE2 179 and PRE4-PRE1 195.
Full offline discovery ran 7,567 tests, OK with four skips (three installed-SDK
checks and disabled real Gateway integration). All 128 Python files compiled
with caches outside the checkout. Diff/whitespace, secret/account, `.env`, public
API/export, broker-operation, durability, authority/scope and V1/V2/V3 compatibility
scans passed. The five intended files are the only changes; all 56 existing tracked
broker/backtest production files remain byte-for-byte unchanged. No dependency
installation, real IBKR connection, broker request/order operation or Gateway
setting change occurred. Cloud results do not establish Windows qualification.
