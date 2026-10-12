# Phase 10C3C5C-PRE6 — private qualified status ledger compatibility contract

PRE6 supplies pure values, compatibility classification and contiguous-coverage
rules. It does not install a PRE5 receiver, accept a production handoff, allocate
a callback sequence, publish into the transport ledger or alter write eligibility.
Gateway Read-Only remains required. AccountMode remains UNKNOWN, public snapshot
mode remains None, LIVE remains unsupported and the first real PAPER order remains
blocked.

The starting checkpoint is `25334aa12d7c9d2308a944d0852f0f7d29842b0b`.
Independent review and actual Windows validation of PRE5 were reported complete:
142 PRE5, 135 PRE4 and 104 PRE3 focused tests; 4,905 full-suite tests with 14 skips;
compilation passed and the working tree was clean. The checkpoint includes the
test-only installed-SDK legacy adapter fix. No Windows validation of PRE6 is
claimed by the cloud implementation.

## Why publication still needs a separate phase

PRE5 preserves source distinctions that `_OrderStatusEvidence` cannot express:
protobuf absence, supplied-unavailable quantities/prices, legacy ambiguous zeros,
protocol, field quality, wire presence and permId. Substituting zero or decoder
fallbacks would manufacture facts. Existing duplicate identity also uses numeric
Decimal equality and can collapse representation and signed-zero distinctions.
C3C4D checks exact retained ledger types before paging; changing only a paging
view would not establish compatibility.

PRE6 defines a contract for that future integration. Existing production consumers
do not import or invoke it. Their arrival, ledger, cursor, acknowledgement,
reservation, persistence and reconciliation behavior remains unchanged.

## Private record and identity

`ibkr_qualified_status_ledger.py` defines frozen, slotted, opaque
`_QualifiedOrderStatusEvidence(generation, sequence, observation)`. Generation and
sequence require exact positive ints, excluding bool. They are supplied data here,
not authenticated transport provenance; no sequence allocator or generation
migration exists. Later publication must obtain them from the exact transport.

The observation must be the exact PRE3 `_OrderStatusObservation` type. Its original
constructor validation is rerun, including exact presence/quality agreement. The
record owns independent observation, presence and Decimal copies. Validation does
not replace an already published record's nested observation. No SDK object,
descriptor, frame, receipt, account data, timestamp or authority field is retained.

`_source_identity()` canonicalizes exact typed primitives. It includes record type
and generation, every PRE3 value, protocol, field quality, sorted wire-supplied
presence and permId. Decimals contribute their sign, digits and exponent, without
arithmetic, normalization, rounding, repr or ambient-context dependence. Sequence
is excluded from content identity. `_duplicate_compressible()` requires identical
canonical content; different protocol, presence, quality, signed zero or numeric
representation prevents compression. A qualified record and its old-model view
are different record types and never compress together.

The same canonical helper can inspect existing sanitized records. This does not
change production `_evidence_identity()` or retroactively recover source details
already absent from old records.

## Required-fact compatibility view

`_legacy_projection()` returns a new unchanged `_OrderStatusEvidence`, or None for
a valid non-projectable source. Malformed records raise the fixed private error.
The required facts are order ID, client ID, recognized status, filled, remaining,
average fill price, last fill price and parent ID.

Every required value must be available and have SUPPLIED or LEGACY_DECODED quality.
UNAVAILABLE or LEGACY_AMBIGUOUS required fields prevent projection. Thus a usual
legacy decoded parent ID zero is conservatively non-projectable; a positively
supplied protobuf parent ID zero can be represented. Legacy Decimal quantity zero
remains a known decoded quantity. Unknown lifecycle status remains captured as
PRE3 UNKNOWN and is not projected as a known lifecycle fact. Existing old producer
records already normalized to UNKNOWN remain structurally valid.

Supplied protobuf zero and supplied INT32 maximum are retained exactly. An absent
protobuf ID is None, never the normalized decoder's maximum-ID fallback. Required
prices/quantities cannot be defaulted, coerced or rounded. Optional permId metadata
is not required by the old view, but remains preserved in the qualified record.

Here "lossless projection" means an exact view of all required legacy facts. It
does not mean the old model can represent the entire source. The qualified record
must remain retained; the view cannot replace it. Both use the same generation
and sequence and represent one occurrence, never two ledger events. No projection
establishes quantity ownership, terminality, currentness or durable source coverage.

## Mixed evidence and consumer support

`_validate_event()` accepts exactly the four existing sanitized callback models
and the new qualified type. Existing primitive rules reuse transport validators;
commission validation reruns its canonical producer validator. No raw getters,
subclass acceptance, coercion or external lookup is introduced.

The prospective `_LedgerConsumer` classification is:

| Record | Transport contract | Dispatch contract | Reconciliation contract | Durable persistence contract |
| --- | --- | --- | --- | --- |
| Existing open/status/execution | SUPPORTED_DIRECT | SUPPORTED_DIRECT | SUPPORTED_DIRECT | SUPPORTED_DIRECT |
| Existing commission | SUPPORTED_DIRECT | UNSUPPORTED_EXISTING_EVIDENCE | SUPPORTED_DIRECT | SUPPORTED_DIRECT |
| Projectable qualified status | SUPPORTED_DIRECT | SUPPORTED_VIA_LOSSLESS_PROJECTION | SUPPORTED_VIA_LOSSLESS_PROJECTION | UNSUPPORTED_QUALIFIED_STATUS |
| Non-projectable qualified status | SUPPORTED_DIRECT | UNSUPPORTED_QUALIFIED_STATUS | UNSUPPORTED_QUALIFIED_STATUS | UNSUPPORTED_QUALIFIED_STATUS |

These are data capabilities of this pure contract, not activated production routes.
Direct commission compatibility does not establish attribution, incorporation or
fee completeness. Dispatch has no commission-economic processing route. Supported
open/execution facts still need existing attempt/session/identity checks before any
actual consumer can process them.

Every qualified record is unsupported for durable-source coverage, even when its
required legacy facts can be projected. Today's journal status schema cannot retain
all source metadata. PRE6 creates no journal event or progress record. A future
durable integration must be separately qualified; it cannot call a value-only
projection proof of qualified-source persistence.

## Contiguous coverage and bounded runs

`_coverage()` takes an immutable expanded page, explicit consumer, generation,
requested cursor, informational through-watermark and optional target. The result
is frozen, slotted and opaque. It reports the highest contiguous supported sequence,
the first unsupported or missing sequence, the target and a coverage state:
COMPLETE, UNSUPPORTED, GAP, INCOMPLETE or GENERATION_MISMATCH.

Every page record is structurally validated, including a suffix beyond a blocker.
Pages require strictly increasing source sequences after the cursor and no event
beyond the watermark. Duplicates, decreasing sequences and malformed primitives
are rejected. A mismatch in generation cannot be adopted.

For supported 100, unsupported qualified 101 and supported 102, the supported
prefix ends at 100. It never reaches 102. A target ending before the blocker may
have complete coverage of that smaller requested range; it says nothing about the
remaining suffix. A fetched watermark is never acknowledgement. A missing page
suffix is INCOMPLETE, not proof that the underlying producer lost an event; an
observed discontinuity is GAP. Neither permits cursor advancement across it.

`_expand_runs()` validates immutable retained events and run ends, with contiguous
run starts beginning at one. It returns at most 128 logical occurrences in the
existing `_EvidenceBatch` value. Each occurrence has its own sequence and retains
its original evidence type and source values. No qualified-to-old conversion
occurs during expansion. Huge spans remain bounded; repeated fetches neither
acknowledge nor clear evidence.

All evidence types share the existing 128-run contract. There is no second queue
or qualified-only capacity. More than 128 supplied runs is structurally rejected.
A watermark beyond the represented prefix remains an incomplete suffix rather
than fabricated evidence. Future production overflow must retain the existing
permanent inhibition/reconciliation behavior; PRE6 mutates no safety state.

## Known order IDs and collision assessment

`_known_order_id()` returns an exact known order ID or None. Supplied protobuf zero
and maximum are known. Absent IDs and ambiguous legacy zero are unknown; permId
never substitutes. Known positive legacy IDs retain their exact values. Existing
old records retain their already-concrete ID contract. Commissions are explicitly
order-independent.

`_reservation_collision()` classifies a bounded page as COLLISION, UNRESOLVED or
NO_COLLISION_OBSERVED for an explicit generation and numeric ID. Current-generation
qualified evidence with unknown order ID is unresolved, not proof of non-collision.
A known exact collision dominates unresolved observations. Stale generations are
not adopted. NO_COLLISION_OBSERVED describes only the supplied page and cannot
authorize a reservation. No allocator, floor or reservation is modified.

## Side effects, errors and protected boundaries

The helpers use no network, SDK request, clock, thread, lock, SQLite, journal owner
or mutable transport state. Ordinary validation failures become only
`INVALID_QUALIFIED_STATUS_LEDGER_CONTRACT`, without raw payload text. KeyboardInterrupt,
SystemExit and GeneratorExit propagate as the original objects; there is no
acknowledgement or stateful cleanup to perform.

`_OrderStatusEvidence`, PRE3/PRE4/PRE5 production code, PRE1 account binding,
reconciliation, C3C4A replay/schema, C3C4B storage, C3C4D persistence and C3C5A/B are
unchanged. Historical owners continue consuming their existing durable evidence.
Public Broker APIs, public write blocking, wire policies, strategy/backtest code,
LIVE behavior and restart quarantine are unchanged.

PRE5 still has no production receiver. An unaccepted status handoff still fails
closed. PRE6 does not clear a fence, allocate a callback sequence, publish a ledger
record, advance any cursor or grant currentness, ownership, inventory, protection,
cancellation, recovery or trading authority.

## Review and next work

Cloud validation on Python 3.12.14 completed:

- PRE6 focused: 158 tests passed, covering pure records, source identity,
  projection, mixed support, coverage, compressed runs, collision, privacy and
  interruption behavior.
- Selected regressions: 2,676 tests run, 2,673 passed and three optional
  installed-SDK tests skipped because ibapi is unavailable in cloud.
- Full offline suite: 5,063 tests run, 5,059 passed and four skipped. The fourth
  skip is the real-Gateway integration opt-in, which remained disabled.
- An independent supported-prefix probe passed 2,188 consumer/target combinations.
- All 111 Python files compiled. Diff, whitespace, protected-file, credential,
  account-identifier, .env, public-surface and network/storage checks passed.

Existing offline persistence regressions use isolated temporary stores; PRE6
performs no storage operations. No dependency was installed or actual IBKR
connection/read/write performed. Actual Windows validation and independent review
remain required before production receiver/ledger integration.

Later publication must atomically preserve one occurrence/one sequence, retain the
complete qualified record, keep the PRE5 fence until successful capture/acceptance,
and integrate explicit unsupported coverage into each actual consumer. Qualified
durability, fresh reconciliation, authenticated continuity, current lifecycle and
inventory/disposition ownership remain separate work. Protection/resizing,
cancel/replace, disconnect/crash handling and reviewed recovery also remain blocked.
The first real PAPER order remains blocked.
