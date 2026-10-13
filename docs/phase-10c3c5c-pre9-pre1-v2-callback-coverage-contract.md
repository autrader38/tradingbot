# Phase 10C3C5C-PRE9-PRE1 — inactive V2 callback occurrence coverage

This foundation adds exact logical-journal-v2 models, codec registrations and
pure replay validation. Production `_create_paper_dispatch_journal_owner()` still
creates **v1**. C3C4D capture, staging, attribution, duplicate reuse, persistence
cursor and progress publication are unchanged. PRE9 qualified-status durability
is not implemented. Independent review and actual Windows validation are required
before PRE9 is re-qualified.

## Why the prerequisite exists

V1 replay intentionally accepts producer-certified progress. The controlled
producer can cover adjacent duplicate callbacks by reusing a committed source
fact, classify untracked callbacks without appending a source, or associate
commission occurrences with `_CommissionReceived`, which has no callback source.
Those valid histories cannot independently explain every sequence after restart.
V2 supplies historical structural provenance without changing V1 meanings.

## Versions and rollout

Exactly two header pairs are accepted:

| Logical version | Format |
| --- | --- |
| 1 | `paper-execution-journal-v1` |
| 2 | `paper-execution-journal-v2` |

The default header format and active `_SCHEMA_VERSION` remain v1. V2 must be
explicitly supplied. Version/format mismatches, bool versions and future versions
are invalid. Every record must match its header. `_EVENT_TYPES_V1` retains the
baseline 15 events; `_EVENT_TYPES_V2` adds only the four exceptional events below.
No v2 event can appear in a v1 record. Existing source/commission/progress field
layouts and canonical encodings are unchanged.

V2 support is inactive in production. PRE9 must later switch fresh creation only
after complete producer coverage and qualified-source durability are implemented
together. There is no in-place migration, discovery, automatic upgrade, journal
switching, session migration, restart adoption or resume authority.

## Closed snapshots

All snapshots are frozen/slotted, exact-type checked and opaque in repr. They
contain only existing sanitized retained callback values, excluding generation
and sequence, which reside in `_CallbackSource`.

| Snapshot | Fields |
| --- | --- |
| `_OpenOrderCallbackSnapshot` | order_id, client_id, order_ref, con_id, security_type, exchange, symbol, currency, side, quantity, order_type, tif, status |
| `_OrderStatusCallbackSnapshot` | order_id, client_id, status, filled, remaining, average_price, last_price, parent_id |
| `_ExecutionCallbackSnapshot` | order_id, client_id, order_ref, execution_id, con_id, side, shares, price |
| `_CommissionCallbackSnapshot` | exec_id, report_id, amount, currency, received_at |

Old status strings map losslessly to the existing lifecycle enum, including
UNKNOWN. No PRE3 protocol/presence/source-quality metadata is invented. Reviewed
numeric/text bounds and exact primitive types are enforced; bool is never an ID.
Commission is signed/finite/bounded, currency is three uppercase ASCII letters,
and report_id follows the cleared versioned commission fingerprint contract.
Receipt is the actual local callback receipt, not execution/broker time. Its exact
datetime fields/fold and UTC metadata survive. The fixed UTC object reconstructed
by the codec need not be Python's timezone.utc singleton, but must have zero offset
and the UTC name. Other offsets/names and arbitrary timezone objects are rejected.

Snapshot identity includes exact model type and every retained field. Decimal
identity uses sign, coefficient digits and exponent without arithmetic, float,
normalize or ambient context. UTC receipt identity includes fold.

This does not strengthen old transport duplicate semantics: old capture can merge
numerically equal values and retain the first representation. V2 records the
expanded retained evidence that reached C3C4D, not discarded raw callback details.
Commission economic duplicate comparison separately preserves numeric equality,
report identity and currency, allowing different receipt instants/Decimal scales.

## Coverage explanations

The key is `(session_id, write_generation, callback sequence)`. Every source must
refer to an already registered session and its exact generation. Journal record
sequence is solely storage chronology. Coverage records create no local attempt.

Direct `_OpenOrderObserved`, `_OrderStatusObserved` and `_ExecutionObserved`
events cover themselves only after all existing semantic validation succeeds.
V2 rejects repeated physical source records at the same callback key; V1 retains
its prior exact-repeat idempotence.

Four exceptional event models have no generic optional-field combination:

| Event | Fields |
| --- | --- |
| `_DuplicateCallbackCovered` | source, exact ordinary snapshot, root_record_sequence |
| `_UntrackedCallbackObserved` | source, exact ordinary snapshot, reason |
| `_CommissionCallbackCovered` | source, commission snapshot, commission_record_sequence |
| `_UntrackedCommissionCallbackCovered` | source, commission snapshot, execution_record_sequence |

Each record creates exactly one explanation. A duplicate/conflicting explanation
for the same key is invalid. Untracked/commission occurrences cannot be used as
ordinary duplicate roots. No redundant marker accompanies a direct source event.

## Duplicate proof

The root is an earlier committed ordinary source record in the same journal,
session and generation. The previous callback occurrence must already be explained
and resolve to exactly that root. The current complete retained snapshot must
match the root snapshot by representation-sensitive identity and exact type.

For source 1, duplicate 2 and duplicate 3, both markers reference the source-1
journal record. Each retains its own callback count/sequence. Nonadjacent repeats
receive ordinary source records under current producer behavior. An execution's
existing execId economic deduplication remains independent from occurrence count.

## Untracked proof

The only reason is `NO_TRACKED_NUMERIC_ORDER_IN_SESSION`. At that journal prefix,
no recorded attempt/reservation in the source session may own snapshot.order_id.
Matching orderRef alone never changes this classification. The full sanitized
snapshot is retained, rather than an ephemeral flag or identity digest.

For executions, a replay-local identity map checks complete representation-sensitive
snapshot identity for repeated execIds. A tracked execId cannot be classified as
untracked or silently switch from untracked to tracked. These conservative checks
do not change attempt execution totals. The classification means historically
unattributable in this session/prefix, not permanent foreign ownership or current
broker/portfolio truth.

## Commission proof and lookahead

Owned commission coverage references an earlier valid `_CommissionReceived` whose
existing tracked execution/attempt relationship belongs to the same session. ExecId,
report_id, economic amount and currency must match. Each occurrence keeps its own
actual receipt and Decimal representation. The marker adds no accounting commission.

Untracked commission coverage references an earlier untracked execution record in
the same session/generation with matching execId. It creates no accounting fact or
attempt relationship. Malformed snapshots are rejected. Different valid financial
reports remain distinguishable historical untracked occurrences; no correction or
latest-wins semantics are introduced.

Unknown linkage has no coverage explanation. No UNKNOWN marker bypasses a gap.
Lookahead permits execution callback 2 to be recorded before commission callback
1 is explained. References always point backward in **journal** chronology; the
callback chronology remains explicit. Self/forward/missing/wrong-type references
are rejected using only the already validated journal prefix.

## Progress and private state

Coverage maps, roots, record references, untracked execution identities, per-session
frontiers and actual occurrence high-water marks are local replay state. The public
and private published `_JournalReplayState` layout remains unchanged. Memory grows
with actual journal records, not claimed sequence values.

Registering an occurrence advances the frontier only while the next key already
exists. Lookahead beyond a gap does not acknowledge the gap. V2 progress requires:

- existing session and monotonic processed/through values;
- processed <= through and processed <= contiguous coverage frontier;
- through >= highest actual durable occurrence already seen at that journal point.

No coverage is required for processed+1..through. Through may describe an observed
undurable suffix and never creates a callback occurrence. Replay never loops over
an attacker-controlled huge processed value. Structural consistency does not
authenticate broker origin, certify uptime, or defeat self-consistent rollback.

The mixed eight-occurrence fixture covers direct open, duplicate open, untracked
status, direct execution, owned commission, nonadjacent repeated execution,
untracked execution and untracked commission. Progress reaches 8; economic execution
quantity remains 3 and accounting commission count remains one.

## SQLite, failure and crash boundaries

Only closed model/enum codec registrations expand. Application ID, user_version=1,
storage format/domain, SQL tables/columns/triggers, WAL/FULL settings, size limit,
digest/genesis framing and all SQLite methods remain unchanged. No DDL migration.
Canonical exact field/tag checks, duplicate-key/float rejection, tuple Decimal
encoding, re-encode checks and complete hash/semantic verification remain in force.

Backing source -> verified exceptional coverage -> verified progress -> memory
cursor is the required future producer ordering. Direct sources need no extra
marker. Backing fact alone cannot cover an exceptional occurrence. Coverage without
progress is evidence, not acknowledgement. An uncertain commit or corruption
inherits existing poison/quarantine/inhibition rules. There is no automatic retry.
KeyboardInterrupt/SystemExit/GeneratorExit retain original exception identity,
including interrupted rollback/close diagnostics. Restart quarantine remains true
for v1 and v2, even when every historical occurrence is covered.

## Protected boundaries and validation

paper_dispatch_journal.py and all callback/transport/PRE3-PRE8 code are unchanged.
Production journals stay v1, so capture, staging, lookahead, adjacent reuse and
cursor behavior retain their cleared semantics. Qualified status remains memory-only
and unsupported by current durable persistence. No callback-thread I/O is added.

C3C5A/B, reconciliation, recovery, strategy/backtest, public APIs and wire policies
are unchanged. Exceptional records never enter attempt observations, executions,
commissions, filled bounds, accounting or lifecycle interpretation. AccountMode
remains UNKNOWN, snapshot mode None and LIVE unsupported. Gateway Read-Only remains
required. The first real PAPER order remains blocked.

Cloud validation:

| Check | Result |
| --- | --- |
| New focused coverage contract suite | 253 passed |
| Selected regressions including the focused suite | 2,754 passed |
| Full offline suite | 5,730 run, OK, skipped=4 (5,726 passed) |
| Compilation | All 114 Python files passed |
| Whitespace/privacy/protected scope/public exports | Passed |
| SQLite baseline AST comparison | Only `_MODELS` and `_ENUMS` differ |

Full-suite skips are three optional installed-SDK tests and the disabled real
Gateway integration test. No dependency was installed; no cloud installed-SDK or
Windows validation is claimed. Temporary SQLite connections, including the
wrong-thread refusal fixture, are released on their owner thread before cleanup.

Maximal bounded snapshot fixtures with int64 source/reference test values produce
the following full canonical journal-record sizes, all below 262,144 bytes:

| Exceptional type | Encoded bytes |
| --- | --- |
| Duplicate | 1,842 |
| Untracked callback | 1,906 |
| Owned commission | 1,135 |
| Untracked commission | 1,143 |

V1 header/history canonical-byte and digest golden values were independently
compared with the exact starting baseline. Existing V1 adjacent reuse, commission
progress and source-free untracked progress remain valid; no stronger V2 coverage
rule is applied to them. The new replay-state maps remain unpublished.

Actual Windows validation remains mandatory using Python 3.12.10, ibapi 10.50.2 and
protobuf 5.29.5, with real integration opt-in disabled. No Gateway connection is
needed for these tests. PRE9 remains blocked pending independent review and that
validation, followed by a new qualification.
