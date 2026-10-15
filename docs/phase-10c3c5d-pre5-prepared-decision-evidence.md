# C3C5D-PRE5 — prepared collection and durable decision evidence binding

PRE5 binds provenance only. The first real PAPER order remains blocked. Gateway
Read-Only remains required, restart quarantine remains mandatory, AccountMode
remains UNKNOWN, public snapshot account mode remains None and LIVE unsupported.
No currentness, finality, lifecycle/remainder, order/attempt/position ownership,
execution incorporation, accounting, inventory, protection, cancel/replace/exit,
recovery, barrier clearing, risk/strategy restoration or trading authority is added.

Starting checkpoint: `a8115391a9434accb20ede25477226b9920effcc`.
The user reported PRE5-PRE1 independently reviewed and actually Windows-qualified
on Python 3.12.10: 207 focused tests, 7,567 full-suite tests with 14 skips, 128 tracked
Python files compiled, clean tree/index and synchronized HEAD/main/origin/remote.
PRE5 requires its own independent review and actual Windows validation before
the next separately authorized foundation. Cloud validation is not Windows proof.

## Preparation remains the live authority

PRE1/PRE2/PRE3 collections are separate intervals, not an atomic snapshot.
PRE5-PRE1 consumed their exact receipts once, before the later decision-boundary
append could invalidate the original collection consumption boundaries. PRE5
uses that exact preparation token; it does not consume source receipts again,
reset flags, prepare a replacement, renew authentication or adopt a restart.

`paper_prepared_decision_evidence._create_prepared_decision_evidence_owner(preparation_owner)`
accepts only the exact registered `_FreshCollectionPreparationOwner`, which must
already have published its token. The original `_require_prepared(token)` path is
used throughout. The new private `_PreparedDecisionEvidenceOwner` captures the
exact preparation/journal/coordinator/control/read/write/header/store/session/thread
association and prepared history tuple. It retains the original bound issuer
validator and journal load methods. Substituted methods or reconstructed lifetime
objects cannot replace them.

The owner is lazily registered once in the original read transport's private
`_prepared_decision_evidence_owner` slot. No existing transport/callback module is
modified. The factory cannot accept a caller journal, store, replay, record tuple,
header, client, account or receipt as alternate authority.

## Exact selector and original durable read

`owner._evaluate(token, boundary_record_sequence, identity)` accepts the original
token, an exact positive integer journal-record sequence bounded by 2^63-1, and
the existing exact `_QualifiedStatusIdentity`. Bool, float, Decimal, string, zero,
negative, oversized, missing and non-boundary selectors fail. A timestamp, payload,
record object or arbitrary PRE4 result/companion cannot substitute. No newest,
closest, earliest or largest-frontier lookup exists.

The original journal owner's `_load()` is the sole storage read. Existing SQLite
header/schema/framing/hash/codec/replay checks remain active. PRE5 independently
requires the exact `_LoadedJournal`, canonical header agreement with the live
owner, and complete record values equal to that owner's current verified history.
The prefix must agree canonically with the exact preparation history; the issuer
continues checking the runtime prefix's original entry identities. A selected
boundary must be **strictly after** that prefix. A boundary already in the prepared
history is invalid, regardless of its time.

The existing `_replay_v3_decision_boundary_evidence` constructs/revalidates full V3
coverage evidence from the loaded header and records. Its replay must agree with
the loaded replay. Future records cannot repair an earlier gap, and corruption
anywhere in the current durable history fails even for an earlier selector.
PRE5 never weakens replay or supplies arbitrary reconstructed replay as runtime
authority.

The selected record must be the exact loaded boundary reference in the companion's
`boundary_records`. `_evaluate_decision_status(companion, boundary, identity)` is
then invoked directly. PRE5 checks the returned exact PRE4 model, boundary/identity
references and boundary-ending record prefix. It neither copies selection logic
nor projects full source status into weaker legacy evidence.

## Shared provenance and strict historical deadline

The token and loaded header must agree canonically, without coercion, on journal ID
and opaque account binding. Account-binding object identity is not required across
reviewed receipt reconstruction; original live issuer identity remains mandatory.

Replay must contain the prepared session with matching write generation and placing
client ID, canonically matching the original registered session. The boundary and
PRE4 identity must share that session and generation. The observed status identity's
client ID **need not equal** the placing client ID. It is a source identity fact,
never proof of bot or attempt ownership.

Read generation/client/configuration/transport provenance remains the live
PRE5-PRE1 issuer's proof. No durable PRE4 read-generation fact is fabricated.
Disconnect, closed lifetime, substituted clients/configuration/account association,
generation changes or altered prepared provenance are rejected through that issuer.

`boundary.decision_at < token.authentication_deadline` is mandatory and exact.
Equality and later time fail. Both use their existing qualified datetime contracts;
no tolerance, deadline renewal or current clock is introduced. A currently expired
deadline does not rewrite this historical comparison while the original lifetime
is still valid. Source collection IDs/markers/sequences are never interpreted as
wall-clock times. Prepared-prefix-before-boundary journal ordering is the ordering
proof; no economic collection-time relationship is invented.

## Result, limitations and issuer revalidation

`_PreparedDecisionStatusEvidence` is frozen, slotted, private, opaque and memory-only.
Its nine fields are `prepared`, `status`, `boundary_record_sequence`, `journal_id`,
`account_binding`, `session_id`, `write_generation`, `authentication_deadline` and
fixed `limitations`. It retains the exact preparation token and exact PRE4 result,
without flattening or merging either source domain.

All PRE4 outcomes are valid combined provenance: SUPPORTED_AT_DECISION_BOUNDARY,
NO_QUALIFIED_STATUS_THROUGH_BOUNDARY and AMBIGUOUS_IDENTITY. No outcome becomes a
trading decision or resolves collection observations. Empty collections remain
limited non-observation; completed-order evidence is not execution evidence.

The fixed PRE5 limitation tuple is:

- INDEPENDENT_COLLECTION_INTERVALS
- NO_CROSS_SOURCE_ATOMICITY
- NO_CROSS_SOURCE_CORRELATION
- NO_CROSS_SOURCE_CONTRADICTION_POLICY
- NO_ORDER_OR_ATTEMPT_OWNERSHIP
- NO_EXECUTION_ABSENCE_PROOF
- NO_DELAYED_CALLBACK_OR_LATE_FILL_GUARANTEE
- NO_BROKER_CURRENTNESS
- NO_FINALITY
- NO_LIFECYCLE_OR_REMAINDER
- NO_INVENTORY_OR_POSITION
- NO_ACCOUNTING_INCORPORATION
- NO_MANAGEMENT_RECOVERY_OR_TRADING_AUTHORITY
- NOT_RESTART_ADOPTABLE

Every PRE1/PRE2/PRE3/PRE5-PRE1/PRE4 limitation remains available through the original
source objects. No source ranking, observation correlation, contradiction policy,
absence inference, late-fill proof or accounting/lifecycle interpretation is added.

The owner retains a bounded **latest successful publication** slot. A new evaluation
supersedes the earlier issued result for exact-owner validation, without changing
the caller-held earlier immutable historical value. It does not keep an unbounded
result registry. `_require_evidence(result)` requires the exact latest object,
original token/reference graph and independent canonical value snapshots, then
reloads the original journal and reuses PRE4 to revalidate the same exact selector.
Equal reconstructed/pickled results, changed identity/status/provenance/reference
nodes or foreign runtime results cannot gain issuer provenance. Model constructor
validity alone is never production qualification.

Later valid callbacks/boundaries do not enrich the earlier PRE4 prefix or its
unknown next-status sequence. The currently issued earlier result can still
validate against a valid later history until superseded by another PRE5 publication.
After restart, the live preparation issuer/token are gone and cannot be recreated
from journal bytes. No combined-result journal event or codec registration exists.

## Storage, locks and failures

Only the exact creator thread evaluates or validates results; wrong-thread calls
are rejected before any coordinator/control/capture lock acquisition. Live generation
and client-ID checks preserve primitive types, rejecting bool/integer aliases.
The owner serializes
its read/publish operation under coordinator -> control. Brief live checks follow
read lifecycle -> read condition -> write dispatch -> arrival, preserving the
original lock direction. Those lower locks are released before SQLite. The existing
callback I/O boundary prohibits active dispatch; active drain/producer and reentrant
combination are likewise rejected. Callback threads remain unchanged and never wait
on this owner for persistence. New memory callbacks do not rewrite historical proof.

PRE5 adds no store writer, append, migration, callback drain or producer invocation.
The reviewed load path may perform its existing read transaction/configuration and
hash checks. No journal data/schema write, progress/cursor mutation or repair occurs.
Existing storage poison/close behavior on a failed load remains in force; PRE5 does
not invent another quarantine or poisoning mechanism.

Ordinary validation/storage/PRE4 errors become the fixed sanitized
`PREPARED_DECISION_EVIDENCE_UNAVAILABLE`. KeyboardInterrupt, SystemExit including code
and GeneratorExit preserve their original objects. Entered failures clear only local
publication state and release the read guard through direct assignments; no fallible
cleanup hook can replace the interruption. No usable partial result is published.
The read-only owner can be used again after an input/non-storage failure when the
original issuer/store remain valid. No source receipt is unconsumed or retried.

V1/V2/V3 bytes, replay semantics, SQLite schema/version/hash, PRE9, PRE1/PRE2/PRE3,
PRE4-PRE1/PRE2/PRE4, PRE5-PRE1, dispatch/callbacks, accounting/lifecycle, commissions,
reconciliation/recovery, public APIs, wire policy, risk and strategy/backtest are
unchanged. The only prior-test adjustment adds this read-only reader to the V3
reference allowlist; its AST check still requires the original journal owner to
be the sole decision-boundary constructor.

## Strongest claim and validation

Success proves only that the exact prepared collections belong to the same
journal/account/session/write-generation lifetime as the selected durable boundary,
that the boundary follows the prepared history, and that its internally captured
decision time preceded the original deadline. PRE4's qualified evidence through
that frontier is attached unchanged. It proves no simultaneous broker state or
ownership/currentness/finality/management permission.

Tests use anonymous fake SDKs and temporary SQLite. Collections/preparation and
the original boundary producer run separately in setup; combination itself forbids
broker requests/orders, writes, callback drain, source consumption/authentication
and current clocks. Synthetic canonical boundary fixtures used to reject equal/
expired deadlines test syntax/replay relations only; they are not runtime producer
certification. Corruption fixtures restore exact schema definitions before exercising
the original hash/replay read. Real Gateway integration remains disabled.

After independent review and actual Windows validation on Python 3.12.10 with
ibapi 10.50.2/protobuf 5.29.5: separately qualify cross-source coverage/correlation
and contradiction policy, exact attempt attribution, execution incorporation,
lifecycle/remainder, inventory/dispositions, protection/resizing, cancel/replace/
exits, continuity/retirement and recovery/risk/strategy restoration. First PAPER
authorization remains a separately reviewed future step. None is implemented here.

## Cloud validation

On Python 3.12.14, 151 focused PRE5 tests passed. Selected regressions ran 4,602
tests, OK with three optional installed-SDK skips, including PRE5-PRE1 207,
PRE4 164, PRE4-PRE2 179, PRE4-PRE1 195 and PRE1/PRE2/PRE3 180/229/284.
Final full offline discovery ran 7,718 tests, OK with four skips (three optional
installed-SDK checks and disabled real Gateway integration). All 130 Python files
compiled with caches outside the checkout. The earlier discovery attempts were
stopped before completion while final exact-type and pre-lock thread checks were
added; only the complete final run qualifies this implementation.

Whitespace/diff, secret/account, `.env`, public API/export, broker-operation,
journal-write/durability, current-clock, authority/scope and V1/V2/V3 compatibility
scans passed. The six intended files are the only changes. All 57 existing tracked
broker/backtest production files remain byte-for-byte unchanged. No dependency
installation, real IBKR connection, broker query/order operation or Gateway
setting change occurred. PRE5 still requires independent review and actual Windows
qualification; these cloud results do not establish Windows or Gateway behavior.
