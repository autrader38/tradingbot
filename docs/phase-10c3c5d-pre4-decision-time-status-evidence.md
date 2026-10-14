# C3C5D-PRE4 — pure qualified decision-time status evidence

This phase adds private pure evaluation of historical qualified V3 evidence only.
The first real PAPER order remains blocked. Gateway Read-Only remains required,
restart quarantine remains mandatory, AccountMode remains UNKNOWN, public snapshot
mode remains None and LIVE remains unsupported. PRE4 requires independent review
and actual Windows validation before any subsequent lifecycle foundation.

Starting checkpoint: `247ede4c6ce1562bbda69f9c69e087d9b679680c`.
The user reported PRE4-PRE2 independently reviewed and Windows validated on Python
3.12.10: 179 focused tests, 7,196 full-suite tests with 14 skips, 124 tracked Python
files compiled, clean diff checks/tree/index and synchronized main/origin/main.

## Original blocker and the qualified decision boundary

PRE9's callback session/generation/sequence and durable coverage alone could not
map an arbitrary decision datetime into the callback stream. Original PRE4 blocked.
PRE4-PRE1 defined the V3 four-field boundary declaration and strict replay-prefix
coverage. PRE4-PRE2 activated fresh V3 journals and the sole authenticated producer,
which internally captures UTC time D and an already-durable frontier N under arrival
serialization. This relates D to the local received stream, not broker/exchange
event time or absence of delayed/in-flight events.

PRE4 consumes that qualified relationship. It never supplies a clock, captures a
new boundary, interpolates between boundaries or assigns wall-clock times to status
callbacks. No wall-clock status interval is claimed.

## Exact inputs and replay proof

`paper_decision_status_evidence._evaluate_decision_status(evidence, boundary, identity)`
accepts only the exact `_V3DecisionBoundaryReplayEvidence`, exact boundary
`_JournalRecord` reference within its `boundary_records`, and exact private
`_QualifiedStatusIdentity`. Schema 3 and `paper-execution-journal-v3` are mandatory.
The existing companion/replay validators are rerun, including registered session,
generation, callback explanations, contiguous frontier, actual high-water, progress
suffix and boundary ordering checks. Future records cannot repair an earlier gap.

An equal reconstructed boundary, boundary from another replay/journal, datetime,
boundary payload alone, V1/V2 replay, arbitrary record tuple, dictionary, SDK object,
memory ledger event or live collection/producer receipt is insufficient input.
Invalid inputs raise the fixed sanitized `INVALID_DECISION_STATUS_EVIDENCE` error.
No nearest/newest-before-time selection or fallback to UNKNOWN is performed.

Canonical test fixtures exercise syntax and replay consistency; they do not certify
real runtime provenance. As with the reviewed companion contract, pure replay
cannot independently authenticate a self-consistent fabricated history. Production
evidence must come from the reviewed durable load/producer path, whose semantics
remain unchanged. Evaluation itself performs no storage or live-owner operation.

## Source-qualified identity

The frozen/slotted/opaque selector contains exact session ID, write generation,
order ID and client ID, with an optional permId qualifier. The primary numeric pair
is mandatory; permId alone, orderRef, symbol, quantity, price, timing, strategy and
collection similarity cannot replace it. Integer validation rejects bool, coercion,
negative values and values beyond existing ID/frontier bounds.

Known identity uses PRE6's existing fact rule: SUPPLIED or LEGACY_DECODED quality.
Explicit supplied protobuf zero/maxima remain known exact values. Absent protobuf
IDs and ambiguous legacy zeros remain unknown. Unknown identities are not attributed
through adjacency or a retained earlier value. A no-status result is absence of a
fully qualified match, not proof that an order/execution does not exist.

LEGACY and PROTOBUF can contribute only their already-known matching numeric facts.
Neither is ranked above the other. The entire selected durable observation retains
its original protocol, per-field quality and wire presence; no source facts are
combined or projected. Client ID is the observed order's identity dimension, not a
claim that it equals the registered placing client or belongs to this bot.

Incompatible known permId values for the same numeric pair produce AMBIGUOUS_IDENTITY,
even if the request supplies one preferred permId. Supplied zero is an explicit
fact, not absence. An optional permId request requires it on the latest observation;
an older matching value cannot fill a later missing/ambiguous value. When permId
is not requested, absence remains preserved metadata rather than inferred identity.
No global permId ownership or cross-order adoption rule is introduced.

## Latest status and frozen sequence-domain applicability

After full input qualification, semantic evaluation and the returned proof use only
the record prefix ending at the exact selected boundary. Within that prefix, only
`_QualifiedOrderStatusObserved` occurrences in the exact session/generation with
source sequence <= N and known matching numeric identity qualify. The greatest
callback source sequence S wins. Journal record sequence and payload position do
not determine callback ordering; ordinary `_OrderStatusObserved`, open orders,
executions, commissions and completed orders cannot substitute for qualified status.

The user explicitly selected **frozen boundary-prefix results** to resolve the tension
between historical stability and future interval metadata. Therefore later records
are not used even to enrich a next-status sequence. `next_status_sequence` is always
unknown in the boundary-specific result. This means no known closing occurrence at
that boundary, never infinity or applicability after N.

In sequence-domain terminology, successive same-identity observations S and T
describe latest-observation selection over [S,T). At a qualified boundary N=S the
S occurrence is selected; at N=T-1 it remains selected; at N=T the T occurrence is
selected. PRE4 reports only each boundary's proven latest-through-N evidence. It
does not retrospectively add T to an earlier result, produce datetime interval
start/end values or extend a final observation beyond the certified frontier.

Valid later callbacks/boundaries, including new conflicting identities, cannot
change the entire earlier result. Increasing-time boundaries at the same frontier
select identical occurrence evidence; only boundary/time/proof metadata changes.
The supplied full companion must still be valid: corruption anywhere in that input
fails qualification rather than being ignored as a malformed future suffix.

## Result, limits and purity

`_DecisionStatusEvidence` is private, frozen, slotted and opaque. It contains only
the validated boundary-ending replay prefix, selector, exact boundary reference,
optional exact selected record, closed outcome and fixed limitations. Construction
reruns validation and selection, rejecting an older/equal substituted record or
status attached to an unsupported outcome. Decision time is the boundary's exact
fixed-UTC value; selected observation and Decimal sign/digits/exponent are preserved.

Outcomes are SUPPORTED_AT_DECISION_BOUNDARY, NO_QUALIFIED_STATUS_THROUGH_BOUNDARY and
AMBIGUOUS_IDENTITY. Only supported results expose a selected status observation.
An absent status/quantity/price field in that observation remains absent; UNKNOWN
stays the existing source value. Support does not certify complete economic values.

The strongest claim is: for the requested qualified identity, occurrence S was the
latest qualified match present in the durably covered local stream through the
exact decision boundary (D,N). It proves no broker currentness, exchange event time,
execution/absence, no late fill, finality, lifecycle/remainder, inventory, order/
attempt/position ownership, accounting incorporation, management, recovery, barrier
clearing, restart adoption, PAPER/LIVE or risk/strategy-restoration authority.

There is no network, SDK request, clock read, filesystem I/O, SQLite, journal append,
callback/transport mutation, live lock or receipt consumption. Inputs are not
reordered or modified. Invalid evidence fails closed; KeyboardInterrupt, SystemExit
including code, and GeneratorExit propagate their original objects. Pure evaluation
has no mutable safety state to secure. V1/V2/V3 formats, codec/replay, SQLite schema/
version/hash, PRE4-PRE2 producer, PRE9, PRE1/PRE2/PRE3, accounting, lifecycle,
reconciliation/recovery, public APIs, wire policies, strategy and risk are unchanged.

## Validation and later foundations

Focused tests use anonymous canonical fixtures with network, file, SQLite, clocks,
journal append and authentication blocked during evaluation. They cover all 512
protobuf presence subsets, supplied zero/maxima, legacy ambiguity, source ordering,
coverage/identity failures, frozen historical results, source representation and
interruption identity. A separate anonymous fake-SDK/temporary-SQLite regression
links actual PRE4-PRE2 producer records to pure evaluation without a live receipt,
then checks evaluation after later records and owner closure. No real broker call
is needed or authorized. Cloud results do not establish Windows qualification.

Cloud validation on Python 3.12.14: 164 focused PRE4 tests passed. Selected
regressions ran 4,231 tests, OK with three optional installed-SDK skips, including
all 179 PRE4-PRE2 and 195 PRE4-PRE1 tests. Full offline discovery ran 7,360 tests,
OK with four skips (three installed-SDK checks and disabled real Gateway integration).
All 126 Python files compiled with caches outside the checkout. Diff/whitespace,
secret/account, `.env`/credential, public export/API, network/I/O/clock purity,
authority/scope and V1/V2/V3 compatibility scans passed. The six intended files
are the only changes; all 55 existing tracked broker/backtest production files
remain byte-for-byte unchanged. The prior V3 test permits the new read-only consumer
and additionally proves only the original owner constructs boundaries. No dependency
installation, real IBKR connection, broker operation or Gateway change occurred.
Actual Windows validation and independent review remain required for PRE4.

After independent review and actual Windows validation: combined PRE1/PRE2/PRE3/PRE4
evidence; cross-source coverage reasoning; exact attempt attribution; contradiction
policy; reviewed new execution incorporation; decision-time lifecycle/remainder;
inventory/disposition; protective ownership/resizing; cancel/replace/exits; session
continuity/retirement; recovery/risk/strategy restoration; separately reviewed first
PAPER authorization. This phase implements none of those foundations.
