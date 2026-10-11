# Phase 10C3C5A — private production PAPER execution / position accounting owner

**HISTORICAL ENTRY ACQUISITIONS ONLY. THE FIRST REAL PAPER ORDER REMAINS BLOCKED.**

This phase adds a private accounting owner for qualified durable entry executions.
It adds no broker request, public API, protective orders, cancellation/replacement,
exit accounting, restart adoption, recovery application or trading permission.
Gateway Read-Only remains required. AccountMode remains UNKNOWN, the public
snapshot mode remains None, and LIVE remains unsupported.

## Lifetime ownership

`_create_paper_execution_accounting_owner()` requires the exact existing
`_PaperDispatchJournalOwner`. Controlled setup creates at most one accounting
owner for that journal-owner lifetime. The association retains exact identities
of the journal owner, coordinator, control broker, read/write transports,
authenticated account-binding receipt, header, SQLite store, registered session
and creator thread. Equal reconstructed components cannot replace them.

Direct construction, copied owners, a second owner, foreign owners and substituted
components cannot establish publication provenance. Private Python isolation is
not a security boundary against hostile interpreter access.

Ownership follows the unchanged qualified chain:

`journal/account/session -> AttemptConsumed -> NumericOrderIdReserved ->
InvocationMayOccur -> qualified durable callback attribution -> execId`

An orderRef alone, symbol, price, timing, position, cash movement or caller label
cannot adopt an execution. C3C4D preserves the earlier strong-open/client-ID rule
when execution orderRef is absent. Foreign numeric orders remain foreign even
when their reference matches. Manual and pre-existing same-symbol holdings and
another client's trades are never silently adopted.

## Evidence handoff

The journal owner supplies a private immutable handoff, not its store or mutable
persistence dictionaries. It checks exact lifetime/thread ownership, fresh
validation of the same PRE1 receipt, store health, current generation/session,
retained callback span integrity, staging/coverage state, persistence cursor and
current transport sequence. Capture-side locks are released before storage.

The store fully verifies durable history. That history must match the journal
owner's retained independent values and exact registered session. Replay is
recomputed internally; a loaded replay or caller-supplied replay is not authority.
The durable progress declaration must agree with the local persistence cursor.

After off-side projection, a second verified load checks the durable tail/history.
Before publication, receipt validation and capture-state checks repeat. A changed
tail, pending-order identity, generation, arrival watermark, cursor, staging or
covered suffix rejects publication. No progress number proves unseen coverage.

The exact issued handoff and exact internally derived candidate are required.
Independent value copies detect subsequent private-model changes. Final
publication consumes an exact one-use internal permit under the established
capture synchronization. Direct publication and equal reconstructions have no
authority. This permit publishes historical accounting only; it is never an
execution, recovery or authorization capability.

The handoff remains usable while dispatch is inhibited, provided the original
association, authenticated read evidence, store and persistence producer remain
usable. No inhibition, reconciliation barrier or authorization is restored.

## Immutable data and lots

Published values are frozen, slotted and opaque. Nested sequences are tuples.
They retain reviewed journal values and independently loaded economic primitives,
without broker/store/coordinator references, raw SDK objects, account numbers,
enrollment authentication or secrets.

`_ExecutionLot` retains the client reference, reserved numeric order ID, execId,
security/conId/symbol/currency, BOT side, shares, actual recorded price, gross
notional, actual callback source and optional known commission. Each distinct
qualified execId becomes at most one lot. Identical repeats do not double-book.
Conflicting execId reuse or distinct shares exceeding requested Q rejects the
entire authoritative projection. The offending durable evidence is never dropped
or replayed as a supposedly clean prefix; existing quarantine/inhibition remains.

Invocation accounting supports only PAPER / ENTRY / BUY / MKT. It does not extend
the journal to SELL, protective or exit invocations.

## Quantity and lifecycle semantics

Each entry keeps these distinct:

| Field | Meaning |
| --- | --- |
| `requested_quantity` | Retained requested Q, not broker acceptance. None for identifier-only denial. |
| `recorded_execution_quantity` | Exact deduplicated durable acquisition sum. |
| `historical_filled_lower_bound` | Existing replay evidence, separately retained. |
| `arithmetic_residual` | Q minus recorded execution quantity; None without Q. |

**Arithmetic residual is not current working remainder or broker unfilled size.**
No executions means recorded zero, not proven zero actual fills or no position.
Partial acquisitions are owned while execution completeness remains unproven.
Qualified status may establish a larger historical filled lower bound without
supplying the missing execution IDs/prices. Filled status never fabricates lots.
Executions remain owned without terminal status. Terminal cancellation of the
order does not close acquired shares or establish dispositions.

When exact recorded executions sum to Q, acquisition quantity and gross
execution-price coverage are complete for that entry. This still does not establish
actual current bot inventory, closed quantity, broker aggregate position or absence
of manual disposition. Current inventory/disposition coverage stays false and
current held quantity, closed quantity and working remainder stay None.

Open and order-status observations remain historical same-event values, including
status, filled, remaining, average/last prices and source identity. Latest retained
status is not promoted to current broker truth. Replay uncertainty, identity,
reconciliation reasons and unresolved obligations remain explicit.

## Exact price and cash arithmetic

Recorded gross notional is exactly `sum(shares * price)`. Existing reviewed pure
numeric helpers use exact rational intermediates and finite Decimal construction,
independent of ambient Decimal precision/traps. They do not instantiate or mutate
research PortfolioEngine or PositionManager.

Weighted average over recorded lots is a reduced `_ExactRatio` with exact integer
numerator and positive denominator; 4/3 remains 4/3. No approximate Decimal price
is published as exact. Supplied valid zero execution price stays zero. Reference
entry, stop/target, account average cost or market price never replace a fill price.

For BUY acquisitions, `gross_execution_cash_subtotal` is negative gross notional.
It is only a transaction subtotal, never account cash, cost basis, equity or P&L.
Realized/unrealized P&L, current marked exposure and fee-inclusive net cash remain
None because this phase has no qualified dispositions or current market marks.

## Commission semantics

Zero durable commission reports means unknown commission, not zero. A supplied
zero report is known zero; signed charges/rebates remain signed. No absolute-value
normalization, conversion, missing-value default or correction/latest-wins exists.

The snapshot supplies the known reported subtotal (None if no reports), counts of
executions with/without a report, and each linked report's identity, currency,
signed amount and actual local receipt time. Identical reports have one financial
effect; conflicting report identity/amount/currency fails closed under unchanged
C3C4A/C3C4D semantics.

Late first reports may update known accounting without changing acquisition lots.
All recorded executions having reports, terminal/Filled status and caught-up
progress do not establish fee finality. Commission completeness is always false;
authoritative fee-inclusive cash/cost basis is unavailable. Future protective
management must not wait for late fees when its independent quantity/price and
write prerequisites are qualified.

## Currentness, atomicity and threads

Snapshot currentness is explicitly **as of publication**:

- VERIFIED_PREFIX: producer persistence is caught up with locally captured evidence.
- PENDING_CALLBACKS: an undurable/staged/covered suffix remains.
- BLOCKED: owner failure or no successful snapshot (reported by the owner).

VERIFIED_PREFIX proves neither broker-history completeness nor current inventory,
fee finality, protection or trading eligibility. A positive durably committed
lookahead execution can be owned while prefix coverage remains pending. Historical
snapshots never change, including their original as-of currentness after failure.
The owner exposes a separate producer-currentness check that detects new locally
captured callbacks or an advanced retained journal tail. That check does not
replace refresh's fresh durable-history verification or prove external history
completeness.

Refresh retains coordinator/control serialization, captures provenance, releases
read lifecycle/condition, write-dispatch and arrival locks, verifies history and
derives a complete candidate off to the side. It rechecks history/provenance and
publishes one immutable snapshot pointer. No partially constructed candidate is
published. An interruption before that linearization preserves the prior snapshot.

Lock direction remains:

`coordinator -> control -> read lifecycle -> read condition -> write dispatch -> arrival`

Only the existing journal creator thread refreshes accounting. Callback threads
never enter accounting or SQLite and never await accounting. No SQLite operation
holds arrival/write/read capture locks; no transaction spans SDK/network activity.
No new queue, lock hierarchy, persistence cursor or callback mutation is added.

## Failure, restart and deferred foundations

Wrong thread/association, stale authentication, generation change, store close,
poison/quarantine/corruption, storage BUSY/failure/unknown outcome, persistence
failure, malformed provenance, sequence gaps, conflicts, overfill or changed
refresh state permanently fail accounting currentness. Prior verified positive
historical facts remain available; no new clean snapshot is published.

Failure inhibits the journal and existing coordinator/control/write safety planes
without appending diagnostics or retrying persistence. KeyboardInterrupt,
SystemExit and GeneratorExit propagate as the exact original object after failure
state is secured. Diagnostic/safety failures never replace the original exception.
There is no alternate store/session, retry, journal adoption or reset path.

Restart quarantine remains True. Historical reconstruction provides no restart
ownership, retry, recovery, migration, barrier clearing or authorization. The
advisory recovery planner and existing journal/storage/persistence semantics remain
unchanged. Public broker write blocking, wire policy and frozen strategy remain.

Before any real PAPER entry: qualified inventory/dispositions, working remainder,
production risk/strategy management, crash-safe broker-side protection, quantity
adjustment, reviewed cancel/replace, session retirement and separately reviewed
disconnect/restart/recovery/audit procedures are still required. C3C5A requires
independent review and actual Windows/Python 3.12 validation before the next phase.

## Offline validation

Linux/Python 3.12 qualification passed 144 new accounting tests, all 1,695 required
prior regression tests, and the full 4,248-test offline suite: 4,247 passed with
the existing opt-in real IBKR read-only integration test skipped. All 100 Python
files compiled; whitespace, changed-file credential/account/`.env` and protected
scope checks passed. No new skip/xfail, dependency installation, real connection,
broker write or Gateway change was introduced. No actual Windows run is claimed.
