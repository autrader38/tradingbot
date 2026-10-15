# C3C5D-PRE6 — private cross-source identity evidence

PRE6 correlates retained source identity facts only. It adds no bot/attempt
ownership, current broker state, lifecycle/remainder, execution completeness,
inventory/position, accounting, protection, cancel/replace/exit, recovery, barrier
clearing, risk/strategy restoration or trading authority. The first real PAPER
order remains blocked. Gateway Read-Only remains required; restart quarantine
remains mandatory, AccountMode UNKNOWN, public snapshot mode None and LIVE
unsupported.

Starting checkpoint: `060a83421a284ffcbf7b951964a1b7e36d1c244a`. Before PRE6 the
user reported PRE5 independently reviewed and actually Windows-qualified on
Python 3.12.10: 151 focused tests, 7,718 full-suite tests with 14 skips, 130 tracked
Python files compiled and synchronized clean main/origin/remote. PRE6 requires
its own independent review and actual Windows qualification before exact attempt
attribution. Cloud results do not establish Windows qualification.

## Exact PRE5 issuer and lifetime

`paper_cross_source_identity_evidence._create_cross_source_identity_evidence_owner(prepared_decision_owner)`
accepts only the exact registered `_PreparedDecisionEvidenceOwner`. It first calls
that original owner's `_require_evidence()` on its currently published PRE5 result.
No caller journal, replay, receipt, account, source owner or alternate identity is
accepted. A private `_CrossSourceIdentityEvidenceOwner` captures that exact root,
original bound validator, association, canonical values and immutable reference
graph, and is registered once in the original read transport's private
`_cross_source_identity_evidence_owner` slot. No existing production module changes.

`owner._evaluate(pre5_result)` accepts only that exact original root. The target is
always `pre5_result.status.identity`. Original PRE5 validation runs before and after
correlation, retaining its durable read/hash/replay and authenticated lifetime
checks. No collection, preparation, receipt consumption or boundary certification
is repeated. A new PRE5 publication supersedes the captured root; PRE6 cannot
retarget itself to reconstructed or newer evidence. This owner is deliberately
scoped to one exact PRE5 publication, with no unbounded issuer registry.

Only the original creator thread may construct, evaluate or validate. Wrong-thread
calls fail before live locks or durable reads. PRE6 adds no locks: original PRE5
validation preserves coordinator -> control -> read lifecycle -> read condition
-> write dispatch -> arrival, releasing lower locks before its reviewed SQLite
read. Beyond that validation PRE6 operates on retained immutable memory only.
No callback, callback storage/wait, request, clock, append or storage writer is added.

## Numeric-pair and source qualification

The primary key is exact source-qualified `(order_id, client_id)`, never the placing
client ID. Each source retains its original encoding, presence and independent
local occurrence order. The reviewed PRE5/source validators establish exact types,
presence booleans and supplied-set agreement. PRE6 then considers a fresh identity
field known only when its retained presence flag is exactly True and its value is
available. No SDK default or missing value is inspected or synthesized.

Supplied read-side order/client zero remains a known numeric fact. Existing read
sanitization classifies permId zero as unavailable (`None`), including when supplied;
PRE6 preserves that rule. PRE4 status identity instead uses its original
`_known_identity_value()` helper: SUPPLIED/LEGACY_DECODED is known, ambiguous legacy
zero and UNAVAILABLE are not. Explicit protobuf status permId zero remains a known
fact. No common normalization erases these source differences.

Legacy PRE3 completed-order occurrences lack qualified order/client IDs and cannot
correlate, even if orderRef, permId, symbol, conId, quantity and timing all resemble
the target. An unknown/different numeric pair is unrelated. Matching permId alone
cannot pull it in; there is no global permId ownership map. Observed client ID
equal to or different from the placing client ID conveys no ownership.

For each fresh source, `_SourceIdentityCorrelation` retains exact ordered tuples:
`candidates`, `matches`, `unresolved_perm_id`, `conflicts`, plus `source`.

- Both known numeric fields must equal the target to become a candidate.
- With no target permId, every pair candidate matches; known source permIds remain
  facts and missing permIds remain missing. No target permId is invented.
- With a target permId, known equal permId matches, known unequal permId conflicts,
  and unavailable/unqualified permId remains unresolved rather than contradictory.

The closed per-source `coverage` classification is IDENTITY_CONFLICT_OBSERVED,
PERMID_UNRESOLVED, TARGET_MATCH_OBSERVED or NO_QUALIFIED_CANDIDATE_OBSERVED, in that
precedence. These describe received source observations, never broker completeness.
Duplicate occurrences and original ordering are preserved without aggregation.

## Identity contradictions and outcome precedence

`_KnownPermIdFact(source, observation, perm_id)` retains every known permId from
fresh numeric-pair candidates, including conflicting candidates. The supported
selected PRE4 status contributes its known permId via the original helper. PRE4
NO_QUALIFIED_STATUS contributes none; AMBIGUOUS_IDENTITY stays ambiguous and cannot
be repaired by fresh apparent agreement. No other PRE4 status-selection algorithm
is introduced and no later history enriches its frozen boundary prefix.

Reasons are a closed, deterministically ordered tuple:

1. DURABLE_IDENTITY_AMBIGUITY: inherited PRE4 ambiguity.
2. REQUESTED_PERMID_CONFLICT: a known pair-scoped fact disagrees with an explicitly
   requested target permId.
3. KNOWN_PERMID_CONFLICT: two known facts for that pair disagree. This can include
   fresh sources, selected PRE4, or repeated facts from one source if its reviewed
   source contract admitted them. It never includes unrelated pairs.

All reasons and all fact references survive; no source wins. Requested conflicts
and known-set conflicts can coexist. Per-source `conflicts` means disagreement with
the requested qualifier; cross-source inconsistency is represented by the overall
known-fact tuple/reasons even when each source individually matches an unqualified
numeric pair.

Overall closed outcome precedence is:

1. DURABLE_IDENTITY_AMBIGUOUS, regardless of fresh facts (other reasons retained).
2. IDENTITY_CONTRADICTION, for any proven requested/known permId conflict.
3. NO_QUALIFIED_FRESH_MATCH, when all three fresh candidate tuples are empty.
4. PARTIAL_IDENTITY_COVERAGE, when a fresh source lacks a full target match or has
   unresolved requested-permId candidates.
5. CORRELATED_NO_PROVEN_IDENTITY_CONTRADICTION, when all three fresh sources have a
   target match and none has unresolved candidates or proven identity conflict.

The final classification is identity-only and does not require PRE4 support to
replace NO_QUALIFIED_STATUS. It never means correct/owned/current/manageable order.
Empty sources mean non-observation within their declared limited intervals.

Status differences, open plus completed plus execution observations, contract,
symbol, action, quantity, order type, TIF and price differences are not identity
contradictions. Collections are independent intervals and may reflect modification,
progression or delay; PRE6 has no modification chronology. No economic divergence
field, status rank, lifecycle conclusion, arithmetic or execution deduplication is
introduced. A returned execution occurrence is not a complete execution history;
absence is not no-execution proof. No source proves no late fill remains possible.

## Result and exact issuer validation

`_CrossSourceIdentityEvidence` is private, frozen, slotted, opaque and memory-only.
Its exact fields are `pre5`, `identity`, `open_orders`, `executions`,
`completed_orders`, `decision_status`, `known_perm_id_facts`, `reasons`, `outcome`
and `limitations`. It retains exact original PRE5/identity/PRE4 and fresh observation
references; it does not rewrite source observations. Structural construction checks
are not production issuer authority.

`owner._require_evidence(result)` requires the exact latest issued object, original
canonical root snapshot, and exact complete immutable node/reference graph, then
calls the original PRE5 issuer again. Reconstructed/pickled/foreign results, replaced
correlations/tuples/observations/identity/receipts or changed provenance fail closed.
Publication checks snapshots captured before the private fault-injection seam.
A new successful PRE6 evaluation supersedes its prior issuer slot, leaving old
caller-held historical values immutable. Equal inputs produce structurally equal
results. There is no serialization/codec/replay registration; restart loses issuer
authority.

Ordinary failures produce only CROSS_SOURCE_IDENTITY_EVIDENCE_UNAVAILABLE. Entered
evaluation/revalidation failure clears local publication metadata and releases its
busy guard by direct assignments, without poisoning broker safety state. No usable
partial result is published. The evaluator can be repeated while the original root
and lifetime remain valid. KeyboardInterrupt, SystemExit (including exact code) and
GeneratorExit preserve the exact original objects; cleanup cannot replace them.
Original PRE5/storage failure behavior remains unchanged.

The fixed PRE6 limitation tuple contains:

- INDEPENDENT_COLLECTION_INTERVALS
- NO_CROSS_SOURCE_ATOMICITY
- SOURCE_CORRELATION_IS_IDENTITY_ONLY
- NO_ORDER_OR_ATTEMPT_OWNERSHIP
- NO_GLOBAL_PERMID_OWNERSHIP
- NO_STATUS_PRECEDENCE
- NO_MODIFICATION_CHRONOLOGY
- NO_EXECUTION_ABSENCE_PROOF
- NO_EXECUTION_INCORPORATION
- NO_DELAYED_CALLBACK_OR_LATE_FILL_GUARANTEE
- NO_BROKER_CURRENTNESS
- NO_FINALITY
- NO_LIFECYCLE_OR_REMAINDER
- NO_INVENTORY_OR_POSITION
- NO_ACCOUNTING_INCORPORATION
- NO_MANAGEMENT_RECOVERY_OR_TRADING_AUTHORITY
- NOT_RESTART_ADOPTABLE

Every PRE1/PRE2/PRE3/PRE4/PRE5 limitation remains retained through the source objects.
Earlier phase-specific limits describe what those phases proved; PRE6 adds only
the identity correlation defined here, without rewriting their historical claims.

## Validation and next foundations

Focused tests use actual original private collectors/consumers and PRE5 durable
validation with anonymous deterministic SDKs and temporary storage. Network and DNS
are blocked. Setup alone invokes fake collections and the existing producer;
correlation itself forbids requests/orders, source consumption/preparation,
boundary production, drain, storage data/schema writes, accounting/lifecycle/
recovery calls and current clocks. SQLite authorizer tests enforce no writes.
Matrices exercise source presence, zero/absence, legacy completed-ID limitations,
requested/known perm conflicts, ambiguity precedence, independent status/economic
differences, duplicates, Decimal shape, reference tampering and interruptions.

V1/V2/V3, SQLite schema/version/hash, PRE9 and all prior production modules remain
unchanged. No public export/API, CLI or wire policy is added. PRE6 must pass
independent review and actual Windows qualification on Python 3.12.10 with
ibapi 10.50.2/protobuf 5.29.5 and real Gateway opt-in disabled. No real connection
is needed or authorized here.

Next, separately authorize exact journal-attempt attribution using this exact
issuer-validated result. Then reviewed execution incorporation, lifecycle/remainder,
inventory/disposition, protection/resizing, cancel/replace/exits, session continuity/
retirement, recovery/risk/strategy restoration and eventual first PAPER authorization
remain separate foundations. PRE6 implements none of those later steps.

## Cloud validation

On Python 3.12.14, the final focused PRE6 run passed all 168 tests. Selected
regressions ran 5,276 tests, OK with three optional installed-SDK skips, including
PRE5 151, PRE5-PRE1 207, PRE4 164, PRE4-PRE2 179, PRE4-PRE1 195 and
PRE1/PRE2/PRE3 180/229/284. Full offline discovery ran 7,886 tests, OK with four
skips (three optional installed-SDK checks and disabled real Gateway integration).
All 132 Python files compiled with caches outside the checkout.

Diff/whitespace, secret/account, `.env`/credential, public API/export,
broker-operation, journal/SQLite mutation, current-clock, forbidden attribution,
execution-arithmetic, authority/scope and V1/V2/V3 compatibility scans passed.
The five intended files are the only changes; all 58 previously tracked
broker/backtest production files remain byte-for-byte unchanged. No dependency
installation, real IBKR connection, broker request/order operation or Gateway
change occurred. PRE6 still requires independent review and actual Windows
qualification; these cloud results do not establish Windows or Gateway behavior.
