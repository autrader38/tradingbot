# C3C5D-PRE7 — exact local attempt attribution evidence

PRE7 adds private numeric-pair journal-attempt attribution only. It grants no
management ownership, lifecycle/remainder, filled quantity, currentness, finality,
execution completeness/absence, inventory/position, accounting incorporation,
protective ownership, cancel/replace/exit, recovery, risk/strategy restoration,
barrier clearing or trading authority. Gateway Read-Only remains required; restart
quarantine remains mandatory, AccountMode UNKNOWN, public snapshot mode None and
LIVE unsupported. The first real PAPER order remains blocked.

Starting checkpoint: `86f8e6381671c6e86019cca454887e5ebaac0652`. The user reported
PRE6 independently reviewed and actually Windows-qualified on Python 3.12.10:
168 focused tests, 7,886 full-suite tests with 14 skips, 132 tracked Python files
compiled, clean tree/index and synchronized local/origin/remote main. PRE7 requires
its own independent review and actual Windows qualification before execution
incorporation/lifecycle work. Cloud tests do not establish Windows qualification.

## Exact issuer and frozen input

`paper_attempt_attribution_evidence._create_attempt_attribution_evidence_owner(cross_source_owner)`
accepts only the exact registered `_CrossSourceIdentityEvidenceOwner` and validates
its current publication through the original `_require_evidence()`. The new
`_AttemptAttributionEvidenceOwner` captures that exact result, original bound
validator, creator thread/association, canonical values and immutable reference
graph. It registers once in the original read transport's private
`_attempt_attribution_evidence_owner` slot. No existing production source changes.

`owner._evaluate(pre6_result)` accepts only that exact root, with no caller identity,
journal, replay, account, reservation, attempt, client_order_id or source owner.
The original PRE6 issuer is called before and after semantic analysis. This retains
PRE6/PRE5's reviewed durable hash/codec/replay and exact lifetime checks without
another load path. Only the original creator thread may construct/evaluate/validate;
wrong-thread calls fail before live locks or storage. PRE7 introduces no locks.
Inherited coordinator -> control -> read lifecycle -> read condition -> write
dispatch -> arrival ordering is unchanged, with lower locks released before the
existing owner-thread durable read. No callback work is introduced.

Semantic selection uses **only** `pre6.pre5.status.prefix.replay`, already frozen
at the exact selected PRE4 boundary. Current journal state is not a candidate
source. Later valid attempts, reservations, callbacks or boundaries cannot enrich
the selected prefix or historical attribution. Original PRE6/PRE5 validation may
verify later durable history but does not supply it to PRE7 selection.

## Attribution chain and conservative precedence

The target is the exact `pre6.identity` numeric `(order_id, client_id)`, scoped to
its session and write generation. Optional permId remains source evidence only;
the local journal has no permId reservation field.

The closed outcome enum follows this conservative selection order:

1. `IDENTITY_EVIDENCE_BLOCKED`: PRE6 DURABLE_IDENTITY_AMBIGUOUS or
   IDENTITY_CONTRADICTION. Local reservations cannot repair broker identity conflict.
2. `NO_BROKER_NUMERIC_PAIR_SUPPORT`: neither supported PRE4 status nor any fresh
   PRE1/PRE2/PRE3 exact numeric-pair candidate. Caller-selected numbers alone cannot
   create attribution, even if a local reservation exists.
3. `LOCAL_SESSION_MISMATCH`, `LOCAL_GENERATION_MISMATCH`, or
   `LOCAL_CLIENT_MISMATCH`: the effective durable session must match the target's
   session, generation and observed client ID, in that order. Missing/duplicate or
   malformed session evidence may already fail original replay/issuer qualification.
4. `NO_LOCAL_NUMERIC_RESERVATION`: the frozen `reserved_order_ids` has no exact
   numeric ID. Duplicate/inconsistent mappings are invalid evidence, not a choice.
5. Using that original `(order_id, client_order_id)` tuple, select exactly one
   original `_AttemptReplayState` by `consumed.client_order_id`. Its order ID must
   agree; its consumed session must match the target, otherwise
   `LOCAL_SESSION_MISMATCH`. Missing/duplicate/inconsistent reserved attempts fail
   as malformed evidence rather than ordinary absence.
6. `LOCAL_ATTEMPT_NOT_INVOCATION_ENABLED`: the original attempt's
   `invocation_may_have_occurred` is not True. A consumed/reserved attempt, including
   explicit closure before invocation, does not establish broker-attempt attribution.
7. `ATTRIBUTED_TO_EXACT_LOCAL_ATTEMPT`: all preceding requirements hold.

Existing replay qualification is stronger than some defensive negative branches:
PRE4/PRE5 already require a registered matching boundary generation, so a tampered
generation/session may be rejected before a classified PRE7 negative can exist.
PRE7 preserves that rejection and does not fabricate a weaker valid root for tests.
Exact integer/bool validation remains inherited from the original qualified models.
Identical repeated session registrations remain idempotent under original replay;
PRE7 uses its one effective frozen registration reference.

One supported broker path is sufficient; three-source agreement is unnecessary.
A fresh numeric-pair candidate with unresolved requested permId can support numeric
attribution when PRE6 is not identity-blocked. Legacy PRE3 without qualified numeric
IDs cannot support it, even if permId/orderRef/symbol/contract match. No source
absence is turned into a contradiction or no-execution proof.

The registered durable session's client ID must equal the broker identity client.
This is a reservation lifetime chain, not the earlier invalid shortcut of treating
the preparation token's placing-client field as ownership. OrderRef, permId alone,
symbol, contract, side, quantity, price, strategy and timestamps are not lookup keys.

**SDK normal return is not required.** Invocation possibility without normal return,
including explicit SDK exception/reconciliation evidence, can attribute the pair
without resolving uncertainty. No old strong-open/broker-observed/ordinary callback
attribution is required. No status, fill, execution, commission or economics field
is used to interpret the attempt. Existing structural validators may still check
those retained models; no independent economic algorithm is introduced.

## Result, validation and failure

`_AttemptAttributionEvidence` is frozen, slotted, opaque and memory-only, with fields
`pre6`, `identity`, `session`, `reservation`, `attempt`, `outcome`, `limitations`.
References are the exact original root/identity/frozen registration/reservation
tuple/attempt, not equal reconstructions. Negatives can retain the actual chain
nodes reached before failure without granting attribution. The convenience property
`client_order_id` is derived from `attempt.consumed.client_order_id` **only** for
ATTRIBUTED_TO_EXACT_LOCAL_ATTEMPT; negatives expose None through this property.
There is no independently trusted duplicate identifier field.

Structural construction is not issuer authority. `owner._require_evidence(result)`
requires its exact latest published object, original root and bound PRE6 method,
canonical values and exact immutable reference graph, then revalidates through the
original PRE6 issuer. Reconstructed/pickled/foreign results, replaced sessions,
reservations, attempts, correlations, prefix or identity fail closed. Superseding
the captured PRE6 publication invalidates this owner's root; it cannot retarget.
Repeated evaluation of the same valid root is deterministic and only replaces one
bounded latest-result slot. Old caller-held historical values remain immutable.

Ordinary failures yield only ATTEMPT_ATTRIBUTION_EVIDENCE_UNAVAILABLE. Entered
publication/revalidation failures clear local result metadata and release the busy
guard via direct assignments. No partial result or new broker poisoning is created.
KeyboardInterrupt, SystemExit with its exact code, and GeneratorExit preserve the
exact original object. A valid original lifetime permits repeat evaluation after
non-storage/input failure. Original PRE6/PRE5 storage failure behavior is unchanged.

The fixed limitations are:

- ATTRIBUTION_IS_NUMERIC_PAIR_ONLY
- LOCAL_ATTRIBUTION_DOES_NOT_ESTABLISH_PERMID_OWNERSHIP
- NO_SDK_ACCEPTANCE_PROOF
- NO_BROKER_ACCEPTANCE_PROOF
- NO_EXCHANGE_ACCEPTANCE_PROOF
- INDEPENDENT_COLLECTION_INTERVALS
- NO_CROSS_SOURCE_ATOMICITY
- NO_EXECUTION_ABSENCE_PROOF
- NO_DELAYED_CALLBACK_OR_LATE_FILL_GUARANTEE
- NO_BROKER_CURRENTNESS
- NO_FINALITY
- NO_LIFECYCLE_OR_REMAINDER
- NO_INVENTORY_OR_POSITION
- NO_ACCOUNTING_INCORPORATION
- NO_PROTECTIVE_OWNERSHIP
- NO_CANCEL_REPLACE_EXIT_AUTHORITY
- NO_MANAGEMENT_RECOVERY_OR_TRADING_AUTHORITY
- NOT_RESTART_ADOPTABLE

All prior source limitations remain available through the exact PRE6 root. Restart
destroys issuer authority; there is no event, codec registration, migration or
durable PRE7 representation. V1/V2/V3, SQLite schema/version/hash, callback capture,
dispatch, accounting/lifecycle, reconciliation/recovery, risk/strategy, public APIs
and wire policy are unchanged. No clock, request, reconnect, renewal, source
collection/consumption, boundary production, append, drain or barrier mutation is
performed. The original inherited durable read is the sole permitted I/O.

## Offline validation and remaining foundations

Tests use anonymous deterministic SDKs with real network/DNS blocked. Most local
attempt histories are canonical temporary-journal syntax/replay fixtures; they do
not certify real dispatch. A separate positive fixture uses the original bridge
with an anonymous fake placeOrder before collection/preparation, then evaluates
attribution with operations forbidden. This proves the existing runtime chain
without a broker operation. SDK-return absence, explicit uncertainty, pre-invocation
closure, exact broker-support matrix, session/client mismatch, multiple attempts,
frozen later history, reconstruction/tampering and interruptions are covered.
SQLite authorizer tests prohibit data/schema writes during evaluation. Source
consumer method replacement is rejected by the original upstream identity guard;
that invariant is not loosened to accommodate mocks.

After independent review and actual Windows qualification on Python 3.12.10 with
ibapi 10.50.2/protobuf 5.29.5 and real Gateway opt-in disabled, separately review
execution incorporation and lifecycle/remainder. Inventory/disposition, protective
ownership/resizing, cancel/replace/exits, session continuity/retirement, recovery/
risk/strategy restoration and eventual first PAPER authorization remain later.

## Cloud validation

On Python 3.12.14, the final focused PRE7 run passed all 102 tests. Selected
regressions ran 5,725 tests, OK with three optional installed-SDK skips, including
PRE6 168, PRE5 151, PRE5-PRE1 207, PRE4 164, PRE4-PRE2 179, PRE4-PRE1 195,
PRE1/PRE2/PRE3 180/229/284, journal/dispatch/reservation, PRE9/V2 and
accounting/lifecycle regressions. Full offline discovery ran 7,988 tests, OK with
four skips (three optional installed-SDK checks and disabled real Gateway
integration). All 134 Python files compiled with caches outside the checkout.

Diff/whitespace, secret/account, `.env`/credential, private API/export,
broker-operation, journal/SQLite mutation, current-clock, forbidden orderRef/
symbol/permId-only attribution, lifecycle/arithmetic, authority/scope and V1/V2/V3
compatibility scans passed. The five intended files are the only changes; all 59
previously tracked broker/backtest production files remain byte-for-byte unchanged.
No dependency installation, real IBKR connection, broker request/order operation
or Gateway change occurred. PRE7 still requires independent review and actual
Windows qualification; these cloud results do not establish Windows or Gateway
behavior.
