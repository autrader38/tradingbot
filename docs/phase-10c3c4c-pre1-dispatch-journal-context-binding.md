# Phase 10C3C4C-PRE1: private context and account binding

PRE1 supplies two separate private boundaries: pure reference projection and
authenticated in-process account association. It does not implement C3C4C,
connect to IBKR, integrate storage with dispatch, reserve order IDs, write a
broker order, attest PAPER mode, or grant recovery authority. Gateway Read-Only
must remain enabled. C3C4C remains blocked until PRE1 is independently reviewed;
the first real PAPER order remains blocked by later runtime foundations too.

## Pure context projection

`paper_dispatch_journal_context._adapt_dispatch_journal_context` takes exact
`OrderRequest`, `RiskPermission`, `EntryApproval`, `InitialPosition`, and
`IBKRContract` values plus a required explicit `at` timestamp. It returns a
frozen/slotted opaque `_DispatchJournalContext` containing new C3C4A order,
contract, strategy and risk values plus a copied validation timestamp. It accepts
no configuration-ID override, capability, journal store or dispatch callable.

The supplied timestamp permits deterministic validity checks without a clock.
It is caller-supplied evidence, not a freshness capability or promise that an
order may be dispatched later. Future C3C4C must revalidate all mutable gates
after durable invocation-marker commit and before any SDK invocation.

| Journal strategy field | Exact supplied source |
|---|---|
| `strategy_version` | `v1.0`, after exact frozen configuration validation |
| `configuration_id` | Private canonical configuration fingerprint |
| `setup_id` | `approval.setup_id` |
| `security_id` | `approval.security_id`, agreeing with order/contract |
| `symbol` | `approval.ticker`, agreeing with order/contract |
| `session_date` | `approval.session.trading_date` |
| `witnesses` | Four ordered A/B/C/D values described below |
| `eligible_entry_minute` | `approval.scheduled_timestamp` |
| `reference_entry` | `approval.reference_open` |
| `raw_reference_stop` | `position.raw_stop` |
| `tick_adjusted_reference_stop` | `position.executable_stop` |
| `raw_reference_target` | `position.raw_target` |
| `tick_adjusted_reference_target` | `position.executable_target` |
| `original_quantity` | Exact whole-share `Decimal(position.quantity)` |
| `risk_budget` | `position.risk_budget` |
| `bod_equity` | `position.account_before.beginning_of_day_equity` |
| `stop_tick_source` | `position.stop_tick.source_id` |
| `target_tick_source` | `position.target_tick.source_id` |

A uses `approval.a.low`, timestamp and source. B uses `approval.b_price` with
`approval.b_origin` timestamp/source and matching HIGH. C uses
`approval.c_price` with `approval.c_evidence.c_origin` timestamp/source and
matching LOW. D uses `approval.d.close`, timestamp and source. Timestamps label
interval starts. The origins must be exact traded `MarketInterval` values for
the same security/symbol, chronologically compatible, and completed before or
at entry. D completion and `d_confirmation_timestamp` must equal the eligible
entry minute. Session and opening provenance are validated without rerunning
eligibility, calendar selection or strategy detection.

The position must contain the same semantically equal approval, canonical
configuration and exact order quantity. Only PAPER/ENTRY/BUY/MKT/DAY reference
forms without limit/stop submission values are supported. Permission binds the
entire order payload, including timestamp instants; its permission boolean is
copied, never approved or changed. Order/risk/contract windows must include `at`;
the verified STK/SMART contract must agree on security, symbol and currency.

Supplied Phase 6 results are checked with exact rational comparisons: raw stop
equals C times .995, risk per share agrees with entry minus executable stop,
raw target equals entry plus 2R, and supplied risk budget agrees with BOD risk.
No replacement result is calculated. Supplied final tick rules must be verified,
available, effective, security/basis/purpose compatible, and place the supplied
executable result on their final-band grid. No supplier lookup, band traversal,
normalization or sizing recalculation occurs; no universal penny tick is assumed.

All retained numbers/identifiers/timestamps use the unchanged bounded C3C4A
validators. New dataclass and datetime/Decimal values are created; whole bars,
approvals, positions, account snapshots and tick objects are not retained.
Optional risk current-equity/exposure/cash fields remain `None`. Current equity
never substitutes for BOD equity. Simulated `full_fill`, commission, fees,
slippage and `account_after` are ignored: none becomes a broker fill, position,
account effect, SDK invocation or recovery fact.

## Configuration identity

`_configuration_fingerprint` hashes ASCII canonical JSON framed by domain
`paper-dispatch-journal-configuration-v1|strategy-v1.0`. Ordered rows include
each of the 63 stored `StrategyConfig` field names and typed values, followed
by the named derived `max_total_breakout_attempts` value. Output is
`cfg-v1-<64 lowercase SHA-256 hex digits>`, within existing identifier bounds.

Integer, finite Decimal, Fraction, time, timedelta, date/datetime, fixed timezone,
IANA ZoneInfo and enum encodings are explicitly typed. Decimal trailing zeros
are removed through tuple manipulation rather than ambient-context rounding;
equal numeric configurations yield the same fingerprint. Timezone identity,
offset and fold distinctions are framed explicitly where applicable. Unsupported
values fail closed. Current configuration fields use int, Decimal, naive time and
timedelta. Run/data labels never enter the hash. Computing an identity is not
configuration approval: projection separately rejects any noncanonical research
configuration instead of assigning it the frozen identity.

## Authenticated issuer and validator

`ReadOnlyTWSTransport._issue_journal_account_binding()` establishes one
independently random `binding-<32 lowercase hex>` namespace after authenticated
enrollment/account matching inside the existing read boundary. It returns an
opaque frozen/slotted `_JournalAccountBindingReceipt` containing only version 1,
the logical binding value and read generation. There is no caller binding label,
path, header or existing journal adoption argument.

The transport keeps one opaque private association: exact receipt identity,
secured namespace/generation/configuration, authenticated enrollment revision
and snapshot deadline. The revision remains issuer-internal and never enters
the receipt, journal, repr, diagnostic or public output. No account number,
fingerprint, salt, key, DPAPI plaintext, raw SDK object or authorization capability
is copied into either association evidence or the journal context.

Issuance authenticates twice, including after randomness/constructor work.
Validation authenticates afresh and requires the exact issued receipt object,
unchanged secured primitive values, generation, configuration, enrollment revision
and deadline. Local SDK connection inspection and a final timestamp sample catch
disconnect/latency/staleness. No SDK request is added. Equal reconstructed,
foreign-owner, malformed or substituted receipts fail. Port, client ID, requested
PAPER mode and a journal label are not account-authentication evidence.

There is at most one issuance per transport lifetime. A failed validation or
interruption discards the association permanently; generation reset/disconnect
also discard it. A repeated issuance cannot switch journals or refresh an old
association. This conservative PRE1 boundary adds no rebind/reset/clear path.
Transport replacement or future continuity requires separate ownership review.

Only read lifecycle then read condition synchronization is used; no SQLite,
paper-write or arrival lock and no callback wait is introduced. Local enrollment
reads reuse the existing authenticated mechanism. Ordinary failures expose fixed
sanitized `INVALID_JOURNAL_ACCOUNT_BINDING` or
`INVALID_DISPATCH_JOURNAL_CONTEXT` codes. KeyboardInterrupt, SystemExit and
GeneratorExit propagate unchanged; an interrupted issuer/validator publishes no
partial association and its locks remain usable.

## Remaining boundaries

Binding validation is account-association evidence only, not execution or
recovery authority. AccountMode remains UNKNOWN; public snapshot mode remains
None; LIVE remains unsupported. Production broker writes remain blocked.

No journal is created/opened/appended by PRE1. Existing dispatch, placeOrder,
authorization, allocator, callback/cursor, reconciliation latches, schemas,
SQLite storage, strategy and backtest behavior are unchanged. Loss of in-process
issuer provenance after restart cannot be repaired by equal receipts/headers,
broker records, account positions or journal replay. Existing restart quarantine
and the prohibition on automatic ownership adoption/retry remain in force.

Offline tests use real Phase 5/6 research fixtures and fake SDK clients only.
Windows/Python 3.12 execution of PRE1 is still required before deployment claims;
Linux regression does not establish Windows runtime qualification. No dependency
or IANA timezone fallback is introduced.
