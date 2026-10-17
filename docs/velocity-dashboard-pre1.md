# VELOCITY-DASHBOARD-PRE1 — local read-only visual workspace

This separately authorized visual phase supplies the first Velocity Trading Group
dashboard. It is independent of the broker execution architecture. The starting
checkpoint is `1a40fc48f402385d74e1294faf58be938f552876`; the user reports PRE9
independent review and Windows qualification complete at that exact SHA.

The dashboard adds no trading or management authority. The first real PAPER order
remains blocked, Gateway Read-Only required, restart quarantine mandatory,
AccountMode UNKNOWN, public snapshot mode None and LIVE unsupported. No safety
barrier, risk/strategy rule or existing broker/backtest file changes.

## Open it on Windows

In the checked-out repository folder, with Python 3.12 installed:

```powershell
py -3.12 -m tradingbot_dashboard --open
```

The existing system browser opens `http://127.0.0.1:8765`. Keep the console open;
press Ctrl+C to stop. Without `--open`, the same server starts without launching
a browser. A port-in-use error is reported concisely; stop the other local server
instead of binding a public host. No dependency installation, build, internet,
environment variable, credentials, Gateway or account configuration is needed.

Python's standard `webbrowser` module opens only the fixed local URL using the
system browser. No caller-controlled command, executable or shell is accepted.
The app cannot select a host or enable trading through CLI arguments.

## Separate package

```text
tradingbot_dashboard/
    __init__.py
    __main__.py           # Fixed local launch, optional browser, clean Ctrl+C
    server.py             # Standard-library, fixed-route GET/HEAD server
    snapshot.py           # Frozen visual models and explicit JSON allowlist
    demo_data.py          # Deterministic synthetic fixtures, no I/O
    static/
        index.html
        styles.css
        app.js            # Vanilla JavaScript and Canvas
        favicon.svg       # Original local Velocity mark
```

The runtime uses only Python 3.12's standard library and local HTML/CSS/JavaScript/
SVG. There is no Node/npm, framework, CDN, remote font, provider SDK, analytics,
image hotlink or external frontend dependency. System fonts and CSS/SVG branding
are used. Static resources are loaded from the package at startup, independent of
the current working directory. There is no configuration draft or setup script
required for this dependency-free workflow.

## What is visible

- Charcoal/navy panels, cyan Velocity accents, green/red synthetic candles and
  amber safety warnings. Header and navigation retain PAPER, READ-ONLY and
  TRADING BLOCKED. UI health means local UI readiness, never broker health.
- Dashboard, Scanner, Charts, Trades, Strategies, Analytics and Bot Health
  navigation scrolls to sections of this page. No route enables management.
- Momentum scanner: five expressly synthetic symbols (ABCD, VELA, NEXA, QBIT,
  MTRX), price/change, volume, relative volume, setup, score and state. All eight
  columns remain in the semantic table; compact rows show two levels of fields.
  Clicking a symbol changes the selected candlestick dataset.
- Demo performance cards, example recent trades, a demo equity curve and a local
  activity feed. No metric represents actual account performance or profitability.
- Bot Status, Order Evidence, Broker / Session and ABCD Breakout context cards.
  Direction is LONG ONLY, primary timeframes 1m/15m, higher-timeframe context
  Weekly, frozen specification v1.0 and automation NOT AUTHORIZED. No active
  strategy rule, parameter or setup score is implemented by this screen.

The page stacks at narrow widths, keeps chart interaction available and confines
wide trade tables to their own scroll container. It includes semantic headings/
tables, a skip link, visible keyboard focus, labeled buttons, Canvas fallback text,
keyboard candle inspection and reduced-motion handling.

## Candlestick implementation

The main chart is drawn from actual arrays of deterministic OHLCV fixtures onto a
device-pixel-ratio-aware Canvas; it is not a screenshot. It renders bodies, wicks,
price grid/scale, UTC time labels, volume bars, a last-demo-price label and an
optional demo VWAP. A ResizeObserver redraws when layout size changes.

All five symbols have distinct 1m, 15m, 1D and 1W datasets. Timeframe buttons redraw
different arrays; these are visual samples, not a provider feed or a backtest.
Synthetic fixtures use fixed UTC dates and integer-derived prices/volumes without
the current clock, randomness, downloads or broker callbacks.

Hover supplies a crosshair and tooltip with time/open/high/low/close/volume. Focus
the chart and use left/right arrows for the same data, Escape to clear, plus/minus
or the chart buttons to zoom. Reset restores the default window.

Optional ENTRY/STOP/TARGET lines, BUY/SELL markers and VWAP are visibly marked
DEMO/EXAMPLES. They are visual illustrations, not orders, signals, current position,
approved strategy parameters or recommended risk levels. Clicking a demo trade
loads its symbol and example entry level; trade rows and synthetic candles are
independent fixtures, not a claimed broker execution chronology.

## Sanitized snapshot contract

`DashboardSnapshot` is a frozen/slotted visual model independent of broker internals.
Its validated source values are bounded tuples of scanner rows, series/candles,
demo trades and equity points. It fixes PAPER, BLOCKED, READ-ONLY REQUIRED,
MANDATORY quarantine, UNKNOWN account mode, LIVE unsupported and demo data enabled.
The server accepts no caller-supplied snapshot or raw broker object.

`to_payload()` explicitly builds this allowlisted structure, making independent
primitive copies. It does not serialize `__dict__`, arbitrary dictionaries,
SDK values, environment variables or dataclasses through a generic fallback.

| JSON section | Meaning |
| --- | --- |
| system | Brand; fixed architecture constraints; local UI readiness; demo flag |
| broker | NOT CONNECTED, Read-Only required, UNKNOWN account mode, snapshot mode null |
| strategy | Frozen contextual labels only; automation NOT AUTHORIZED |
| scanner | Synthetic rows, each and entire domain explicitly demo |
| chart | Four timeframes per synthetic symbol; validated, ordered UTC OHLCV |
| trades | Independently labeled demo trade rows |
| performance | Demo metrics and explicitly demo equity series |
| evidence | No active evidence, null historical fields, UNKNOWN current values |
| activity | Static local/safety/demo explanations, no broker-session claim |

`GET /api/snapshot` returns this deterministic, UTF-8 JSON snapshot. It is a visual
fixture of architectural restrictions, not a live poll or broker-state attestation.
Market/performance/trade domains remain demo true. Account numbers, usernames,
credentials, tokens, webhook URLs, raw broker values and private environment
contents are absent.

The evidence panel intentionally shows **No active evidence loaded**. Historical
lifecycle, execution lower bound and possible remaining upper bound are empty.
Current remainder and current position are UNKNOWN, with the persistent distinction
**HISTORICAL EVIDENCE ≠ CURRENT BROKER STATE**. PRE9 data is not fabricated or loaded.

## HTTP and security boundary

- Bind address is always IPv4 `127.0.0.1`; the CLI uses port 8765. No public-host
  option or `0.0.0.0` binding is available. Binding bypasses HTTPServer's hostname
  lookup, so startup requires no DNS. Ephemeral port zero supports local tests.
- Exact read routes: `/`, `/styles.css`, `/app.js`, `/favicon.svg`, `/api/snapshot`.
  GET and HEAD are supported. HEAD returns matching representation headers without
  a body. POST/PUT/PATCH/DELETE and OPTIONS/TRACE/CONNECT return 405 and Allow GET/HEAD.
- Everything else, including traversal, encoded paths and query-string file tricks,
  returns a fixed 404. Paths never become filesystem names. No directory listing,
  upload, command execution, eval/exec or user-controlled request forwarding exists.
- Host is restricted to the literal loopback address/current port, blocking foreign
  host names/DNS rebinding. No CORS authorization or credential-entry form exists.
- Responses include explicit content types/lengths, no-store, nosniff, no-referrer,
  same-origin resource policy, frame denial and disabled camera/microphone/location.
  CSP permits only the necessary same-origin scripts/styles/JSON/images; default
  sources, forms, frames, objects, remote fonts and inline script/eval are disabled.
- The SVG namespace identifier is not an external resource request. All actual
  asset references are local. No polling, external fetch or browser storage is used.

Loopback is not an authentication mechanism against other programs on the same
computer. This phase serves only reviewed synthetic/non-secret values. A future
real-data adapter needs its own security/privacy review; this is not that adapter.

## No broker coupling or authority

Dashboard modules do not import `tradingbot_broker`, `tradingbot_backtest` or strategy
code. They cannot authenticate, collect/reconcile, prepare receipts, capture a PRE4
boundary, correlate/attribute/incorporate, evaluate PRE9 or invoke broker orders.
There are no trade buttons, mutation endpoints, journal/SQLite access, settings
controls or safety-state writes. All existing production files remain unchanged,
including V1/V2/V3 codecs/replay/storage, wire policy, strategy and active risk.

## Validation

Focused standard-library tests exercise fixed safety flags, exact snapshot copying,
demo domains, deterministic OHLCV, timeframe/scanner schemas, local HTTP requests,
HEAD, rejected methods/traversal/foreign hosts, headers and static/purity guards.
Tests never launch the user's browser and block outbound test connections outside
the ephemeral loopback server. CLI browser behavior is mocked.

Manual validation uses the real module entrypoint, loopback requests and clean
Ctrl+C. An already available local Chromium/Playwright installation may additionally
inspect the rendered page; it is not a project dependency or passing requirement.
Cloud/Linux validation does not establish Windows dashboard qualification.

Implementation validation on cloud Python 3.12.14:

- Focused dashboard tests: 73 passed, no skips.
- Full offline unittest discovery: 8,338 tests OK, skipped=4, including existing
  V1/V2/V3 compatibility tests. Real Gateway integration opt-in remained unset.
- All 144 tracked Python files compiled. Staged/unstaged diff checks passed.
- Real module entrypoint: root, CSS, JavaScript, favicon and JSON returned 200;
  POST returned 405. SIGINT/Ctrl+C stopped cleanly with exit zero. No local server
  was left running.
- Available local Chromium inspection: no page/CSP errors or external resource
  requests; symbol selection, all four timeframes, hover/keyboard inspection,
  zoom/reset, overlay toggles and demo-trade selection worked. Layouts at
  1920×1080, 1440×900 and 390×844 had no horizontal page overflow.
- Scope: exactly 13 intended files, including nine package files, one test module
  and three documentation files. All 64 existing files beneath broker/backtest/
  strategy remain byte-for-byte unchanged. Secret/account, credential/.env,
  external-resource, broker-import/operation, shell/eval and mutation scans passed.

No dependency was installed, no real IBKR connection or broker operation occurred,
and Gateway settings were untouched. This validates the cloud implementation;
the Windows launch command remains available for local Windows checking.

## Future work remains separately authorized

This visual surface commits to no final frontend architecture. Real market adapters,
read-only runtime snapshots, authentic PRE9 display, cross-source currentness,
inventory/disposition/ownership, management, protection, cancel/replace/exits,
recovery/risk/strategy restoration and any order authorization remain separate.
The first real PAPER order is still blocked; LIVE is unsupported.
