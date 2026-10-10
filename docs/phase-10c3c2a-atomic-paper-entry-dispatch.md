# Phase 10C3C2A — offline atomic paper entry dispatch

**OFFLINE ONLY. NO REAL IBKR CONNECTION OR ORDER TRANSMISSION.**

The internal `_PaperOrderDispatchCoordinator` joins the read-only control plane,
authenticated local enrollment, session authorization, existing external risk
permission, verified stock contract and isolated C1 dispatch primitive. It is not
a Broker implementation. It exposes no public connect or write lifecycle, CLI,
environment activation or strategy bridge. Gateway Read-Only remains enabled;
AccountMode remains UNKNOWN and LIVE remains structurally unsupported.

## Explicit UNKNOWN-account-mode eligibility

Local enrolled paper intent does not independently attest broker PAPER mode.
The broker must request TradingMode.PAPER, report AccountMode.UNKNOWN and have a
verified current AccountSnapshot whose mode is exactly None. Authenticated
enrollment must still match the exact account observed by the read plane.
The original C3B authority must validate the exact issued PLACE_ORDER capability:
identity, independent issuance snapshot, scope, generation, enrollment revision,
fresh observation, current local SDK connectivity and both deadlines remain gates.
Neither a port nor paper configuration grants permission or establishes PAPER mode.

The unchanged `submission_gates()` evaluates the actual account, connection,
request, external RiskPermission and control states. Only after valid PLACE_ORDER
authorization does the coordinator create an ephemeral Controls value with
trading_enabled=True for this gate evaluation. Entries-paused and emergency-stop
values come from the real broker; its stored controls remain unchanged and disabled.
The generic failure tuple must be **exactly (ACCOUNT_MODE_UNVERIFIED,)**: exact tuple
type, one member, exact BrokerReason type and enum identity are required. Strings,
equal-looking values and tuples containing additional failures cannot pass. Any other
failure, missing failure, unverified account, PAPER/LIVE account mode, reconciliation
state or missing authorization denies the local attempt. Generic account gates
and existing strategy/risk sources are unchanged.

## Construction and translation

Only exact ReadOnlyIBKRBroker and PaperTWSTransport components and an immutable
tuple of exact IBKRContract mappings are accepted. No duck-typed/subclass transport
can replace these boundaries. Each mapping has unique security_id and con_id.
Caller-visible immutable inputs are checked for exact concrete field types before
equality; independent primitive snapshots detect same-object mutations. No raw
SDK object can be injected through the bridge.

Both validated configurations must use the same literal localhost host and Gateway
port, with different positive client IDs. This expresses intended pairing with the
same local Gateway; it is not independent account or PAPER attestation. Configuration
changes after construction deny dispatch. No account identifier is used for pairing.

One exact coordinator owner is permanently bound to each PaperTWSTransport instance.
The transport atomically retains a strong reference to that owner and captures its
reviewed preparation and final-validation methods once. Constructing another coordinator
for the same transport is rejected, including with another contract tuple. Disconnect,
reconnect, generation reset, pending confirmation and reconciliation never release the
owner. Dropping an external coordinator reference cannot erase its duplicate or pending
history. There is no public ownership release, validator setter or per-dispatch callback
argument. Private Python boundaries isolate application APIs; they are not a sandbox
against arbitrary code rewriting private objects or module definitions.

Only PAPER / ENTRY / BUY / MARKET / DAY, positive Decimal quantity and no limit/stop
price are supported. The exact order-bound external RiskPermission must be available,
unexpired and explicitly permit entry. No risk permission is minted here.
The selected verified SMART/STK contract must match security_id, symbol and currency;
its positive integer con_id is never parsed from text. Available_at and the half-open
validity window are checked at final commitment. Translation produces only C1's
private stock/market specification. SELL, EXIT, PROTECTIVE, LIMIT, STOP, replacements,
cancel, global cancel and flatten are not coordinator operations.

## Atomic commitment and lock order

The order is **coordinator → ReadOnlyIBKRBroker → read transport lifecycle → read
transport condition → paper dispatch → paper arrival**, followed by its socket lock
inside synchronous SDK framing. Read-plane polling happens before the nested transport
locks; no callback wait, reader join, sleep or broker response wait occurs in the atomic
section. C1 callbacks announce ambiguous errors under arrival locking before waiting
for dispatch bookkeeping; they release arrival locking before taking dispatch locking.

Preliminary authenticated validation happens before local translation. The coordinator
releases control/transport locks during translation, then reacquires them in the same
order and rejects changed observation identity or generation. C1 then prepares private
SDK objects and reserves a NEW broker order ID. The bound owner refreshes authenticated
enrollment/account evidence under the held outer locks, before acquiring the final
paper arrival/commitment lock. This keeps enrollment-file I/O outside that lock.

Inside the final commitment lock, C1 rejects reconciliation and creates a fresh private
per-dispatch challenge. The captured owner validator verifies the active attempt and
prepared evidence identity, all required outer lock ownership, current SDK connection
states, exact capability identity/PLACE_ORDER scope, generation and account/enrollment
binding, immutable inputs, controls, risk, contract and plane pairing. It freshly
samples wall and monotonic clocks and rechecks account, authorization, order, risk and
contract expiry. Authenticated evidence cannot change through the read transport while
those outer locks remain held. Final validation performs no enrollment-file I/O, SDK
request, callback wait or worker join.

Only successful validation returns the exact challenge object. C1 requires
`proof is challenge`; False, True, None, integers, equal-looking objects and stale
proofs fail closed. No challenge is retained or exposed in results, repr or audits.
After that identity check, C1 directly rechecks reconciliation, records the known order
ID and marks invocation started. No caller hook, clock sample, yield or additional lock
acquisition is placed between successful proof and those commitment operations.
It then releases the arrival lock and invokes the exact named SDK placeOrder method.
Failed preparation or final proof produces local NOT_DISPATCHED/DENIED without touching
the SDK write method; the reserved broker ID and consumed client_order_id stay consumed.

Disarm, disconnect, refresh, account updates, emergency-stop and entries-pause APIs
share the held control locks: a transition that takes effect before commitment denies
the invocation; a concurrent transition after commitment waits for the one synchronous
SDK call. Final validation runs inside commitment rather than before acquiring its
lock; no deadline gap is intentionally permitted after the proof. Clock deadlines
remain checked despite locking. No deadline is renewed.
An ID reserved before a final failure remains consumed by C1 for its transport lifetime.

## Attempts, outcomes and recovery barriers

Every exact typed request with a bounded client_order_id consumes that ID in the
coordinator's private lifetime attempt history, even if later gates deny it. A duplicate
or changed payload cannot reserve a second broker ID or retry the attempt. Permanent
transport ownership prevents coordinator reconstruction from resetting this history.

| Internal state | Meaning |
|---|---|
| DENIED | Local failure before SDK write invocation; not broker rejection. |
| DISPATCHED_PENDING_CONFIRMATION | One named SDK invocation returned normally; acknowledgement remains unresolved. |
| OUTCOME_UNKNOWN | Reconciliation is required for an unresolved write; the current attempt may have been blocked before invocation. |

**DISPATCHED_PENDING_CONFIRMATION is NOT broker acceptance.** No OrderAcknowledgement,
accepted flag, working order, position update or strategy success is produced. A clean
return installs a permanent coordinator pending-confirmation barrier before constructing
the result, blocking all further NEW attempts. There is no public clearing API.

C1 OUTCOME_UNKNOWN, ambiguous synchronous/delayed errors and reconciliation-flagged
returns latch the coordinator and transport barriers, mark control-plane reconciliation
and revoke authorization with the fixed PAPER_WRITE_RECONCILIATION_REQUIRED reason.
The precedence is **reconciliation required > pending confirmation > ordinary new-order
eligibility**. Every coordinator entry synchronizes fatal transport/control state under
the coordinator and broker locks before duplicate or pending fast returns. Therefore a
delayed C1 error after a clean pending dispatch escalates at the next coordinator
interaction: coordinator/control-plane reconciliation is latched, authorization is
invalidated and the result is OUTCOME_UNKNOWN / RECONCILIATION_REQUIRED. The historical
pending flag may remain true, but cannot outrank reconciliation. Repeated escalation is
idempotent; it cannot clear or downgrade any barrier. A blocked current invocation is
not claimed to have begun merely because an earlier unresolved write requires recovery.

Paper callbacks do not acquire earlier coordinator/broker locks to propagate state
asynchronously. C1 latches its own barrier immediately; the coordinator synchronizes it
on its next interaction. This avoids reverse lock order while ensuring neither pending
nor duplicate returns hide fatal transport state. Reconnect and fresh synchronization
do not clear barriers. Restart is not proof of reconciliation.

Only the C1 call boundary handles unexpected dispatch failures. Regular exceptions
are sanitized to OUTCOME_UNKNOWN after securing reconciliation. Post-invocation
KeyboardInterrupt, SystemExit and GeneratorExit secure coordinator/control/transport
safety before re-raising the original interruption unchanged. A pre-invocation
interruption propagates without falsely claiming dispatch uncertainty; any already
reserved ID and client_order_id remain consumed. Final-validator interruptions follow
that same pre-invocation rule. No exception path retries.

Results contain only bounded technical IDs, typed state/reason, timestamps and barrier
flags. No account identifier, fingerprint, salt, capability ID, key, SDK object, socket
or raw error text is recorded in bridge results or control authorization audits.

## Remaining boundary

The permanent ReadOnlyIBKRBroker, its wire allowlists, enrollment, C3B authority and
existing RiskPermission/strategy controls remain unchanged. The production Broker
protocol still has no route to this private coordinator or paper transport. Import and
construction send nothing and install nothing; all qualification uses offline doubles.
No real Gateway session or order was used, and Gateway settings were not changed.

Phase 10C3C2B must separately implement broker callback confirmation, attribution,
reconciliation and recovery. Cancel/replace/global-cancel integration, any real connection
lifecycle and PAPER operational qualification remain separately reviewed work. This
phase does not provide production reconciliation or enable real paper trading.
