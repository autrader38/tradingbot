# AI Day Trader

A staged research and paper-trading project for a TradingView + Interactive Brokers workflow. The initial scope is strategy specification, data review, and paper validation. It does not place trades.

## Proposed first architecture

1. TradingView supplies charts and runs deterministic, user-approved Pine Script conditions.
2. TradingView alerts go to a hosted webhook bridge (initial candidate: TradersPost) which connects to an IBKR paper account.
3. This project maintains the strategy specification, alert contract, historical research, and independent review of paper results.
4. No custom cloud server is needed in this first phase. The hosted bridge is a third party with order-routing access, so its permissions and behavior must be reviewed and tested with paper trading before activation.

TradingView's native IBKR panel supports chart-based manual trading; it is not the webhook automation path. Capitalise.ai is not the default choice because its current IBKR availability is unclear from conflicting public information. Confirm availability with IBKR and Capitalise before relying on it. A hosted bridge is only a candidate until a paper end-to-end test proves it works on this account.

## Safety boundary

- Paper account only.
- No credentials or real webhook endpoints in this repository.
- No live order code or live trading switch.
- No strategy rules invented by the software. Define each setup and risk limit with the user first.
- A TradingView alert is not proof of an IBKR fill. Review bridge logs, broker orders, executions, and positions.

## Start here

- Read [`docs/architecture.md`](docs/architecture.md).
- Fill in [`docs/strategy-inputs.md`](docs/strategy-inputs.md) before writing entry or exit logic.
- Review [`docs/bridge-evaluation.md`](docs/bridge-evaluation.md) before connecting any account.
- Use [`strategy/README.md`](strategy/README.md) for the later Pine Script signal implementation.

## Planned phases

1. Agree on one setup and fixed paper-only risk constraints.
2. Specify an alert schema and write a deterministic TradingView strategy.
3. Validate signals without broker access, including duplicate/stale alert behavior.
4. Connect the bridge to IBKR paper and verify small, controlled test orders manually.
5. Collect and analyze paper results before considering any further automation.

This repository is not financial advice and makes no return claims.
