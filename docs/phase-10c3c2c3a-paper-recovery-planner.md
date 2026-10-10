# Phase 10C3C2C3A — pure offline paper recovery planner

This phase adds an **advisory planner only**. It consumes a retained immutable
C3C2C2A reconciliation bundle and internally recomputes the reviewed C3C2C2
assessment. Its output describes missing evidence and infrastructure; it grants
no recovery authority. No real IBKR connection or broker write occurred in this
phase. Gateway Read-Only remains unchanged and must remain enabled.

The private entry point is `_plan_paper_recovery(bundle)` in
`tradingbot_broker/paper_recovery.py`. It accepts the exact bundle type, never a
caller-supplied assessment. It performs no network/SDK calls, I/O, clocks, random
generation, locking, receipt consumption or callback synchronization. Repeated
planning of the same bundle yields an equal plan and preserves all input state.
It follows the evaluator's controlled malformed-bundle error contract.
KeyboardInterrupt, SystemExit (including its code) and GeneratorExit propagate
unchanged; they are not converted into advisory plans.

## Independent domains

The frozen, slotted `_PaperRecoveryPlan` contains six independent domain records:
EVIDENCE, CONTROL, PORTFOLIO, ORDER_MANAGEMENT, WRITE_SESSION and AUTHORIZATION.
Statuses are NOT_REQUIRED, READY, BLOCKED, REQUIRES_MORE_EVIDENCE,
REQUIRES_INFRASTRUCTURE and MANUAL_INTERVENTION. **READY is used only for the
broker-evidence assessment domain. It never means recovery application is ready.**
NOT_REQUIRED means the classified target lifecycle does not establish that
particular requirement; it does not attest account-wide synchronization.

Every domain record has fixed enum reason codes. Reasons and advisory requirement
categories are deduplicated in enum order. Several requirements can coexist; there
is no FULLY_SYNCHRONIZED category and no aggregate recovered/success boolean.

| Assessment | Evidence | Control | Portfolio | Order management |
| --- | --- | --- | --- | --- |
| INCOMPLETE | More evidence | Blocked | Blocked | Blocked |
| UNRESOLVED | More evidence | Blocked | Blocked | Blocked |
| CONFLICT | Manual intervention | Manual intervention | Manual intervention | Manual intervention |
| OPEN_WORKING | Assessment ready | Blocked | More evidence | Infrastructure required |
| PARTIALLY_FILLED_WORKING | Assessment ready | Blocked | More evidence | Infrastructure required |
| PARTIALLY_FILLED_TERMINAL | Assessment ready | Blocked | More evidence | Infrastructure required |
| FILLED | Assessment ready | Blocked | More evidence | Infrastructure required |
| TERMINAL_UNFILLED | Defensive assessment ready | Blocked | Not required for target fills | Not required for target fills |

WRITE_SESSION and AUTHORIZATION are always BLOCKED. TERMINAL_UNFILLED remains
unreachable from current real bundles: aggregate status/quantity fields do not
prove same-event terminal zero fill, and unread target events are not incorporated
as new positive lifecycle truth. Defensive handling exists for a separately
reviewed future evaluator, not for a forged caller assessment.

`working_order_observed` is true only for positively classified working states.
False is not proof of open-order absence, especially for an incomplete, unresolved
or conflicting assessment. `working_order_management_required` describes that
working-order obligation; protective management for filled exposure is reported
separately in the ORDER_MANAGEMENT domain.

An incomplete receipt takes precedence over economic/conflicting evidence. Its
records are not used for price/commission/position diagnostics. An unresolved or
conflicting assessment also does not yield positive price-completeness claims.
Conflict requires investigation; fresh evidence is not promised to resolve it.

## Quantity, prices and accounting

FILLED means the original target order quantity was economically executed.
**FILLED does not mean portfolio recoverable.** `order_quantity_proven` denotes
this narrow fact. It says nothing about the account's current net position.

The planner reuses the evaluator's exact attribution helpers and missing-reference
anchor policy. It forms a bounded local/read execution union by exact execId.
The evaluator has already checked identity, cross-source conflicts and duplicate
economics. Compatible local/read copies count once; unrelated executions do not
contribute. Read and write generation numbers are never compared as shared counters.
Execution venue is not required to equal the SMART submission route.

`complete_execution_prices` is a diagnostic for **gross price coverage of the
original order quantity**. It requires a positive FILLED assessment, exact quantity,
strong attribution, and a deduplicated attributable execution set whose priced
shares sum exactly to the original Q and whose count agrees with the evaluator.
Each retained execution already has a validated price. Arithmetic uses a private
512-digit Decimal context, independent of ambient precision/traps. No average
entry price or cost basis is synthesized.

HISTORY_LIMITED alone cannot establish completeness. A positively attributable
priced sum of exactly Q can cover the proven original quantity despite limited
history; this is not a guarantee about later corrections or account-wide activity.
Cumulative quantity Q with returned priced shares below Q, or local last_filled Q
without complete execution records, leaves complete_execution_prices false.
Partial execution subsets remain incomplete even when each returned price is valid.
Historical local filled/remaining aggregates are not associated with the current
last_status as if they came from one callback; the evaluator's conservative
provenance rules remain unchanged.

The private read snapshot has no commission ledger or commission-completion signal.
The paired public snapshot may contain reports received during collection for
returned executions. The planner reports only the count of matching execution
IDs with received commission reports. Even a report for every observed execution
does not establish complete fee coverage: late reports and limited execution
history remain possible. **Missing commission is never zero.**
`complete_commission_evidence` is always false. Gross execution-price coverage is
separate from fee-inclusive accounting. Exact cost-basis recovery remains false.

## Position corroboration and strategy state

Positions come only from the exact public snapshot paired with the read receipt.
The collection is asynchronous, not an atomic account-wide economic instant.
No position is used to attribute orders or reconstruct target fills.

Public position security keys can differ from the write mapping. Candidate
corroboration uses the security key or returned symbol/currency; it never creates
strong contract attribution. With exact positive quantity, one compatible position
of that quantity is QUANTITY_AGREES, not proof of this order's contribution.
Missing, smaller, larger or incompatible candidates are INCONSISTENT diagnostics.
For lower bounds, a compatible position at/above the bound remains AMBIGUOUS.
Without positive classified quantity, the diagnostic remains AMBIGUOUS, or
NOT_ASSESSED for failed/unresolved/conflicting assessment. Manual or other API
trades, a pre-existing position or subsequent exits can explain a difference.
The planner does not change the evaluator's broker-ownership classification.
All classified portfolio cases retain POSITION_CONTRIBUTION_AMBIGUOUS.

The existing PositionManager/PortfolioEngine are research/backtest components;
the planner does not instantiate or use them. A production runtime would need
original/remaining quantity, actual entry prices/times, initial stop, 2R target,
partial and runner state, breakeven/trailing state and EOD obligations. Economic
exposure plans report missing protective state and strategy replay requirements.
Original strategy context, trustworthy post-entry market intervals and actual
broker exit/management history are absent. Strategy-management recovery and
exact current-position recovery remain false.

## Barriers, ownership and authorization

The plan separately reports current pending-confirmation, broker-state,
coordinator, control and write latches, write closure and write-generation loss.
It does not acknowledge callbacks or advance cursors. Pending confirmation may
already be false through existing qualified C3C2B observation; other barriers
remain independent. There is no reviewed pending-order retirement API.

The write reconciliation latch is permanent for its transport instance. If it
was present in either local capture, the plan requires session retirement/migration
infrastructure. It never proposes clearing the latch or constructing a replacement.
Naive replacement loses pending ownership, duplicate history, callback truth and
lifetime allocator constraints. Authoritative retention/migration must be separately
qualified. Allocator exhaustion and socket poison are not fields of this immutable
bundle: the planner explicitly reports that this safety evidence is not captured
instead of inventing their values. Even an unlatched transport is not classified
as reusable by this phase.

Coordinator broker-state/general and control barriers have no reviewed recovery
clear APIs. Understanding an outcome does not supply such an API. Fresh explicit
authorization is required before any future write eligibility; an existing ARMED
capability does not satisfy post-recovery authorization. The planner neither
revokes nor re-arms it. AccountMode remains UNKNOWN and AccountSnapshot.mode None;
LIVE remains unsupported. None of these observations attests PAPER mode.

Process-restart recovery is unsupported. Lost retained pending identity is a
malformed/unavailable input, not something reconstructed from broad broker records.

## Advisory values and future work

Plans and nested records have opaque reprs and retain only frozen sanitized
primitives/enums/tuples. They retain no bundle, broker, coordinator, transport,
account identifier, capability, enrollment data, raw SDK/protobuf object, socket,
arbitrary status/error text or callable mutation hook. Equal/reconstructed plans
have no authority. A future applier must establish exact owner-held one-shot
authority and current-state comparisons independently.

Fixed non-authority fields cannot be constructor overrides: new_entry_permitted,
write_session_reusable, exact_position_quantity_recoverable,
exact_cost_basis_recoverable, complete_commission_evidence and
strategy_management_state_recoverable are always false.
explicit_reauthorization_required is always true.

This phase does not recover state, clear barriers, retire pending orders, create
fills, update holdings/cash/equity/risk, resume working orders, replace transports,
re-arm authorization or enable another entry. No public Broker/ReadOnlySnapshot
API or package export is added. Future apply, production accounting/management
and session migration require separate qualification. Real paper writes remain
disabled and Gateway Read-Only must remain enabled.
