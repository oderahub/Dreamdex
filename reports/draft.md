# Feedback Reports — Draft

Working notes from the testnet shakedown. Each section becomes a polished feedback
report submitted to DevRel. Capture as we go; polish once we have enough evidence.

---

## Report 1 — `[CATEGORY]`: `[ONE-LINE SUMMARY]`

**Category:** API / Docs / WebSocket / Contract / UX / Leaderboard
**Severity:** Low / Medium / High / Critical

### Summary
_One sentence._

### Expected Behavior
_What the docs imply or what we expected._

### Actual Behavior
_What we observed. Include exact response payloads, status codes, log excerpts._

### Steps to Reproduce
1.
2.
3.

### Evidence
- Network: testnet (chain 50312) / mainnet (chain 5031)
- Wallet:
- Market:
- Timestamp (UTC):
- Tx hash:
- Log file lines: `logs/bot.jsonl:LXXX–LXXX`
- Evidence file: `logs/probes.jsonl` filter by `probe="..."`

### Impact
_Why this matters for builders/bots/agents on dreamDEX._

### Suggested Fix
_Specific, actionable._

---

## Candidate findings to chase during shakedown

1. **REST rate limit is undocumented.** The docs do not specify per-endpoint or per-account REST rate limits. Run `probe_rate_limit_burst` and document the empirical ceiling. This is likely the strongest report.

2. **JWT has no refresh endpoint.** Docs only describe `POST /v0/auth/login`. After expiry the client must complete a full SIWE re-auth. Document the friction this creates for long-running bots.

3. **`stp` modes — verify both behave as documented.** Place a resting PostOnly, fire a self-crossing taker once with `cancelTaker`, once with `cancelMaker`. Capture on-chain behavior in both cases. Compare with the docs.

4. **Silent rejection path.** Docs warn that direct contract calls can return `(false, 0)` with empty logs on insufficient vault balance. Trigger this deliberately. Verify the receipt-checking advice in the docs actually distinguishes silent-reject from accepted-but-not-filled.

5. **Decimal-string vs raw-unit boundary.** Docs say "REST uses decimal strings; contracts use raw units." Probe whether REST will accept a numeric string with 18 decimal places. Whether it accepts scientific notation. Whether the API rounds, truncates, or rejects.

6. **WS reconnect drift.** If we drop the WebSocket mid-fill, does the re-subscribed `orders` channel deliver missed events, or only state-since-reconnect? If the latter, the bot's view of its own orders silently diverges from the matching engine.

7. **Tick/lot precision rejection messages.** When we submit off-grid prices or undersized quantities, the rejection message should specify *which field* failed and *what the constraint was*. Capture how informative the actual messages are.

8. **Native SOMI vs ERC-20 funding parity.** The vault deposit flow differs between SOMI/USDso (uses `depositNative` with msg.value) and the other pools (uses `approve` + `deposit`). Document any UX inconsistencies, error message differences, or testing friction discovered while implementing both paths.

9. **The CCXT branch.** `ccxt/ccxt#28591` was opened May 13, 2026. Smoke-test the branch for basic reads (markets, orderbook). Report which methods work cleanly and which need work before public release.

10. **`fetchOrders` / `fetchMyTrades` pagination contract.** Unclear from docs whether cursor-based or offset-based. Probe with a known-non-empty history and document.
