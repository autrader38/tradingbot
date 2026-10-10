# Phase 10C3A — Local paper-account enrollment

**THIS PHASE REMAINS READ-ONLY. MATCHED ENROLLMENT DOES NOT ENABLE TRADING.**

The TWS API does not independently attest whether the connected account is PAPER
or LIVE. `AccountMode.UNKNOWN` therefore remains unchanged. Local enrollment
records the user's explicit declaration that they were logged into IB Gateway
Simulated/Paper Trading when they enrolled the observed account. Port 4002,
account identifiers, balances and configuration are not PAPER proof.

## Architecture

The existing `ReadOnlyIBKRBroker` facade delegates enrollment and matching to the
existing read-only transport. Managed identity stays inside the private transport
boundary. Public snapshots, identities, enrollment results and audit records never
contain the raw account identifier. Matching performs local file reads only.
It does not request a new broker capability or change account-mode evidence.

Enrollment requires a healthy, completed, unexpired read-only snapshot containing
exactly one managed account. Localhost-only configuration, positive client ID and
`IBKR_READ_ONLY=true` remain mandatory. Disconnection, stale snapshots or invalid
local state cannot yield a usable enrollment match. Existing connection generation,
callback chronology and account-evidence freshness protections are preserved.

`PaperEnrollmentStatus` is separate from `AccountMode`:

| Status | Meaning |
| --- | --- |
| UNENROLLED | No enrollment record exists; nothing is created automatically. |
| MATCHED | An authenticated enrollment record matches the observed account. This is user enrollment, not broker attestation. |
| MISMATCH | A valid authenticated record refers to a different account. |
| INVALID | Malformed, unreadable, unauthenticated or tampered state; missing/damaged authentication key; or unusable connection evidence. |

## Local setup and commands

On the user's bot machine, install the official IBKR Python API as described in
[Phase 10C2](phase-10c2-real-ibkr-readonly.md). Log into IB Gateway **Simulated/Paper
Trading** and enable its Read-Only API. Use localhost, a positive client ID, and
`IBKR_READ_ONLY=true`. Port 4002 is the normal PAPER Gateway connection setting,
but enrollment never treats that port as account-mode evidence.

Run:

```bash
python -m tradingbot_broker.ibkr_paper_enroll
```

The interactive prompt requires this exact, case-sensitive phrase:

```text
I CONFIRM IBKR SIMULATED TRADING
```

An incorrect phrase, EOF or interruption fails closed. There is no automatic
confirmation option. The command never asks the user to enter an account number
and never displays the observed raw identifier. It disconnects after inspection.

An existing enrollment, including an invalid record, cannot be silently replaced.
Intentional re-enrollment requires both the explicit option and the same phrase:

```bash
python -m tradingbot_broker.ibkr_paper_enroll --replace-existing
```

Local diagnostics remain read-only:

```bash
python -m tradingbot_broker.ibkr_diagnostics
```

They add `Paper enrollment: MATCHED / UNENROLLED / MISMATCH / INVALID`, while
continuing to show `Account mode: UNKNOWN (not independently attested)` and
`Trading permission: UNUSABLE`. MATCHED cannot enable trading or bypass any gate.

## Storage, pseudonyms and authenticated tamper detection

Enrollment files live outside repositories, in the user's application-data folder:

| Platform | Default directory |
| --- | --- |
| Windows | `%LOCALAPPDATA%\VelocityTradingGroup`, with per-user `AppData\Local` fallback |
| Linux / other POSIX | `$XDG_DATA_HOME/VelocityTradingGroup`, or `~/.local/share/VelocityTradingGroup` |
| macOS | `~/Library/Application Support/VelocityTradingGroup` |

`ibkr-paper-enrollment.json` stores only schema, version, a random 32-byte salt
encoded as lowercase hex, a SHA-256 fingerprint, UTC creation time, and an
HMAC-SHA-256 authentication tag. Fingerprint input is the UTF-8 encoding of:

```text
VelocityTradingGroup|IBKR|PaperEnrollment|v1|<salt>|<raw-account-id>
```

Salt generation uses Python's standard-library `secrets` module. This fingerprint
is a local pseudonym; it is **not encryption or secret account storage**. No raw
account identifier is persisted in either file. `hmac.compare_digest` verifies
both record authentication and the account fingerprint.

At the user's explicit request, authenticated tamper detection uses a **separate
local trust anchor**, `ibkr-paper-enrollment-auth.key`. Its independent random
32-byte key is protected with per-user Windows DPAPI on Windows; there is no
plaintext fallback there. On POSIX it uses an owner-only file with mode `0600`
inside an owner-only directory with mode `0700`; ownership and permissions are
verified on use, and unsafe existing permissions fail closed.
The HMAC covers all record fields except its own tag, with a separate versioned
authentication domain and deterministic JSON encoding. A well-formed edit to the
salt, fingerprint or timestamp therefore produces INVALID rather than MISMATCH.
Missing or damaged key material is never recreated during matching. Explicit
re-enrollment can recover it. Loss of both files requires fresh enrollment.

These guarantees rely on the local OS user boundary and trust anchor. They do not
protect against an attacker controlling that user's process or both the record
and authentication key. They do not establish broker PAPER attestation, durable
rollback protection, or production execution permission. The user reported
successful local Windows qualification: enrollment completed, DPAPI-protected
state survived a fresh Python process, and fresh read-only diagnostics returned
MATCHED. AccountMode remained UNKNOWN, trading remained UNUSABLE, and no orders
were transmitted. This is local enrollment qualification, not independent broker
PAPER attestation. Codex offline tests use a simulated protector and do not claim
to have run Windows DPAPI or connected to the user's Gateway.

Writes use a complete flushed/fsynced temporary file in the destination directory.
Initial installation uses atomic create-if-absent linking; explicit replacement
uses atomic replacement. Concurrent creation cannot overwrite the winner. A
filesystem without the necessary operation fails closed. New POSIX directories
use mode `0700`. Repository paths, relative paths and record/key symlinks are
rejected. No enrollment or key file belongs in Git.

Record and key reads acquire a low-level descriptor, use nonblocking opening on
POSIX, and require descriptor-based regular-file validation before any read.
FIFOs, sockets, directories and devices fail closed as INVALID without holding
the broker/transport locks indefinitely. Reads use that same descriptor, close it
on every path, and retain the 4 KiB record and 8 KiB protected-key limits. Windows
uses its supported flags and binary mode; DPAPI protection is unchanged.

## Preserved broker safety and next phase

Neither the SDK dispatch guards nor the final socket allowlists are changed:

- Legacy read IDs: `{7,16,17,49,61,62,63,64,71,99}`.
- Protobuf read IDs: `{207,216,217,249,261,262,263,264,271,299}`.
- Order-write protobuf IDs `203`, `204` and `258` remain blocked independently at
  both the client and final socket boundaries.

Placement, cancellation, modification, replacement, global cancellation and
flattening remain blocked. LIVE remains disabled. No real connection is made by
offline validation; no dependency, account credential or provider is added.
Frozen strategy and research economics are unchanged.

Separately authorized [Phase 10C3B](phase-10c3b-paper-authorization.md) models
ephemeral session authorization only; it enables no writes. Phase 10C3C requires
separate authorization and review before any PAPER order transmission, including
execution permissions, risk/account-state reconciliation and lifecycle gates.
Enrollment alone grants none of those permissions.
