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

Phase 10C3C5C-PRE6 adds [the private qualified status ledger compatibility contract](docs/phase-10c3c5c-pre6-qualified-status-ledger-contract.md): independent source-aware records, exact canonical identity, required-fact compatibility views and contiguous supported-prefix assessment. All helpers are pure and unused by production callback flows. No receiver, sequence allocation, ledger publication, qualified durability or authority is added. Independent review and actual Windows validation remain required before production ledger integration; Gateway Read-Only and the first-PAPER-order block remain in force.

Follow [AGENTS.md](AGENTS.md). Keep strategy rules deterministic and versioned; never commit credentials, account identifiers or real webhook endpoints. AI may explain results, but may not change active rules or risk limits.

After separate implementation authorization, prepare trustworthy point-in-time data and build/validate the deterministic historical research model. Later phases may evaluate realistic execution, observe paper results, scan live market data and consider reviewed automated paper execution. Live routing or a paper-to-live switch requires later explicit approval.

TradingView/IBKR with a hosted bridge was an earlier integration candidate, not a selected or connected service. [Bridge evaluation notes](docs/bridge-evaluation.md) are retained for future verification, not part of the historical fill model. A signal is not evidence of a broker fill.
