# Initial architecture

## Current phase: research and paper validation

```text
TradingView charts + Pine conditions
               |
         JSON alert
               v
       Hosted webhook bridge
               |
        IBKR paper account

This repository: rules, alert contract, research notes, and paper-result analysis
```

The hosted bridge avoids operating a custom public webhook server. It also means a third party handles account authorization and order routing. Keep that integration isolated from this project's research tools.

## Boundaries

- TradingView is the chart/signal source for this phase; it is not treated as a complete market-data warehouse or as proof of execution.
- The bridge routes a signal; IBKR remains the execution and position source of truth.
- Before any strategy is automated, specify symbol, direction, order type, quantity rule, maximum exposure, stop/invalidation, exit, session, expiration, and unique signal identity.
- Prefer explicit, bounded quantities and protective order behavior. Do not use an alert that can repeatedly add to a position without a defined cap.
- If delivery or broker state is uncertain, freeze new entries and reconcile through the bridge and IBKR.
- Scanner, news, Level 2, and multi-timeframe research are later phases. They need separate data-source and licensing decisions and are not assumed to be supplied by TradingView alerts.

## Decisions still open

- First setup to encode.
- Allowed symbols/universe and market hours.
- Paper account and market-data permissions.
- Exact bridge plan, broker permissions, and account connection method.
- Position sizing and maximum daily loss. No defaults should be invented.
