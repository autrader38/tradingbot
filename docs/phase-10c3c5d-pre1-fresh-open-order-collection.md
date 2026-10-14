# Phase 10C3C5D-PRE1 — authenticated same-session open-order collection provenance

This phase adds private **memory-only collection evidence**. It grants no
management currentness, lifecycle, remainder, order/position ownership,
protection, cancel/replace, exit, recovery, barrier-clearing or trading authority.
Gateway Read-Only remains required. AccountMode remains UNKNOWN, public snapshot
mode remains None, LIVE remains unsupported and the first real PAPER order stays
blocked. Restart quarantine remains mandatory.

Starting checkpoint: `0c2ff5191de2c75027478eeee9f08c98b65f4088`.
The user reported PRE9 independent review and Windows validation complete:
399 focused, 253 V2 coverage regression and 6,129 full-suite tests, with 14 skips.

## Exact private ownership

`_create_fresh_open_order_collection_owner(journal_owner)` accepts only the
exact registered live journal owner on its captured creator thread. It retains
the exact coordinator, control, read/write transports, issuer account-binding
receipt, journal header, store identity, registered session and owner thread.
It also secures the original read/write client views, read snapshot, enrollment
association and deadlines. Journal ownership checks and fresh issuer validation
are reused; equal reconstructed models do not restore issuer provenance.

One collector is installed per read-transport lifetime. It has one request
attempt. Success, failure, timeout, invalidation and interruption do not create a
retry/replacement path. This deliberately avoids confusing delayed untagged
callbacks from an ambiguous request with a later request. A separate future
contract would be needed to permit repeated collections safely.

An existing pending/reconciliation barrier is required and remains in force.
An inhibited journal can supply evidence while its original association remains
valid. No barrier is cleared and no authorization is re-armed. Closed, poisoned,
quarantined or persistence-failed journal ownership cannot initiate a collection.
No SQLite operation is needed by this phase, including collection orchestration.

## Collection and request provenance

The only SDK request is `reqAllOpenOrders`, through the captured existing guarded
read client. No connect, public refresh, reconnect, reset or additional account,
position, completed-order or execution collection occurs. The original public
snapshot and account-authentication deadline are preserved.

The read transport issues a monotonic **local** collection ID and logical start
marker before invocation. The ID is not transmitted to IBKR: openOrder and
openOrderEnd have no broker request ID. The receipt explicitly states that
observations can be requested, unsolicited or delayed, and that receipt timing
does not establish economic event timing or an atomic broker snapshot.

Issuance attempted, SDK normal return, observations and end-marker receipt are
separate memory facts. SDK normal return is not completion or broker acceptance.
Zero observations with explicit openOrderEnd can be a successful empty declared
interval. Zero observations without that end marker cannot be empty success.

The strongest successful outcome is
`COMPLETED_DECLARED_OPEN_ORDER_COLLECTION_INTERVAL`: the declared scope completed
under the exact authenticated association with no detected local capture failure.
It is not universal order completeness or current management truth. Absence means
non-observation in this collection only, not filled, canceled, terminal, zero
filled or free of late executions. Buffered callbacks that can be identified as
pre-boundary or stale are not relabeled as requested response evidence. The SDK
does not identify every delayed/unsolicited same-generation observation; this
limitation remains explicit.

## Sanitized bounded observations

The existing `_ReconciliationOrderEvidence` and `_ReconciliationPresence` models
are reused for OPEN_ORDER only. Existing LEGACY/PROTOBUF supplied-field semantics,
identity, requested quantity, contract, side, type, TIF and categorized status are
preserved. Decimal representation is copied exactly without context rounding.
No filled, remaining, average-price or last-price fields are invented.

Raw protobuf precursors use the existing qualified read-side copy contract and
must pair immediately with a same-generation/thread decoded follower. Raw
objects never survive capture. Orphan, mismatch, malformed source, mixed encoding
or an intervening callback fails the collection. Before issuance, obsolete
generation callbacks and post-failure callbacks are ignored before source getters.

An independent transport-lifetime observation sequence records each retained
source occurrence. It is neither the PRE7/PRE8 write callback sequence, journal
record sequence nor a broker request ID. Every duplicate is retained separately.
Conflicting repeated order IDs, contracts or statuses remain visible; no
latest-wins or economic booking occurs. Orders from other clients on the declared
account remain unattributed. Other accounts are filtered privately and their
identifiers never enter receipt observations.

The bound is 1,024 records. The first valid overflow occurrence advances the local
watermark and permanently fails the collection; retained records are not presented
as complete success. No acknowledgment, persistence cursor or write callback
watermark is advanced. An observation or duplicate end after openOrderEnd but
before final publication also prevents success.

## Receipt, boundaries and expiry

`_FreshOpenOrderReceipt` is frozen, slotted and opaque. It contains the local ID,
scope, opaque account namespace, journal/session IDs, read/write generations,
read/placing client IDs, issue/end markers, initial/final sequence watermarks,
SDK result, end-marker flag, closed outcome, original authentication deadline,
boundary-change flag, independently copied observations and fixed limitations.
No account-number field, authentication revision, credentials, SDK objects,
frames, connection objects or raw broker message payloads are published.
Whitelisted bounded identity/reference fields preserve the existing read contract.

Before issuance, after completion and before publication/consumption, the owner
checks exact association and authentication. Generation, client, snapshot,
configuration, account association, deadline, connection/failure state, journal
association, capture integrity and retained barriers must remain valid. Write
arrival/callback watermarks, persistence cursor and journal-tail changes prevent
success or later consumption. This is conservative evidence validation, not a
management-currentness permit. It does not establish commission finality.
Boundary comparisons preserve primitive types and datetime representation; bool
or float equality cannot stand in for an exact integer generation, client ID or
watermark. Callback counter validation likewise occurs before incrementing.

The original UTC account deadline is never extended. A captured monotonic deadline
also bounds the collector lifetime; elapsed-clock expiry cannot be renewed by
wall-clock rollback. Expiry invalidates use of the receipt. Consumption requires
the exact issued receipt object once, unchanged primitives and a still-valid
association. Caller-held receipts thereafter are immutable historical evidence,
not renewable freshness or authority.

Receipt construction and validation precede the final publication pointer.
Failure/interruption leaves no partial successful receipt. Outcomes are COMPLETED,
FAILED, TIMED_OUT, INVALIDATED, OVERFLOW or INTERRUPTED. No reporting failure or
successful capture can repair a failed lifetime. KeyboardInterrupt, SystemExit
and GeneratorExit secure local failure first and propagate the original object.

## Threads, locks and protected boundaries

Only the exact journal-owner thread orchestrates and consumes. Brief boundary
capture follows coordinator → control → read lifecycle → read condition → write
dispatch → arrival. All those locks are released before SDK request/wait.
Callbacks use only read-local synchronization, bounded sanitized memory and
notification. They perform no SQLite/filesystem work, SDK request, network/DNS
activity, coordinator/control acquisition or owner-thread persistence wait.

No journal append, V1/V2 event or replay change, SQLite format/DDL/hash change,
PRE9 persistence change, public API/export, wire-policy change or SDK write is
introduced. The older newer-generation reconciliation handshake/evaluator and
advisory recovery planner are unchanged. C3C5A/B do not consume these receipts.
Disconnect and restart invalidate the live provenance; replay cannot recreate it.

## Validation and remaining work

Focused offline tests use anonymous fake SDKs and temporary fixture journals.
They cover association substitution, expiry, callbacks before/after SDK return,
empty/missing completion, duplicates/conflicts, bounded overflow, source presence,
foreign orders, thread/lock boundaries, interruption and absence of authority.
Actual installed-Windows SDK validation and independent review are required before
the next phase. Cloud results do not establish Windows or Gateway behavior.

Cloud offline validation: 180 new focused tests passed; 4,096 selected existing
regressions passed across the targeted runs and final full discovery. The full
suite ran 6,309 tests, OK with four skips (three optional installed-SDK tests and
one disabled real Gateway integration). All 118 Python files compiled. Whitespace,
privacy/account/secret, `.env`, public-export, callback-I/O and protected-file
checks passed. One targeted command used an incorrect module name; its executable
tests passed, and the correctly named capture-integrity suite was run successfully.
No dependency installation, real connection, broker read/write or Gateway change
occurred. The cloud has no installed ibapi; Windows SDK validation is outstanding.

Later phases still need source-qualified fresh status quantities, completed and
execution collections with coverage limits, exact journal-attempt attribution,
bounded decision currentness, lifecycle/remainder resolution, inventory and
dispositions, protection/resizing/cancel/replace/exits, authoritative retirement
and continuity, risk/strategy restoration and reviewed recovery/audit application.
This receipt alone cannot authorize the first real PAPER order.
