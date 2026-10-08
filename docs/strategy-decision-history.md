# Strategy Spec v1.0 — Approved Decision History

Decisions **#1–47 are approved and frozen**. This is a history and coverage map, not a competing implementation specification. The [canonical specification](strategy-spec-v1.0.md) and [default table](strategy-parameters-v1.0.md) contain the final authoritative behavior. This record summarizes each approved decision rather than claiming to reproduce every conversational rule verbatim. No decision is renumbered; no Decision #48 is introduced.

| Decision | Approved subject and preserved outcome | Canonical sections / important clarifications |
|---|---|---|
| #1 | A begins impulse; B is impulse high/resistance; C is pullback/higher low; D is breakout, not base | §1; replaces original prototype/document labels |
| #2 | >=8% impulse, 1–10 one-minute impulse candles, at least one >=2x prior-20 participant; real-time B | §6–8; #21/#29 clarify counts; #45 prevents same-A-candle upward sequencing |
| #3 | >=3% candidate activation, preceding A window, volume participation; two-candle B confirmation and >=1% pullback | §7–8; #24 specifies HIGH/participants; #21/#26/#29 refine B |
| #4 | C retracement, midpoint/A protection, development/consolidation, range/volume/higher low; early-breakout rejection | §9; #5 makes 50% exclusive; #16 separates provisional/locked C; #22 starts clock after B origin |
| #5 | D buffer/extension/two volume tests; next-open entry with gap cancellations | §10–11; #18/#30 clarify local baseline; #31 one opportunity; #11 calendar cutoff |
| #6 | C×0.995 stop, positive actual-entry risk, touch/gap fills, original stop-first ambiguity | §12–13; #34 makes rounded stop authoritative; #15 clarifies breakeven ambiguity |
| #7 | $10k, 1% BOD risk, floor whole shares, 1k/20%/3/60% limits, cash/no margin, >=2 shares, no additions | §12, §15; #13 valuation; #34 tick-normalized risk |
| #8 | Fixed 2R, target touch/gap fills, floor half with odd runner, breakeven, audit leg accounting | §13; #15 same-bar rule; #34 executable price normalization |
| #9 | Non-decreasing runner, three extra candles/three-low trailing, prospective stops/gaps, intraday EOD | §13–14; #11 replaces fixed EOD clock; #32 traded-only trailing count |
| #10 | Accepted-entry trade count, realized exit-leg P&L, completed loss streak, sticky daily locks/session reset | §15; #11 actual sessions; #36 NET supersedes before-cost classification |
| #11 | Actual exchange sessions/DST/early closes; exclusive close-minus-30 entry cutoff; final-minute-open liquidation | §2, §14; authoritative over fixed clock wording |
| #12 | Shared chronological portfolio, known open exits first, D RVOL ranking/ticker tie, no later proceeds for earlier entry | §15; #44 explicit opening timestamp/full-fill abstraction |
| #13 | Cash+open-marked shares equity, market exposure, BOD risk fixed, missing open rejects entries, caps entry-only | §12, §15; #32 reporting marks never substitute for entry valuation |
| #14 | Missing/invalid held-position data pauses shared run; replacement replay; unresolved trade/run incomplete | §3; preserved distinctly from #33 no liquidity and #35 tick failure |
| #15 | Original stop priority; 2R/entry-touch ambiguity awards partial then conservative breakeven runner exit | §13, §17; #34 execution tick normalization still applies |
| #16 | Provisional C lower-low updates/reset, lock after qualification, fixed locked C; exact touch holds | §9; resolves earlier provisional/invalidation conflict |
| #17 | Two failed/three total attempts, resolution timer, overextension expiry, canceled post-D setup consumed | §10; #29 makes resolution elapsed minutes; #18/#30 local volume |
| #18 | Rolling local-three baseline per attempt, excludes attempt and includes prior failures naturally | §6; #30 makes chronological/no-trade treatment explicit |
| #19 | Maximum wait after C locks, no timer restart, session expiry, no overnight setup | §10; #29 makes ten elapsed eligible minutes authoritative |
| #20 | One setup/position per ticker, no overlap or same-impulse reuse; unique IDs and fresh post-termination price data | §18, §20; #27 permits partial fresh windows; #47 final-exit quarantine |
| #21 | A=impulse candle 1, qualifying B by 10; two extra confirmation candles only; post-deadline higher high invalidates | §7–8; #29 valid traded counts; #26 equal-high origin treatment |
| #22 | B-confirmation candles supply potential C; origin+1 clock start, reset on allowed B move; no premature official C | §8–9; supersedes post-B-confirmation clock start |
| #23 | A-through-final-B inclusive impulse-volume arithmetic mean; freeze on B confirm; C average strictly lower | §6; #26 equal-high origin also recalculates |
| #24 | Previous eligible A lows/earliest tie; trigger HIGH >=3%; own previous-20 RVOL per participating candle | §7; #27/#29 price window/session; #46 chronological RVOL |
| #25 | Initialize B from already-known A-through-trigger highs; sequential known confirmation; separate logical/activation time | §7–8; **#26 overrides earliest B equal-high origin with latest**; #45 sequencing limits |
| #26 | Latest equal-high B origin inside deadline, reset confirmation/potential C, recalculate impulse baseline; no extension | §8; A tie remains earliest; later equal high cannot move origin |
| #27 | Current regular-session ABCD, up-to-ten A candles, same-day premarket 04:00 volume context, no prior-day carry | §2, §6; #29 count mapping; #30 D local context |
| #28 | Positive NO_TRADE classification distinct from unknown data; no synthetic prices; gap invalidation/continuity rebuild | §3; held positions still #14; #29 counts; #46 zero baselines |
| #29 | Traded counts for A/impulse/B/C/consolidation; elapsed counts for locked wait/resolution; no-trade B consecutiveness | §20 timer registry; supersedes #17/#19 candle timer wording |
| #30 | D local baseline previous three chronological regular minutes; verified zeros included; strict absolute comparison | §6; independent positive previous-20 test remains required |
| #31 | One next-interval entry; no-trade/unknown interval cancels+consumes; no later fill or trade count | §11, §18; exception-specific pending-entry data handling |
| #32 | Held no-trade cannot trigger exits/trailing; traded-only lows/counts; reporting marks; irrevocable EOD fill/no liquidity | §3, §13–14; no marks for entry eligibility; #33 portfolio consequence |
| #33 | One unresolved EOD no-liquidity position stops entire run at close; preserved results/state; no invented next day | §14; exact incomplete status; distinct from missing data |
| #34 | Authoritative ticks, stop down/target up, rounded-stop risk/sizing, actual fill precision, safe arithmetic, monotonic stops | §12; completed rules 1–58; #35 uncertainty consequence |
| #35 | Missing stop/target tick before entry rejects/consumes only setup; needed open-order tick failure terminates shared run | §12; completed rules 1–60; clean rerun with repaired metadata |
| #36 | Auditable configurable costs/slippage, disclosed zero baseline, net daily/streak/equity accounting, proportional entry costs | §15–16; rules 1–93; supersedes #10 before-cost breakeven |
| #37 | Point-in-time common-stock universe: <=$5, >=+3%, >=$50M, ADV10>1M; no arbitrary minimum/spread; delisted coverage | §4–5; #38 volatility; #39 dynamic state; #43 compatible units |
| #38 | Prior actual 20-session regular ADR%, HLC formula, >=5 eligibility/10 category, fixed intraday, no missing skips | §4; rules 1–80; resolves HIGH/VERY_HIGH labels |
| #39 | Completed CLOSE dynamic failure terminates pre-entry; actual OPEN final checks consume; no recovery/revival | §5; rules 1–69; open positions managed normally |
| #40 | Mandatory 15m T3 high/low>=T1, current three bars; weekly confirmed 2-left/2-right nearest overhead, >=5% room | §11; rules 1–108; rejects MA substitutes; #41/#42 clarify context |
| #41 | Fixed chronological 15m blocks, traded-only OHLC, entirely no-trade/invalid blocks unavailable, no skipping | §11; rules 1–90; earliest normal context-ready entry 10:15 |
| #42 | 52 candidate weeks + up to 2 older context weeks; trustworthy confirmation; distinguish missing vs pre-listing absence | §11; rules 1–80; no-resistance pass requires valid evaluation |
| #43 | Already-effective share-basis normalization, price/volume inverse scaling, cap/ADV/ADR/weekly consistency, no double adjust | §19; rules 1–115; active-position transformations remain unapproved |
| #44 | Full designated quantities, no fake depth; OPEN events at interval start, known exits before entries; research disclosures | §15–16; rules 1–70; resolves audit blocker 1 |
| #45 | Order-invariant or adverse setup OHLC result, no same-A-candle impulse proof, B-origin not C, locked C break beats D | §7, §9, §17; rules 1–76; resolves audit blocker 2 |
| #46 | Exactly 20 chronological volume slots, verified zeros included; zero ratio undefined/fails, finite ranking; local-three literal | §6; rules 1–87; resolves audit blocker 3 |
| #47 | Whole final-exit interval quarantined even OPEN exits; next whole traded interval fresh; volume continuity separate | §18; rules 1–73; resolves audit blocker 4 |

## Freeze and supersession record

The full #1–#43 consistency audit identified four bounded material blockers: initial fills/timestamps, setup OHLC sequence, previous-20 zero denominator, and post-final-exit fresh participation. Decisions #44–#47 resolve them respectively. The final freeze check found no introduced contradiction preventing deterministic implementation. The user then explicitly approved freezing Strategy Spec v1.0 and permanent documentation, without authorizing a backtester or external integration.

Canonical implementation precedence is the final approved behavior recorded in the specification, including the explicit overrides above. In particular: C is >=20%/<50%; C clock starts after final B origin; B equal-high origin is latest; D local volume is rolling chronological; #17/#19 timers are elapsed minutes; risk uses rounded executable stop; net P&L controls streaks; calendar times are session-relative. Do not resurrect older prototype defaults or the former strategy-inputs narrative.

There is no approved Decision #48. Provider verification, implementation conventions, deferred realism and future research are separately labeled; they do not alter approved v1.0 signal rules.
