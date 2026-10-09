# Phase 10C2: local IB Gateway read-only connection

**PHASE 10C2 DOES NOT ENABLE ORDER TRANSMISSION.**

This adds an optional real socket transport for inspection on the user's bot machine.
No cloud-to-user connection, authenticated session or successful PAPER connection is
claimed. No strategy/backtest economics change. The Phase 10C1 fake execution layer
and its in-memory IBKR adapter retain their existing behavior.

## Selected official API

Use Interactive Brokers' official **TWS API through IB Gateway**. The user resolved
this architecture using current official documentation: the API uses a TCP socket,
supports account/position/order/market-data callbacks, and Gateway is the intended
long-running host. Default Gateway ports are PAPER **4002** and LIVE **4001**; the
configured socket port may differ. Neither port proves the account environment.

Retail Client Portal/Web API is not selected: its gateway requires recurring
authentication/session maintenance and is less suited to this persistent bot host.
Authentication occurs in Gateway itself, including any required user login/2FA and
session maintenance. Python receives no username/password/token. This phase adds
neither unattended authentication nor a production restart/session renewal service.

These are the user-approved architecture facts, not a claim of SDK/runtime validation
against an installed local Gateway. No external documentation was accessed during
this resumed phase.

## Architecture and safety boundaries

`ReadOnlyIBKRBroker` → `ReadOnlyTWSTransport` → privately held official SDK client →
local IB Gateway. The existing shared broker facade supplies account-evidence
freshness, connection chronology, audit records and latched emergency controls.
The historical portfolio engine is not connected to this transport.

Two safety layers are required:

1. Configure **Read-Only API** in IB Gateway.
2. Python rejects place, modify, replace, cancel, cancel-all and flatten actions with
   `READ_ONLY_BROKER_TRANSPORT`, including backend dispatch hooks. Trading cannot
   be enabled. Named read-only client views expose no raw SDK client, SDK class,
   connection or generic sender. The allowlist is also enforced on the framed
   bytes at the socket send boundary, below SDK method dispatch. Calling an SDK
   base send method cannot bypass this boundary.

Allowed SDK requests are handshake/start API, managed accounts, account summary,
positions, all open orders, supported completed orders, executions and broker time;
account-summary/position subscription cancellation is read-stream cleanup, not order
cancellation. Client ID zero is prohibited; order auto-binding and `reqOpenOrders`
are not used. No raw client or arbitrary request dispatcher is part of the public API.
Protobuf/unknown message encodings fail closed with
`UNSUPPORTED_IBAPI_WIRE_ENCODING`; they need a separately reviewed read allowlist.
The exact locally installed official SDK/Gateway version must pass local read testing.

Local inspection of the official TWS API 10.50.2 found that its OUT message IDs are
enum members with numeric `.value` fields. The adapter normalizes only existing
approved read-message members belonging to that OUT enum, with matching names and
exact positive integer values. Plain integer constants remain supported; strings,
floats, booleans and arbitrary coercible objects are rejected. Both the wire allowlist
and read-only metadata contain plain integer opcodes. This is compatibility handling,
not an expansion of the outbound allowlist; protobuf remains blocked.

The guarded connection installs a socket descriptor before the SDK creates its
socket. The socket view exposes no raw socket or file descriptor. It accepts only
one legacy-framed read message at a time, or the initial bounded version-negotiation
frame; unknown opcodes, combined frames and unsupported encodings fail closed.
Partial writes may continue only with the exact unsent suffix. The official SDK's
Python `Connection.socket` and framed-wire contracts must be verified locally;
unsupported connection implementations fail closed. This is an application safety
boundary, not a sandbox against arbitrary hostile Python code or a separately
created socket. No official SDK was installed or connected during these corrections.

The old `IBKRAdapter` still accepts only `InMemoryIBKRTransport`. It cannot acquire a
real transport through a relaxed type check. LIVE routing is still disabled, and even
a PAPER order cannot be sent through the new layer.

## Independent account-mode evidence

Managed-account identifiers identify accounts, not an independently attested execution
environment. Account type describes account characteristics, not a PAPER assertion.
This implementation has no sufficiently authoritative environment attestation in its
selected TWS callbacks. It therefore reports **AccountMode.UNKNOWN** and preserves
`AccountSnapshot.mode = None`. It does not classify by account prefix, port, configured
environment, username, balance size or human statement.

The account snapshot's `verified` flag means the required read batch completed and its
values passed validation; it does **not** verify PAPER. The existing safety gates still
treat UNKNOWN as unusable for trading. Diagnostics may succeed as read-only inspection
while explicitly reporting this limitation. Independent PAPER qualification remains
unresolved for a future execution phase.

## Observations and timing

One managed account is supported. Multiple accounts fail closed instead of guessing
which account to select. Cash, NetLiquidation and BuyingPower must have one explicit
currency; positions must fit the existing account's currency contract. Missing,
malformed, unset/nonfinite or conflicting values fail the batch; no FX conversion,
missing balance, synthetic position or zero commission is invented.

Account-summary monetary strings retain Decimal precision. SDK floats are converted
using their decimal text representation, never `Decimal(float)`. Precision already
lost inside an SDK float cannot be recovered. Contracts retain stable `conId` identity;
broker order IDs prefer `permId`, with a client/order ID fallback. Observed orders have
no fabricated submission time, risk permission or executable request. Unknown order
types/statuses remain explicit. Fills are confirmed execution observations, not a
simulation of instantaneous full fills. Commissions are separate actual callbacks.

Execution timestamps require an explicit broker time zone or the configured Gateway
time zone. Ambiguous DST, nonexistent local times and future executions are rejected.
Configure Gateway to return unambiguous time-zone-bearing values where possible.
Broker UTC time is preserved separately from local callback receipt time.

TWS summary/position callbacks do not provide an atomic account valuation timestamp.
`observed_at` is the read collection's last callback receipt time; `available_at` is
the batch completion time. They are **not** fabricated historical effective dates.
The batch spans callback arrivals and must not be used as an atomic risk reservation.
Accepted evidence never regresses to an older observation, and equal-time conflicts
require reconciliation using the existing Phase 10C1 protections.

Reads are bounded snapshots, not an execution reconciliation service. A cached snapshot
expires after 15 seconds by default; it is never silently considered current afterward.
`refresh()` disconnects and collects a new socket generation. Untagged position/order
callbacks cannot contaminate the next batch. Obsolete-generation callbacks are ignored;
disconnect invalidates active account proof. Reconnect preserves accepted freshness
boundaries and emergency/pause controls. Recovery notices alone do not restore proof:
connectivity interruption/recovery codes require a fresh batch. Informational data-farm
codes are recorded numerically without exposing raw error messages.

Connection and disconnection operations are serialized. A concurrent connect waits
for the owning operation and may return its unchanged cached snapshot; it cannot
extend that snapshot's expiry. Each new session receives distinct request IDs and
generation-bound callbacks. Obsolete worker cleanup affects only its own client.
Before publication, the collecting generation must still own the session.

Every read collection moves from NOT_REQUESTED to ACTIVE before dispatch, then to
COMPLETED only on its matching completion callback. Premature, obsolete-generation
and mismatched-request completion markers cannot authorize an empty snapshot.
An issued empty response with its legitimate end marker is valid; an unfinished
request times out. Post-completion data callbacks cannot reopen that collection.

Event draining assigns a processing time no earlier than any drained event's
occurrence time. Facade polling also respects its prior processing boundary.
Disconnect or SDK state-inspection failure invalidates facade account usability,
including when the callback arrives after a reader captured its timestamp.

Completed orders are requested only when the SDK/server supports them. Executions and
completed-order history are limited to what the active broker session/API returns;
this is not an archival trade history. Commissions arriving after collection closes
are not included in that snapshot. Absence of a commission report means unavailable,
not zero. A later production reconciliation service must handle streaming statuses,
late commissions, corrections, durable restart and account/risk-version reservations.

## Local Windows setup still required

1. Install IB Gateway from Interactive Brokers on the bot machine. Launch and manually
   log into **PAPER**, not LIVE. Complete authentication. No credentials belong here.
2. Obtain the official TWS API distribution from Interactive Brokers. Install its
   bundled Python client into the bot's virtual environment, for example from the
   extracted distribution's `source/pythonclient` directory using
   `python -m pip install .` with the same interpreter used for diagnostics. Use Python
   3.11 or newer and supply the IANA time-zone database required by the existing
   foundation; Windows installations may need the `tzdata` package. **Do not use an
   unrelated PyPI replacement for ibapi.** No dependency
   is downloaded or installed automatically by this repository.
3. In Gateway API settings enable socket API access and **Read-Only API**. Restrict
   access to the local machine. Confirm the configured port (normally PAPER 4002).
   Keep Gateway running and logged in. Firewall/API permissions may require user action.
4. Set only non-secret connection variables in the local process. PowerShell example:

   ```powershell
   $env:IBKR_HOST = "127.0.0.1"
   $env:IBKR_PORT = "4002"
   $env:IBKR_CLIENT_ID = "1"
   $env:IBKR_READ_ONLY = "true"
   $env:IBKR_TIMEOUT_SECONDS = "10"
   # Only if execution strings lack a zone: match Gateway's actual time-zone setting.
   $env:IBKR_TIMEZONE = "America/New_York"
   python -m tradingbot_broker.ibkr_diagnostics
   ```

   Client ID must be positive and unused by another connection. Loopback hosts only
   are accepted in this phase. `IBKR_READ_ONLY` must be exactly `true`. Wrong settings,
   a missing SDK, disconnected Gateway or incomplete account batch yield fixed setup
   codes and instructions, not a traceback or fabricated success.
5. Inspect diagnostics: connection, UNKNOWN account mode, masked account, explicit
   currency balances, position/order counts, read-only enabled and transmission disabled.
   UNKNOWN is expected; it does not establish independent PAPER verification.

## Tests

Offline SDK-shaped test callbacks need neither the official package nor Gateway:

```bash
python -m unittest discover -s tests -v
```

The local real inspection test is skipped unless explicitly enabled. Only enable it
after manually logging into PAPER Gateway and setting Read-Only API:

```powershell
$env:IBKR_RUN_READ_ONLY_TESTS = "1"
python -m unittest tests.test_ibkr_readonly_real -v
Remove-Item Env:IBKR_RUN_READ_ONLY_TESTS
```

This test performs reads only, confirms UNKNOWN remains unusable for execution, and
does not falsely claim independent PAPER attestation. The normal suite never requires
Gateway availability or credentials. No real connection was attempted in the cloud.

## Secrets and deferred work

Account identifiers stay private in volatile SDK collection state; normalized output
uses `account-1`. SDK payload logging is suppressed. Application errors/audits retain
fixed internal codes, numeric error codes, safe timestamps and normalized data; raw
SDK exception/rejection text is not copied. No usernames, passwords, account IDs,
tokens, cookies, credential caches or `.env` files are added. Existing `.gitignore`
already excludes `.env` and `.env.*`. Keep Gateway's own local files outside Git.

SDK construction, read dispatch, state inspection, socket access and shutdown errors
are translated to fixed structured categories, such as
`SDK_CLIENT_CONSTRUCTION_FAILED`, `SDK_ISCONNECTED_FAILED` and
`SDK_DISCONNECT_FAILED`. Diagnostics have a final sanitizing boundary, including
cleanup failures, and never print external exception text or tracebacks. Cleanup
first invalidates transport/facade state even if the SDK shutdown itself fails.

Deferred: authoritative PAPER attestation, production streaming reconciliation,
durable recovery, portfolio risk/account-version bridging, order transmission,
market-data subscriptions, dashboards, provider integration and all LIVE execution.
