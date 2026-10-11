# Phase 10C3C4D-PRE1 — private sanitized commission capture

**MEMORY-ONLY EVIDENCE. NO DURABLE CALLBACK PERSISTENCE OR PAPER EXECUTION.**

C3C4D qualification was blocked because the PAPER wrapper discarded both commission
report payloads and recorded only a kind flag. PRE1 supplies the missing sanitized
producer, without changing C3C4A schemas/replay, C3C4B storage, account binding,
the PRE2 commitment algorithm, C3C4C journal ownership, or public Broker APIs.

Gateway Read-Only remains enabled. AccountMode remains UNKNOWN, account snapshot
mode remains None, and LIVE remains unsupported. The first real PAPER order remains
blocked. Independent review and actual Windows/Python 3.12 validation of PRE1 are
required before C3C4D is re-qualified.

## Callback contract and private value

The repository's previously qualified official 10.50.2 read-side callback contract
uses `execId`, `currency` and the appropriate amount field:

| Callback | Required amount field |
| --- | --- |
| `commissionReport(report)` | `report.commission` |
| `commissionAndFeesReport(report)` | `report.commissionAndFees` |

The cloud has no installed optional IB API package. Official archive/source endpoints
were unavailable during this implementation; no SDK was installed and no new official
SDK runtime validation is claimed. Offline fixtures use exactly these previously
qualified fields. Actual Windows validation must exercise the user's SDK environment.

Each wrapper passes its report only into private sanitization. There is no fallback
between amount fields, no missing-value default and no access to account, realized
P&L, yield, arbitrary text or other unused SDK attributes. Unsupported shapes fail
closed rather than guessing fields.

Frozen/slotted/opaque `_CommissionEvidence` contains only:

- exact positive write `generation` and transport-lifetime callback `sequence`;
- bounded exact `exec_id`;
- deterministic local `report_id`;
- signed finite Decimal `amount`;
- qualified `currency`;
- aware UTC `received_at`.

The execId uses the existing 1–100 character ASCII identifier contract, with no
string coercion. Currency is a nonempty, three-uppercase-ASCII-letter code; USD is
not assumed. Unsupported currency labels, account-like identifiers and free-form
text are rejected. Currency membership/account denomination is not inferred.

Amounts may be exact Decimal, int or float, excluding bool/subclasses. SDK int/float
values become `Decimal(str(value))`; no float arithmetic occurs. The result must be
finite with at most 64 coefficient digits and an exponent in [-64, 64]. Signed
rebates and supplied zero are retained, including the original Decimal tuple.
Missing amounts, NaN, infinity and unbounded unset sentinels are rejected. There is
no rounding, absolute-value normalization, fee total or completeness claim.

## Local report fingerprint

These qualified callback shapes supply no independently qualified upstream report
identifier. `report_id` is explicitly a **local economic fingerprint**, never an
IBKR report number, authenticated capability or ownership proof.

SHA-256 hashes `paper-commission-report-v1` plus a NUL separator, followed in fixed
order by these typed fields: `exec_id:str`, `amount:decimal`, `currency:str`.
Each tag has a NUL separator, then a four-byte big-endian byte length and ASCII
value. Decimal canonicalization uses sign, coefficient digits and exponent with
trailing coefficient zeros removed exactly; numeric zero, including signed zero,
has canonical value `0`. It performs no context-dependent Decimal arithmetic.
Nonzero numeric encoding is `sign:digits:exponent`.

The result is `commission-` plus 64 lowercase hex characters. Same execId, numeric
amount and currency produce the same ID across callback variants, transports,
sequences and receipt times. Changed execution ID, amount or currency is
distinguishable. This fits the unchanged journal identifier contract but does not
create `_CommissionReceived` or any other journal event.

## Receipt time and source sequence

The constructor captures private `_commission_receipt_time`, which returns local
`datetime.now(timezone.utc)`. Tests supply a private deterministic clock. There is
no public clock/configuration API. The clock runs during receipt/sanitization under
arrival synchronization, without coordinator/control/store access. Its aware
timestamp is validated and normalized to UTC.

`received_at` means only local receipt/sanitization of this particular callback.
It is not broker event time, execution time, account freshness, source ordering,
or a cross-generation clock proof. Wall-clock regressions do not redefine the
monotonic source sequence.

Current-generation commission callbacks now join the existing arrival boundary:

`arrival lock -> generation check -> lifetime arrival marker -> primitive copy ->
callback sequence -> bounded ledger -> release arrival -> deferred bookkeeping`

An obsolete or closed generation is rejected before clock/payload access. Accepted
commissions use the same lifetime sequence as open/status/execution evidence; it
never resets on reconnect. Commission capture also works before coordinator binding,
without giving an unbound caller access to owner-bound paging or dispatch authority.

Commission has no numeric order ID and cannot advance the allocator floor. The
reserved-ID collision predicate excludes the exact commission evidence type;
PRE2 still rejects changed arrival/sequence watermarks, unusable arrival and all
existing order-bearing collision/floor/latch conditions. A commission that arrives
between reservation and commitment therefore blocks invocation even while its
ordinary write bookkeeping is waiting. No PRE2 proof or one-shot rule is weakened.

## Bounded staging, duplicates and conflicts

There is one existing evidence ledger, bounded by `_EVIDENCE_CAPACITY` (128 distinct
source records). No second queue, execId lookup, attempt attribution or unbounded
staging map is added. Commission may arrive before execution, synchronously during
placeOrder, or after SDK return. Capture does not assign client_order_id.

Economic identity is `(exec_id, report_id, amount, currency)`. Source identity also
contains generation and the actual receipt instant. Adjacent exact source duplicates
may compress into the existing sequence span; paging expands every occurrence with
its exact sequence. Economically identical callbacks at different receipt instants
remain separate records. Their identical economic fingerprint prevents receipt-time
differences from being mistaken for financial conflict; their timestamps are never
invented by duplicate expansion.

Interleaved different reports remain observable. There is no latest-wins, financial
correction, averaging, replacement or conflict resolution. The ledger retains the
facts for later qualification/attribution and fail-closed persistence.

Overflow permanently latches reconciliation. The advanced source sequence exposes
the uncovered suffix; no fetch/cursor jump claims it was recorded. `_evidence_since`
returns immutable bounded pages and an informational through-watermark. It neither
acknowledges, clears nor persists evidence.

The existing coordinator is unchanged and has no commission-attribution handler.
After a pending dispatch, synchronization encountering commission evidence fails
closed, requires reconciliation and leaves that commission unacknowledged. Existing
pre-dispatch consumption of ineligible callback sequences creates no attribution or
durability claim. Later C3C4D requires a separate durable cursor, qualified commission
staging/progress, and an evidence-only journal drain while dispatch stays inhibited.
`_callback_kinds` remains a non-economic presence flag only.

## Failures, locks and deferred work

Malformed current-generation data marks arrival unusable; after invocation has
started it also latches reconciliation. No fabricated evidence or success sequence
is assigned for failed sanitization. Interrupted capture marks arrival unusable and
permanent reconciliation before propagating the exact KeyboardInterrupt, SystemExit
or GeneratorExit. Consumed IDs, existing latches and no-retry rules are unchanged.

Lock direction remains coordinator -> control -> read lifecycle -> read condition
-> write dispatch -> arrival. Callback capture uses arrival only, releases it before
waiting for deferred write bookkeeping, and never acquires coordinator/control/store
locks. It performs no SQLite, journal append, durable progress update, SDK request
or callback wait. No raw report is stored in bookkeeping, identity or evidence.

C3C4A `_CommissionReceived` still lacks `_CallbackSource`; explicit durable commission
source/progress association must be qualified in C3C4D. PRE1 does not infer that
association from adjacent journal records. Missing fees remain unknown. Different
financial reports must not be silently repaired or interpreted as fee corrections.

No restart adoption, automatic journal resume, retry authority, account/session
migration, recovery application, portfolio mutation, barrier clearing, authorization
re-arm, PAPER attestation or real execution is added. Production position/exposure,
protective management, cancel/remainder management and separately reviewed recovery
foundations remain required after durable callback persistence.

## Offline validation

Linux/cloud validation passed 55 new focused tests and 925 relevant existing
transport, PRE2, write-ahead integration, callback confirmation and journal tests
(980 focused regressions). The full offline suite ran 3,948 tests: 3,947 passed
and the one existing real-IBKR integration test was skipped with its opt-in disabled.
All 97 Python files compiled; whitespace, secret/account/.env and changed-file
scope checks passed. New tests contain no skip/xfail. Fake SDKs alone exercise
the invocation seams; callback threads never append SQLite/journal evidence.

This is not actual Windows runtime validation or new installed-SDK qualification.
No dependency was installed, no real IBKR connection/write occurred, and Gateway
settings were untouched. Independent review and Windows validation remain required.
