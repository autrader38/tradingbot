# Architecture

## Frozen historical research scope

[Strategy Spec v1.0](strategy-spec-v1.0.md), [parameters](strategy-parameters-v1.0.md), and [decision history](strategy-decision-history.md) define the authoritative approved behavior. The design phase is complete; only the separately authorized [Phase 1 foundation](phase-1-foundation.md) has been implemented.

```text
Historical security/reference/calendar/corporate-action data
                          +
Regular-session minute data / same-day premarket volume / daily-weekly history
                          |
                 Validate and normalize point-in-time
                          |
             Universe gates + one setup state per ticker
                          |
             One chronological shared-portfolio simulator
                          |
       Approved research fills + risk/cash/exposure/lockout accounting
                          |
        Reproducible audit records, complete/incomplete results, disclosures
```

The future implementation should keep price-pattern state, chronological volume history, security-reference state, portfolio accounting and run-integrity status separate. Market-data schemas must preserve classifications and information availability; no-trade is not missing data. Full fills and modeled OPEN timestamps are explicit research assumptions, not verified broker executions.

Python is the Phase 1 research/runtime foundation, using the standard library only. The components in the diagram beyond configuration/data primitives are not yet implemented. The existing Pine indicator is a non-authoritative historical prototype. Mandatory 15-minute structure and weekly swing-high room use the approved price rules, not that prototype's moving averages.

Suitable historical data are not yet established. Do not infer readiness from a frozen design or a passing syntax check. See [implementation readiness](implementation-readiness.md) for provider capabilities and meaningful validation.

## Future integration candidate, outside v1.0 execution

The earlier proposal was TradingView signals → hosted webhook bridge → IBKR paper account. TradersPost was a first evaluation candidate, not an activated selection. [Bridge notes](bridge-evaluation.md) retain verification questions.

Before any external integration, verify current service capabilities, broker permissions, protective orders, duplicate handling, reconciliation, recovery, authentication and kill/disable behavior. TradingView alerts are signals, not fills; broker executions/positions are authoritative for any future execution system. Scanner/news/order-book services and market-data licensing need their own evaluation.

Current implementation authorization covers Phase 1 foundation only: no detector, strategy simulation, service connection, order placement or live implementation. Paper/live execution realism and acceptance criteria are not supplied by the research freeze and must not be invented. Live routing requires a later explicit reviewed change.
