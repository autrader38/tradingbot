# Phase 10C3C5C-PRE8 — qualified status ledger publication and consumer stops

PRE8 connects qualified PRE5 status delivery to the PRE6 source-aware record and
PRE7 shared bounded capture. This phase is **memory-only**. It adds no qualified
source durability, currentness, terminal finality, current working remainder,
ownership, inventory, protection, cancel/replace, exits, recovery application,
barrier clearing or trading authority. Gateway Read-Only remains required and
the first real PAPER order remains blocked.

Starting checkpoint: `5d1563c65f7eb5e5c82cfd2d367e8f361a99b7e4`. The user reported
independent PRE7 review and Windows validation complete: 171 focused cases,
PRE6/PRE5/PRE4/PRE3 regressions and 5,234 full-suite tests with 14 skips. Those are
preceding-checkpoint results, not PRE8 Windows validation.

## Exact receiver and registration

`_PaperStatusDelivery` owns one frozen `_StatusReceiver` for its exact Decoder
registration. After final SDK Decoder verification and hook installation, while
the delivery is still REGISTERING, it creates the receiver and captures the
reviewed `PaperTWSTransport._publish_qualified_status` bound method and qualified
record type. Only then can READY and transport registration eligibility be
published. Run cannot see a registration without its receiver capability.

The receiver contains no caller-supplied callable. The delivery's ordinary
attribute assignment is prohibited; callback delivery checks the captured method
origin and registration association. There is no public issuance/replacement or
external registration hook. The former future/test issuance seam now refuses
issuance because registration already created the sole receiver.

Same-client reconnect remains prohibited. A later transport generation creates
a new client, Decoder, registration, PRE4 boundary and receiver. Old objects and
equal reconstructions cannot publish under the new association. No boundary,
read/write session, accounting owner or authenticated binding is migrated.

## Source and routing

Registered SDK LEGACY and PROTOBUF delivery retain the PRE5 positive handler,
frame, invocation and thread checks. PROTOBUF arrival is fenced before parsing;
PRE4 raw/follower pairing and PRE3 same-event validation complete before PRE8
publication. LEGACY uses PRE3's qualified normalized callback adapter.

The registered Wrapper route produces only a qualified handoff and record. It
does not call the old `owner._callback(..., 'status', ...)` branch. Existing C1
doubles with **no SDK connect/run/Decoder lifecycle** retain that old fixture
route. They do not issue a receiver or acquire a production protocol identity.
Registered lifecycle failure never falls back to that fixture path.

PRE6 helpers are loaded and captured in the transport constructor, after the
transport module is complete, before client construction and callbacks. This
avoids the reciprocal module import cycle and callback-thread import/file I/O.
The frozen private contract retains exact class/function references and rejects
replacement or deletion. No dynamic callback import occurs.

## One-shot claim and capture

Each temporary PRE5 entry now tracks attempted, started, assigned sequence,
complete and publication-result state. A guard-owned claim verifies the exact
entry/thread, handoff, receipt, receiver, registration, generation, protocol and
observation snapshot. Claiming is irreversible for that entry and allocates no
sequence while the PRE5 guard is held.

The guard is released before the captured transport method runs. Under arrival
synchronization it rechecks the owner/registration/fence and exact active entry,
then marks publication started. It validates PRE7 metadata and determines the
next transport-lifetime sequence. The entry records that sequence before
qualified construction, so a failed valid occurrence is still accounted for and
cannot be retried at another sequence.

`_QualifiedOrderStatusEvidence(generation, sequence, observation)` independently
copies the complete PRE3 observation. Its only fields remain generation,
sequence and observation. Protocol, quality, presence, permId and Decimal
representation survive. No raw protobuf object, SDK object, frame, receipt,
Decoder, account identifier or authorization is published.

The exact canonical identity and known ID feed the same arrival-owned PRE7
commit helper used by all four old callback types. Capture completes only after
truthful metadata, callback watermark, conservative ID floor and final invariant
check complete. The entry's immutable result then records the event and exact
sequence. Arrival synchronization releases before handoff acceptance.

Acceptance requires that completed result, its exact type/generation/sequence
and original source snapshot. Never-attempted, incomplete, swapped, forged,
duplicate or mismatched acceptance fails closed. A successful acceptance does
not clear the fence; successful handler/delivery exit must also occur. All new
entry publication fields are erased in ordinary cleanup.

One SDK status occurrence therefore has one handoff and at most one consumed
callback sequence. Its retained logical occurrence is either a new qualified
run or one exact occurrence in an adjacent duplicate span. Compatibility views
never become additional ledger events.

## Shared identity, capacity and loss

The exact union is open, old status, execution, commission and qualified status.
No subclasses or arbitrary values are accepted. PRE7 validates qualified records
canonically while retaining all original structural checks for the old types.

Old evidence keeps its existing identity rules. Qualified identity uses PRE6's
typed canonical form, including generation, protocol, quality, wire presence,
permId and Decimal sign/digits/exponent. Signed zero, supplied/unavailable facts
and protocol distinctions cannot disappear through equality. Old and qualified
status types never compress together.

The shared limit remains 128 retained runs. No queue, eviction or larger limit is
added. Extension requires exact tail identity, sequence adjacency and no loss.
A distinct qualified occurrence at capacity consumes its one sequence, records
first loss and freezes the old prefix. A valid qualified occurrence after
existing loss also consumes once without appending/extending or moving first
loss. Both remain unaccepted and permanently inhibit PRE5 delivery. A later
duplicate cannot repair coverage.

`_EvidenceBatch` and its informational watermark remain unchanged. Paging uses
the existing bounded replacement expansion, which preserves qualified concrete
type and independently copied source values. It never acknowledges or clears
evidence. PRE7's refusal at/beyond first loss and incomplete-prefix behavior
remain intact.

## Failure and interruption

Before a trustworthy sequence can be determined, rejected/stale/forged source or
invalid metadata cannot manufacture a callback sequence. Once the valid handoff
has consumed its sequence, construction, canonical validation, identity,
insertion, extension, watermark, floor or final invariant failure restores the
untouched truthful prefix, preserves the monotonic watermark, establishes first
loss where necessary and secures reconciliation/unusable arrival. No partially
constructed qualified event is retained.

If acceptance fails **after complete capture**, the truthful event and sequence
remain; acceptance failure alone does not create a fictitious capture loss.
Every failure leaves no retry publication path. Exact publication failure also
retires transport registration eligibility before returning, including a direct
duplicate attempt whose exception a surrounding test caller catches.

KeyboardInterrupt, SystemExit and GeneratorExit propagate as the exact original
objects after local safety is secured. Ordinary errors follow sanitized PRE5
delivery errors without raw payload text. Failed delivery latches permanent
inhibition before its temporary fences are removed. No safety latch is cleared.

## IDs and reservation

PRE6 `_known_order_id()` supplies floor semantics. Absent protobuf IDs and
ambiguous legacy zero remain unknown. Supplied protobuf zero, positive and MAX
IDs, and positive decoded legacy IDs remain known. permId never substitutes.

Known IDs conservatively raise the lifetime floor after the capture commit; MAX
preserves permanent exhaustion. Failed transitions retain conservative floor
advancement, never lower it, and never invent an ID when qualified construction
or validation did not establish one. Permanent reconciliation blocks further
allocation after such capture failure.

Reservation validation now uses PRE6 collision classification instead of direct
qualified `.order_id` access. Known collision and current-generation unknown ID
both refuse proof; commission remains order-independent. Every other PRE2
provenance, generation, watermark, allocator, readiness, fence and one-shot
commitment gate remains unchanged. Arrival invalidates pre-existing reservations
before source parsing/publication and capture cannot restore their proof.

## Consumer boundaries

Dispatch classifies qualified evidence before legacy field access or cursor
advancement in both pending and pre-entry branches. A lossless DISPATCH view
uses the same generation/sequence through existing historical status rules.
An unsupported record stops before that sequence, requires reconciliation and
cannot be bypassed by later records. Old evidence behavior is preserved.

Reconciliation validates the complete qualified record without flattening it.
A lossless RECONCILIATION view can enter the unchanged historical assessment
logic; the bundle still retains its qualified unread source. A non-projectable
record is explicitly unresolved and stops logical coverage before any suffix.
Missing ID, quantity or price is never fabricated. PRE1 signed commission and
quantity-independence rules remain unchanged. No evaluator cursor is mutated.

C3C4D is unchanged. Its existing exact-type snapshot gate rejects every qualified
record before durable staging/progress, including projectable records. It never
persists a flattened `_OrderStatusObserved` from qualified source. An older
persisted prefix stays before the blocker, and later old evidence cannot bypass
it. This conservative whole-drain refusal can permanently inhibit the journal
owner. Memory cursor advancement cannot satisfy the final-entry durability gate.

Qualified source durability needs a later separately qualified contract. PRE8
does not modify journal/replay events or SQLite schema, codec, thread checks,
WAL/commit verification or storage semantics. C3C5A/B consume their original
durable historical records and are unchanged.

## Threads, locks and restart

The decoder, Wrapper, receiver and publication execute on the invoking callback
thread, including synchronous delivery during a fake SDK placeOrder. PRE4/PRE5
guards release before publication takes arrival synchronization. Publication
does not acquire write bookkeeping, coordinator/control or store locks and can
complete while write bookkeeping is blocked. It makes no SDK/network request,
clock lookup, file access, import, journal append or SQLite call.

The established outer direction remains coordinator → control → read lifecycle
→ read condition → write dispatch → arrival. No transaction spans SDK activity.
Receipt provenance is temporary and never reaches consumers or storage.

Crash-equivalent boundaries before allocation, between metadata/watermark,
between capture/acceptance and after acceptance/fence exit preserve conservative
restart semantics. Memory source can be absent from the journal; restart
quarantine remains mandatory. No adoption, resume, migration, re-arm, recovery
application or barrier clearing is introduced.

## Validation and next boundary

The focused PRE8 suite covers receiver issuance and immutable origin; all source
shapes; representation-aware duplicates; bounded spans and loss; nested delivery;
construction/validation/commit/acceptance failures with four exception classes;
ID floors and reservation collisions; pending/pre-entry consumer stops; pure
reconciliation; and actual offline C3C4D refusal and synchronous normal-return
ordering. Callback tests guard network, DNS, file, imports, SQLite and journal
operations. Existing PRE5/PRE6 tests update only superseded integration expectations.

Cloud/Linux validation: **243 new PRE8 cases passed**; selected regressions
**3,307 run, 3,304 passed, 3 skipped**; full offline suite **5,477 run, 5,473
passed, 4 skipped**. Three skips are optional installed-SDK cases because cloud
ibapi is unavailable; the fourth is the disabled real Gateway integration test.
All 113 Python files compiled. Exact eleven-file scope, protected-file/PRE6
functional-AST checks, public-surface/callback-I/O checks, whitespace,
secret/account-identifier and hidden/ignored `.env` scans passed. No dependency
was installed and no real IBKR operation or Gateway change occurred.

Actual Windows validation with Python 3.12.10, ibapi 10.50.2 and protobuf 5.29.5
remains required. Keep real Gateway integration opt-in disabled. Independent
review and Windows validation are required before the next broker-lifecycle
phase. No cloud run establishes Windows qualification.

AccountMode remains UNKNOWN, public snapshot mode remains `None`, public Broker
writes remain blocked and LIVE remains unsupported. Source statuses remain
received historical observations, including Filled/Cancelled: they grant no
terminal finality, no-late-fill guarantee, current inventory or protection sizing.

Later qualified durability, fresh-read/status and continuity work, bounded
current-lifecycle evidence, inventory/disposition ownership, protective and
cancel/replace/exit lifecycles, and reviewed recovery/risk/strategy restoration
remain separate. The first real PAPER order remains blocked.
