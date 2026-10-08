# Strategy artifacts

The authoritative strategy is [Frozen Strategy Spec v1.0](../docs/strategy-spec-v1.0.md), with its [parameter table](../docs/strategy-parameters-v1.0.md) and [Decisions #1–47 history](../docs/strategy-decision-history.md).

## Historical chart prototype

[`abcd_chart_prototype.pine`](abcd_chart_prototype.pine) is preserved unchanged as a chart-only research artifact. It predates the frozen specification and **does not implement Strategy Spec v1.0**.

Its A/B labels, retrospective pivot confirmation, numerical defaults, base terminology and moving-average context checks differ from approved v1.0. Do not import those behaviors into a future backtester. The approved definitions are A = impulse beginning, B = high/resistance, C = pullback, D = confirmed breakout; mandatory 15-minute and weekly checks use the frozen price-structure rules.

The prototype is an indicator, not a strategy backtest or execution system. It contains no alertcondition and cannot establish broker/account state, executable ticks, universe eligibility, portfolio limits or verified fills. No compilation/backtest or TradingView connection is claimed by this documentation change.

Never embed bridge endpoints, credentials or account identifiers in Pine or alerts. Any later prototype alignment, alert implementation, external connection or order routing requires separate authorization. No source code has been changed here.
