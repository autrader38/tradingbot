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

## Safety and next phases

Follow [AGENTS.md](AGENTS.md). Keep strategy rules deterministic and versioned; never commit credentials, account identifiers or real webhook endpoints. AI may explain results, but may not change active rules or risk limits.

After separate implementation authorization, prepare trustworthy point-in-time data and build/validate the deterministic historical research model. Later phases may evaluate realistic execution, observe paper results, scan live market data and consider reviewed automated paper execution. Live routing or a paper-to-live switch requires later explicit approval.

TradingView/IBKR with a hosted bridge was an earlier integration candidate, not a selected or connected service. [Bridge evaluation notes](docs/bridge-evaluation.md) are retained for future verification, not part of the historical fill model. A signal is not evidence of a broker fill.
