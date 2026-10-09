# Phase 9 — Historical backtest and point-in-time universe

**RESEARCH BACKTEST MODEL — V1.0**

**ZERO-FRICTION BASELINE**

**FULL-FILL RESEARCH ASSUMPTION — MARKET DEPTH AND PARTIAL FILLS NOT MODELED**

This phase connects the existing strategy stack to supplied offline historical
facts. The frozen specification, 64-default table and decision history remain
authoritative and unchanged. No provider/calendar/venue whitelist is selected,
no external service is connected, and no dependencies are added. Synthetic tests
demonstrate behavior, not actual historical coverage or realistic live execution.

## Data contract and prerequisites

`historical_data.py` defines `HistoricalDataset` and the offline `InMemoryDataset`
reference implementation. Each query returns explicitly identified immutable
records. Source tuple/file/mapping order has no economic significance.

| Record/interface | Required supplied facts |
|---|---|
| `SecurityReference` | Stable ID; historical ticker and applicability dates; classification, U.S./supported-exchange/OTC verification; listing date and its availability; delisting time; listing venue and source |
| `DailySession` | Actual session date/close time, RTH high/low/close/share volume, trustworthy status and exact source cause |
| `PriorClose` | Official immediately preceding RTH close, security/session identity and provenance |
| `MarketCapitalization` | Explicit point-in-time applicability interval and availability; authoritative cap, or verified historical shares and compatible contemporaneous price/basis |
| `HistoricalMinute` | Validated original `MarketInterval`; separate opening/completion availability; provenance and share basis |
| `SessionBasis` | Verified effective evaluation-session share denomination and availability |
| `HistoricalWeekly` | Historical weekly RTH bar with completion/availability, classification, source and share basis |
| `SplitAction` | Exact new-shares/old-shares ratio, old/new basis, effective/available times, verified simple-action status and source |
| Existing `SessionCalendar` | Verified actual sessions, holidays and early closes; unknown dates must raise, not return a guessed holiday |
| Existing `TickSource` | Historical security/basis/purpose/price-band rules and verified adjacent-band metadata, with availability and validity |

`Provenance` separates source identity, availability, units, verification, exact
quality cause, and `RAW`/`VERIFIED_POINT_IN_TIME`/`UNKNOWN`/`UNSAFE` methodology.
Availability equality at the modeled event is permitted. Unknown/unsafe
adjustment treatment is unavailable. No synthetic OHLC, default tick, guessed
market cap, reconstructed current shares, or unverified NO_TRADE is generated.

Providers must separately establish supported listing venues, eligible trade
conditions, official-close methodology, historical share/cap applicability,
corporate-action lineage, true no-trade verification, security-master coverage,
calendar certainty, and historical tick coverage. These facts must be declared
and verified by an adapter; the runner does not infer provider conventions.
Credentials do not belong in source identifiers or reproducibility metadata.

Missing minute records become explicit `MISSING` observations with a source-gap
cause, never NO_TRADE. Conflicting available records become INVALID. Unknown
calendar facts and contradictory identity/basis contracts raise
`HistoricalInputError`, requiring input resolution rather than arbitrary selection.

## Universe and corporate actions

`evaluate_universe()` implements Spec §4 / Decisions #37–39/#43:

- Verified U.S.-listed common stock on a supplied supported exchange, excluding
  OTC, ADR and every specified non-common instrument type. No symbol-name inference.
- Actual point-in-time price `<= $5`, and gain `>= 3%` versus compatible official
  immediately preceding RTH close. These remain separate dynamic results.
- Trustworthy applicable market cap `>= $50 million`; missing cap uses
  `MARKET_CAP_DATA_UNAVAILABLE`.
- ADV10: exactly the previous ten actual completed sessions, current session
  excluded, split-comparable volume, arithmetic average **strictly > 1 million**.
  Missing required observations cannot be replaced by older sessions.
- ADR20: exactly the previous twenty actual completed sessions, fixed using
  history available by RTH start. Average `(high-low)/close*100`; LOW `<5`, HIGH
  `>=5 and <10`, VERY_HIGH `>=10`. Missing/insufficient history retains canonical
  reasons and source causes. Early-close sessions count normally.

The dataset supplies the historical identity population, including delisted,
inactive, merged and renamed securities. No current-active list exists in the
contract. Separate identities distinguish ticker reuse; reference applicability
chooses the historical symbol. SPACs, recent IPOs and reverse splits receive no
extra exclusion; all normal history/trust gates still apply. This architecture
does not prove that a particular provider has complete survivorship coverage.

`normalize()` follows only verified, already-effective and available simple
split/reverse-split paths. Price divides by the cumulative factor; share volume
multiplies. An already-correct required basis is not adjusted twice. Future
actions do not rewrite earlier calculations. Missing, conflicting, complex or
unverified required transformations are unavailable. Exact Fractions preserve
nonterminating ratios without a rounded eligibility threshold. Current execution
prices remain actual supplied Decimals in the historical denomination.

Compatibility extensions in existing modules accept exact Fraction reference
closes, weekly prices and normalized share volumes, and retain those values in
audits. Existing Decimal calculations, price boundaries and execution formulas
are unchanged.

## Driver and information boundary

`HistoricalBacktest(dataset, code_version=..., run_id=...).run(start, end)` walks
the inclusive date range using actual calendar sessions. Every run creates fresh
Phase 5/8 engines; the next session begins only from resolved ending equity.

At each eligible RTH minute:

1. Collect source facts knowable at OPEN and point-in-time eligibility for all
   represented historical securities. A previously confirmed D may produce its
   one opening-only Phase 5 validation via the existing `EntryHandoff` contract.
   Later scheduled-minute H/L/C/volume is not inspected for approval/allocation.
2. Call Phase 8 `on_open()` collectively. It owns known required OPEN exits,
   accounting/lockouts, current-OPEN valuation, D-RVOL/ticker ranking, sequential
   resizing/construction and entry fills. Pattern/context filters are not rerun.
3. At interval completion, deliver only now-available completed records to
   Phase 8; Phase 7 owns intrabar exits and prospective protective updates.
4. Feed completed, eligible unoccupied intervals through the existing DDetector
   (which composes A/B/C). Store trustworthy current-session context for the
   existing 15-minute filter. No pattern formula is copied into the runner.

Completion at the same timestamp precedes the following OPEN. A later intrabar
exit cannot fund an earlier OPEN. All simultaneous approvals reach Phase 8 as a
set; its canonical Spec §15 / Decision #12 order remains **D previous-20 RVOL
descending, then alphabetical ticker**. Subsequent candidates see updated state.

The original immutable source interval may contain its complete historical
payload. OPEN consumers deliberately consult only its OPEN, classification,
identity and already-available provenance; actual source OHLC is not replaced by
an invented constant-price candle. The runner separately checks completion
availability before delivering extrema/close/volume to completion consumers.
An unavailable scheduled OPEN consumes D even if its source appears later.
Static universe gates are evaluated again at completed-candle activation time;
an OPEN-time cap flag cannot substitute for metadata that changes at completion.

The thin `HistoricalDetector` ownership adapter preserves chronological volume
separately from pattern state. Every new session starts fresh A/B/C/D. Same-date
04:00 premarket contributes only already-completed volume, not pattern prices;
verified NO_TRADE occupies zero-volume slots and gaps break continuity. Pattern
history is collected independently of unavailable initial eligibility references;
no signal can activate until required references become available. If verified
identity arrives later, already-known current-session RTH history may seed the
ordinary lookback without generating retrospective signals. Premarket still
cannot supply price-pattern evidence.
Pattern reset on ownership transfer excludes the consumed entry interval. Occupied
intervals and final-exit intervals are price-excluded, with Phase 8 quarantine
checked defensively. Trustworthy volume remains usable under its separate rules.
Ordinary pattern expiration/invalidation/restoration stays in the original
detector. Verified point-in-time static eligibility may change; prior-close or
share-basis corrections require clean input replay instead of mixed pattern units.
Verified ticker changes on an unheld stable identity preserve the existing setup
sequence and historical source symbols; the entry uses its applicable ticker.

The existing Phase 5 code constructs completed current-session 15-minute blocks
and checks all chronological constituent slots. Weekly input is normalized using
only available action terms, excludes current week, and delegates the 52+2/12
history and exact swing/room rules to the existing weekly filter. Unavailable
substantive weekly values are not passed as trusted opening evidence.

## Completion delivery, pauses and incomplete runs

`PortfolioEngine.on_close(timestamp, intervals=...)` and
`PositionManager.complete_interval()` add completion-only ingestion without
changing the legacy `on_close()`/`feed()` paths. A completion record must preserve
the already-known OPEN/classification/identity. Corrections to known OPEN facts
require a clean rerun. Validation precedes completion economic effects.

An explicit held completion gap pauses the whole completion phase. The checkpoint
preserves successful OPEN entries, gap exits and target partials already known
at OPEN. Reliable exact-interval replay resumes completion only; it cannot refill
an entry or credit partial proceeds twice. Original OPEN gaps retain the existing
OPEN checkpoint/replay contract. Exact causes and last valid quantities/stops
survive both paths.

An offline run with no reliable replacement stops and reports BACKTEST INCOMPLETE.
It does not skip forward or start another session. A corrected dataset can be
rerun deterministically, using a new fingerprint; the portfolio APIs also expose
the narrow reliable-replacement replay contract. Canonical tick failure or
unresolved EOD liquidity propagates immediately to shared-run termination.

The approved one-minute EOD limitation remains: the final interval spans the
entire period until official close. If verified NO_TRADE, no subsequent eligible
one-minute trade exists before close. Liquidation remains irrevocable and the
official close produces the canonical unresolved/fatal condition, on normal and
early-close sessions. No next-day fill or confirmed ending equity is manufactured.

Intraday held identity/action changes without a verified compatible contract
raise an explicit input error; no new position transformation rule is invented.

## Ledgers, summaries and reproducibility

`BacktestResult` is immutable and serializes deterministically through `to_json()`.
Decimals retain precision and Fractions retain numerator/denominator. Outputs
include:

- Trade ledger: complete immutable Phase 6 entry/approval/A-B-C-D/context/account/
  tick evidence, Phase 7 partial/final fill legs and quantities/prices/times, gross/
  costs/net/outcome, final quarantine, initial fixed dollar risk, realized net R,
  entry count and pre-entry lock context. Nested evidence is retained rather than
  discarded for a flattened report.
- Session summary: full Phase 8 BOD/cash/equity/counts/gross/cost/net/lock/fatal
  state, wins/losses/breakevens, confirmed daily return and maximum positions.
- Backtest summary: start/confirmed end, net dollars/return, sessions/entries/
  completions/outcomes, win rate, average win/loss/trade, profit factor when
  defined, largest win/loss, confirmed-equity drawdown, lock counts/reasons and
  complete/incomplete status.
- Chronological audits: source classifications/provenance, universe/window/
  normalization evidence and unchanged strategy/entry/exit/portfolio decisions.
  Audits retain actual processing order and information-availability timestamps.

Maximum drawdown samples only confirmed portfolio valuations: session starts,
trustworthy current-OPEN equity and resolved no-holding cash endpoints. It is
not an intraminute drawdown estimate or a calculation from carried reporting
marks. In an incomplete run this metric describes the confirmed prefix only.
Final ending equity/total return/net change are unavailable; earlier completed
trade metrics, partial realized state, unresolved positions and last confirmed
equity/time remain reportable. No undefined ratio becomes infinity.

The manifest contains strategy/config hashes, dataset/source identity/version,
contract version, inclusive range, all represented security IDs, dataset hash,
calendar/tick versions and optional caller-supplied code commit/version. The hash
uses canonical sorted public source records and actual relevant calendar facts,
including non-sessions. Tick callbacks must be reproducible under their declared
immutable `tick_version`; they are not introspected. Input changes alter the data
fingerprint; input storage order does not. The caller's run identifier is separate
from the full dataset hash so later-data changes do not rewrite earlier setup IDs.

## Usage, validation and Phase 10 handoff

```python
from tradingbot_backtest.historical_runner import HistoricalBacktest

# dataset implements HistoricalDataset with verified offline source facts.
result = HistoricalBacktest(dataset, code_version="reviewed-commit", run_id="research-run").run(start, end)
serialized_result = result.to_json()
```

```bash
python -m unittest discover -s tests -v
python -m compileall -q tradingbot_backtest tests
git diff --check
```

The new tests independently exercise universe boundaries, exact expected history,
splits, survivorship identity, actual complete trades, lockouts, simultaneous
ranking/allocation, OPEN versus completion, quarantine, replay, fatal stops,
reports and source-order/repeat determinism. All earlier test files and frozen
defaults remain unchanged.

Phase 10 may provide reviewed offline provider adapters, representative licensed
data and usability/report presentation on these contracts after authorization.
No real provider ingestion format, external calendar package, bulk file loader,
optimization, live scanner, broker/TradingView connection or live trading exists
here. Suitable historical data and live fill realism remain unverified.
