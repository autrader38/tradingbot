# Phase 10C3C4B — private transactional SQLite/WAL journal storage

**C3C4B ADDS DURABLE SQLITE STORAGE BUT STILL DOES NOT ENABLE PAPER EXECUTION.**

This phase stores the unchanged C3C4A contract. It has no dispatch/callback hook,
broker SDK call, startup ownership adoption, recovery application, order management,
stop/target placement, pending retirement, barrier clear, quarantine clear or
authorization re-arm. AccountMode remains UNKNOWN; AccountSnapshot.mode remains
None. LIVE remains unsupported. Gateway Read-Only stays enabled.

## Qualified runtime and guarantees

The actual cloud runtime is CPython **3.12.14**, SQLite **3.53.1**. WAL,
synchronous=FULL (2), foreign_keys=ON (1), busy_timeout=0 and STRICT tables were
qualified before implementation. Storage requires SQLite 3.37 or later; unsupported
semantics fail closed. No dependencies were installed. Windows execution was not
tested here; the implementation uses standard-library APIs and portable subprocess
tests, with platform-specific path/permission limits below.

Confirmed appends are transactionally committed to the configured SQLite/WAL store
under verified synchronous=FULL semantics. These guarantees depend on SQLite, the
filesystem, OS and storage device honoring their contracts. They are not protection
against every power loss, device/controller failure or malicious modification.

Committed records can reside in the WAL. The database and active WAL state are one
logical store; SHM supports SQLite's coordination. Copying/moving only the `.db`
while active is not a qualified backup. SQLite handles valid WAL recovery on reopen.
There is no manual WAL parsing, per-append checkpoint or extra fsync protocol.
SQLite's own checkpoint behavior remains in effect. Backup/restore is not implemented.

## Private entry points and explicit paths

`tradingbot_broker/paper_execution_journal_sqlite.py` exports nothing through the
package, Broker protocol, CLI, UI or strategy. Its only dependencies are standard
library tools, the C3C4A models/replayer and fixed read-only error/account-mode types.

- `_SQLiteJournalStore._create(path, header)` creates a new store.
- `_SQLiteJournalStore._open(path, header)` validates an existing store against the
  caller's exact expected C3C4A header.
- `_append(payload)` accepts an exact supported event, never a caller-assigned record.
- `_load()` returns a frozen opaque header/complete-records/replay value.
- `_close()` and context-manager exit close explicitly; no destructor is relied on.

There is no default path, environment-derived path, cloud-drive synchronization or
network-share support. Callers must supply a trusted local path and parent directory.
Existing directories, nonregular files, symlinks and symlink ancestors are rejected.
Existing WAL/SHM sidecars must be regular nonsymlink files. Creation rejects orphaned
sidecars and never overwrites an existing target, even an empty or corrupt file.

Creation reserves the target with O_CREAT|O_EXCL and O_NOFOLLOW where available,
owner-only 0600 mode on POSIX. SQLite opens a percent-escaped URI with mode=rw;
it cannot silently recreate an absent file. File identity is compared before/after
SQLite opening. Windows ACLs are not established or guaranteed. These standard-library
checks do not eliminate adversarial path replacement races, Windows reparse-point
ambiguity or prove a path is on a local filesystem. Trusted parent directories and
platform/filesystem qualification remain required. No secrets are stored regardless.

## Exact schema and creation

Storage identity is separate from C3C4A schema version 1:

- SQLite application_id: **0x504A4231**.
- SQLite user_version: **1**.
- Storage format: **paper-execution-journal-sqlite-v1**.
- Header: the exact canonical C3C4A `_JournalHeader`, including UNKNOWN mode.

Three STRICT tables hold the event stream:

| Table | Retained fields |
|---|---|
| `metadata` | Singleton, fixed storage format, canonical header BLOB, genesis digest |
| `records` | Sequence, fixed event-type tag, canonical complete `_JournalRecord` BLOB, previous digest, digest |
| `store_state` | Singleton, tail sequence/digest, monotonic quarantine flag, optional fixed SEMANTIC_INVALID reason |

Nine triggers reject record UPDATE/DELETE, metadata UPDATE/DELETE/reinsertion,
state DELETE/reinsertion, decreasing tail/quarantine and noncontiguous/quarantined
record insertion. They also prevent normal INSERT OR REPLACE shortcuts. Triggers are
defense in depth; someone able to replace the schema/database can bypass them.
Exact schema SQL, including every trigger, is validated rather than trusted.

Creation uses one BEGIN IMMEDIATE transaction for application/user versions, all
tables/triggers, header/genesis and initial state. There is no executescript implicit
commit. A failure/crash before initialization commit leaves an unusable reserved file,
not a valid empty journal. It is not deleted or reinitialized automatically. A normal
creation commit is followed by validated read-back. An ambiguous creation commit may
leave either an initialized store or an invalid file; callers must reopen and inspect.

## Canonical codec

JSON is canonical UTF-8 with sorted keys, compact separators, ensure_ascii=True and
explicit tags. The closed allowlist covers all 15 C3C4A event classes, the header,
record, account binding, callback source and nested order/contract/strategy/risk models.
Exact dataclass fields are required, including fields with defaults. There is no
pickle, marshal, eval, repr storage, arbitrary import or object reconstruction.

Decoding rejects unknown/extra/missing fields and tags, duplicate object keys at any
depth, floats/constants, wrong primitive types, malformed UTF-8/JSON, trailing data
and noncanonical alternate encodings. Re-encoding must match the original bytes.
Constructors and C3C4A validators revalidate reconstructed exact private models.

Decimal uses exact sign, coefficient digits and exponent; it never passes through
float. Trailing zeros, signed zero and scale survive. The C3C4A limits remain 64
coefficient digits and exponents [-64,64]; replays use the unchanged exact arithmetic.
SQLite-owned sequence columns are explicitly limited to signed 64-bit positive ints.
Nested C3C4A integer facts are encoded as JSON integers, not SQLite-coerced columns;
bool cannot masquerade as a C3C4A integer.

Date/time encodings use explicit numeric components, fold and timezone metadata.
Fixed offsets retain integer microseconds and a bounded printable name. Named ZoneInfo
timestamps retain key and observed offset; local timezone data must exist and agree
with that offset. Anonymous ZoneInfo objects without a portable key are rejected before
writing. Unsupported timestamps never silently become naïve or normalized values.
Journal sequence controls order; timestamps never override it. No clock is read.

Enum encodings use explicit allowed enum class and member names. Raw status strings
cannot substitute for enums. AccountMode cannot become PAPER on load.

The bound is **262,144 bytes (256 KiB) per canonical header/record**. A maximally
expanded string/Decimal context fixture fits comfortably; ordinary records are much
smaller. Length/type is checked before selecting/decoding a stored BLOB. SQLite's
connection length limit is payload bound + 4,096 bytes, with a 65,536-byte SQL limit.
There is no lifetime record cap or silent truncation. Complete-history verification
uses O(n) memory/work per append, making repeated appends correctness-first rather
than an optimized indefinite-lifetime journal. Future optimization needs separate review.

## Hash chain and integrity limits

SHA-256 uses explicit eight-byte length framing around every byte component.
Genesis hashes the fixed storage domain, `genesis` tag and canonical header.
Each record hashes domain, `record` tag, canonical header, eight-byte sequence,
event type, canonical full record and previous digest. Record 1 links to genesis.
Tail state binds the last sequence/digest, detecting missing suffix rows as well as gaps.

These hashes detect accidental/inconsistent modification. They are not a MAC,
authenticated attestation or protection against an attacker who rewrites everything
and recomputes hashes. A self-consistent older copy can remain valid: **there is no
anti-rollback freshness authority**. Expected-header matching does not solve rollback.
Future startup/session authority must address that separately if required.

## Append, concurrency and read-back

Every connection verifies WAL, FULL, foreign keys, zero busy timeout and
trusted_schema=OFF. BEGIN IMMEDIATE makes SQLite the cross-process writer authority;
no process-global Python lock or cached tail assigns sequence.

Inside the write transaction storage validates identity, exact schema, quick_check(1),
header, state, every row, the whole hash chain, codecs and complete C3C4A replay.
An already corrupt/invalid history is never extended as ordinary clean history.
Sequence is persisted tail + 1. The exact new C3C4A record is inserted, then the full
candidate history is replayed and tail/state updated before commit.

BUSY fails immediately without spinning or retrying. No sequence is consumed by a
failed lock acquisition. After release, callers may make a new explicit operation;
the transaction rereads the persisted tail. Connections are single-thread handles;
independent threads/processes need independently opened instances.

Success returns an immutable `_CommittedRecord` only after commit returns normally
and a coherent post-commit transaction verifies identity, full chain and candidate
row/digest. A later legitimate writer can extend the tail without falsifying the
candidate's commit. A later quarantine prevents a clean success result.
Load uses its own coherent read transaction and returns immutable copied values,
not raw sqlite rows or a connection. An empty store is a valid storage container,
never broker-clean state; C3C4A restart quarantine remains true.

## Semantic quarantine and structural corruption

If a syntactically valid exact candidate makes C3C4A replay reject (duplicate owned
IDs, impossible invocation, identity/execution conflict or overfill), the candidate
record and **SEMANTIC_INVALID quarantine** are committed in the same transaction.
The offending record is not rolled back merely because it contradicts the journal.
After confirmed commit the operation raises fixed QUARANTINED. Normal load/open and
further ordinary appends cannot expose a replayable prefix as authoritative state.

The entire journal is unusable for authoritative replay. Quarantine survives close
and reopen. No event is silently dropped, skipped, repaired or ignored. Automatic
trading/recovery must not continue. Manual investigation/recovery through future
separately reviewed tooling is required. There is no clear, repair, delete, reset,
reinitialize, partial-replay or mutation-authority API.

Existing structural corruption (wrong schema/header/version/row/sequence/hash/codec,
SQLite integrity failure or a semantically invalid unquarantined history) fails closed
without trying to repair/rewrite the database or relying on writing a flag into a
possibly corrupt store. Manual investigation is required; missing/corrupt storage
must never be replaced with an empty supposedly clean journal.

## Commit uncertainty, poisoning and interruptions

Any ordinary error once commit is attempted, including a failed post-commit
verification, produces **COMMIT_OUTCOME_UNKNOWN**. Both failure before actual commit
and failure after actual commit but before return have that disposition. The instance
is permanently poisoned/closed, cannot append again and never retries automatically.
Only a new fully validated reopen can determine whether the candidate is present.
No absence/presence assertion is made by the failed operation itself.

Severe transaction/connection failures also poison the instance. Failures before
commit are rolled back by closing the connection; initialization failures leave
an invalid reserved file. Invalid caller syntax is rejected before a transaction.
BUSY at acquisition leaves the unchanged instance usable for an explicit later attempt.
Closed/poisoned state cannot be reset. Close never clears persistent quarantine.

KeyboardInterrupt, SystemExit(37) and GeneratorExit propagate as their exact original
objects. An interrupted active transaction poisons/closes the handle first; incomplete
work is not reported committed. An interruption after actual commit still propagates
and requires reopen. Secondary cleanup errors cannot replace the original interruption.

Errors are private fixed codes: NOT_FOUND, ALREADY_EXISTS, INVALID_PATH,
INVALID_DATABASE, HEADER_MISMATCH, CORRUPT, QUARANTINED, BUSY,
COMMIT_OUTCOME_UNKNOWN, CLOSED, POISONED, INVALID_EVENT and STORAGE_FAILURE.
They contain no SQL, raw diagnostic or path text. Results, handles and errors have
opaque reprs. Canonical fields contain only sanitized C3C4A identity/audit primitives;
no account number, SDK/protobuf object, credential, authorization capability,
enrollment phrase or session secret is introduced. Caller-provided identifiers must
remain nonsecret as C3C4A requires. Storage is not encryption or OS access control.

## Future integration and offline validation

Progression: **C3C4A contract/replay -> C3C4B SQLite/WAL storage -> C3C4C separately
reviewed write-ahead dispatch integration.**

C3C4C must receive a confirmed durable append of the invocation-may-occur marker
**before** calling an SDK write method. FAILED/BUSY/POISONED/OUTCOME_UNKNOWN storage
means SDK invocation must not occur. C3C4B calls no SDK method itself.

Future callback integration must durably append sanitized facts without holding
broker locks across slow storage unless separately proven safe. This phase does not
solve runtime lock ordering, synchronize portfolio/risk state, bind transports or
adopt restarted ownership. Additional position, lifecycle, protection, management,
strategy/data, session migration and recovery foundations remain necessary before
the first real PAPER write.

Run `python -m unittest tests.test_paper_execution_journal_sqlite -q` and the full
offline suite with the real integration opt-in unset. Tests use only temporary
databases, including abrupt child-process exits with committed/uncommitted WAL and
initialization crash windows, independent writers, commit-error seams, deliberate
corruption, every supported codec, quarantine, permissions and interruption safety.
