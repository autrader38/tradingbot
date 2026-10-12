# Phase 10C3C5C-PRE4 — private decoder-scoped order-status pairing

PRE4 is an isolated memory-only prerequisite. No production transport installs it,
no callback enters a ledger, and no reconciliation or journal consumes its result.
Gateway Read-Only remains required. AccountMode remains UNKNOWN, public snapshot
mode remains None, LIVE remains unsupported, and the first real PAPER order is
blocked.

The starting checkpoint is `00ea2ebbc8d2e713ac19143599318bec4cf1d799`. PRE3 independent
review and actual Windows validation were reported complete: 104 focused tests,
4,628 full-suite tests with 14 skips, and compilation passed.

## Qualified SDK boundary

The user supplied the offline-inspected Python 3.12.10 / ibapi 10.50.2 / protobuf
5.29.5 contract. EClient.run uses ordinary instance lookup to call
`self.decoder.processProtoBuf(text, msgId)`. That synchronous dispatcher resolves
`msgId2handleInfoProtoBuf[msgId]` and invokes its processMeth. The ORDER_STATUS
entry is exactly `Decoder.processOrderStatusMsgProtoBuf`.

That handler parses one message, invokes `orderStatusProtoBuf(raw)`, converts the
normalized values, then invokes `orderStatus(...)`. The SDK supplies no invocation
token. Reentry is possible, and conversion/callback/interruption failure can omit
the normalized follower. Generation/thread/order-ID matching cannot repair an
orphan. Equal PRE3 UNKNOWN projections also cannot distinguish differing raw text.

## Per-instance installation

The private module `ibkr_order_status_pairing.py` provides
`_install_order_status_pairing(decoder)` and `_OrderStatusPairingBoundary`.
Installation lazily imports Decoder/IN; module import alone imports no SDK.
It accepts only the exact qualified Decoder type, checks method signatures and the
ORDER_STATUS HandleInfo identity, and captures the original bound processProtoBuf.
The message identity comes from the SDK's IN.ORDER_STATUS; no numeric opcode is
invented. Qualified integer/SDK enum identity is checked against the actual table.

A bound interceptor and an exact boundary marker are stored only in that Decoder
instance's dictionary. An installation lock serializes competing installations.
Double installation, an existing instance dispatcher override, a subclass, copied
installation claims, and malformed SDK structure fail with the fixed
`INVALID_IBKR_ORDER_STATUS_PAIRING` error. Binding integrity is rechecked on use,
including class dispatcher/table/handler and instance interceptor identity.

Decoder/EClient classes, HandleInfo and class dispatch tables are never modified.
There is no teardown/rebinding API. Installation requires a quiescent decoder,
before its dispatch loop is used. A copied bound method remains bound to its
original decoder; an explicitly foreign receiver cannot use the interceptor.
This is a private application boundary, not a sandbox against arbitrary hostile
Python inspection or mutation.

Non-ORDER_STATUS calls delegate the exact payload/message objects to the original
dispatcher exactly once. They create no pairing receipt and invoke no PRE3 adapter.
A nested unrelated dispatch temporarily hides its parent's pairing scope; a status
invocation nested inside it receives its own scope. Legacy interpret is untouched.
SDK unknown-message handling and BadMessage reporting/rethrow remain delegated.

## Scope and provenance

Each intercepted ORDER_STATUS call creates an exact `_OrderStatusInvocation`.
It is frozen/slotted with an opaque repr, a private boundary namespace object,
bounded monotonically increasing invocation number, diagnostic thread identifier,
depth and parent number. Actual receipt identity and the captured exact Thread
object establish provenance; thread number or equal reconstructed values do not.
The boundary is associated with one exact decoder for its lifetime.

Only the dispatcher owns the private scope-entry key. The active stack supports
same-thread LIFO reentry, up to a fixed depth of 32. Concurrent foreign-thread
ORDER_STATUS entry is rejected; sequential invocations may use different threads.
Invocation numbers stop at 2^63-1 and never reset/reuse. No completed/orphan history
accumulates. Published results held by a caller are immutable data only.

The private callback harness obtains `_current_invocation()`, calls
`_capture_raw(receipt, raw)`, then `_consume_normalized(receipt, *nine_values)`.
Production callback hooks remain unchanged and do not call these methods.
Foreign-thread operations, reconstructed/foreign/stale receipts and non-LIFO access
cannot consume another frame or remove its owner's state.

## Raw copy and exact follower

Raw capture first checks the original descriptor using PRE3. It copies the nine
qualified fields and their exact HasField booleans into a bounded immutable
primitive snapshot. Missing getters are never read. Independent immutable local
schema metadata supports the PRE3 adapter; no SDK descriptor or message is stored.

PRE3 performs all source-value validation. Bounded temporary raw status/quantity
strings are retained only in the private active frame, with no repr/error exposure.
They permit exact unknown-status comparison before UNKNOWN projection. They are
discarded immediately on successful consumption, invalidation or scope exit.

The normalized follower is checked by PRE3's existing normalized-tuple validation:
exact primitive types, Decimal numeric equivalence with original raw representation,
float hex/sign agreement, and exact raw status comparison. All nine qualified absent
fallbacks are checked, including UNSET_INTEGER/LONG/DECIMAL/DOUBLE and empty status.
Missing and supplied-unavailable values never become usable economic facts.

One raw capture and one normalized consumption are allowed per invocation.
Duplicate/malformed/contradictory operations invalidate the frame; catching the
validation error cannot restore success. No latest-wins or economic deduplication
exists. Identical messages in separate invocations get separate receipts.

The result `_PairedOrderStatus` contains only the immutable invocation receipt and
the PRE3 `_OrderStatusObservation`. It records successful same-invocation pairing,
not successful SDK completion: the original dispatcher can still raise afterward.
Its receipt cannot be used after scope exit, and a reconstructed result grants no
capability. There is no result history or transferable authority registry.

## Guaranteed cleanup

The interceptor delegates the original processProtoBuf inside try/finally. Missing
raw, missing follower, duplicate capture/consumption, mismatch or invalid nesting
cannot survive decoder exit. Cleanup clears temporary references and removes the
exact frame and any invalid descendants while preserving outer scopes.

Ordinary SDK/callback errors, BadMessage, KeyboardInterrupt, SystemExit and
GeneratorExit preserve their original exception identity. Cleanup occurs before
propagation; an orphan-reporting error cannot mask an original SDK exception.
PRE4's own malformed data/usage uses one sanitized fixed error. Interruptions during
PRE3 adaptation/verification invalidate temporary state and propagate unchanged.
An inner orphan caught by its caller leaves a valid outer invocation usable.

No clock, timeout, generation, order-ID or timestamp proximity supplies pairing.
The private state lock is held only for short state transitions, never while
delegating SDK processing or executing source getters/adaptation. It touches no
transport/coordinator/control/arrival/store lock and changes no existing lock order.

## Protected boundaries and deferred work

PaperTWSTransport, ReadOnlyTWSTransport, read-only broker, dispatch coordinator,
reconciliation/PRE1, PRE3, C3C4A journal/replay, C3C4B SQLite, C3C4D persistence,
C3C5A accounting, C3C5B lifecycle, recovery, wire policies, public Broker APIs,
Strategy v1.0 and backtests remain unchanged.

There is no generation, callback sequence, request ID, collection completion,
callback capture/durability, ownership, currentness, current remainder, inventory,
cancellation, protection, exit, recovery or trading authority. Authenticated
session/generation continuity, source-capture integration and subsequent current
lifecycle qualification remain separate work. Restart quarantine is unchanged.

## Offline validation and Windows requirement

Focused tests use qualified anonymous Decoder/protobuf doubles and socket/DNS
guards. Seven contract scenarios additionally use the actual installed Decoder
and OrderStatusProto when ibapi is available, without constructing an EClient.
They check dispatcher/table identity, per-instance interception, presence/fallbacks,
sequential receipts, reentry and orphan cleanup. The cloud has no installed ibapi
or protobuf; actual-SDK execution and Windows runtime validation are not claimed.

Run the focused suite, relevant regressions and full offline suite with the real
Gateway integration opt-in disabled, followed by compilation and scope/privacy
checks. Independent review and actual Windows/ibapi 10.50.2 focused/full validation
are mandatory before production callback capture integration.

Linux/Python 3.12 validation completed: 135 new focused tests and 2,167 selected
existing regression tests passed. The final full offline suite ran 4,763 tests:
4,762 passed and the existing opt-in real Gateway integration test was skipped.
All 107 Python files compiled. Whitespace, secret/account identifier, `.env`,
private import and protected-file scope checks passed. Exactly this module,
its focused tests, this document and minimal README/readiness updates changed.
