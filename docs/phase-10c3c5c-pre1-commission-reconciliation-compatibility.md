# Phase 10C3C5C-PRE1 — private commission-aware reconciliation compatibility

This phase is a pure internal compatibility bridge. It lets the existing
reconciliation evaluator encounter the already-qualified sanitized commission
record in an unread callback page. It adds no collection, accounting, current
lifecycle ownership, management, recovery or trading authority.

Gateway Read-Only remains required. AccountMode remains UNKNOWN, the public
account snapshot mode may remain None, LIVE remains unsupported, and the first
real PAPER order remains blocked.

## Existing producer and validation

The evaluator reuses the exact private `ibkr_paper_transport._CommissionEvidence`
model, with its existing generation, sequence, exec_id, report_id, amount,
currency and received_at fields. No replacement model or public export is added.

`_local()` accepts only the exact four qualified callback types: open order,
order status, execution and commission. A commission takes its own validation
branch and calls the existing `_CommissionEvidence.__post_init__` validator.
It never goes through the order quantity validator or accesses order_id,
client_id, order_ref, con_id, side, shares, filled or remaining.

The existing canonical producer validation requires:

- Exact positive int generation and sequence; bool and other numeric types fail.
- Exact bounded reference-format exec_id.
- Exact string report_id matching the producer's versioned, typed,
  length-framed SHA-256 fingerprint of exec_id, canonical numeric amount and
  currency. Sequence, receipt time and callback variant are excluded.
- Exact finite Decimal amount, including negative values, supplied zero and
  positive values. The existing bounds are 64 coefficient digits and an
  exponent from -64 through 64. There is no float/int coercion in this evaluator,
  rounding, absolute-value conversion or ambient Decimal precision dependency.
- Exact three-letter uppercase ASCII currency; USD is not assumed.
- Exact validated datetime whose tzinfo is the producer's timezone.utc object.
  Receipt time is local receipt/sanitization time, not broker or execution time.

Validation is repeated for both initial and final local captures. Constructor
success alone does not excuse a malformed received record. Ordinary validation
errors retain the sanitized `INVALID_BROKER_RECONCILIATION_BUNDLE` result.
KeyboardInterrupt, SystemExit and GeneratorExit propagate as the original
exception objects. No malformed evidence is normalized, repaired or ignored.

## Unread sequence participation

`_unread()` performs the existing sequence/gap accounting before interpreting
each record. Commission is recognized before any order-specific field access.
An unread, current-generation commission after the pending invocation's callback
commit watermark makes the assessment conservatively UNRESOLVED when no earlier
INCOMPLETE or CONFLICT condition takes precedence. It does not make an otherwise
valid bundle structurally invalid merely by being present.

The existing rules for another generation or an occurrence at/before the commit
watermark are preserved: those occurrences participate in page coverage but are
not incorporated into this target attempt's lifecycle. Missing, repeated or
reordered source sequences, a partial page, and disagreement with the callback
watermark remain unresolved. Invalid primitives still fail validation regardless
of generation or commit watermark.

A commission is not treated as already incorporated. The evaluator neither
acknowledges nor suppresses it. Fetching or evaluating evidence does not advance
callback_cursor, unread_through_sequence or the C3C4D persistence cursor; it does
not copy one cursor into another. The immutable initial page also remains
evidence if the final capture shows a later acknowledgement.

## Relationship, duplicates and conflicts

This bridge performs no commission-to-order or commission-to-execution join.
Known local execId, fresh-only execId, unknown execId, and commission before or
after execution all take the same conservative compatibility path. No relationship
is inferred from adjacency, timestamp, symbol, quantity, amount or caller labels.

Both legacy and commission-and-fees callbacks already normalize to this producer
model. Equal economic facts have the same report fingerprint even with different
receipt times and callback sequences. Each supplied source occurrence stays in
the unread page; this evaluator creates no additional economic fact or subtotal.

Different amount/currency facts remain distinguishable and unincorporated. The
bridge does not choose a latest report, apply a correction, average reports,
discard an occurrence or convert a financial disagreement into a target lifecycle
conflict. C3C4D attribution/persistence and C3C4A replay retain their existing
financial conflict rejection rules unchanged. Missing commission stays unknown;
neither a matching report nor a complete unread page establishes fee completeness
or financial finality.

## Quantity and lifecycle boundary

Commission evidence cannot increase executed quantity, quantity certainty,
attributed execution count or strong attribution, and cannot manufacture a
lifecycle status. Existing order/execution evidence supplies those fields exactly
as before. The assessment state may become UNRESOLVED because an unread received
fact remains unincorporated; this is a coverage/control limitation, not a new
quantity or lifecycle fact. INCOMPLETE and CONFLICT precedence remain unchanged.

Commission-free assessments keep the existing rules for OPEN_WORKING,
PARTIALLY_FILLED_WORKING, PARTIALLY_FILLED_TERMINAL, FILLED, INCOMPLETE, UNRESOLVED
and CONFLICT. TERMINAL_UNFILLED remains unproven by current zero-fill absence;
the recovery planner's defensive terminal-unfilled branch remains advisory only.
Quantity truth is independent of commission finality.

## Protected boundaries and later prerequisites

Only `paper_reconciliation.py` changes in production. This bridge adds no locks,
clock reads, files, SQLite access, network/SDK activity or background work. It is
thread-neutral over immutable evidence. There is no mutable published owner state.

C3C4A schema/replay, C3C4B storage, C3C4D capture/persistence, C3C5A acquisition
accounting and C3C5B historical lifecycle projection are unchanged. The fresh
order/execution/presence/request/receipt/snapshot models are unchanged; filled and
remaining fresh status capture is not implemented. Public Broker interfaces,
read/write requests, legacy/protobuf wire policies and LIVE behavior are unchanged.

ReadOnlyIBKRBroker.refresh(), connect/disconnect behavior, authenticated account
binding/deadlines, enrollment revisions, generation requirements and lifetime
session associations are unchanged. No owner is rebound or migrated. No barrier
is cleared, pending order retired, write transport reused, authorization re-armed,
restart quarantine cleared or recovery applied. `paper_recovery.py` stays unchanged
and recomputes its advisory assessment internally.

The next prerequisite remains qualification of the installed SDK lifecycle field,
presence and request-correlation contracts. Authenticated session/generation
continuity must also be qualified before any current-lifecycle ownership phase;
a non-disconnecting query by itself does not solve account-binding expiry or
renewal. This bridge supplies neither of those foundations.

## Offline validation and review

The focused suite covers valid signed/bounded facts; producer fingerprint and
timestamp validation; tampered exact records and forged/subclassed models;
sequence gaps and mixed pages; known/unknown execution references; duplicates and
conflicts; commission-free assessment stability; unchanged quantity/lifecycle
fields; cursor and real owner invariance; deterministic parallel evaluation;
BaseException identity; pure execution; advisory recovery and public write blocking.

Commands (leave the real integration opt-in disabled):

```text
python -m unittest -q tests.test_paper_reconciliation_commission_compatibility
python -m unittest -q tests.test_paper_reconciliation tests.test_paper_reconciliation_handshake tests.test_paper_recovery tests.test_paper_callback_confirmation tests.test_paper_callback_persistence tests.test_paper_commission_capture tests.test_paper_execution_accounting tests.test_paper_working_order
python -m unittest discover -s tests -q
python -m compileall -q tradingbot_backtest tradingbot_broker tests
git diff --check
```

Independent review and actual Windows/Python 3.12 validation remain mandatory
before the next foundation. Linux/cloud validation is not Windows qualification.
No broker connection, read or write is needed or authorized for these tests.
Gateway Read-Only must remain enabled; the first real PAPER order remains blocked.

Linux/Python 3.12 offline validation completed with 85 new focused tests and
1,318 requested existing regression tests passing. The full suite ran 4,524 tests:
4,523 passed and the existing opt-in real Gateway integration test was skipped.
All 103 Python files compiled. Whitespace, changed-file scope, secret/account
identifier and tracked `.env` scans passed. A differential probe compared 104
commission-free complete assessments with the starting evaluator and found every
field identical; only `_local()` and `_unread()` changed in production logic.
