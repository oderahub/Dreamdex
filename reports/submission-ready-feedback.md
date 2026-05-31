# DreamDEX Alpha Feedback - Submission-Ready Findings

Integration testing performed against Somnia Shannon testnet, chain `50312`,
and `https://stg.api.dreamdex.io` between May 9 and May 25, 2026, in
preparation for the May 26 mainnet trading competition.

Findings below came from an automated bot running real
prepare -> sign -> broadcast -> receipt-confirmation cycles, plus targeted
read-only probes against REST and pool contracts. These are testnet findings,
not claims about mainnet behavior.

## 1. Status=1 IOC transactions can silently no-fill with non-zero expiry


**Impact:** affects bot correctness and inventory accounting.

### Claim

Some IOC order transactions can succeed at the EVM transaction level
(`receipt.status = 1`) while emitting zero logs and placing/filling no order.
The Discord channel already established that `expireTimestampNs = 0` can produce
this symptom. The examples below show the same symptom with non-zero future
expiry values, so the status=1/empty-logs pattern has at least one other cause.

### Network / Market

- Network: Somnia Shannon testnet, chain `50312`
- Pool: `SOMI:USDso`
- Pool address: `0x259fD6559214dd5aD3752322426eA9F9fABEFff4`
- Wallet: `0x0bA50b9001b2ECcd3869CC73c07031dca1e11412`
- Observed function selector: `0x1c792779`

Selector note: I checked several plausible `placeTakerOrderWithoutVault(...)`
signatures locally, but they did not hash to `0x1c792779`. I am therefore
leaving this as selector evidence rather than claiming a function name without
the exact ABI.

### What I Expected

If an IOC order can no longer cross or is otherwise invalid at execution time,
the transaction should either revert with a decodable reason/custom error, emit
a diagnostic event, or expose a clear failure code through a documented return
path.

### What Actually Happened

Two transactions returned `receipt.status = 1`, consumed gas, emitted zero logs,
and did not place/fill an order.

The 50 SOMI `msg.value` was refunded in both failed transactions. I verified
historical wallet balance deltas at the transaction block:

```text
failed tx balance delta = gas_used * gas_price
successful control delta = 50 SOMI + gas_used * gas_price
```

Successful control transaction:

```text
tx: 0xf6cad0fea122fbc3c052f9f1d836b8b988c7514a3ef56581f0966c283f0ab1f6
selector: 0x1c792779
value: 50 SOMI
limit price raw: 170100000000000000
quantity raw: 50000000000000000000
expiry word value: 1779788920541467357
receipt status: 1
logs_count: 4
gas_used: 499526
wallet balance delta: 50.002997156 SOMI
```

Silent no-fill transactions:

```text
tx: 0xd4a0deeec2600a65d13d5c88b636b7bd124d71972c7787d1e3f59b626656b211
selector: 0x1c792779
value: 50 SOMI
limit price raw: 170700000000000000
quantity raw: 50000000000000000000
expiry word value: 1779788715816155188
receipt status: 1
logs_count: 0
gas_used: 159322
wallet balance delta: 0.000955932 SOMI

tx: 0x0e07d732d074335a70f35f78c4b269f6934f84ed57599e487905c0866cc7fa2f
selector: 0x1c792779
value: 50 SOMI
limit price raw: 170100000000000000
quantity raw: 50000000000000000000
expiry word value: 1779788926051260404
receipt status: 1
logs_count: 0
gas_used: 159322
wallet balance delta: 0.000955932 SOMI
```

The gas difference is a useful clue: failed txs used `159,322` gas while the
successful control used `499,526` gas. That suggests the contract hits an early
validation/no-fill branch and exits without emitting a diagnostic event.

### Why This Matters

For IOC orders, `receipt.status == 1` is not a reliable success signal. A naive
bot will count these transactions as successful orders, corrupt its inventory
state, and continue trading from a false local view.

### Suggested Fix

1. Emit an explicit rejection/no-fill event for this branch, if feasible.
2. Document all known causes of `status=1` with empty logs.
3. Publish custom error selectors / return codes for pool order failures.
4. In bot examples, state directly that order success requires `OrderPlaced` /
   fill logs or balance reconciliation, not just `receipt.status == 1`.

## 2. Prepare endpoint gas hints are not sufficient for approval/order flows

**Impact:** affects transaction reliability for bot authors.

### Claim

Prepared transactions sometimes need local gas estimation and buffering. In my
testing, approval and order transactions commonly required gas well above what a
simple fixed limit or low prepare hint would safely cover.

### What I Observed

My bot had to add local `eth_estimateGas` and a 25% buffer for prepared
transactions. Representative observed estimates:

```text
SOMI:USDso USDso approval
estimated gas: 1,391,074
buffered gas used by bot: 1,738,842
tx: 0x12f3ea5ce833c22d796926f055bd92900b57ce4dac131ef3942dd7eb0756124f

WETH:USDso USDso approval
estimated gas: 1,091,074
buffered gas used by bot: 1,363,842
tx: 0x565af4141ed36d22f2be4a12d77d6e78235fc1f9a7b88d81c941b6ceaa90ed66

WBTC:USDso USDso approval
estimated gas: 1,391,074
buffered gas used by bot: 1,738,842
tx: 0xd77235eeb190bd79b456e0b11ea5233dbdc4a750fc0698d469dd29794eb3890f
```

The bot also recorded `gas_estimate_failed` events for some prepared orders when
the underlying call would not execute successfully. In those cases the bot
refused to broadcast after simulation failed.

### Why This Matters

Bot authors using prepared transactions need to know whether returned gas fields
are authoritative or merely hints. If they trust a low/static gas value, approval
or order transactions can fail on-chain. If they blindly fall back to very high
gas without simulation, they risk sending transactions that should have been
skipped.

### Suggested Fix

1. Document whether prepare response gas fields are estimates, lower bounds, or
   intended transaction gas limits.
2. Add an example that performs local `eth_estimateGas` with a buffer.
3. Add guidance: if gas estimation fails, run `eth_call` / inspect the prepared
   tx before deciding whether to broadcast.

## 3. REST orderbook snapshots can lag pool `getBookLevels` by a few ticks

**Impact:** affects IOC/taker reliability and docs clarity.

### Claim

REST `/v0/orderbooks` and pool-level `getBookLevels` usually agree, but testnet
sampling found short-lived top-of-book drift within a 2.3-3.4 second measurement
window. This is not a "REST is broken" claim; it is a freshness/semantics issue
that matters for IOC bots.

### How I Confirmed

Probe method:

1. Call REST `/v0/orderbooks?symbols={market}&depth=5`.
2. Immediately call pool `getBookLevels(bool,uint64)` for bids and asks.
3. Decode as `(uint128 priceRaw, uint128 quantityRaw)[]`.
4. Compare decimalized top levels.

Representative latest run:

```text
SOMI:USDso: 1 mismatch / 5 samples
WETH:USDso: 2 mismatches / 5 samples
WBTC:USDso: 2 mismatches / 5 samples
```

Discovered selector:

```text
getBookLevels(bool,uint64) -> 0x4f1ce9a7
```

Example:

```text
WETH:USDso sample
REST bid:  2117.99 x 0.063
Chain bid: 2117.85 x 0.063
REST ask:  2118.43 x 0.037
Chain ask: 2118.05 x 0.037
```

### Why This Matters

For an IOC bot, a few ticks of stale top-of-book data can make a prepared order
fail simulation or fail to cross by execution time.

In an earlier fast-book testnet session, the bot saw a meaningful pre-broadcast
failure rate (`order_simulation_rejected` / `order_simulation_failed`). In a
later 90-minute WETH-only soak under more stable conditions, the same bot path
submitted hundreds of orders without simulation rejects. That contrast supports
the idea that short-lived book drift is one contributor.

The team also recommended using `getBookLevels(true, 5)` in Discord to confirm
liquidity. That method is useful, but I could not find it documented with its
signature/return shape.

### Suggested Fix

1. Document what REST orderbook `timestamp` means: API response time, indexer
   time, matching-engine event time, or source block time.
2. Document the expected freshness relationship between REST orderbooks and
   pool state.
3. Document `getBookLevels(bool,uint64)`, return shape, and raw-unit scaling.
4. Recommend simulate-before-broadcast for IOC examples.

## 4. WebSocket order lifecycle requires per-order subscription

**Impact:** affects docs discoverability for bot authors.

### Claim

The documented WebSocket model supports per-order lifecycle tracking via the
`order` channel and an `orderId`. It does not appear to provide a global private
own-orders / own-fills channel. This is workable, but it is easy for bot authors
to miss because many exchange APIs expose account-wide private streams.

### What I Expected

My first implementation looked for account-level private channels such as
`orders`, `fills`, or `account`, subscribed once after authentication.

### What Docs Say / What Works

The docs show:

```json
{
  "operation": "subscribe",
  "channel": "order",
  "params": {
    "orderId": "..."
  }
}
```

So for resting orders, the intended flow is:

1. prepare/simulate/broadcast order
2. get or infer the `orderId`
3. subscribe to `order` with that `orderId`
4. process lifecycle updates
5. unsubscribe when terminal

### Why This Matters

Bots with maker/resting orders must dynamically manage subscription churn and
local order-id mappings. IOC-heavy bots may be better served by receipt +
balance reconciliation instead of subscribing per IOC order.

### Suggested Fix

Add a short docs section named `Order Lifecycle Tracking`:

1. State directly whether account-wide own-orders / own-fills channels exist.
2. Show the per-order lifecycle flow.
3. Clarify whether public `trades` includes own-fill attribution or is only
   market-wide trades.
4. Document expected server behavior for unsupported channel names.

## 5. Native SOMI markets need gas-vs-inventory guidance

**Impact:** affects bot safety on native-token markets.

### Claim

`SOMI:USDso` uses SOMI both as a tradable base asset and as native gas. A bot
author treating `SOMI:USDso` identically to an ERC-20 base market can produce a
bot that either:

- skips quote-only bootstrap because gas SOMI looks like base inventory, or
- sells its own gas while trying to flatten base inventory.

### What I Observed

In my bot, a fresh wallet with USDso plus native SOMI gas initially caused a
bootstrap bug: the SOMI gas balance looked like tradable SOMI base inventory.
The same class of issue can affect inventory drift rules and sell sizing.

The bot-side fix was to add an explicit native reserve:

```yaml
native_base_reserve_by_market:
  SOMI:USDso: "10.00"
```

Strategy/risk code then uses:

```text
tradable_somi = max(0, native_somi_balance - native_somi_reserve)
```

### Suggested Fix

Add a docs section: `Native token markets: gas balance vs trading inventory`.
It should recommend:

- reserving native SOMI for gas
- excluding that reserve from strategy inventory
- only treating native balance above reserve as sellable base
- handling native sells with `msg.value`
- handling ERC-20 buys/sells with approval flow

## 6. SIWE domain must match the active API host

**Impact:** affects staging/mainnet integration startup.

### Claim

The SIWE message domain must be derived from the active API host. For staging it
is `stg.api.dreamdex.io`; for production it is `api.dreamdex.io`. Hardcoding a
generic domain caused auth failures during early integration.

### Why This Matters

Bots commonly switch hosts between staging and production. If the SIWE domain is
hardcoded, the bot fails before it can reach trading logic.

### Suggested Fix

Add worked SIWE examples for both environments:

```text
staging API:    https://stg.api.dreamdex.io
SIWE domain:   stg.api.dreamdex.io

production API: https://api.dreamdex.io
SIWE domain:    api.dreamdex.io
```

Also include the expected error shape for domain mismatch so bot authors can
diagnose this quickly.
