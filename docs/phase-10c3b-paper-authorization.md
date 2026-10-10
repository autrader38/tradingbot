# Phase 10C3B — Session paper-execution authorization foundation

**NO BROKER WRITES ARE ENABLED. ALL TRANSPORT WRITE IDS REMAIN BLOCKED.**

This phase models an additional future execution-environment authorization gate.
It neither routes orders nor changes frozen strategy/backtest economics.
`AccountMode.UNKNOWN` remains honest: the TWS API does not independently attest
PAPER versus LIVE. User enrollment and session arming are user declarations,
never broker attestation. Port 4002 is a connection setting, not proof.

## Arming and the public API

The existing `ReadOnlyIBKRBroker` offers:

- `arm_paper_execution(confirmation, *, scopes=DEFAULT_PAPER_SCOPES)`:
  returns an immutable `PaperExecutionAuthorization` after all conditions pass.
- `disarm_paper_execution()`: always allowed, idempotent, immediately revokes the
  issued capability without touching broker orders.
- `paper_execution_status`: checks current evidence before returning a status.
- `validate_paper_execution(capability, scope)`: validates only this additional
  authorization gate; it is **not** an order or risk permission.

Arming requires the exact phrase:

```text
I ARM IBKR PAPER TRADING
```

The enrollment phrase is insufficient. There is no automatic arm from enrollment,
port, environment, reconnect or UI state. No standalone arm CLI is added: an
authorization from a short-lived process cannot arm a different bot process.

Issuance requires a healthy local read-only connection, positive nonzero client
ID, exactly one managed account, authenticated MATCHED enrollment, fresh
completed account observations, requested PAPER mode, no reconciliation-required
state, and no latched emergency stop. UNKNOWN account mode is not changed.
Arming leaves trading disabled and entry-pause controls untouched.

## States, scope and capability authenticity

| State | Meaning |
| --- | --- |
| UNAVAILABLE | Current connection, enrollment or safety evidence cannot support arming. |
| DISARMED | No issued authorization; healthy MATCHED enrollment alone stays disarmed. |
| ARMED | This controller minted a session capability after explicit user confirmation. No transport permission is implied. |
| EXPIRED | The issued capability reached its fixed deadline. |
| INVALIDATED | Previously issued authorization was revoked by changed/unavailable evidence or safety controls. |

EXPIRED and INVALIDATED never renew automatically. A new explicit successful arm
is required; disarm may also clear either state without granting authorization.

Typed scopes are `PLACE_ORDER`, `CANCEL_ORDER`, and `GLOBAL_CANCEL`. Default order
scopes include PLACE_ORDER and CANCEL_ORDER only. GLOBAL_CANCEL is a separately
requested emergency-control scope; it is never implied by entry authorization.
Emergency stop still invalidates **all** scopes. Future emergency execution
semantics require separate review; this phase supplies no emergency write bypass.

The frozen capability contains generation, aware issue/expiry timestamps, ordered
typed scopes, a random 32-byte capability ID, and a fixed sanitized source. It
contains no account identifier, account fingerprint, authentication key, or SDK
object. The capability ID is omitted from repr and audits.

A private broker-owned issuance registry accepts only the exact minted object
and its original immutable field values. Its independent primitive snapshot
contains no caller-held capability or scope-container references. Every exposed
field is checked for exact type and value before comparison; scopes are checked
by enum identity and authorized only from the controller's snapshot. Exact
datetime values require immutable standard-library timezone or ZoneInfo objects.
Equality-spoofing objects, enum-like strings and same-object field mutation fail
closed. Equivalent dataclass reconstruction,
modified objects, another broker's capability and prior-session capabilities fail
closed. Each new explicit arm replaces the prior registry entry. This is an
application boundary, not a sandbox against arbitrary hostile Python code with
access to private controller internals.

## Expiration, evidence and lifecycle

Lifetime is at most **15 minutes**, shortened to the earliest expiry of the
current accepted facade/transport account observations. The usual 15-second
read-only snapshot TTL therefore produces a shorter authorization. Extending
snapshot freshness in a future phase requires reviewed broker observation and
reconciliation semantics; this phase does not extend account evidence.

Expiry checks use both aware broker processing timestamps and a fixed elapsed
deadline from `time.monotonic`. Wall-clock rollback cannot extend authorization;
malformed or regressing monotonic evidence invalidates it. Status checks do not
extend deadlines and there is no automatic renewal.

After authenticated evidence collection, arming and validation inspect current
SDK connectivity using the restricted client's local `EClient.isConnected()`
state method, then resample both
wall and monotonic clocks under the existing locks, then recheck account expiry,
connection generation and safety controls. Validation checks the original fixed
deadlines and issuance integrity again before accepting a controller-owned scope.
Arming charges collection time against its proposed lifetime; only final fresh
evidence can install a capability and produce an ARMED audit. Failed arming leaves
no active issuance. Account refresh does not reset an existing deadline.

Only an exact boolean True from the same current client/generation is accepted.
Disconnected, missing, malformed or exception-producing SDK evidence fails
closed even when the disconnect callback has not yet updated cached flags.
This inspection sends no request, reads no file and performs no callback wait or
worker join. Its private result is only True, False or None (unavailable), never
an SDK object or exception text. Existing broker/lifecycle/callback lock order is
unchanged, and clocks are sampled after inspection so inspection time cannot
extend authorization. Connectivity does not attest PAPER account mode.

The private binding includes connection generation, accepted connection boundary,
connection settings and the authenticated enrollment revision. Neither the raw
account nor enrollment fingerprint is exported in the capability. Disconnect,
emergency stop and explicit re-enrollment revoke authorization. Callback
disconnect, direct transport lifecycle changes, changed/missing/tampered
enrollment, stale evidence and reconciliation-required state are checked at
poll/status/validation boundaries before a capability can be accepted. Local file
changes are discovered through these reads; no background filesystem watcher is
introduced. Repaired state cannot resurrect an invalidated capability.

Broker, transport lifecycle and callback locks protect final evidence collection
and issuance/validation. Cleanup/polling occurs before holding the collection
locks. A successful validation is a point-in-time result, never a promise that
account or risk state cannot subsequently change. Phase 10C3C must integrate
authorization with atomic check/dispatch and broker/risk reconciliation.

## Preserved transmission boundary and future contract

| Future scope | Legacy write ID | Protobuf write ID | Phase 10C3B |
| --- | --- | --- | --- |
| PLACE_ORDER | 3 | 203 | Blocked |
| CANCEL_ORDER | 4 | 204 | Blocked |
| GLOBAL_CANCEL | 58 | 258 | Blocked |

These IDs are documentation contracts only; no transport consumes them as
permissions. The existing active sets remain exactly:

- Legacy reads: `{7,16,17,49,61,62,63,64,71,99}`.
- Protobuf reads: `{207,216,217,249,261,262,263,264,271,299}`.

SDK placement/cancellation/global cancellation, replacement/modification,
flattening and enabling trading remain blocked even when all scopes are ARMED.
The final guarded socket independently rejects all six write IDs, including
base-class and raw-connection bypass attempts. Gateway Read-Only remains required.
LIVE execution remains disabled.

The existing account/submission gates still reject UNKNOWN mode. RiskPermission,
portfolio permissions, order expiry, trading controls, entry pause, freshness,
reconciliation and emergency stop remain separate mandatory protections.
Phase 10C3C requires explicit authorization and review before integrating a
separate environment-authorization gate with any PAPER write transport.

## Audits and validation

Fixed audit actions are PAPER_EXECUTION_ARMED, PAPER_EXECUTION_DISARMED,
PAPER_EXECUTION_EXPIRED, PAPER_EXECUTION_INVALIDATED and PAPER_EXECUTION_ARM_FAILED.
They contain sanitized statuses, reasons, timestamps and appropriate scopes or
generation only. No account, enrollment digest/key, capability nonce or SDK
object is logged. Authorization and its registry are memory-only; no new local
file, credential, dependency or persisted armed state is introduced.

Offline tests cover arming, typed scopes, immutable/forged capabilities,
expiration/clock regression, restart, concurrent issuance/revocation, enrollment
and reconciliation invalidation, privacy and every write boundary. Real broker
connectivity and order transmission are not exercised by this phase.
