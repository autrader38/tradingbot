# Project rules

- This project is paper-only. Do not add live order routing, live credentials, or a paper-to-live switch unless the user explicitly approves a later, reviewed change.
- Never place or cancel a broker order during development. Integrations start in read-only or simulated modes.
- TradingView alerts are signals, not fills. Reconcile execution and position state from the broker/bridge before treating a trade as open or closed.
- Keep strategy rules deterministic and versioned. AI may explain or analyze results, but may not alter active rules or risk limits.
- Fail closed: malformed, stale, duplicated, out-of-session, or unrecognized signals must not create an order.
- Do not commit secrets, account identifiers, webhook URLs, or credentials. Use environment variables and sanitized examples.
- Do not claim profitability. Record fees, spread, slippage, rejected orders, and missed alerts in any performance analysis.
- Before implementing a strategy, get explicit definitions for its entry, exit, invalidation, sizing, and trading hours.
