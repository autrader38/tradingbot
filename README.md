# AI Day Trader

A long-only ABCD Breakout research project. **Strategy Spec v1.0 is frozen:** approved Decisions #1–47 are permanently documented. A begins the impulse, B is its high/resistance, C is the controlled pullback, and D is the confirmed breakout above B.

## Read the frozen specification

- [Canonical Strategy Spec v1.0](docs/strategy-spec-v1.0.md): final authoritative rules, timing, state machine, accounting, data behavior and audit requirements.
- [Canonical parameter table](docs/strategy-parameters-v1.0.md): every frozen numerical default and policy boundary.
- [Approved decision history](docs/strategy-decision-history.md): Decisions #1–47 and supersession map.
- [Implementation readiness](docs/implementation-readiness.md): dataset requirements, remaining implementation work and validation checklist.
- [Architecture](docs/architecture.md): research components and separately gated future integration.
- [Phase 1 foundation](docs/phase-1-foundation.md): Python data/configuration primitives and offline test commands.
- [Phase 2 A/B detector](docs/phase-2-ab-detector.md): deterministic per-ticker activation, impulse and B confirmation with supplied historical inputs.
- [Phase 3 C development](docs/phase-3-c-detector.md): potential C, consolidation, higher lows and immutable C locking evidence.
- [Phase 4 D confirmation](docs/phase-4-d-detector.md): elapsed breakout timers, attempts, volume qualification and pending-entry signal handoff.
- [Phase 5 entry validation](docs/phase-5-entry-validation.md): opening-only checks, mandatory 15-minute/weekly context and approved candidates without fills.
- [Phase 6 trade construction](docs/phase-6-trade-construction.md): supplied ticks/account state, exact levels/sizing and simulated zero-friction opening entries.
- [Phase 7 position management](docs/phase-7-position-management.md): single-position stops/2R/runner/EOD exits, data pause/incomplete outputs and final-exit quarantine.
- [Phase 8 portfolio engine](docs/phase-8-portfolio-engine.md): shared OPEN/completion ordering, current-OPEN allocation, daily risk locks, cash/P&L and incomplete-run propagation.
- [Phase 9 historical backtest](docs/phase-9-historical-backtest.md): provider-neutral historical contracts, point-in-time universe, chronological end-to-end runner, exact results and reproducibility manifest.
- [Phase 10C1 paper integration foundation](docs/phase-10c1-paper-integration.md): broker-neutral models, backend safety gates, fake execution lifecycle and an IBKR adapter restricted to an in-memory transport.

**RESEARCH BACKTEST MODEL — V1.0**

**ZERO-FRICTION BASELINE**

**FULL-FILL RESEARCH ASSUMPTION — MARKET DEPTH AND PARTIAL FILLS NOT MODELED**

Freezing the rules does not prove suitable historical data are available or establish realistic live execution. The initial research model uses explicit full-fill and opening-timestamp abstractions. Zero-friction results are not expected live returns. No profitability claim is made.

## Current repository

The repository contains frozen documentation, a dependency-free Python strategy stack, a provider-neutral historical universe/session runner, exact research results and offline tests, a reference image, and a [historical chart-only Pine prototype](strategy/README.md). The runner accepts supplied historical data; real provider ingestion, representative historical coverage and integration services remain unverified or unimplemented. The prototype's labels, parameters, pivots and moving-average filters do not implement frozen v1.0. Do not use it as the canonical strategy or connect its output to a broker.

The user separately authorized Phases 1–9: foundation, A/B/C/D detection, opening/context validation, simulated entries/exits, offline portfolio orchestration and a provider-neutral historical research runner. This does not authorize external provider connections, IBKR/TradingView connections or broker order placement. Later phases require separate authorization; paper and live execution require separately reviewed data, execution and integration readiness.

Separately authorized Phase 10C1 adds an isolated offline broker foundation. **NO REAL BROKER ORDERS ARE ENABLED BY THIS PHASE.** LIVE submission is hard blocked; the IBKR adapter accepts only the bundled in-memory transport. No IBKR session, provider connection, credentials or external dependency is added. The historical backtester remains unchanged.

Phase 10C2 adds an optional [local IB Gateway read-only transport and diagnostics](docs/phase-10c2-real-ibkr-readonly.md), with lazy official TWS API dependency loading. **ORDER TRANSMISSION REMAINS DISABLED.** Account mode remains independently UNKNOWN. The user reported successful local qualification with official IBKR TWS API 10.50.2; this phase makes no real connection from Codex.

Phase 10C3A adds [explicit local paper-account enrollment](docs/phase-10c3a-paper-enrollment.md), a salted account fingerprint and separately authenticated local record. Enrollment status remains separate from account mode: **UNKNOWN and UNUSABLE remain unchanged even when enrollment is MATCHED.** All broker writes remain blocked; no account identifier is persisted.

Phase 10C3B adds [memory-only session authorization](docs/phase-10c3b-paper-authorization.md) with explicit arming, scoped capabilities and lifecycle revocation. **ARMED DOES NOT ENABLE BROKER WRITES.** AccountMode remains UNKNOWN; all six legacy/protobuf write IDs remain blocked. The user reported successful Windows enrollment persistence across a fresh process; this does not establish broker PAPER attestation.

Phase 10C3C1 adds a [separate offline paper write-transport foundation](docs/phase-10c3c1-paper-write-transport.md): exact SDK/wire contracts, generation-bound order IDs and internal dispatch outcomes. **NO REAL CONNECTION OR ORDER TRANSMISSION.** The existing read-only path remains unchanged; production authorization/risk/dispatch integration is deferred to Phase 10C3C2.

Phase 10C3C2A adds an [internal offline atomic NEW-entry bridge](docs/phase-10c3c2a-atomic-paper-entry-dispatch.md), combining explicit PLACE_ORDER authorization, authenticated enrollment, existing risk gates, controls and verified contracts with C1 private dispatch. One coordinator is permanently bound to each transport; exact challenge/proof validation occurs inside final dispatch commitment, and reconciliation outranks pending confirmation. **NO REAL PAPER TRADING IS ENABLED.** AccountMode remains UNKNOWN; SDK normal return means pending confirmation only. Production Broker APIs remain read-only and Gateway settings remain unchanged. Its local SDK return still requires the separately reviewed callback layer below.

Phase 10C3C2B adds [offline callback attribution and confirmation](docs/phase-10c3c2b-paper-callback-confirmation.md): exact validated orderRef, immutable pending identity, sanitized bounded callback evidence and transport-lifetime sequence. Strong broker observation keeps further entries blocked for local state reconciliation; executions, conflicts and generation loss invalidate authorization and require reconciliation. **BROKER_OBSERVED DOES NOT MEAN STRATEGY ACCEPTANCE.** No real connection/order, PAPER attestation, reconciliation clear or production write route is provided. Gateway Read-Only remains unchanged.

Phase 10C3C2C1 adds [private read-side reconciliation evidence](docs/phase-10c3c2c1-read-reconciliation-evidence.md): immutable exact identity, protobuf presence, bounded capture and explicit collection completeness. Public snapshots remain unchanged. **CAPTURE DOES NOT CLEAR RECONCILIATION OR ENABLE ORDERS.** Future C3C2C2 must consume a fresh post-dispatch collection; completed/execution history remains coverage-limited.

Phase 10C3C2C2A adds a [private one-shot fresh collection handshake](docs/phase-10c3c2c2a-reconciliation-handshake.md), binding retained pending state to a later read collection and its exact public/private snapshot pair. It captures provenance only; recovery and barrier clearing remain deferred. No real connection or broker write is enabled.

Phase 10C3C2C2 adds a [pure offline reconciliation evaluator](docs/phase-10c3c2c2-paper-reconciliation-evaluator.md) over consumed immutable handshake bundles. Its frozen lifecycle/quantity assessment is evidence only: no collection, callback acknowledgement, portfolio recovery, barrier clearing, PAPER attestation or trading permission.

Phase 10C3C2C3A adds a [pure offline advisory recovery planner](docs/phase-10c3c2c3a-paper-recovery-planner.md), recomputing that assessment and reporting independent evidence, control, portfolio, order-management, session and authorization requirements. FILLED does not establish portfolio recoverability. No recovery application, barrier clearing, transport replacement, authorization re-arm or trading permission is added.

Phase 10C3C4A adds a [private execution journal contract and pure replay model](docs/phase-10c3c4a-paper-execution-journal.md), preserving immutable ownership, consumed IDs, invocation uncertainty and sanitized observations. **THIS IS NOT DURABLE STORAGE.** No file/database, dispatch hook, restart recovery, barrier clearing or PAPER write is added. Transactional storage and write-ahead integration require separate phases.

Phase 10C3C4B adds [private transactional SQLite/WAL journal storage](docs/phase-10c3c4b-sqlite-journal-storage.md) with verified FULL synchronization, canonical codecs, hash-chain validation, durable semantic quarantine and conservative commit uncertainty. It adds no dispatch hook, broker write, recovery authority or startup ownership adoption. Write-ahead integration remains a separately reviewed phase.

## Safety and next phases

Phase 10C3C4C-PRE1 adds [private dispatch-journal context and authenticated account binding](docs/phase-10c3c4c-pre1-dispatch-journal-context-binding.md): pure reference projection with canonical configuration identity and an exact in-process account-association receipt. No dispatch/storage integration, broker write, PAPER attestation or recovery authority is added. Independent review and actual Windows validation were reported complete before C3C4C; Gateway Read-Only remains required.

Phase 10C3C4C-PRE2 adds [private reservation-bound dispatch commitment](docs/phase-10c3c4c-pre2-reservation-dispatch-commitment.md), binding the actual allocated ID and exact intended order to arrival-aware one-shot final proof. Deferred callback bookkeeping cannot hide reserved-ID evidence, errors or connection-closed arrivals. No journal/storage integration or real execution is enabled by PRE2. Independent review and actual Windows validation were reported complete before C3C4C; Gateway Read-Only remains required and the first real PAPER order remains blocked.

Phase 10C3C4C adds [private durable write-ahead dispatch integration](docs/phase-10c3c4c-write-ahead-dispatch-journal.md). Every coordinator-bound NEW dispatch requires an exact lifetime/thread-bound authenticated journal owner and a committed, verified invocation marker before fresh gate checks and PRE2 commitment. SDK normal return is durably recorded separately; storage failure or post-marker uncertainty permanently inhibits further writes. This phase itself adds no durable callbacks, restart adoption, recovery authority or production position management. Independent review and actual Windows validation were reported complete before C3C4D; Gateway Read-Only stays enabled and the first real PAPER order remains blocked.

Phase 10C3C4D-PRE1 adds [private memory-only commission capture](docs/phase-10c3c4d-pre1-commission-capture.md) for both callback variants: signed amounts, deterministic local economic fingerprints, actual local receipt times, generation/sequence provenance and bounded staging under arrival synchronization. This prerequisite itself adds no SQLite callback append, durable progress, attribution or recovery authority. Independent review and actual Windows validation were reported complete before C3C4D; the installed Windows SDK supplies the modern fees callback.

Phase 10C3C4D adds [private durable callback persistence](docs/phase-10c3c4d-durable-callback-persistence.md): creator-thread-only evidence draining, exact attribution, bounded commission lookahead/staging, verified prefix progress and an independent persistence cursor. Callback threads never access SQLite; undurable evidence blocks bound entries, and persistence never clears safety barriers or grants recovery/restart authority. Independent review and actual Windows validation were reported complete before C3C5A. Gateway Read-Only remains enabled and the first real PAPER order remains blocked.

Phase 10C3C5A adds [private entry-execution accounting](docs/phase-10c3c5a-paper-execution-accounting.md): one exact lifetime-bound journal consumer, immutable historical acquisition lots, exact quantity/notional/rational averages, known signed commission subtotals and explicit coverage/currentness. Current inventory, dispositions, fee finality and P&L remain unknown. No protection, recovery, barrier clearing or trading permission is added. Independent review and actual Windows validation were reported complete before C3C5B. Gateway Read-Only remains required and the first real PAPER order remains blocked.

Phase 10C3C5B adds [private historical entry lifecycle/remainder evidence](docs/phase-10c3c5b-working-order-ownership.md): one exact C3C5A consumer, verified immutable handoff, same-event observations, progression/contradiction diagnostics and conservative historical model bounds. Current remainder, canceled quantity, inventory and protective sizing remain unqualified. No broker query/write, recovery or barrier clearing is added. Independent review and actual Windows validation were reported complete before C3C5C-PRE1. Gateway Read-Only remains required and the first real PAPER order remains blocked.

Phase 10C3C5C-PRE1 adds [private commission-aware reconciliation compatibility](docs/phase-10c3c5c-pre1-commission-reconciliation-compatibility.md): the evaluator validates the existing signed commission model and preserves unread sequence coverage, returning conservative uncertainty for unincorporated current-generation commission evidence. No attribution, quantity/lifecycle effect, fee completeness, cursor advancement, session continuity or trading/recovery authority is added. Independent review and actual Windows validation were reported complete before PRE3; PRE2 qualified the installed SDK source contract. Continuity remains deferred; Gateway Read-Only stays enabled and the first real PAPER order remains blocked.

Phase 10C3C5C-PRE3 adds [private order-status source values and pure adapters](docs/phase-10c3c5c-pre3-order-status-source-model.md): same-event immutable values, explicit protobuf presence, legacy zero ambiguity, unavailable sentinels and exact numeric/raw-decoded validation. No callback capture, reconciliation integration, ownership, current remainder, inventory, recovery or trading authority is added. Independent review and actual Windows validation were reported complete before PRE4. Gateway Read-Only remains enabled and the first real PAPER order remains blocked.

Phase 10C3C5C-PRE4 adds [private decoder-scoped order-status pairing](docs/phase-10c3c5c-pre4-order-status-pairing.md): isolated per-instance interception, exact nested invocation/thread receipts, PRE3 consistency checks and guaranteed orphan cleanup. Production callbacks, journals and reconciliation remain unchanged; no capture, currentness, ownership or management authority is added. Independent review and actual Windows validation were reported complete before PRE5. Gateway Read-Only remains required and the first real PAPER order remains blocked.

Phase 10C3C5C-PRE5 adds [private decoder registration and fenced status delivery](docs/phase-10c3c5c-pre5-status-delivery.md): post-connect registration, a run gate, exact decoder/generation binding, positive LEGACY/PROTOBUF provenance and one-shot immutable handoffs. No production receiver or callback-ledger publication is enabled; an unaccepted handoff permanently inhibits the transport. No journal, currentness, ownership, inventory or management authority is added. Independent review and actual Windows validation were reported complete before PRE6. Gateway Read-Only remains required and the first real PAPER order remains blocked.

Phase 10C3C5C-PRE6 adds [the private qualified status ledger compatibility contract](docs/phase-10c3c5c-pre6-qualified-status-ledger-contract.md): independent source-aware records, exact canonical identity, required-fact compatibility views and contiguous supported-prefix assessment. All helpers are pure and unused by production callback flows. No receiver, sequence allocation, ledger publication, qualified durability or authority is added. Independent review and actual Windows validation were reported complete before PRE7; Gateway Read-Only and the first-PAPER-order block remain in force.

Phase 10C3C5C-PRE7 adds [private callback ledger capture integrity](docs/phase-10c3c5c-pre7-callback-ledger-capture-integrity.md): exact duplicate adjacency, an immutable lifetime first-loss marker, frozen truthful prefixes and interruption-safe bounded capture. Paging cannot manufacture a missing occurrence and existing C3C4D checks prevent durable progress across loss. PRE7 itself adds no qualified publication or management authority. Independent review and actual Windows validation were reported complete before PRE8; Gateway Read-Only and the first-PAPER-order block remain in force.

Phase 10C3C5C-PRE8 adds [private qualified status ledger publication and consumer stops](docs/phase-10c3c5c-pre8-qualified-status-ledger-publication.md): one exact armed receiver, one callback sequence per qualified occurrence, complete PRE3 source semantics under PRE7 capture integrity and conservative dispatch/reconciliation boundaries. Qualified evidence remains memory-only and blocks C3C4D durable progress; no currentness, finality, ownership or management authority is added. Independent review and actual Windows validation were reported complete before PRE9-PRE1. Gateway Read-Only remains required and the first real PAPER order remains blocked.

Phase 10C3C5C-PRE9-PRE1 added [the initially inactive v2 callback occurrence coverage contract](docs/phase-10c3c5c-pre9-pre1-v2-callback-coverage-contract.md): strict historical replay proof for direct sources, adjacent duplicates, untracked callbacks and commission occurrences. Independent review and actual Windows validation were reported complete before PRE9. V1 bytes and original replay semantics remain preserved.

Phase 10C3C5C-PRE9 adds [qualified historical source durability and atomic V2 producer activation](docs/phase-10c3c5c-pre9-qualified-status-durability.md): complete PRE3/PRE6 status semantics and one verified durable explanation for every covered callback occurrence before progress. Fresh private journals explicitly use V2; historical V1 remains readable without migration. Callback capture, accounting/lifecycle authority, public APIs and wire policies remain unchanged. Independent review and actual Windows validation were reported complete before C3C5D-PRE1. Gateway Read-Only remains required and the first real PAPER order remains blocked.

Phase 10C3C5D-PRE1 adds [private authenticated same-session open-order collection provenance](docs/phase-10c3c5d-pre1-fresh-open-order-collection.md): one bounded memory-only request on the original valid association, explicit end-marker completion and an exact one-shot receipt. Local request identity is not broker correlation; no refresh, renewal, currentness, ownership, journal change or trading authority is added. Independent review and Windows validation were reported complete before PRE2; Gateway Read-Only and the first-PAPER-order block remain in force.

Phase C3C5D-PRE2 adds [private authenticated same-session execution collection provenance](docs/phase-10c3c5d-pre2-fresh-execution-collection.md): one bounded memory-only query with an exact SDK request ID, matching execDetailsEnd and the original authenticated association. Every accepted occurrence is retained without accounting incorporation; history remains limited and no currentness, ownership, late-fill guarantee or trading authority is added. Independent review and actual Windows validation were reported complete before PRE3; Gateway Read-Only and the first-PAPER-order block remain in force.

Phase C3C5D-PRE3 adds [private authenticated same-session completed-order collection provenance](docs/phase-10c3c5d-pre3-fresh-completed-order-collection.md): one bounded memory-only interval using the fixed reqCompletedOrders(False) policy, explicit untagged completion and exact one-shot receipt provenance. No broker request ID, combined evaluator, currentness, ownership, execution inference, accounting or trading authority is added. Independent review and actual Windows validation were reported complete before PRE4-PRE1; Gateway Read-Only and the first-PAPER-order block remain in force.

Phase C3C5D-PRE4-PRE1 adds [initially inactive V3 decision-boundary durability support](docs/phase-10c3c5d-pre4-pre1-decision-boundary-v3-contract.md): a private fixed-UTC decision-time/session/generation/frontier declaration with strict PRE9 prefix coverage and a separate replay companion. V1/V2 bytes and meanings and SQLite schema/hash remain unchanged; this prerequisite initially left production on V2. No producer, interval evaluator, currentness or trading authority was added. Independent review and actual Windows validation were reported complete before PRE4-PRE2.

Phase C3C5D-PRE4-PRE2 adds [the sole private qualified decision-boundary producer](docs/phase-10c3c5d-pre4-pre2-qualified-decision-boundary-producer.md): fresh runtime journals use V3, and an explicit owner-thread call captures internal UTC time and the already-durable callback frontier under arrival serialization. Post-capture callbacks remain durably ordered after the historical boundary. No status evaluator, collection combination, currentness or trading authority is added. Independent review and actual Windows validation were reported complete before PRE4; Gateway Read-Only, restart quarantine and the first-PAPER-order block remain in force.

Phase C3C5D-PRE4 adds [pure qualified decision-time status evidence](docs/phase-10c3c5d-pre4-decision-time-status-evidence.md): exact V3 boundary/reference and source-aware numeric identity matching select the latest qualified status through the certified callback frontier. Results freeze at the boundary prefix without future lookahead or fabricated wall-clock intervals. No collection combination, lifecycle/remainder, execution inference, ownership or trading authority is added. Independent review and actual Windows validation were reported complete before PRE5-PRE1; the first real PAPER order remains blocked.

Phase C3C5D-PRE5-PRE1 adds [private same-session collection receipt preparation](docs/phase-10c3c5d-pre5-pre1-fresh-collection-bundle-preparation.md): exact PRE1/PRE2/PRE3 receipts are prevalidated and consumed by their original owners before any later decision-boundary append. The memory-only issuer token retains independent intervals and limitations; partial consumption fails terminally. No source correlation, PRE4 binding, currentness, ownership or trading authority is added. Independent review and actual Windows qualification were reported complete before PRE5.

Phase C3C5D-PRE5 adds [private prepared-collection and durable decision evidence binding](docs/phase-10c3c5d-pre5-prepared-decision-evidence.md): the exact preparation issuer and original journal load prove a selected boundary follows the prepared prefix and precedes the original deadline. PRE4 outcomes and independent source limitations remain unchanged. No observation correlation, contradiction resolution, currentness, ownership or trading authority is added. Independent review and actual Windows qualification were reported complete before PRE6.

Phase C3C5D-PRE6 adds [private cross-source identity evidence](docs/phase-10c3c5d-pre6-cross-source-identity-evidence.md): the exact PRE5 issuer qualifies retained numeric order/client pairs and pair-scoped known permId conflicts. Source presence, duplicates and independent intervals remain intact; status/economic differences are not identity contradictions. No attempt attribution, lifecycle/remainder, currentness, accounting or trading authority is added. Independent review and actual Windows qualification were reported complete before PRE7.

Phase C3C5D-PRE7 adds [private exact local attempt attribution](docs/phase-10c3c5d-pre7-exact-attempt-attribution.md): the exact PRE6 issuer and frozen decision-boundary replay connect broker-supported numeric order/client identity to one reserved, invocation-enabled local attempt. SDK normal return is not required. No lifecycle/remainder, accounting, management or trading authority is added. Independent review and actual Windows qualification were reported complete before PRE8.

Phase C3C5D-PRE8 adds [private attributed execution evidence](docs/phase-10c3c5d-pre8-attributed-execution-evidence.md): the exact PRE7 issuer qualifies frozen durable attempt executions and PRE6 fresh execution candidates. Agreeing execIds count once toward an exact historical execution lower bound, preserving all supplied occurrence references; proven conflicts withhold the bound. No completeness, lifecycle/remainder, current position, accounting, management or trading authority is added. Independent review and actual Windows qualification were reported complete before PRE9.

Phase C3C5D-PRE9 adds [private historical decision-boundary lifecycle evidence](docs/phase-10c3c5d-pre9-decision-lifecycle-evidence.md): the exact PRE8 issuer and retained PRE4 selected status support historical reported classifications and an exact execution-derived possible remaining upper bound. Only selected-status/local-order contradictions are classified; independent execution/status quantity differences do not prove conflict. Missing source fields remain missing. No broker currentness, current remainder, inventory, accounting, management or trading authority is added. Independent review and actual Windows qualification were reported complete before VELOCITY-DASHBOARD-PRE1.

## Open the Velocity Dashboard

From the repository folder on Windows, run:

```powershell
py -3.12 -m tradingbot_dashboard --open
```

The browser opens the local dashboard at `http://127.0.0.1:8765`. No installation,
internet connection or broker connection is needed. Market data, scanner states,
trade markers and performance are clearly labeled **DEMO**. Candles, timeframes,
scanner selection and chart inspection work offline. Bot/trading state is read-only;
**TRADING BLOCKED** remains visible and no order controls are provided.

The [interactive scanner](docs/velocity-dashboard-pre2.md) screens 24 synthetic
stocks locally. Adjust price, change, market cap, average volume, volatility,
relative volume and setup score; search tickers or sort results immediately.
Defaults show 13 matches. Reset restores defaults; Loosen updates the visible
filters. **DEMO MARKET DATA — NO LIVE FEED** applies to the entire scanner.
These browser-only settings do not change Strategy Spec v1.0 or risk rules.

Keep the console open; **Ctrl+C** stops the server. Omit `--open` to start without
opening a browser. See the [dashboard guide](docs/velocity-dashboard-pre1.md).
Gateway Read-Only remains required, restart quarantine mandatory, AccountMode UNKNOWN,
public snapshot mode None and LIVE unsupported. The first real PAPER order remains blocked.

An optional [real EOD market-data preview](docs/velocity-market-data-pre1.md) uses
Massive Stocks Basic through a separate standard-library adapter. Default startup
stays offline DEMO. For personal/non-business use, configure your own
`MASSIVE_API_KEY` securely in the server environment, then start:

```powershell
py -3.12 -m tradingbot_dashboard --market-data-preview --watchlist "AAPL,MSFT" --open
```

This makes **no provider request on startup**. In Market Data, explicitly load one
EOD bar or bounded daily history for a selected ticker. The preview shows source
dates, quality, missing scanner fields and EOD/DELAYED/STALE/UNAVAILABLE states.
The demo scanner and main chart remain separate. No credential goes to the browser;
there is no broker connection or trading authority. See the guide for masked
PowerShell credential entry, current provider terms and Windows validation.
The [PRE1A timestamp compatibility repair](docs/velocity-market-data-pre1a.md)
preserves endpoint-specific source timestamps; it adds no provider or broker requests.
The [real EOD watchlist scanner](docs/velocity-market-data-pre2.md) calculates exact
close-to-close change, previous ten-bar average volume and EOD relative volume from
manually loaded history. Load date-scoped market-cap reference separately. Its
one-to-five-ticker research filters are independent of DEMO and strategy rules;
volatility/ABCD qualification and trading remain unavailable. Default startup is
still offline DEMO; the existing preview flag enables only explicit manual loads.

The [EOD market discovery panel](docs/velocity-market-data-pre3.md) can now load one
dated Massive grouped market summary on an explicit click, then filter/search/sort
its validated preliminary candidates locally in pages of 50. Default price is
strictly below $5; the optional same-day-volume heuristic is off at zero and is
not the previous ten-day average rule. Select up to five discovered symbols for a
visible session-only PRE2 research shortlist, then manually load history and dated
reference separately. The configured watchlist and DEMO scanner stay unchanged.
**PRELIMINARY EOD CANDIDATES — NOT FULLY QUALIFIED** is not a live signal or proof
of complete U.S. coverage. No startup polling, automatic follow-up, persistence,
commercial redistribution or trading authority. PRE3A Windows validation was
reported complete. [PRE3B safe rejection diagnostics](docs/velocity-market-data-pre3b.md)
adds fixed validation-stage codes without publishing raw responses or weakening
the parser. The actual provider rejection remains unproven pending a later manual
code report; PRE3B requires independent review and actual Windows qualification.
The user subsequently confirmed `ROOT_FIELDS_UNEXPECTED`.
[PRE3C root-field diagnostics](docs/velocity-market-data-pre3c.md) adds only fixed
categories and bounded counts; extra fields still reject the entire response.
The actual extra metadata remains unidentified until a later category report.
PRE3C requires independent review and actual Windows qualification.

Follow [AGENTS.md](AGENTS.md). Keep strategy rules deterministic and versioned; never commit credentials, account identifiers or real webhook endpoints. AI may explain results, but may not change active rules or risk limits.

After separate implementation authorization, prepare trustworthy point-in-time data and build/validate the deterministic historical research model. Later phases may evaluate realistic execution, observe paper results, scan live market data and consider reviewed automated paper execution. Live routing or a paper-to-live switch requires later explicit approval.

TradingView/IBKR with a hosted bridge was an earlier integration candidate, not a selected or connected service. [Bridge evaluation notes](docs/bridge-evaluation.md) are retained for future verification, not part of the historical fill model. A signal is not evidence of a broker fill.
