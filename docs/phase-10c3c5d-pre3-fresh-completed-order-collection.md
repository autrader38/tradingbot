# Phase C3C5D-PRE3 — authenticated same-session completed-order collection provenance

This phase adds private, memory-only completed-order query evidence. A successful
receipt proves only that one declared collection interval completed under the
exact authenticated same-session association with no detected local capture
failure. PRE1, PRE2 and PRE3 remain independent evidence sources. No combined
evaluator, attempt attribution, execution inference, accounting incorporation,
currentness, lifecycle/remainder, ownership, inventory, protection, cancel/replace,
exit, recovery, barrier clearing, renewal, restart adoption or trading authority
is added. The first real PAPER order remains blocked. Gateway Read-Only remains
required; AccountMode stays UNKNOWN, public snapshot mode stays None and LIVE
remains unsupported. Restart quarantine remains mandatory.

Starting checkpoint: `2e55f2638fbc08e4e019a0e24ad5ffa875b2160e`.
Before PRE3, the user reported independent review and actual Windows validation
of PRE2 on Python 3.12.10: 229 focused tests, 6,538 full-suite tests with 14 skips,
120 compiled Python files, clean working tree and synchronized main/origin/main.
PRE3 requires its own independent review and actual Windows validation.

## Exact live owner and one attempt

`_create_fresh_completed_order_collection_owner(journal_owner)` requires the
exact registered journal owner and creator thread. The private immutable owner
captures coordinator/control, read/write transports and client views, issuer-bound
authentication/account association, header/store/session, generations, client IDs,
snapshot, configuration values, clock, original deadline and exact journal-history
object. Equal reconstructed owners, associations, clients, sessions, headers or
history objects cannot replace the original lifetime provenance.

One collector is registered per read-transport lifetime. It permits one request
attempt. Success, failure, timeout, disconnect, malformed capture or interruption
never provides retry/replacement authority. Active unsealed PRE1/PRE2 captures
prevent PRE3 issuance. Sealed independent receipts remain unchanged. Existing
safety barriers must remain set; poisoned/quarantined stores and failed persistence
are rejected using existing owner checks, without reading SQLite.

## Fixed request policy and correlation limit

The captured existing guarded read client invokes only
`reqCompletedOrders(False)`, exactly the qualified startup `apiOnly` policy.
Callers cannot supply a scope flag, SDK client/function, account or request object.
The original completed-order capability, server-version and allowed-opcode metadata
must remain valid. Unsupported or changed capability fails before issuance or
invalidates later publication/consumption. SDK capability inspection and request
execution occur outside incompatible locks.

Completed-order callbacks and `completedOrdersEnd()` have **no SDK request ID**.
No fake ID is allocated, no shared SDK request counter is advanced and the local
collection ID is not broker correlation. Delayed or unsolicited callbacks cannot
be distinguished by a nonexistent request tag. The contract relies on one active
local interval, exact same-session/generation association, bounded capture,
explicit untagged completion and unchanged final boundaries. It does not claim an
atomic broker snapshot or that a callback was generated in response to this request.

Local creation, issuance attempt, SDK normal return, received occurrences,
completion and final publication remain separate facts. Synchronous callbacks/end
before SDK return are supported; an SDK exception afterward still fails collection.
Empty success requires an actual attempted request, normal return, explicit end
and valid final boundaries. Silence, callback-only delivery and normal return
without end never complete a collection. Duplicate end or callback-after-end
before sealing fails conservatively.

## Sanitized source occurrences and pairing

The unchanged `_ReconciliationOrderEvidence`, `_ReconciliationPresence` and
qualified raw/decoded sanitizers are reused. Every accepted callback occurrence
is independently retained with read generation and monotonic read-local sequence,
source COMPLETED_ORDER, encoding, supplied presence, available identities,
contract, order economics and categorized source status. These are received facts,
not proof of current lifecycle, execution, finality or economic disposition.

Legacy completed callbacks do not qualify numeric order/client IDs. Those fields
remain None and their decoded SDK defaults are never inspected. Qualified protobuf
identity is preserved only where wire-supplied; missing values stay missing.
Supplied zero retains existing source/presence semantics. No top-level completed
order ID is invented. PermId and orderRef alone never establish bot ownership.

One immutable sanitized raw envelope is permitted. Its immediate follower must
match source, generation, thread and supplied values/presence. No raw protobuf
object survives precursor capture. Orphan/two precursors, incompatible intervening
callbacks, raw/decoded disagreement, malformed values, wrong source/thread/generation
or protocol downgrade fail closed. Raw Contract/Order/OrderState/protobuf objects
and account identifiers never enter the receipt. Existing private account
membership filters out other accounts before retaining their observations.

Compatible duplicates remain separate occurrences. Only a supplied numeric
order/client pair creates a source-consistency key; a supplied positive permanent
ID may additionally check consistency when that pair exists. Incompatible retained
facts for such keys fail rather than choose the latest occurrence. Missing identity
does not manufacture a key from permanent ID, reference, symbol, quantity or timing.
These checks grant no bot/order/attempt attribution. Numeric-compatible Decimal
representation variants may coexist; both representations remain preserved.

Quantity validation reuses the existing finite, positive, bounded rules (64
coefficient digits and exponent in [-64, 64]); bool/float coercion is rejected
where the qualified quantity source rejects it. Sanitized Decimals are copied by
tuple without arithmetic, rounding or ambient-context dependence. This phase does
not capture fill/remaining/price quantities from order status.

The bound is 1,024 accepted **occurrences**, not distinct IDs. First overflow
advances the local observation watermark and permanently fails collection.
Already-sanitized prefix evidence may appear in a failure receipt, never truncated
success. No economic aggregation, fill booking or position update occurs.

## Receipt, invalidation and interruption

`_FreshCompletedOrderReceipt` is frozen, slotted, private and opaque. It contains
local ID, fixed scope, opaque account binding, journal/session IDs, generations and
client IDs, issuance/completion markers, initial/final local watermarks, SDK result,
completion flag, outcome, original authentication deadline, boundary-change flag,
independently copied sanitized occurrences and fixed coverage limitations. It
contains no SDK object, raw account, credential, request function or authority.

Consumption requires the exact issued receipt object, issuing owner, creator
thread, successful outcome, canonical untampered values, unchanged capture and
association/boundaries, valid original authentication and no previous consumption.
It only marks local receipt consumption; no accounting, persistence/callback
cursor, barrier, risk or authorization is changed.

Before request, after completion, during publication and before consumption the
original UTC and monotonic authentication deadlines remain fixed. Connection,
generations, clients, account/session/header/store/history identity, configuration,
snapshot, persistence cursor, write arrival/callback watermarks, capture-loss state,
status fences, barriers and active attempt must remain valid. Capture identity,
source occurrence coverage, counter/marker types, completion and publication
facts are rechecked. Tampered or changed boundaries cannot yield usable success.

Outcomes are COMPLETED, FAILED, TIMED_OUT, INVALIDATED, OVERFLOW and INTERRUPTED;
malformed/interrupted publication may leave no receipt. KeyboardInterrupt,
SystemExit and GeneratorExit secure local unusable state, clear raw precursors,
preserve the exact original object/code and propagate on the raising thread.
Cleanup/notification failures cannot replace the original interruption.

History and declared scope are limited. Absence proves neither that an order never
existed nor that no execution occurred. Completed-order evidence is not execution
evidence, current open-order state, an atomic snapshot, commission completeness,
final economic disposition, a no-late-fill guarantee, management currentness,
order/position ownership, accounting or trading authority.

## Threads and protected phases

The exact owner thread constructs, issues, waits, validates and consumes.
Boundary locking preserves coordinator -> control -> read lifecycle -> read
condition -> write dispatch -> arrival. SDK capability inspection/request and
callback waiting release incompatible locks. Callbacks use only existing read-local
synchronization for bounded sanitization/staging/notification: no SQLite,
filesystem, journal append, SDK request, persistence wait or earlier-plane locks.

PRE1/PRE2 receipt semantics, PRE9, V1/V2 journal formats, SQLite schema/version/hash,
public Broker/snapshot APIs, execution/read wire policy, strategy/backtest and risk
rules are unchanged. Query results do not become PRE9 callback occurrences or enter
C3C5A/B, reconciliation or advisory recovery. Disconnect/restart invalidates live
provenance; replay cannot recreate a fresh receipt or clear restart quarantine.

## Validation and remaining foundations

Focused tests use anonymous fake SDKs, deterministic callbacks and explicit network
blocks. Real Gateway integration remains opt-in and disabled. Cloud validation is
not Windows qualification; independent review and actual Windows tests with the
cleared installed SDK remain required before the next lifecycle phase.

Cloud validation: 284 focused PRE3 tests passed. Selected regressions ran 4,755
tests, OK with three optional installed-SDK skips. Full offline discovery ran
6,822 tests, OK with four skips (three optional installed-SDK checks and disabled
real Gateway integration). All 122 Python files compiled. Whitespace,
secret/account-identifier, `.env`/credential, public-export/API, callback-I/O and
protected-file scans passed. Only the six intended files changed; every other
tracked broker/backtest production file is byte-for-byte unchanged. No dependency
installation, real IBKR connection, broker read/write/order operation or Gateway
change occurred. Cloud has no installed ibapi; no Windows qualification is claimed.
Actual Windows validation on Python 3.12.10 with ibapi 10.50.2 and protobuf 5.29.5,
and independent review, are still required for PRE3.

Remaining foundations: source-qualified decision-time status intervals; combined
PRE1/PRE2/PRE3 evidence with explicit coverage reasoning; exact journal-attempt
attribution and contradiction handling; reviewed incorporation of newly discovered
executions; decision-time currentness; lifecycle/remainder; inventory/dispositions;
protective ownership/resizing; cancel/replace/exits; session continuity/retirement;
recovery/audit application and risk/strategy-management restoration. None is built
by this phase, and the first real PAPER order remains blocked.
