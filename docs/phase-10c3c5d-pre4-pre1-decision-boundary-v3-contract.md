# C3C5D-PRE4-PRE1 — initially inactive V3 decision-boundary durability contract

This phase defines private schema/model/codec/replay support only. It activates
no producer and supplies no decision-time status evaluator. Current fresh runtime
journals remain V2. The first real PAPER order remains blocked, Gateway Read-Only
remains required, restart quarantine remains mandatory, AccountMode remains UNKNOWN,
public snapshot mode remains None and LIVE remains unsupported.

Starting checkpoint: `0de207c65d7a1db706aa772e8f7d8274056a3f91`.
The user reported PRE3 independently reviewed and Windows validated on Python
3.12.10: 284 focused tests, 6,822 full-suite tests with 14 skips, 122 tracked Python
files compiled, clean diff checks/tree and synchronized main/origin/main.
PRE4-PRE1 requires its own independent review and actual Windows validation.

## Why original PRE4 blocked

PRE9 retains qualified status protocol, presence, quality, values and exact
session/generation/callback-sequence provenance. V2 replay proves durable callback
occurrence coverage. Neither provides a qualified mapping from an explicit
decision datetime to a stream frontier. Record order, replay time, receipt creation,
timestamp validation and eventually durable progress cannot manufacture that
relationship. Original PRE4 therefore remains blocked pending a real qualified
producer. This prerequisite defines the format of the future producer's declaration.

## Versions and preserved formats

| Logical schema | Exact format ID |
| --- | --- |
| 1 | `paper-execution-journal-v1` |
| 2 | `paper-execution-journal-v2` |
| 3 | `paper-execution-journal-v3` |

V1 and V2 retain their closed event unions, bytes, hashes, replay meanings and
source/economic models. V3 inherits unchanged V2 events and adds only
`_QualifiedDecisionBoundaryObserved`. V3-only payloads under V1/V2 records, version/
format mismatches and unsupported future versions fail closed. No migration,
rewrite, store adoption or automatic conversion is added. Older software rejects
unknown V3 headers/tags. Generic default headers remain V1 as before; the reviewed
production factory still explicitly creates V2, with unchanged version gates.

## Four-field boundary declaration

The private event is frozen, slotted and opaque:

| Field | Contract |
| --- | --- |
| session_id | Existing exact opaque session identifier |
| write_generation | Exact positive integer, at most 2^63 - 1 |
| decision_at | Explicit exact datetime in canonical fixed UTC, fold=0 |
| through_sequence | Exact integer in [0, 2^63 - 1] |

Bool is not an integer. The timestamp must use the exact fixed `timezone` type,
zero UTC offset, name `UTC` and fold zero. A fixed UTC clone reconstructed by the
existing codec is valid; singleton identity is unnecessary. Naive datetimes,
non-UTC offsets/names, ZoneInfo and arbitrary custom tzinfo are rejected. The
contract validates supplied canonical input; it does not obtain a clock, infer a
timestamp or silently convert receipt/append/monotonic time into decision time.

The declaration means: **a future qualified producer certified that at the
explicit decision time, this exact session/generation callback stream was complete
through this frontier under its producer contract**. Replay verifies the durable
prefix consistency of that declaration. It cannot independently authenticate
the real producer, prove that the datetime was the real decision point, establish
uptime or defeat self-consistent fabricated/rolled-back history. Canonical fixture
construction and successful replay are not production authority to certify it.

There is no account number, order/attempt identity, symbol, status, quantity,
position, risk/strategy state, SDK object, connection or credential in the event.
It is a stream boundary, not an order event.

## Required prefix coverage and ordering

At the boundary's position in journal chronology, the session must already be
registered and its write generation must match exactly. V3 reuses the existing
PRE9 `_V2CallbackCoverage` proof. The claimed frontier must equal its contiguous
frontier, and the actual occurrence high-water must equal that frontier too.
Every occurrence 1..N therefore already has its validated explanation, with no
known later durable occurrence beyond a hole. Future records cannot repair an
earlier invalid boundary. There is no loop over a claimed integer range.

The latest durable progress record must not declare a known observed/undurable
suffix above N. Progress need not already acknowledge every durably covered
occurrence: coverage and progress remain separate. Neither eventual persistence
nor a larger processed/through number can substitute for prefix coverage.

Zero is valid only with contiguous frontier/high-water zero and no known progress
suffix. No fake callback is required. The boundary itself is never a callback
occurrence, duplicate root, execution/commission fact or progress record. It
advances only ordinary journal record chronology, never callback coverage,
callback watermark or persistence acknowledgement.

Within each exact session/generation, decision datetimes strictly increase and
frontiers never decrease. All equal timestamps are rejected, including identical
frontiers; a unique per-stream decision instant prevents ambiguity. A later
decision time cannot claim an older frontier. Other sessions are independent:
their dates are not ordered as one global stream. Re-registering an identical
session does not reset ordering, and the existing contract rejects conflicting
generation registration for the same session identifier. Client-ID equality
never connects separate streams or creates restart/session continuity.

## Replay companion and codec

The original `_JournalReplayState` layout is unchanged. Normal replay returns
that same exact type for V1/V2 and V3. Private
`_replay_v3_decision_boundary_evidence(header, records)` accepts only complete V3
input and returns `_V3DecisionBoundaryReplayEvidence`: the ordinary replay state,
complete immutable record tuple and exact references to validated boundary records.
Companion construction independently replays the supplied history and rejects
omitted, replaced or inconsistent references/results. It is not a public API,
live receipt, authorization token or interval-selection evaluator.

The existing canonical JSON codec retains explicit tags, sorted field framing,
exact numeric/time representation, size limits, duplicate-key rejection and
decode/re-encode byte equality. One closed model registration is added. A V3-only
timestamp precheck rejects non-fixed UTC encodings before generic datetime decoding
could consult timezone files. Existing event encoding and V1/V2 decoding are
unchanged. Invalid tags/types/UTF-8, bool integers, corrupt dates, noncanonical
framing, duplicate/extra/missing fields, truncation and trailing data fail closed.

## SQLite and inactivity

SQLite DDL, tables/columns/triggers, application ID, user_version=1, storage format,
hash domain/framing, WAL/FULL settings, payload limit and every storage method are
unchanged. Explicit anonymous V3 fixture databases can be created/loaded using the
existing private storage mechanism. Existing V1/V2 databases reopen unchanged;
there is no migration or automatic header upgrade. Semantic corruption and invalid
append outcomes retain existing poison/quarantine behavior. BaseException identity
and SystemExit codes remain preserved by the existing pure/replay/codec seams.

No callback, dispatch, reconciliation, risk, timer, startup or restart code
constructs/appends a boundary. No runtime minting helper exists. Transport,
decoder/pairing/delivery/capture, PRE9 producer, PRE1/PRE2/PRE3, accounting,
lifecycle/remainder, reconciliation/recovery, public APIs, wire policies, enrollment,
authentication, strategy/backtest and active risk rules remain unchanged. Pure
contract/replay uses no network, SDK, clocks, filesystem, SQLite or live locks.
Separate storage tests use temporary databases only. No broker interaction occurs.

The event grants no order/attempt ownership, execution/absence proof, finality,
late-fill guarantee, current broker state, lifecycle/remainder, position/inventory,
accounting, protection, cancel/replace/exit, recovery, barrier clearing, risk/strategy
restoration, restart adoption or PAPER/LIVE authority.

## Required next phases

1. **C3C5D-PRE4-PRE2** — separately qualify and implement the sole production
   decision-boundary producer, with exact live provenance, explicit time semantics,
   locking, arrival/fence/capture checks, durable coverage and transactional rules.
2. **C3C5D-PRE4** — pure decision-time status interval evaluation using qualified
   V3 boundaries; no pre-first inference or unproven open-ended tail.
3. Combined PRE1/PRE2/PRE3/PRE4 evidence and explicit coverage reasoning.
4. Exact journal-attempt attribution and contradiction handling.
5. Reviewed incorporation of newly discovered executions.
6. Lifecycle/remainder interpretation and decision-time currentness qualification.
7. Inventory/disposition ownership.
8. Protection/resizing and cancel/replace/exit order management.
9. Session continuity/retirement, recovery/audit application and risk/strategy restoration.
10. First PAPER authorization only after separately reviewed prerequisites.

Independent review and actual Windows validation of this inactive contract are
required first, using Python 3.12.10, ibapi 10.50.2 and protobuf 5.29.5 with real
Gateway integration opt-in disabled. Cloud tests are not Windows qualification.

## Cloud validation

The new focused contract suite passed 195 tests. Selected regressions ran 3,826
tests, OK with three optional installed-SDK skips. Full offline discovery ran
7,017 tests, OK with four skips (three optional installed-SDK checks and disabled
real Gateway integration). All 123 Python files compiled. V1/V2 canonical-byte and
digest goldens, SQLite AST/schema/version/hash checks, six-file scope, whitespace,
secret/account, `.env`/credential, public-export/API, network/I/O purity, producer
inactivity and protected-file scans passed. Production creation remains explicitly
V2. No dependency installation, real IBKR connection, broker operation or Gateway
change occurred. These are cloud results; PRE4-PRE1 independent review and actual
Windows validation remain required.
