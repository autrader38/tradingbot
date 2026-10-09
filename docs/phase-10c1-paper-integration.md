# Phase 10C1 — Offline paper broker integration foundation

**NO REAL BROKER ORDERS ARE ENABLED BY THIS PHASE.**

This is an isolated execution contract and simulation layer. It does not change
Strategy Spec v1.0 or the Phase 1–9 historical backtester. There is no IBKR SDK,
TWS/Gateway connection, socket transport, broker login, provider integration,
credential, account number, or live-routing switch. No dependencies are added.

## Architecture and ownership

The intended future boundary is:

```text
Market data → strategy → portfolio/risk → generic Broker → broker adapter
```

`tradingbot_broker.broker.Broker` is the provider-neutral protocol. It represents
connection status, explicitly reported account mode, account summaries and
positions, placements, cancellations, modifications, order status, executions,
commissions and backend controls. Existing historical engines are not wired to a
broker by this phase. A future execution coordinator will translate validated
decisions into these contracts; strategies need not import IBKR code.

`SimulatedBroker` supplies one shared safety/lifecycle implementation to
`FakeBroker` and `IBKRAdapter`. Only the adapter knows IBKR contract IDs, order
type codes, status strings and execution-side codes. Exact numerical helpers are
reused read-only from the historical package; no historical module is modified.

## Immutable contracts and exact amounts

`OrderRequest`, `OrderAcknowledgement`, `OrderStatus`, `Fill`, `Commission`,
`Position`, `AccountSnapshot`, `RiskPermission` and audit records are frozen.
Requests support BUY/SELL, MARKET/LIMIT/STOP and DAY. Required prices, identities,
currency, timezone-aware creation time and an explicitly supplied expiration are
validated. Prices/quantities/cash use finite Decimals; weighted fill averages use
exact Fractions. Binary floats and truthy non-boolean permission/control values
are rejected. No quantity sizing, tick guessing, currency conversion or session
calendar rule is introduced here.

Broker positions may represent externally reported long or short holdings. This
does not authorize a short strategy or alter the frozen long-only strategy.
Fractional quantities can be represented by the generic execution contracts;
the frozen historical sizing engine still uses whole shares unchanged.

## Paper interlocks

Global trading starts disabled. Each placement and modification requires:

- Configured mode and requested mode PAPER. LIVE is always hard blocked.
- A connected broker with an explicitly supplied, verified PAPER account snapshot.
- Account information available at the action time and before its supplied expiry.
- Global trading enabled, emergency stop inactive, and entries not paused for an
  ENTRY request.
- An order within its supplied creation/expiration window.
- An available, unexpired external portfolio/risk permission granting entry and
  matching the **entire** immutable order, including quantity and prices.
- No unresolved execution reconciliation problem; IBKR also requires verified,
  applicable, already-available contract metadata matching identity/symbol/currency.

These checks run in the backend, under the same reentrant lock as dispatch and
control changes. There is no UI-only safety gate or automatic mode fallback.
All independently knowable failures are retained in deterministic order; the
first reason is the primary label. Availability is checked before inspecting
unavailable account-mode or permission fields. Expiration boundaries are exclusive.

Risk permission is supplied by an external risk engine; this layer does not
recalculate daily lockouts or reproduce historical economics. Permissions are
not inferred from buying power, a UI switch or the absence of an error.
Validation of actual sessions, strategy eligibility, order-level ticks and
portfolio limits remains the responsibility of the future execution/risk input
producer. No real connection may be enabled before that bridge is reviewed.

Client order IDs are reserved on **every** attempted placement, including blocked
attempts. An old intent cannot silently become executable after resume/reconnect;
a fresh reviewed intent and ID are required. Modification keeps identity,
direction, order type and validity window fixed and needs new matching permission.
It cannot reduce total quantity to/below quantities already filled.

## IBKR adapter and transport phase lock

`IBKRTransport` describes the future boundary. The adapter constructor currently
accepts **only the exact bundled `InMemoryIBKRTransport` class**. Arbitrary clients
and subclasses are rejected before their methods can run. It has no endpoint,
authentication or injected callable. This deliberate phase lock must not be removed
as a routine setup change.

Contracts explicitly supply security ID, symbol, conId, exchange, currency,
verification, provenance and availability/validity. There is no implicit SMART
route, guessed conId or symbol-only security inference. IBKR translation uses
MKT/LMT/STP, DAY and exact quantity/prices. Missing/expired/future/unverified
contracts block before dispatch. An acknowledgement does not establish a fill.
The public in-memory transport remains a simulation fixture; direct fixture calls
are not an authorized application submission path or a real network transport.

## Events, disconnects and reconciliation

Orders can progress through submitted, acknowledged, working, partially filled,
filled, cancelled, rejected and expired states. The in-memory transport may
acknowledge submission immediately but never manufactures executions. Tests supply
execution evidence explicitly. Order-status quantities cannot fabricate fills.

Event occurrence/execution timestamps are distinct from receipt/audit timestamps.
Calls and receipt times must be monotonic; delayed source events can retain older
origin timestamps. Duplicate event IDs and execution IDs cannot apply fills twice.
Conflicting IDs, overfills, identity mismatches and invalid lifecycle regressions
preserve previously accepted quantities and require reconciliation. Repeated status
reports can confirm a state without adding an execution.

Each accepted order retains its actual submission timestamp separately from request
creation and later modification times. Lifecycle events and executions cannot
predate submission. Equality is permitted, and delayed receipt of a valid later
execution is permitted. In-place modification preserves the original submission
boundary. Rejected events retain accepted quantities and audit the submission and
source timestamps; they do not create economic effects.

A cancellation request is not a confirmed cancellation unless the simulated
response or subsequent callback confirms it. Partial executions remain recorded
after cancellation. Disconnect preserves known orders/executions, blocks new
submissions and clears connected account evidence. Reconnect needs an explicit
account report and never resubmits old orders or clears an emergency stop.
Known executions may still arrive during a disconnection.

Connection occurrence chronology is independent of callback receipt and execution
chronology. Local connect/disconnect actions use their action timestamp. Older
connection events are ignored with an audit record. At equal occurrence timestamps,
disconnect wins regardless of callback order; a reconnect must occur later than
that disconnect. A stale event cannot restore account proof or alter safety
controls. These are conservative integration conventions, not strategy rules.

Dispatch/cancel/modify exceptions produce an unknown-outcome/reconciliation audit,
not a guessed fill or automatic retry. A terminal-order fill race, late lifecycle
regression or commission conflict also fails closed. There is no automated
reconciliation reset in this phase. Future real transport work must buffer/reconcile
out-of-order IBKR callbacks and authoritative account/open-order/execution snapshots
before reopening submissions. This foundation is not a real-session-ready client.

## Account and commission evidence

Account cash, equity, buying power and holdings are supplied snapshots. Fills and
commission reports are stored separately; they do not invent authoritative broker
balances or run a second portfolio ledger. Fake tests may explicitly report new
balances/positions after executions. The future risk bridge must reconcile these
snapshots and working-order reservations before granting new permissions.

All account installation paths (handshake, explicit report and reconnect callback)
share one freshness check using `observed_at`. The last accepted snapshot is
retained across disconnect separately from connected account proof. Older snapshots
are rejected without replacing accepted evidence. An identical snapshot at the same
observation timestamp is idempotent; any conflicting snapshot at that timestamp is
rejected without merging, retains the first accepted evidence and latches submission
reconciliation. Newer available observations update normally. Unavailable metadata
cannot advance this boundary or expose substantive values in rejection evidence.
This does not implement account-version-bound risk permissions or working-order
reservations; those remain prerequisites for a future execution/risk bridge.

No commission report means **unknown**, not zero. Commission reports can arrive
after executions and retain exact amounts/currency. The historical ZERO-FRICTION
and FULL-FILL RESEARCH assumptions apply only to the unchanged backtester.
Broker execution contracts deliberately support delayed, rejected and partial
fills; they make no live fill claim.

## Emergency controls

Pause/resume controls affect new ENTRY intents. Resume does not enable global
trading, clear daily risk denial or release the latched emergency stop.
Emergency stop blocks every new placement/modification immediately under the
backend dispatch lock. No recovery/unlatch operation is exposed in this phase.

Cancel-working-orders acts only on simulated known paper orders and is permitted
while trading is disabled/emergency-stopped: cancellation is not a new submission.
It remains subject to PAPER account/connection proof and confirmation semantics.

`flatten_positions()` returns a deterministic **dry-run plan of unsent orders**,
including opposite-side quantities for reported holdings. It never fills, changes
cash/holdings or submits orders. Plans can be inspected during an emergency.
Dispatching any plan request still uses the universal placement gates and explicit
matching risk permission. There is no emergency/risk bypass; a future separately
reviewed risk-reducing execution lane would need its own authorization.

## Audit contract

Audit records retain action time, configured/account mode, immutable order payload
(symbol, side, quantity, type, prices, currency, intent and client ID), broker ID,
state, all failure codes, fill quantities/prices/IDs, source/receipt times,
commissions, controls and available risk provenance. Records are deterministic
in-memory immutable views; a durable sink is future integration work.
Detail entries must themselves be immutable two-element tuples containing only
supported immutable scalar values. Mutable nested pairs and values are rejected;
caller-owned containers cannot subsequently change an accepted audit record.

No account-number or credential fields exist. Raw exception/IBKR message text is
not copied into audit records; numeric broker error codes and structured internal
reasons remain. Callers must use anonymous non-secret source/client/event identifiers.

## Validation and future connection step

```bash
python -m unittest discover -s tests -v
python -m compileall -q tradingbot_backtest tradingbot_broker tests
git diff --check
```

Before any real paper connectivity: separately approve a transport/library,
paper-account identification and secure configuration, account/order/execution
reconciliation, session/tick/risk validation, durable audits, request pacing,
disconnect recovery and cancel/fill races. Market-data integration, secrets,
TWS/Gateway connectivity, real broker actions and live trading remain disabled.
