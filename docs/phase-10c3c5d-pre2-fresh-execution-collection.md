# Phase C3C5D-PRE2 — authenticated same-session execution collection provenance

This phase adds private **memory-only execution query evidence**. It adds no
status/completed-order collection, accounting incorporation, management
currentness, current lifecycle/remainder, order/position ownership, protection,
cancel/replace/exit, recovery application, barrier clearing, restart adoption,
authentication renewal or trading authority. Gateway Read-Only remains required;
AccountMode is UNKNOWN, public snapshot mode is None, LIVE is unsupported and the
first real PAPER order remains blocked. Restart quarantine remains mandatory.

Starting checkpoint: `0b50988dda3fade76a68bd88059d54494fa5db0e`.
The user reported independent review and Windows clearance of C3C5D-PRE1 at this
checkpoint: 180 focused tests, 6,309 full-suite tests with 14 skips, compilation
passed and a clean tree. This phase requires its own independent review and
actual Windows validation.

## Exact owner and one attempt

`_create_fresh_execution_collection_owner(journal_owner)` accepts only the exact
registered live journal owner on its creator thread. It captures the coordinator,
control, read/write transports and client views, authenticated issuer receipt and
account association, header, store identity, registered session, generations,
client IDs, original public read snapshot, clock and authentication deadline.
Existing journal ownership checks and issuer authentication are reused. Equal
reconstructed objects cannot replace issuer/lifetime association.

One execution collector is registered per read-transport lifetime. It permits one
request attempt only. Failure, timeout, malformed capture, interruption and
successful completion do not create a replacement or retry path. Existing safety
barriers must remain set; an inhibited journal can supply evidence under its
original valid association. Poisoned, quarantined, closed or persistence-failed
ownership is rejected. The collector performs no SQLite operation.

An unsealed open-order capture prevents execution collection. PRE1 remains an
independent one-shot collector and is not upgraded into a combined evaluator.
Its receipt does not establish execution coverage or currentness.

## Request and filter

The only request is the captured guarded read client's
`reqExecutions(sdk_request_id, filter)`. The positive exact-integer request ID is
allocated beyond the transport request watermark, startup account-summary and
execution IDs, and prior fresh-execution IDs. The shared SDK-request watermark is
advanced so later startup allocation cannot collide. Startup execution identity
is not reassigned. Bool, exhausted and malformed counters fail before issuance.

A separate monotonic local collection ID and read-local observation sequence are
not SDK request IDs, journal sequences or PRE9 write callback sequences.

The filter is constructed internally through the original existing SDK filter
factory. The already-qualified default filter is reused; no unqualified account
filter property or external filter object is introduced. Decoded callback account
membership enforces the authenticated account scope privately. Other clients on
that account can be returned without being adopted. The receipt carries only the
closed policy `DECLARED_EXECUTION_HISTORY_DEFAULT_FILTER_ACCOUNT_FILTERED`.
Filter client defaults never establish placing-client identity. The SDK filter
is not retained in capture, owner or receipt after invocation.

Creation/allocation, issuance attempt, SDK normal return, matching observations,
matching end receipt and final validation remain separate facts. Callbacks and
`execDetailsEnd` may occur synchronously before SDK return. A later request
exception still prevents success. Zero observations can succeed only with exact
matching end completion and valid final boundaries. Silence is never completion.

## Qualified bounded callback evidence

The existing `_ReconciliationExecutionEvidence`, `_ReconciliationPresence`, raw
protobuf copy and decoded sanitizer are reused unchanged. Evidence retains
encoding, supplied identity/presence, execId, order/client/perm/reference identity,
contract, side, shares, price, optional cumulative quantity/time, read generation
and local sequence. Missing protobuf identity remains missing; supplied zero and
empty references retain their existing presence semantics. Execution time is
bounded source text, not proof of economic time within the collection interval.

The decoded exact request ID is authoritative for query correlation. No guessed
top-level protobuf request property is accessed. Wrong decoded IDs are ignored
before payload getters unless an unmatched raw precursor makes pairing ambiguous;
that ambiguity fails the collection. A wrong end ID cannot complete it.

One raw envelope is permitted. Its immediate decoded follower must match source,
generation, thread and supplied values under the existing qualified contract.
No raw object survives the precursor callback. Orphans, mismatches, intervening
callbacks, malformed source or a protobuf-to-legacy downgrade fail closed.

Each accepted callback occurrence is retained independently, including identical
execId repeats. Compatible numeric representation differences remain received
observations; no economic total is computed. Incompatible facts for the same
execId fail the collection, preserving the earlier received prefix without a
latest-wins replacement. Missing/supplied differences likewise remain conservative.

Shares must be positive. Price and optional cumulative quantity must be finite
and nonnegative. Existing type checks, 64 coefficient digits and exponents in
[-64, 64] are retained. SDK floats use the existing exact string-based Decimal
conversion; already-sanitized Decimals are copied by tuple without rounding.
Zero price is not missing price. No broker precision beyond the source contract
is claimed.

The limit is 1,024 **occurrences**, not distinct execution IDs. The first overflow
advances the local observation watermark and permanently fails capture; retained
records cannot be published as complete success. Duplicate/conflict checking is
bounded by the same occurrence limit. No callback or persistence cursor advances.

## Receipt and validation

`_FreshExecutionReceipt` is frozen, slotted and opaque. It retains local and SDK
request IDs, declared scope, opaque account namespace, journal/session IDs,
read/write generations and client IDs, issue/end markers, initial/final sequence
watermarks, SDK result, completion flag, outcome, original deadline,
boundary-change flag, independent sanitized observations and fixed limitations.
There is no account-number field, credential, raw SDK object, connection or filter.

The strongest success is `COMPLETED_DECLARED_EXECUTION_COLLECTION_INTERVAL`:
a matching execution query ended under the exact authenticated association with
no detected local capture failure. Execution/filter history remains limited.
Absence does not prove no execution occurred; completion does not prove no late
fill remains possible, universal history, atomic broker state, currentness or
ownership. No fee completeness is claimed.

Association/authentication is checked before issue, after completion, during final
publication and before consumption. The original UTC deadline and captured
monotonic deadline cannot be extended by the collection or clock rollback.
Read/write generations remain separate domains. Connection, clients, session,
snapshot/configuration, authentication, barriers, active attempt, status fences,
capture loss, write arrival/callback watermarks, persistence cursor and journal
tail must remain valid. Capture identity, counters, occurrence coverage and end
markers are independently checked before publication. Receipt consumption requires
the exact issued object once and another unchanged association/boundary check.

Outcomes are COMPLETED, FAILED, TIMED_OUT, INVALIDATED, OVERFLOW or INTERRUPTED.
Some malformed/interrupted publication failures deliberately leave no receipt.
No partial success is exposed. KeyboardInterrupt, SystemExit and GeneratorExit
secure local unusable state and propagate the exact original object on the raising
thread. Diagnostic failure cannot repair capture or replace the original interruption.

## Threads and protected boundaries

The exact creator thread constructs, requests, waits, validates and consumes.
Boundary capture follows coordinator -> control -> read lifecycle -> read condition
-> write dispatch -> arrival. All incompatible locks are released before SDK
request/filter creation and callback waiting. Callbacks use read-local synchronization
for bounded sanitization, staging and notification only: no SQLite, filesystem,
journal append, SDK request, earlier-plane acquisition or persistence wait.

No public Broker/snapshot API, wire opcode/policy, journal V1/V2 event, storage
format, SQLite DDL/version/hash or PRE9 persistence changes. Query evidence is not
converted into write callback occurrences or fed into C3C5A/B. Existing reconciliation
and advisory recovery remain unchanged. Disconnect/restart invalidates live provenance;
replay cannot recreate the receipt and restart quarantine stays mandatory.

## Validation and remaining foundations

Focused tests use anonymous fake SDKs, deterministic callbacks and temporary
fixture journals. Real Gateway opt-in must stay disabled. Independent review and
Windows validation with the cleared installed SDK are required; cloud results
are not Windows qualification.

Remaining work includes source-qualified status intervals and completed-order
provenance, combined evidence with explicit coverage, exact attempt attribution,
contradiction handling, reviewed incorporation of newly discovered executions,
decision-time currentness, lifecycle/remainder assessment, inventory/dispositions,
protective ownership/resizing, cancel/replace/exits, session retirement/continuity,
risk/strategy restoration and reviewed recovery/audit application. No query receipt
alone authorizes the first real PAPER order.

Cloud validation: 229 new focused tests passed. Selected regressions ran 3,637
tests, OK with three optional installed-SDK skips. Full offline discovery ran
6,538 tests, OK with four skips (three optional installed-SDK checks and one
disabled real Gateway integration). All 120 Python files compiled. Scope,
whitespace, secret/account, `.env`, public-export, callback-I/O and protected-file
scans passed. Every other tracked broker/backtest production file is byte-for-byte
unchanged. No dependency installation, real IBKR connection, broker read/write or
Gateway change occurred. Cloud has no installed ibapi; no Windows qualification
is claimed.
