# Phase 10C3C2C2: pure offline reconciliation assessment

This phase interprets an already-consumed immutable C3C2C2A bundle. It does not
collect evidence, reconcile portfolio state, clear barriers, book production fills,
attest PAPER, authorize trading or enable another order. No real connection or broker
write occurred. Gateway Read-Only remains unchanged. Recovery requires a later,
separately reviewed phase; real paper writes remain disabled.

`paper_reconciliation._evaluate_broker_reconciliation(bundle)` is private and pure:
no SDK calls, I/O, clocks, randomness, locks, callback synchronization, acknowledgement,
receipt consumption or mutation. Repeating evaluation returns an equal frozen assessment.
The coordinator does not import or invoke this evaluator. Existing production files,
public models and package exports remain unchanged.

## Consumed provenance and structure

The evaluator requires exact reviewed bundle, attempt, receipt, local-state and evidence
model types. Initial and final state must retain the same `_PendingOrder` object.
Request fields must agree with receipt fields. Read generation must follow the request's
read baseline; start must follow its issuance marker; completion must follow start.
The read snapshot must agree with receipt markers, monotonic samples and outcome.
This is structural validation of consumed provenance, not new authority/replay checking.
No read/write generation numbers are compared to each other.

A terminal non-COLLECTED receipt is INCOMPLETE. COLLECTED additionally requires its
paired public snapshot and completed requested open/execution sources, including an
execution request ID. Completed orders must be requested/completed or explicitly
unsupported/unavailable. Empty tuples never establish completion. Malformed structure
raises only `ReadOnlyError('INVALID_BROKER_RECONCILIATION_BUNDLE')`; raw exception text
is suppressed. KeyboardInterrupt, SystemExit and GeneratorExit propagate unchanged.

Presence metadata must agree with retained fields. Absent protobuf identity/status
cannot be promoted from decoded defaults. Required contract/economic fields must be
supplied. Legacy completed evidence retains no numeric order/client ID. All retained
strings, IDs and finite Decimal primitives are bounded and checked before comparison.
The evaluator accepts at most the reviewed 1,024 read records and 128 local/page records.
It cannot restore a malformed or overflowed collection's completeness.

## Exact attribution and conflicts

A related record shares the exact nonempty target reference, numeric order ID **and**
placing client ID, a positively attributed permId, or an already-owned local execId.
Known local execIds are checked before ordinary related/unrelated filtering. Scan all records; choose no
preferred match. A related record with a supplied conflicting reference, numeric/client
identity, conId, symbol, currency or economics is CONFLICT. Unrelated valid orders are
ignored. Missing fields cannot establish independent ownership.

A fresh OPEN_ORDER requires supplied numeric order ID/client ID/reference plus exact
conId, STK, SMART, symbol, currency, BUY, quantity, MKT and DAY. Protobuf completed
orders require the same identity/economics and a recognized terminal status for
independent attribution. Legacy completed records are corroboration only.

An independent read execution requires supplied exact numeric order ID/client ID/ref,
conId, STK, symbol, currency, BOT, nonempty execId, positive shares and nonnegative price.
Execution exchange is validated/preserved bounded evidence, not submission-route
identity: the qualified contract does not require every execution venue to be SMART.

Missing execution reference requires exact supplied numeric ID/client ID and contract,
plus an independent fresh open/completed anchor or retained strong-openOrder provenance
for this same pending object. A prior execution, observed enum or `broker_observed`
flag alone cannot supply this anchor. A permId join does not relax that execution
fallback. Missing numeric/client ID with reference alone stays insufficient.

Only independent strong open/protobuf completed/execution attribution establishes a
permId join key. Joined completed records may corroborate lifecycle if supplied identity
and economics agree. Distinct attributed permIds for the target or a conflicting record
sharing an attributed permId cause CONFLICT. C3C2B retains no permId, so its memory-only
anchor cannot invent one for joining legacy completed orders.

## Economic quantity and lifecycle

All arithmetic uses a private 512-digit Decimal context, independent of ambient precision
and traps. The reviewed bounded primitives and sums fit exactly. Capture sequence is
not execution chronology: cumulative quantities are compared with their own shares and
the original quantity, never treated as a chronologically increasing read sequence.

Exact execId plus identical retained evidence is idempotent; differing evidence is
CONFLICT. Read/local execution IDs are joined using C3C2C2A's frozen primitive layout:
write generation, order ID, client ID, reference, execId, conId, side, shares, price.
Overlapping supplied identity/economics must agree. Each execId's shares are counted once.
An owned local execId makes a fresh execution related even if its numeric ID, client ID,
reference and permId would otherwise look foreign. Compare all overlapping supplied
fields before filtering, then require every supplied target field to be compatible.
Missing optional fields are not invented and still follow the existing attribution
policy. Extra fresh cumulative evidence may strengthen quantity without adding the
same execution's shares twice. Read generation is never compared with local write
generation. A same-execId conflict outranks every positive lifecycle classification;
an untrusted/failed read receipt still takes precedence and remains INCOMPLETE.
This uses the already-qualified C3C2B attribution contract for local executions, including
its optional placing client ID when exact reference independently established observation.
A local total must agree with its captured identities. Never add two undeduplicated totals.

Observed quantity is the maximum of the deduplicated execution sum, supplied cumulative
quantity and qualified local fill observation. More than original quantity, cumulative
less than own shares or an impossible historical local filled/remaining sum cause
CONFLICT. No approximate comparisons are used.

The current C3C2C2A aggregate local state does not retain same-callback provenance
between `last_status` and `last_filled` / `last_remaining`. A later openOrder can change
status while preserving earlier orderStatus totals. They are not one atomic terminal
observation. With retained exact strong openOrder ownership, positive local filled
quantity is historical economic evidence: below the original quantity it is a
LOWER_BOUND; the full original quantity is positive full-fill proof unless conflicting
evidence exists. A historical zero supplies no positive zero-fill proof.

Local remaining validates only the historical orderStatus arithmetic. It is not
current terminal truth, cannot establish terminal zero fill, and cannot contradict
newer positive execution evidence merely because the retained lifecycle changed.
Thus old zero/partial totals do not conflict with later larger or full executions.
Positive local partial evidence plus qualified non-Filled terminal lifecycle can be
classified as PARTIALLY_FILLED_TERMINAL, with LOWER_BOUND certainty. This does not
exclude an additional unreturned fill or authorize recovery.

TERMINAL_UNFILLED would require explicitly qualified same-event terminal-zero proof.
The existing unread-event policy leaves target events unincorporated and UNRESOLVED;
it does not promote even an unread terminal orderStatus's own zero/full-remaining
totals. Consequently TERMINAL_UNFILLED is deliberately unavailable from the current
aggregate bundle. A later recovery phase may add richer provenance if useful; it is
not needed for safe conservative evaluation here.

| State | Required interpretation |
| --- | --- |
| INCOMPLETE | Failed receipt or required source/public pairing incomplete. |
| UNRESOLVED | Valid collection but insufficient identity/economics, limited history, unread target truth or unqualified chronology. |
| CONFLICT | Positively incompatible identity, economics, duplicate execId, permId or monotonic local history. |
| OPEN_WORKING | Exact fresh current-open identity and working status; no incompatible lifecycle/full-fill evidence. **Does not mean zero fills.** |
| PARTIALLY_FILLED_WORKING | Working truth plus positive economic evidence below original quantity; may be LOWER_BOUND. |
| PARTIALLY_FILLED_TERMINAL | Qualified non-Filled terminal lifecycle plus positive historical local partial fill; quantity remains LOWER_BOUND. Limited history cannot exclude an unreturned fill. |
| FILLED | Positive full quantity from distinct executions, exact cumulative quantity or qualified positive local full-fill evidence; no incompatible lifecycle evidence. |
| TERMINAL_UNFILLED | Requires provenance-qualified same-event terminal-zero evidence; unavailable under the current aggregate and unread-event policies. |

Filled status alone remains UNRESOLVED. Cancellation alone plus empty execution history
never means unfilled. Partial fill followed by cancellation can be valid and is not
rejected merely for being cancelled. Full economic proof combined with a working or
non-Filled terminal observation is left UNRESOLVED because the bundle cannot qualify
collection chronology. Incompatible strongly attributed terminal statuses are CONFLICT;
differing otherwise plausible lifecycle observations without ordering are UNRESOLVED.

A completed open collection with no target says only that it was not returned as currently
open. Completed and execution history are HISTORY_LIMITED. Empty/no-match histories do
not prove no order, cancellation or execution. Quantity certainty is NONE, LOWER_BOUND
or EXACT and is reported explicitly. Positions, cash, equity and public account changes
are deliberately excluded from ownership and positive lifecycle classification.

## Local evidence, barriers and unread callbacks

Initial/final status, fill, execution identities, cursor and watermark are checked for
monotonic consistency. Initial strong provenance, observed identity, processed executions
and permanent reconciliation flags cannot silently disappear. Callback filled regression,
remaining increase, status reversal or incompatible terminal changes are CONFLICT.
Authorization invalidation during refresh is expected and does not create a broker conflict.

Both frozen unread pages remain evidence. A gap, missing suffix through the watermark or
a target-related unread lifecycle/execution event makes evaluation UNRESOLVED unless a
conflict is positively established. Relevant unread identity/economic conflicts outrank
positive classification. Provably old write-generation events or events at/before the dispatch commit sequence
cannot establish target identity. The evaluator neither advances the callback cursor nor acknowledges anything.
It deliberately does not reinterpret unprocessed target events into updated local state.

The assessment retains initial/final barrier flags and sanitized authorization statuses,
and records callback watermark change.
Broker lifecycle and control consistency are distinct. General reconciliation, lost write
generation/connectivity, pending or unobserved local ownership, or incomplete local evidence
keep control consistency UNCERTAIN. Proven contradiction is CONFLICT. Otherwise compared
local/broker evidence may be CONSISTENT; this grants no recovery authority and proves
nothing about synchronized portfolio/risk state.

## Privacy and boundaries

The frozen slotted assessment has an opaque repr, fixed enum categories and sanitized
primitives only. It retains no bundle, account, SDK/protobuf object, authorization
capability, enrollment secret, socket, broker error text or arbitrary status string.
It contains no success flag, clear/recover capability, action recommendation or trading
permission. Existing barriers remain intact regardless of the assessed lifecycle.

AccountMode remains UNKNOWN and AccountSnapshot.mode remains None. No callback, full fill,
reference, client ID, permId, receipt or port attests PAPER. LIVE remains unsupported.
Production Broker APIs remain read-only; wire policies and REQ_IDS exclusions are unchanged.
Memory-only pending identity is required. A process restart that loses it cannot construct
a valid handshake bundle; the evaluator neither reconstructs ownership nor adds persistence.
