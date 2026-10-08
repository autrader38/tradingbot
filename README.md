# AI Day Trader

A long-only ABCD Breakout research project. **Strategy Spec v1.0 is frozen:** approved Decisions #1–47 are permanently documented. A begins the impulse, B is its high/resistance, C is the controlled pullback, and D is the confirmed breakout above B.

## Read the frozen specification

- [Canonical Strategy Spec v1.0](docs/strategy-spec-v1.0.md): final authoritative rules, timing, state machine, accounting, data behavior and audit requirements.
- [Canonical parameter table](docs/strategy-parameters-v1.0.md): every frozen numerical default and policy boundary.
- [Approved decision history](docs/strategy-decision-history.md): Decisions #1–47 and supersession map.
- [Implementation readiness](docs/implementation-readiness.md): dataset requirements, remaining implementation work and validation checklist.
- [Architecture](docs/architecture.md): research components and separately gated future integration.

**RESEARCH BACKTEST MODEL — V1.0**

**ZERO-FRICTION BASELINE**

**FULL-FILL RESEARCH ASSUMPTION — MARKET DEPTH AND PARTIAL FILLS NOT MODELED**

Freezing the rules does not prove suitable historical data are available or establish realistic live execution. The initial research model uses explicit full-fill and opening-timestamp abstractions. Zero-friction results are not expected live returns. No profitability claim is made.

## Current repository

The repository contains documentation, a reference image, and a [historical chart-only Pine prototype](strategy/README.md). It contains no completed historical backtester, scanner or execution service. The prototype's labels, parameters, pivots and moving-average filters do not implement frozen v1.0. Do not use it as the canonical strategy or connect its output to a broker.

Documentation is the currently authorized work. No backtester construction, IBKR/TradingView connection or order placement is authorized by the specification freeze. Future implementation requires separate authorization; paper and live execution require separately reviewed data, execution and integration readiness.

## Safety and next phases

Follow [AGENTS.md](AGENTS.md). Keep strategy rules deterministic and versioned; never commit credentials, account identifiers or real webhook endpoints. AI may explain results, but may not change active rules or risk limits.

After separate implementation authorization, prepare trustworthy point-in-time data and build/validate the deterministic historical research model. Later phases may evaluate realistic execution, observe paper results, scan live market data and consider reviewed automated paper execution. Live routing or a paper-to-live switch requires later explicit approval.

TradingView/IBKR with a hosted bridge was an earlier integration candidate, not a selected or connected service. [Bridge evaluation notes](docs/bridge-evaluation.md) are retained for future verification, not part of the historical fill model. A signal is not evidence of a broker fill.
