# Phase 8 — Portfolio orchestration and daily risk

**RESEARCH BACKTEST MODEL — V1.0**

**ZERO-FRICTION BASELINE**

**FULL-FILL RESEARCH ASSUMPTION — MARKET DEPTH AND PARTIAL FILLS NOT MODELED**

This phase adds a shared offline account/session engine in
`tradingbot_backtest/portfolio.py`. The frozen specification, parameter table and
decision history remain authoritative and unchanged. No historical scanner,
bulk dataset runner, broker connection, live data or live trading is included.
No dependencies are added.

## Canonical portfolio rules

[Specification §15](strategy-spec-v1.0.md#15-shared-portfolio-valuation-event-order-and-daily-lockouts)
defines valuation, event phases, ranking and daily locks. Simultaneous scheduled
entries are ranked by **D's finite previous-20 RVOL descending, then alphabetical
ticker** (Decision #12; opening ordering clarified by #44). Each acceptance updates
the account before the next candidate is sized. Unknown cross-ticker intrabar
order uses alphabetical ticker, without changing independent fill prices.

Each remaining held position must have a trustworthy **current OPEN** for entry
allocation. Current exposure is the sum of remaining quantities times those
opening prices; current equity is cash plus that exposure. Reporting marks carried
through NO_TRADE do not satisfy this allocation prerequisite. If any remaining
position lacks a reliable current OPEN, every scheduled new candidate is consumed
and rejected at that timestamp.

## Phase 5/6/7 contracts

`PortfolioEngine` receives the existing calendar/tick interfaces, run/data version
and frozen default configuration. `start_session(date)` verifies an actual session
and starts with $10,000 for the first session, or the prior resolved ending cash.
BOD equity, daily entries, realized accumulators, loss streak and locks reset only
at the next valid session. A paused or incomplete session cannot continue into
another session.

Upstream Phase 1–5 producers supply immutable Phase 5 `EntryApproval` records for
the scheduled OPEN. The engine does not repeat pattern/context decisions. It
validates matching session, timestamp, security/ticker and reference OPEN, and
blocks repeated scheduled opportunities. Malformed or duplicate input raises an
input-contract error rather than inventing a ranking or creating a trade.

For each ranked candidate, the engine creates a fresh Phase 6 `PortfolioSnapshot`
from the current shared account and held OPENs. The unchanged `TradeConstructor`
applies tick normalization, BOD risk, all sizing caps, cash, exposure, position
count and lockout checks. No stale preliminary quantity is reused. Every accepted
`InitialPosition` is registered with an unchanged Phase 7 `PositionManager`.

## Separate OPEN and completion events

The API deliberately has two phases:

1. `on_open(timestamp, intervals, approvals)` processes known required existing
   OPEN exits first, applies proceeds/realized accounting/completion/locks, values
   remaining holdings, ranks and sequentially allocates new candidates.
2. `on_close(timestamp)` processes the pending interval's completed information:
   intrabar exits, partials and prospective stop updates. Its timestamp must be
   exactly one minute after the pending OPEN. This completion must precede the
   next OPEN call, even when they have the same timestamp.

Completed historical `MarketInterval` records may be supplied for replay, but
later HIGH/LOW/CLOSE/volume are not inspected for OPEN allocation. Phase 7's
separate OPEN/completion contract enforces this boundary. Later intrabar proceeds
cannot fund an earlier OPEN entry; later losses cannot undo its acceptance.
Unknown intrabar fill times remain unknown in the underlying exit records.

Every held interval must be explicit and consecutive, including NO_TRADE and
MISSING/INVALID. Absent intervals cannot silently bridge held price history.
The engine may skip vacant minutes when there are no positions; upstream signal
producers still maintain their own complete chronological histories. This API is
not a bulk historical ingestion loop.

## Accounting and immutable state

`PortfolioState` preserves session/BOD equity, cash, authoritative current equity
and exposure, separate reporting values, immutable held states, daily counts,
gross/net/cost accumulators, loss streak, sticky lock reasons, last event time and
incomplete status. `CandidateDecision` and `PortfolioStep` preserve allocation
results and newly emitted audits. Completed trades retain the Phase 6 construction
and Phase 7 exit legs/evidence.

On entry, purchase value reduces cash and becomes an open holding; it is not a
realized loss. Equity is preserved at the fill absent a mark change. On a sale,
cash increases by proceeds minus commission/fees. Realized gross/net P&L is added
once from the actual new exit leg. Slippage remains separately recorded and is
already reflected in fill-based gross P&L; it is not subtracted twice. This phase
supports the current zero-friction baseline only, with no invented cost model.

Partials reduce remaining quantity and exposure but leave the position registered,
with no completed-trade classification. Final exits remove the registry entry,
record one completed trade and its final net outcome, and release the position
slot. Known OPEN fills update current OPEN valuation before allocation. At
completion, when holdings remain, `current_equity`/`exposure` are unavailable until
the next trustworthy OPEN; separate reporting values use Phase 7's last permitted
marks. They cannot be substituted into entry sizing. With no holdings, equity is
cash and exposure is zero.

All financial operations use the existing exact decimal utilities; RVOL ranking
uses exact Fractions. No binary float or outcome-dependent ordering is used.

## Daily risk locks

The frozen defaults are read from `FROZEN_V1`:

- The fifth actual accepted entry is allowed, then the five-entry lock blocks
  subsequent candidates. Cancellations/rejections and exit legs do not add entries.
- Every partial/final realized NET leg contributes to daily NET P&L. At
  `daily NET <= -2% * BOD equity`, new entries lock; unrealized P&L is excluded.
- Only a completed trade's final NET result changes the consecutive-loss count:
  loss increments, win or exactly-zero breakeven resets. The third consecutive
  completed loss locks new entries.

All independently triggered lock reasons are retained. Locks are one-way until
the next eligible session; later profits or a reset loss streak cannot unlock
entries. Existing positions continue normal management. A known OPEN exit that
triggers a lock blocks new candidates at that same timestamp.

## Data pause, replay and fatal propagation

An explicit held MISSING/INVALID interval pauses the shared timestamp before any
account effects or allocation. A checkpoint preserves all managers, constructor
consumption state, registry/account, completed trades and quarantine boundaries.
Exact source causes remain in Phase 7 and portfolio audits.

`resume_with_replacements()` requires trustworthy replacements for every paused
held security at that exact interval. It restores the shared checkpoint and
replays that OPEN deterministically; later data cannot bridge the gap. Prior
failure audits remain, with an explicit replay-generation record identifying the
replacement research replay. `mark_data_incomplete()` preserves an unresolved
pause without inventing an exit or ending equity.

Canonical Phase 7 fatal flags stop the shared engine immediately, including
`INCOMPLETE_TICK_SIZE_UNAVAILABLE_OPEN_POSITION` and
`INCOMPLETE_UNRESOLVED_EOD_LIQUIDITY`. Later events/sessions are rejected. Known
cash, prior realized legs, last valid stops/marks and unresolved quantities remain
available, but normal ending equity is not finalized. Official-close processing
respects the approved final-minute NO_TRADE limitation on normal and early-close
sessions; no next-day price is used.

A normal `close_session()` requires official close, no pending interval and no
open position. It finalizes ending cash/equity and emits a signal-session-reset
requirement. The final interval's completion calls this automatically when resolved.

## Fresh-setup integration and audit

`price_pattern_permitted()` exposes the held-ticker/final-exit quarantine gate.
The final-exit interval cannot participate in fresh same-security price patterns;
NO_TRADE cannot participate either. Approval price evidence is checked defensively
against that boundary. Partial exits do not release the ticker. Historical volume
baseline continuity is separate and is not erased by this gate.

Upstream detectors remain responsible for fresh-price-history and session resets,
universe/dynamic eligibility and producing Phase 5 approvals. This engine supplies
occupancy/quarantine information rather than detecting another setup itself.

Portfolio audits record session start/close, event phase, OPEN valuation,
candidate order and individual RVOL, pre/post cash/exposure allocation, entry
results, per-leg proceeds/realized P&L, completion/loss streak, every sticky lock,
data pause/replay and fatal state. Existing Phase 6/7 audits retain sizing,
protective tick evidence, stop/target details and underlying source causes.
Ordinary portfolio status/rejection descriptions are implementation vocabulary;
canonical reason/run codes and strategy behavior remain unchanged.

## Validation and Phase 9 handoff

Run offline with:

```bash
python -m unittest discover -s tests -v
python -m compileall -q tradingbot_backtest tests
git diff --check
```

The Phase 8 fixtures independently check portfolio cash/P&L, exact limits,
sequential RVOL/ticker allocation, OPEN versus completion, sticky locks, shared
pause/replay, fatal propagation, session reset and final-exit quarantine. All
previous phase tests and the canonical 64-default comparison remain unchanged.

Phase 9 can supply trustworthy historical calendar/session data, complete minute
classifications and compatible price/share units, point-in-time tick schedules and
Phase 1–5 signal/approval streams to this API. Provider selection, actual historical
coverage, licensing and source availability still require verification. Historical
universe discovery, bulk multi-day ingestion and a performance-reporting suite are
not built here. Offline tests do not establish available data, profitability,
historical market depth or realistic live execution.
