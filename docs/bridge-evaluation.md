# Hosted bridge evaluation

## Candidate to evaluate first: TradersPost

Its current documentation describes TradingView webhook signals routed to Interactive Brokers and paper-account testing. It exposes signal fields for actions, quantities, stop-loss, and take-profit. Those claims establish technical compatibility, not execution quality or suitability. Confirm current account eligibility, support for the intended US equities and order types, paper-account connection, subscription costs, and required permissions directly before connecting.

## Other options

- SignalStack documents TradingView alert routing to IBKR and paper-account connection steps. Its IBKR instructions indicate that a live trading account and market-data subscription are prerequisites for setup, even if connecting a paper account. Confirm whether that applies to this account and the exact workflow.
- Capitalise.ai advertises TradingView alerts and IBKR integration on public pages, but current availability is unclear and public information conflicts. Do not build the system around it without written confirmation from IBKR/Capitalise that new US equity IBKR users can still subscribe and execute.
- TradingView's native IBKR integration is useful for manual chart trading, but use a supported webhook bridge for automatic alert routing.

## Paper acceptance checklist

Do not activate any production webhook until all of these are checked:

- Bridge is connected to the IBKR paper username/account, not live.
- A test alert creates exactly one expected paper order.
- Duplicate alerts do not create duplicate exposure, or the strategy explicitly handles repeats.
- Reject/partial-fill/cancel/reconnect behavior is understood from logs and broker state.
- The stop and exit behavior is confirmed for the selected order types and trading session.
- The alert payload contains no credentials or personal account data.
- There is a clear way to disable the alert and revoke the bridge connection.

TradingView says webhooks can fail to arrive and cancels requests when a remote endpoint takes longer than three seconds. Treat alert delivery as fallible and inspect its alert log.
