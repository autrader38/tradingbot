# Phase 10C3C5C-PRE5 — private status delivery handoff

PRE5 adds private decoder registration, a status-arrival fence and positive
LEGACY/PROTOBUF delivery provenance. It stops before callback-ledger publication.
No production handoff receiver is installed. A status with no accepted handoff
permanently inhibits the transport; successful pairing alone is insufficient.

Gateway Read-Only remains required. AccountMode remains UNKNOWN, public snapshot
mode remains None, LIVE remains unsupported and the first real PAPER order remains
blocked. Independent review and actual Windows validation are required before a
later phase may publish qualified handoffs to the production callback ledger.

## Qualified SDK lifecycle

The user supplied offline inspection of Windows Python 3.12.10, ibapi 10.50.2
and protobuf 5.29.5. This is source evidence, not a cloud connection test.

- EClient construction sets decoder to None and calls reset.
- Reset does not clear decoder.
- Connect creates a new Decoder inside the handshake.
- Final server version is assigned to client and decoder before CONNECTED,
  EReader startup, startApi, connectAck and connect return.
- EReader can queue data before connect returns; queued bytes are not dispatched
  callbacks. Run later uses the current decoder's processProtoBuf or interpret.
- Disconnect invokes connectionClosed and reset; the old decoder reference can
  remain. Subsequent connect creates a replacement decoder.
- Default connectAck and orderStatusProtoBuf only log. The legacy status handler
  calls normalized orderStatus without a raw precursor.

Constructor-time PRE4 installation is invalid because no decoder exists yet.
PRE5 selects post-super-connect registration, with run prohibited until READY.
No production call to connect/run is added and neither capability is exposed by
PaperClientView. Lifecycle tests invoke a fake Client base, never EClient.connect.

## Registration and retirement

The exact private GuardedClient, Wrapper, Decoder, PRE4 boundary, PRE5 owner and
transport generation form one association. Connection state and exact positive
final server-version agreement are checked before installation. READY is published
only after both instance boundaries are installed and verified.

GuardedClient's decoder property intercepts SDK assignment. Unexpected replacement
retires eligibility and latches inhibition before publishing the replacement.
Run rejects missing, incomplete, replaced or retired registration immediately.
Every decoder dispatch verifies the captured association, instance interceptors,
original SDK handlers/tables, Wrapper hooks and final server version.

Disconnect retires registration before SDK shutdown. Active fences force permanent
reconciliation before being removed. Old decoder references and receipts cannot
publish. PRE4 is not uninstalled, reused or migrated.

Same-generation reconnect is prohibited, including after failed registration.
An explicitly created later transport generation constructs a distinct client,
registration and PRE4 boundary. Permanent reconciliation is never cleared.
No session continuity, restart adoption or generation migration is asserted.

## Private values and identity

Frozen, slotted, opaque values are:

| Value | Contents |
| --- | --- |
| `_DecoderRegistration` | Private namespace, exact generation |
| `_StatusDeliveryReceipt` | Registration, lifetime delivery number, protocol |
| `_StatusArrivalFence` | Exact delivery receipt, arrival marker |
| `_StatusHandoff` | Generation, exact delivery receipt, immutable PRE3 observation |
| `_StatusReceiver` | Exact registration association |

Internal ownership checks require exact issued objects; equal reconstruction is
insufficient. Handoff acceptance also rechecks primitive observation snapshots,
including Decimal representation and presence. Values contain no SDK object,
descriptor, frame, account data, callback sequence, timestamp or authority.
The internal owner retains SDK references only to verify identity.

## Protobuf delivery

PRE4 has a private, one-time observer seam accepting only the exact PRE5 owner,
with captured methods rather than caller-selected notification functions.
The existing processProtoBuf interceptor remains installed once. No second wrapper,
global registration, SDK class mutation or class dispatch-table mutation is used.

Before payload validation/parsing, exact ORDER_STATUS entry secures arrival
synchronization, increments the transport lifetime marker and installs a fence.
It releases arrival before PRE4 frame creation, SDK parsing or raw getters.
The raw Wrapper hook requires positive delivery provenance and captures through
PRE4. The normalized hook consumes the same PRE4 invocation and creates a handoff.
Raw and normalized halves produce one arrival, and no callback evidence sequence.

## Legacy delivery

The original interpret is intercepted only on the registered decoder instance and
delegated once. Its SDK legacy dispatch table is discovered by exact status-handler
identity, not a guessed attribute name or message number. Installation requires an
ordinary self.attribute lookup in the original interpreter's bytecode, a unique
matching handler and a bounded table. Unsupported SDK shapes reject registration.

An instance-local table copy replaces only that status handle with a facade. The
facade delegates the original SDK handler after fencing entry, before status-field
decoding. Original HandleInfo objects and class tables are not mutated. Other
legacy messages have no status fence/handoff and delegate unchanged.

The normalized status uses PRE3's nine-field legacy adapter, preserving permId,
unavailable sentinels and ambiguous decoded zeros. No legacy wire parser is copied
or rewritten, and no fresh collection/request correlation is invented.

## Positive call provenance

Protocol is never inferred from values, numeric IDs, an active outer PRE4 scope
or catching a pairing error. The delivery receipt identifies the actual dispatcher
route. Transient frame checks require the exact registered Wrapper method,
original SDK handler code and decoder receiver, and exact enclosing delivery frame.
Protobuf raw/follower hooks must share the same active handler-frame witness.

Only scalar internal witness identities survive the transient checks, and those
are erased at exit. No frame, locals dictionary or raw input is retained in evidence.
Unrelated normalized reentry cannot consume an outer receipt. Legitimate nested
protobuf/legacy deliveries receive separate receipts with same-thread LIFO checks.

## Arrival, acceptance and failure

The lifetime arrival marker invalidates an earlier reservation. A separate active
fence blocks a newly allocated reservation whose marker is already current.
Both PRE2 final reservation validation and the existing isolated write commitment
reject active fences or an ineligible registration. Numeric IDs remain consumed;
no release, floor reduction, exhaustion reset or retry is introduced.

The combined status/legacy-dispatch stack is bounded to 32 entries, with a bounded
delivery counter and no completed handoff history. Failed/orphaned delivery remains
an arrival; rejected exact entry counts once even before a fence could be returned.

The receiver seam issues one exact private receiver object, not an arbitrary
callback function. Acceptance is one-shot for the exact live handoff. Acceptance
does not clear its fence: SDK delivery must also complete successfully.
The production factory does not issue a receiver or accept handoffs. An unconsumed
handoff therefore marks arrival unusable and permanently requires reconciliation
before removing the fence and erasing temporary state.

Malformed raw/legacy values, mismatches, duplicates, orphaning, stale binding,
wrong thread, overflow, replacement and interruptions fail closed. No weaker
status projection, fabricated ID or latest-wins behavior exists. Errors are fixed
and sanitized. KeyboardInterrupt, SystemExit and GeneratorExit secure safety and
cleanup before the original exception object propagates; an earlier interruption
takes precedence over a secondary cleanup interruption.

Existing C1 SDK doubles with neither connect nor run retain their original isolated
callback fixture path. This is not protocol classification. A genuine SDK lifecycle
or any registration attempt cannot use that fixture path as fallback.

## Locks and protected boundaries

Existing direction remains coordinator → control → read lifecycle → read condition
→ write dispatch → arrival. PRE4/PRE5 guards are released before transport locks.
Arrival is held briefly for markers, fences and safety flags. SDK parsing/getters
run outside transport and internal guards. Callback code acquires no coordinator,
control or SQLite locks and makes no broker/network requests.

No handoff enters the existing 128-run evidence ledger. `_OrderStatusEvidence`,
duplicate compression, spans, callback/persistence cursors, acknowledgements and
order-ID floor adoption are unchanged. PRE5 preserves the whole PRE3 observation,
including protocol, presence, source quality and permId, but none grants ownership.

PRE3, PRE1 account binding, reconciliation, C3C4A schema/replay, C3C4B storage,
C3C4D persistence, C3C5A/B, public APIs, wire allowlists and frozen strategy remain
unchanged. PRE4 standalone behavior remains unchanged without a PRE5 observer.
There is no journal event, SQLite operation, currentness, terminal finality,
inventory, cancellation, protection, exit, recovery application or trading permit.

## Validation and remaining work

Focused tests cover offline lifecycle doubles, adversarial delivery, provenance,
fencing, bounded cleanup and interruption seams. Three optional tests use an actual
installed Decoder with local protobuf/legacy payloads and a fake Client lifecycle;
they never instantiate/connect a real EClient. Socket and DNS guards remain active.
Those tests skip when ibapi is absent. Cloud validation does not qualify Windows.

Cloud validation completed on Python 3.12.14:

- PRE5 focused: 142 tests, 139 passed, three optional installed-SDK tests skipped.
- Selected existing regressions: 2,081 passed, including standalone PRE3/PRE4,
  PRE2, transport/callback/persistence, reconciliation/recovery, accounting,
  historical lifecycle, broker safety and protobuf/wire checks.
- Full offline suite: 4,905 tests, 4,901 passed, four skipped. The additional
  skip is the existing real-Gateway integration opt-in, which remained disabled.
- All 109 Python files compiled. Whitespace, secret/account/.env, public/export,
  protected-file and network/storage scope checks passed.

The three notification-return interruption probes also verify cleanup when PRE4
has not yet stored the returned PRE5 receipt. Scope identity preserves outer
deliveries; no temporary fence/handoff survives interruption.

Later work must qualify ledger compatibility/publication and durable preservation
of source semantics, then fresh reconciliation and authenticated continuity.
Current lifecycle/inventory/dispositions, protection and resizing, cancel/replace,
disconnect/crash handling and reviewed recovery remain separate prerequisites.
No barrier is cleared. The first real PAPER order remains blocked.
