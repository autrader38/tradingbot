# Phase 10C3C5C-PRE9 — qualified source durability and atomic V2 activation

This phase preserves **historical evidence only**. Fresh private production
journals use logical V2. Every callback occurrence crossed by durable progress
has a verified committed V2 occurrence explanation. Qualified order status
retains the complete PRE3/PRE6 source observation rather than an old-model view.

Starting checkpoint: `c35466fa536569cdcefc49af62a9b2a26824e429`.
The user reported PRE9-PRE1 independent review and Windows validation complete:
253 focused, 187 core journal, 257 SQLite and 170 dispatch-journal tests; 5,730
full-suite tests with 14 skips; compilation and a clean working tree. Those are
preceding-checkpoint results, not PRE9 Windows validation.

Gateway Read-Only remains required. AccountMode remains UNKNOWN, public snapshot
mode remains None and LIVE remains unsupported. The first real PAPER order
remains blocked. No real connection, broker read/write or Gateway change is
needed or authorized for these tests.

## Storage-owned source values

The journal defines frozen/slotted/opaque `_QualifiedStatusProtocol`,
`_QualifiedStatusSourceQuality`, `_QualifiedStatusPresence`,
`_QualifiedStatusObservation` and `_QualifiedOrderStatusObserved`.
The event contains only `_CallbackSource` and the durable observation. It has
no client-order key, attempt label, ownership, currentness or management permit.

Protocol is exactly LEGACY or PROTOBUF. Each of the nine observation fields
retains its value and explicit quality: SUPPLIED, LEGACY_DECODED,
LEGACY_AMBIGUOUS or UNAVAILABLE.

| Field | Durable value |
| --- | --- |
| order_id | Exact int in [0, 2147483647], or qualified absence |
| status | Reviewed PRE3 status string, including UNKNOWN, or qualified absence |
| filled / remaining | Exact bounded nonnegative Decimal, or unavailable |
| average_fill_price / last_fill_price | Exact bounded nonnegative Decimal, or unavailable |
| perm_id | Exact int in [0, 9223372036854775807], or qualified absence |
| parent_id / client_id | Exact int in [0, 2147483647], or qualified absence |

Boolean IDs are invalid. Status retains PRE3 normalization; UNKNOWN cannot
recover the original unrecognized broker text. permId is source metadata only.
No field is used to attach this event to an attempt or restore an allocator.

LEGACY wire presence is unknown (`None`). PROTOBUF stores a tuple of supplied
field names in the explicit nine-field source order. Duplicate names, unknown
names, reordered tuples and incompatible protocol/presence combinations fail.
Storage does not infer supply from a value.

Absent fields, supplied zero, supplied-unavailable quantity/price sentinels,
legacy decoded values and legacy ambiguous zero IDs/prices remain distinct.
PRE3 permits supplied integer maxima as actual source values; they are not
converted to absence. Supplied unavailable ID/status combinations that PRE3
does not permit are rejected. Contradictory value/quality/presence triples fail.

Decimal sign, coefficient digits and exponent survive unchanged, including
signed zero and trailing-zero scale. Values are finite, limited to 64 coefficient
digits and exponents [-64, 64]. No float conversion, normalize, arithmetic
identity or ambient-context-dependent rounding is used for source copying.

## Pure adapter and separation

`paper_qualified_status_journal.py` accepts the exact PRE6 evidence type, reruns
PRE6 validation and independently copies all reviewed values. It translates
protocol/quality enums and frozenset presence into storage-owned enums and the
canonical tuple. A typed representation comparison proves exact equivalence,
including Decimal tuples. Generation and sequence come from transport evidence;
session ID comes from the exact registered journal session.

The owner imports the adapter. The adapter imports PRE3/PRE6 and journal values.
Journal and SQLite do not import the adapter, transport, Decoder or SDK.
PRE6's prior durable-consumer classification remains unchanged; this separate
PRE9 adapter is the activated V2 durability contract. A legacy projection never
counts as qualified-source durability.

## Complete producer mapping

| Callback path | Verified V2 occurrence explanation |
| --- | --- |
| Attributed ordinary open/status/execution | Existing direct source event |
| Adjacent duplicate attributed ordinary callback | `_DuplicateCallbackCovered` referencing the original direct root |
| Untracked ordinary open/status/execution | `_UntrackedCallbackObserved` with complete retained snapshot |
| First owned commission | `_CommissionReceived` backing, then `_CommissionCallbackCovered` |
| Duplicate owned commission | Another `_CommissionCallbackCovered` referencing the original accounting fact |
| Commission linked to untracked execution | `_UntrackedCommissionCallbackCovered` referencing durable untracked execution |
| Unknown commission relationship | No explanation; remains staged |
| Qualified status | One `_QualifiedOrderStatusObserved` per logical callback sequence |

Old adjacent duplicates preserve ordinary fact reuse and the cleared retained
representation semantics. Each duplicate now has its own occurrence marker.
Markers always reference the original direct source, never another duplicate.
Nonadjacent execution repeats retain separate source occurrences while execId
economic deduplication counts the execution only once.

Owned commissions keep existing economic duplicate/conflict rules. Occurrence
snapshots preserve each actual receipt and Decimal representation; accounting
retains one `_CommissionReceived`. Missing commission is unknown and no fee
finality/completeness is asserted. Untracked commission reports remain historical
source facts without accounting adoption or correction/latest-wins semantics.

Untracked executions retain exact identity, durable event and verified committed
receipt. Later commission linkage uses that receipt, not a heuristic history
search. Qualified source never enters numeric target lookup, anchor logic,
ordinary adjacent reuse or compatibility projection.

PRE7 can compress identical qualified occurrences in memory. Paging expands
their sequences and the producer commits a separate qualified source event for
each one. No qualified receipt reuse or ordinary duplicate marker is permitted.

## Receipts, progress and lookahead

`_CallbackCoverage` retains its three fields, now strictly meaning staged
occurrence, durable occurrence explanation and verified committed receipt.
None/backing-only coverage is invalid. Before admission to the covered set and
before prefix advancement, the owner checks the exact event/receipt types,
payload identity, record envelope/history and source session/generation/sequence.
The event must exactly correspond to retained evidence. Backing facts are
explicitly separate and cannot acknowledge their callback occurrence alone.

Capture -> stage -> release capture locks -> verified backing append if needed
-> verified occurrence append -> covered set -> contiguous prefix -> revalidate
-> verified progress append -> revalidate -> persistence cursor publication.

Commission-before-execution lookahead remains two-pass. An execution at callback
N+1 can be committed before commission N. All backing references point backward
in journal chronology; the explicit callback sequences retain actual source
chronology. Progress cannot cross N until its coverage explanation commits.

PRE7 first loss remains absolute. Retained runs remain limited to 128; staging
and covered maps retain the existing bounds. No second receipt/duplicate queue
is added. Lifetime relationship maps retain only the already-required execution
and commission identities plus committed references. No marker repairs lost
capture and loss during I/O prevents subsequent progress publication.

## Replay, versions and SQLite

V1's 15-event allowlist, original replay meanings, canonical bytes/digests and
producer-certified progress remain unchanged. Logical V2 includes those events,
the four PRE9-PRE1 coverage forms and the new qualified source event. There is
no future-version wildcard. Qualified source in V1 is invalid.

Qualified replay validates the exact source values and registered session/
generation, then registers one occurrence in the existing V2 contiguous-frontier
proof. It performs no attempt lookup, ID-floor update, lifecycle interpretation,
execution accounting or commission accounting. Duplicate occurrence explanations
are invalid even when source values match. Published replay-state shape remains
unchanged and restart quarantine always remains true.

Fresh setup explicitly creates version 2 with `paper-execution-journal-v2`.
The owner checks that immutable version/format throughout its lifetime. There
is no V1 production fallback, existing-store adoption, upgrade, migration,
store switching, session migration or restart resume.

SQLite changes only its closed model/enum registrations. Application ID remains
0x504A4231, user_version remains 1, and storage format/hash domain remain
`paper-execution-journal-sqlite-v1`. DDL, tables, columns, triggers, WAL/FULL,
hash framing, payload limit (262,144 bytes) and all storage methods are unchanged.
Existing V1 and earlier V2 data retain their original bytes and meanings.
Older code encountering unsupported new tags fails closed.

## Threading, failures and authority

PRE8 callback capture is unchanged and memory-only. No callback performs journal,
SQLite, filesystem, fsync, DNS, socket or broker-request activity. Persistence
uses the exact captured creator thread and default SQLite thread checks.

The lock direction remains coordinator -> control -> read lifecycle -> read
condition -> write dispatch -> arrival. Capture/revalidation use those locks;
SQLite executes after releasing all read/write/arrival capture locks. No store
transaction spans SDK/network activity and callbacks never wait for persistence.

Conversion, validation, serialization, storage, revalidation or publication
failure permanently inhibits the owner under existing policy. BUSY, poisoning,
quarantine, corruption and unknown commit outcome grant no automatic retry.
If backing commits without coverage, the callback remains uncovered. If source
commits without progress, the cursor remains behind. If progress commits before
cursor publication, it remains durable evidence. Interrupted cleanup may leave
memory behind or at proven progress; the failed owner cannot resume.

KeyboardInterrupt/SystemExit/GeneratorExit secure safety and propagate the exact
original exception object. Diagnostics never replace an original interruption.
Restart evidence cannot prove continuity or missed-callback absence.

C3C5A/B, reconciliation, recovery, public Broker APIs, wire policies, strategy,
backtest and LIVE paths are unchanged. Qualified/coverage events add no accounting
or lifecycle fact. Current broker truth, inventory/dispositions, protection,
cancel/replace, session continuity and reviewed recovery remain later phases.
Independent review and actual Windows validation are required before the next
broker-lifecycle phase, using Python 3.12.10, ibapi 10.50.2 and protobuf 5.29.5
with real Gateway opt-in disabled.

## Cloud validation

| Check | Result |
| --- | --- |
| New focused PRE9 suite | 399 passed |
| Selected regressions | 3,665 run, OK, skipped=3 |
| Full offline suite | 6,129 run, OK, skipped=4 |
| Compilation | All 116 Python files passed; cache outside the repository |
| SQLite AST comparison | Only closed `_MODELS` and `_ENUMS` registrations changed |
| Original journal models/validators | Unchanged; old replay adds only the exact qualified branch |
| Scope/privacy/public/wire review | Passed; protected production implementations unchanged |

The focused suite also exercises every one of the 512 protobuf presence subsets.
The maximal bounded SDK-source fixture with int64 provenance/reference values,
maximum IDs, 64 coefficient digits, exponent -64 and longest retained status is
2,555 bytes as a full canonical record, below 262,144 bytes. This measurement
does not claim an infinite bound on arbitrary Python provenance integers;
existing serialization and payload limits still fail closed.

Four prior test suites receive narrow compatibility updates for formerly
inactive V2/qualified durability. In particular, unproven progress is now rejected
by V2 storage replay before it can reach the unchanged C3C5A publication guard.
No accounting implementation is modified.

Three optional installed-SDK tests skip because ibapi is unavailable in cloud;
the real Gateway integration test skips because opt-in remains disabled. No
dependency was installed. No new installed-SDK or Windows validation is claimed.
