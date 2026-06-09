# DreamDEX Alpha Competition — Phase 2 Follow-Up Feedback

## Scope

These observations were collected during Phase 2 of the Alpha Trading Competition
(June 8 – June 9, 2026) while operating an unattended bot against the
mainnet API at `https://api.dreamdex.io` and the Somnia mainnet RPC.

The bot wallet used:

```text
0x4258950186a12492Bf805f2B9D7facd202921F34
```

These are mainnet observations, not claims about testnet behavior. Each
finding lists the artifact (tx hash, log timestamp, or decoded error
payload) that the observation rests on.

This follow-up references the Phase 1 final submission
(`reports/competition-final-submission.md`) and the earlier consolidated
report (`reports/final-consolidated-feedback.md`). Several Phase 1
findings recurred in Phase 2 and are noted here as confirmations rather
than re-stated in full.

---

## Finding 1 — WS orderbook staleness: second documented instance, on a different symbol

**Phase 1 cross-reference:** Finding 1 (USDC.e WS feed froze for hours).

**What we observed in Phase 2.** During a live yield-maker experiment on
`USDC.e:USDso` between 15:07 and 15:12 UTC on 2026-06-09, the strategy
posted PostOnly bid and ask at what it believed were the current
top-of-book prices. When we later cancelled and inspected the orders
through the cancel helper, we found:

```text
id=202914184810808497282  side=sell  price=1.0006  qty=4.99
id=36893488147422531293   side=buy   price=0.9994  qty=5
```

A REST orderbook query at the same wall-clock time returned
`best_bid=0.9999`, `best_ask=1.0001` — a real spread of 2 bps. Our
resting orders were therefore **5 ticks (5 bps) outside the actual BBO
on each side**, sitting in deep book where the Gaussian yield weighting
collapses and where the queue position is irrelevant.

The strategy carries a `requote_threshold_bps: 1.0` configuration and a
`requote_min_interval_sec: 5.0` cadence. With 5 bps of price drift
against the live BBO, the requote should have fired immediately. It did
not, because the strategy's in-memory book — populated by the WS
`orderbook` channel — was reporting the same wide quote as the order
itself, so `drift_bps` evaluated to near-zero.

Net effect across the test window: **zero fills in ~15 minutes of live
resting**, on a market doing 38 trades per minute over the same window.
The wasted time was not a configuration error — it was the same silent
per-symbol stale-channel pattern documented in our Phase 1 submission,
now reproduced on a separate session on the same symbol.

**Why this is hard for bot authors.** The Phase 1 mitigation was
*restart*. That works when the operator notices, but a maker bot that
never gets filled looks like a soft failure (no errors, just no fills),
making the underlying cause hard to attribute without external
comparison against REST. Our `ws_staleness` risk rule fires only on
*global* WS silence, which never trips when other symbols are still
streaming.

**Suggested protocol-side improvement.** Same as Phase 1 Finding 1: a
per-symbol heartbeat at a documented cadence, or documented
stale-channel semantics so clients can detect and re-subscribe.

**Bot-side mitigation we plan to add.** A periodic REST orderbook
comparison against the in-memory WS book, with auto-resubscribe when
drift > N bps for > M seconds on any subscribed symbol.

---

## Finding 2 — Vault funding can pull from wallet inventory (undocumented)

**What we observed.** The Order Types doc states that PostOnly orders
"require vault funding." We interpreted this as a hard requirement that
the asset has to physically reside in the vault.

In practice, after a bid filled, we held 5 USDC.e in the **wallet** (not
the vault) and zero in the vault. The strategy then posted a PostOnly
ASK with `funding=vault` for that 5 USDC.e:

```text
2026-06-09T15:19:32  inventory.initialized  market=USDC.e:USDso
                     vault_base=0  vault_quote=0.004  wallet_base=5
2026-06-09T15:19:33  engine.order_submitted  coid=ym_sell_255723de
                     side=sell  strategy=yield_maker
                     tx_hash=13b86c25626671c228c7468171caf514d0d143e114a5d51d0f41cf05b5fe598a
```

The order broadcast, passed simulation, and rested on the book. The
protocol evidently pulled inventory from wallet on order placement
rather than requiring an explicit vault deposit step.

**Why this matters.** This is genuinely useful behavior — it removes a
deposit / withdraw cycle between every maker fill — but it is not
documented anywhere we found. The natural reading of "requires vault
funding" leads bot authors to write an explicit deposit step before
posting maker orders, which is unnecessary friction.

**Suggested fix.** Add a note to `trading/common/order-types.md` clarifying
that for PostOnly orders, the protocol can pull base or quote from
either the vault or the wallet at order-placement time. If there is a
preference order (vault first, then wallet?), document it.

---

## Finding 3 — Cancel refunds released collateral go to wallet, not vault

**What we observed.** We deposited 15 USDso to the `USDC.e:USDso`
vault:

```text
deposit_tx = 82c2a8dcfca9c5b5905d2ef91d7713030b0b9845b5fa30bbc6a6e84728c2b3a5
deposit_status = 1
```

Across two bid orders, 10 USDso was locked as collateral. After
cancelling both orders cleanly:

```text
cancel_tx_1 = e4909e5034137b78e93a70b99a82e9a2be2c0bae7a58e985a1ccb136f44eb5ee  status=1
cancel_tx_2 = 1e440e1db6b065c1fed87db2dc816e0b1d9ff6401b199fcaca9ac83008284eb4  status=1
```

we expected the released ~10 USDso to be back in the vault as free
balance. Instead, a subsequent vault withdraw attempt reverted with:

```text
ContractCustomError 0xcf479181
  data: 0x000e35fa931a0000  → "have"  ≈ 0.004 USDso
        0x8ac7230489e80000  → "want"  = 10 USDso
```

A direct on-chain wallet balance check confirmed the funds had landed
in the **wallet** as ERC-20 USDso, not back in the vault:

```text
wallet USDso: 54.98 (up by ~10 vs the post-deposit baseline)
vault USDso:  0.004 (down to dust)
```

**Why this is hard for bot authors.** The mental model for an LP-style
flow is "deposit → resting → fill → resting again." Cancellation routing
the refund to wallet means the next cycle has to re-deposit, which adds
latency, gas, and operational complexity. It also caused us to write a
withdraw helper that turned out to be unnecessary in this state — we
had nothing meaningful to withdraw because the funds had already left
the vault.

**Suggested fix.** Document the destination of collateral on cancel
explicitly — likely in `trading/vaults.md` or the order-types page — so
maker-bot authors can model the collateral lifecycle correctly.

---

## Finding 4 — Yield payout cadence is `[TODO]` on the Fees page

**What we observed.** The Collateral Yield Algorithm doc
(`trading/common/yield-algorithm.md`) defines the scoring formula
clearly: `score = quantity × W × seconds`, with `W` Gaussian-weighted to
mid-price proximity. Payment is described as periodic settlement to the
maker's wallet.

The Fees doc (`trading/common/fees.md`) has the payment-method line:

> Payment: Yield is paid out in USDso to the maker's wallet. \[TODO: Add methodology]

**Why this matters.** Without a documented cadence (per block, per
hour, per day, per week, per settlement window?), a bot author running a
short-window experiment cannot decide whether maker quoting is viable.
We ran a 25-minute live maker test and saw no yield credit in the
wallet — but with the cadence undocumented, we cannot tell whether that
means yield wasn't accrued, or whether it accrued but the next
settlement boundary wasn't crossed yet.

For Alpha-Competition-sized windows (days), this gap is decision-blocking:
a daily payout is meaningful; a weekly payout might miss the entire
contest.

**Suggested fix.** Replace the `[TODO]` with the actual cadence, the
trigger (cron / epoch / block-height / on-demand?), and how to verify
historical accrual.

---

## Finding 5 — Custom error selectors are not documented

**What we observed.** A vault withdraw revert returned the custom error
data shown in Finding 3, beginning with selector `0xcf479181`. We were
able to interpret it only because the two parameters happened to be
straightforward `(have, want)` uint256 amounts, and we could check both
against the live vault balance.

For other revert paths — particularly the contract-level place-order
rejection paths the Phase 1 testnet report enumerated under
`order_simulation_rejected` — we could not identify a documented
mapping from selector to meaning.

**Suggested fix.** Publish a list of custom error selectors in
`developers/contracts/events.md` (or a new `errors.md` page next to it),
with the ABI encoding of their parameters, so bot authors can decode
reverts without inspector access.

---

## Finding 6 — Small-capital makers cannot competitively quote at BBO

**Phase 1 cross-reference:** Finding 4 noted that `USDC.e:USDso` is the
most-efficient market for cohort volume. This finding adds a
maker-side counterpart.

**What we observed.** During the live yield-maker test, we quoted at
BBO match (`improve_ticks: 0`, so `bid_price = best_bid` and
`ask_price = best_ask`) at $5 size on `USDC.e:USDso`, on a market doing
~38 trades/minute with ~$1,750/minute in volume. Over 25 minutes of
live resting time spread across two bot-restart cycles, we received
**one fill**: a single 5-USDC.e bid was hit during a pause window
when the dominant maker presumably did not refresh in time.

The dominant maker at $0.9999/$1.0001 evidently had both larger
displayed size and earlier time-in-queue, which under Price-Time
Priority means our $5 quote sat behind every previously-placed order
at that price level. Most takers swept the dominant maker's quantity
and were fully filled before reaching us.

**Why this matters for bot authors.** The Profit-Oriented Strategy
Guidance section of the protocol docs encourages maker quoting at fair
value. In practice on the cohort markets, there is a **minimum capital
threshold** below which maker quoting at BBO does not return fills at a
meaningful rate. Bot authors below that threshold are better off using
IOC (taker) for volume goals even though they pay the spread.

**Suggested fix.** Either (a) publish the typical top-of-book quantity
range per market so bot authors can size accordingly, or (b) add a
maker-strategy note suggesting that participants with small starting
capital should not expect significant fills at BBO until the dominant
maker is consumed. Together with Finding 4 from the cumulative report,
this would let a new participant make a calibrated maker-vs-taker
decision on day one.

---

## Finding 7 — Throughput improvement from parallelizing balance refresh

**Phase 1 cross-reference:** Finding 5 measured 6.6 tx/min wall-clock
ceiling on the bot, with the bottleneck called out as four sequential
vault-balance GETs after every tx.

**What we observed in Phase 2.** After refactoring the engine's
`_refresh_balances` to `asyncio.gather` across the four watched markets
(commit-level change in our repo), measured throughput on a
dual-market `volume_mill` (`WETH:USDso` + `WBTC:USDso`) cycling at
`cycle_interval_sec: 10.0`:

| Metric | Phase 1 | Phase 2 |
| --- | --- | --- |
| Tx rate (single-market push) | 6.6 / min | 11 / min |
| Volume rate | \$1,170 / hour (WETH-only after USDC.e add) | \$19,800 / hour |
| Cost-per-dollar-of-volume | \~3 bps (USDC.e + WETH mix) | \~0.83 bps (WETH + WBTC) |

The 1.67× tx-rate improvement and the better cost ratio both came
without any per-tx code-path changes — just a parallel I/O fan-out on
the post-tx balance refresh. We confirm that with this change the
binding constraint is the protocol round-trip (REST prepare + RPC
broadcast), not application-side serialization.

**Why this matters.** The Phase 1 recommendation that the protocol
expose a combined "prepare + execute" endpoint is still the cleanest
path to higher single-wallet throughput. But for bot authors who can't
wait for that change, parallel balance refresh is a meaningful
short-term lift and worth highlighting in any future bot-author guide.

---

## Summary of suggested doc additions

In priority order, by what a new cohort would benefit from most:

1. **Per-symbol WS heartbeat** (Finding 1, recurring) — highest-impact
   reliability change.
2. **Vault funding can use wallet inventory** (Finding 2) — removes
   significant unnecessary deposit/withdraw friction.
3. **Cancel collateral routing** (Finding 3) — prevents a confusing
   debugging session for any maker-bot author.
4. **Yield payout cadence** (Finding 4) — decision-blocking for
   competition contexts.
5. **Custom error selector list** (Finding 5) — accelerates revert
   debugging.
6. **Maker BBO competitiveness disclaimer** (Finding 6) — calibrates
   participant expectations.

None of these are protocol-breaking. They are all places where the
protocol behaves correctly but the behavior is either undocumented or
surfaced through error paths a bot author has to reverse-engineer.

---

## Source artifacts (for protocol-team verification)

```text
Vault deposit
  approval_tx:   0xf7b59b8a279c22b613e219f0db7efae72aab02287235a5b91d6eefed60968995
  deposit_tx:    0x82c2a8dcfca9c5b5905d2ef91d7713030b0b9845b5fa30bbc6a6e84728c2b3a5

Maker orders posted live (yield_maker test)
  buy_run1:      0xe39ba7d625586dc2eec336d3a892fdb6ce3b1d16136f646bdb9ec0c19425f19d
  buy_run2:      0x7b2633d5c03e05cd56a240369fd588fbb58c8620f997e2fa927b958093aaab1d
  sell_run2:    0x13b86c25626671c228c7468171caf514d0d143e114a5d51d0f41cf05b5fe598a

Order cancellations
  cancel_ask:    0xe4909e5034137b78e93a70b99a82e9a2be2c0bae7a58e985a1ccb136f44eb5ee
  cancel_bid:    0x1e440e1db6b065c1fed87db2dc816e0b1d9ff6401b199fcaca9ac83008284eb4

Flatten leftover USDC.e to USDso (IOC sell)
  flatten_tx:    0x7db27d07f54b470cc3db3ccebb682f3fde982fb7ddba649240b7fed670638061

Vault withdraw revert (custom error decoded in Finding 3)
  selector:      0xcf479181
  have:          0x000e35fa931a0000  =  0.004 USDso
  want:          0x8ac7230489e80000  = 10.000 USDso
```
