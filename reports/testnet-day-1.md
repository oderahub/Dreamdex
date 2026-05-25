# DreamDEX Testnet Day 1 Integration Report

## Scope and disclaimers

These findings are from testnet integration against `stg.api.dreamdex.io` between May 9 and May 23, 2026, in preparation for the mainnet trading competition starting May 25, 2026. They are integration and documentation-friction findings, not mainnet performance claims.

Validated during testing: SIWE login flow, REST order preparation, wallet-funded IOC buy/sell on `WETH:USDso`, ERC-20 approval flow with local gas estimation, simulate-before-broadcast safety, and post-transaction balance refresh for inventory correctness.

Not yet validated: mainnet liquidity conditions, fresh quote-only wallet bootstrap with exactly 50 USDso starting capital, WBTC cycle execution at full capital, JWT refresh after 1-hour expiry, high-rate per-order WebSocket lifecycle subscriptions, and a multi-hour unattended soak.

Test wallet used for the strongest end-to-end testnet evidence: `0x0bA50b9001b2ECcd3869CC73c07031dca1e11412`.

## Report 1: Per-order WebSocket subscription pattern is easy for bot authors to miss

### What I expected

Based on common exchange API patterns, my first bot design assumed two WebSocket channel categories:

- Public market data: `orderbook.{SYMBOL}` and `trades.{SYMBOL}`
- Private account data: account-wide `orders` and `fills` channels, subscribed once after authentication

That mental model is common across many CEX and DEX-style APIs. It led to an early implementation that expected a global private channel to deliver all own-order lifecycle events and fills. After rereading the docs and implementing the documented path, the issue is not that DreamDEX lacks lifecycle streaming; it is that the per-order model is easy to miss unless the reader already knows to look for it.

### What the docs say

The DreamDEX Real-Time Feed docs list the supported subscription channels as:

- `orderbook`
- `ohlcv`
- `trades`
- `order`

The `order` channel is not account-wide. It is a lifecycle stream for one specific order and requires an `orderId`:

```json
{
  "operation": "subscribe",
  "channel": "order",
  "params": {
    "orderId": "0x1234567890abcdef"
  }
}
```

The docs also define `order:snapshot` and `order:update` messages for that one order. There is no documented global `orders`, `fills`, or `account` private channel. Once implemented as documented, this model is workable for resting orders.

### Why this matters

This is a different architecture from what many bot authors will reach for first. A bot placing resting orders must dynamically subscribe to `order.{orderId}` after it obtains the ID, then unsubscribe when the order reaches a terminal state.

That has three practical consequences:

- It adds subscription churn if applied to high-frequency IOC orders.
- It changes how order lifecycle state is modeled locally.
- If a bot author assumes account-wide private channels exist, they can spend hours debugging "missing fills" that are actually undocumented channel assumptions.
- It creates a design choice: resting/maker orders should use per-order lifecycle streams, while high-frequency IOC/taker strategies may be better served by post-transaction balance reconciliation to avoid subscription churn.

### Recommendation

Make the discovery path obvious by adding a short section to `developers/websocket-api/real-time-feed.md` titled `Order Lifecycle Tracking`:

1. State directly that DreamDEX does not expose a single account-wide private orders/fills channel.
2. Show the intended flow: prepare/simulate order, extract `orderId`, subscribe to `order` with that `orderId`, process `open`/`partial`/`filled`/`cancelled`, then unsubscribe.
3. Mention that public `trades` is market-wide trade data, not an authenticated own-fills stream.
4. Document the expected behavior for unsupported channel names, especially whether the server returns `error:error` or silently leaves the client with no useful lifecycle events.
5. Add guidance for IOC bots: using per-order subscriptions for every IOC order can create unnecessary churn; balance refresh or order polling may be the simpler source of truth for wallet-funded taker loops.

### Bot-side implementation

For Day 1, my bot treats post-transaction balance refresh as the inventory source of truth for wallet-funded IOC cycling. That keeps the hot path correct without high-rate per-order subscription churn. I implemented per-order WebSocket support for resting orders only, where lifecycle events matter and churn is lower. This path is unit-tested but not yet stress-tested under live resting-order load because the Day 1 competition config keeps YieldMaker disabled and uses IOC-only VolumeMill strategies.

## Report 2: Prepare/simulate pipeline has meaningful pre-broadcast failure modes under IOC cycling

### Headline numbers from supervised testnet cycling

In a supervised testnet session on `WETH:USDso`, the structured event log recorded:

- 88 approval submissions
- 78 order submissions
- 18 `order_simulation_rejected` events
- 12 `order_simulation_failed` events
- 12 `gas_estimate_failed` events

These were testnet/debug-session numbers, not a mainnet throughput claim. The important finding is qualitative: several failure modes occur before broadcast and must be treated as expected control flow by a serious bot.

### What the docs say

The Trading docs explicitly recommend:

1. Call the returned transaction with `eth_call` before sending.
2. If `placeOrder` returns `(false, 0)`, do not submit.
3. After confirmation, check receipt logs for `OrderPlaced`; a receipt can have `status = 1` but empty logs, meaning no order was placed.

That warning is important and correct. Bot authors should not equate successful REST prepare or successful EVM transaction status with a placed order.

### Failure modes observed

`order_simulation_rejected`: the contract returns a clean would-reject result. In IOC cycling this can happen when the book moves between observation and simulation, or when the IOC no longer crosses by the time the call executes.

`order_simulation_failed`: the `eth_call` itself fails with a JSON-RPC or execution error rather than a clean false return. This needs separate handling from a clean contract rejection.

`gas_estimate_failed`: local `eth_estimateGas` fails. The bot now treats this as a contained pre-broadcast issue, records it, and falls back conservatively only where appropriate.

Follow-up measurement on May 25 found a likely contributor to the clean IOC
simulation rejects: REST `/v0/orderbooks` and on-chain `getBookLevels` can
briefly disagree at top of book during testnet movement. In 5-sample runs, the
latest probe saw 1/5 mismatches on `SOMI:USDso`, 2/5 on `WETH:USDso`, and 2/5
on `WBTC:USDso`, generally within a few ticks and a 2.3-3.4s measurement
window. This makes simulate-before-broadcast non-optional for IOC bots.

### Silent status-1 receipts with empty logs are reproducible beyond zero-expiry

On May 25, I also reproduced the status-1/empty-logs path while manually
swapping native SOMI/STT into USDso on the `SOMI:USDso` testnet pool
`0x259fD6559214dd5aD3752322426eA9F9fABEFff4`.

Successful control tx:

```text
tx: 0xf6cad0fea122fbc3c052f9f1d836b8b988c7514a3ef56581f0966c283f0ab1f6
selector: 0x1c792779
value: 50 SOMI
limit price raw: 170100000000000000
quantity raw: 50000000000000000000
expireTimestampNs candidate: 1779788920541467357
expireTimestampNs UTC: 2026-05-26T09:48:40Z
receipt status: 1
logs_count: 4
gas_used: 499526
```

Silent no-fill txs:

```text
tx: 0xd4a0deeec2600a65d13d5c88b636b7bd124d71972c7787d1e3f59b626656b211
selector: 0x1c792779
value: 50 SOMI
limit price raw: 170700000000000000
quantity raw: 50000000000000000000
expireTimestampNs candidate: 1779788715816155188
expireTimestampNs UTC: 2026-05-26T09:45:15Z
receipt status: 1
logs_count: 0
gas_used: 159322

tx: 0x0e07d732d074335a70f35f78c4b269f6934f84ed57599e487905c0866cc7fa2f
selector: 0x1c792779
value: 50 SOMI
limit price raw: 170100000000000000
quantity raw: 50000000000000000000
expireTimestampNs candidate: 1779788926051260404
expireTimestampNs UTC: 2026-05-26T09:48:46Z
receipt status: 1
logs_count: 0
gas_used: 159322
```

These failed examples had non-zero future expiry timestamps, so the
status-1/empty-logs pattern is broader than the known `expireTimestampNs = 0`
docs discrepancy. In this case the likely trigger is IOC book movement or no
crossing liquidity by execution time. The important bot-author takeaway is the
same: receipt `status == 1` is not sufficient evidence that an order was placed
or filled; bots must check return values during `eth_call` and logs after
broadcast.

### Approval gas discovery

The most concrete gas issue was approval gas. The prepare response gave an approval gas hint that was too low for the observed USDso approval path. During testing, approval transactions failed on-chain until local estimation and buffering were added. A manual high-gas diagnostic showed the approval path could require far more than a standard ERC-20 `approve`.

This may be token-specific behavior in USDso rather than a DreamDEX pool issue, but bot authors experience it through the DreamDEX prepare/approval flow.

### Recommendation

1. In the Trading docs, keep the simulate-before-broadcast guidance prominent.
2. Add a code example that distinguishes clean simulation rejection, simulation RPC failure, gas-estimation failure, and successful simulation.
3. State whether API `gasLimit` fields are authoritative or merely hints.
4. Publish common custom error selectors or revert meanings for pool/order rejection paths.
5. Add a note that status-1 receipts with empty logs are not placed orders and should be reported as silent rejections.

### Bot-side mitigation

The bot now:

- Runs `eth_call` before broadcast.
- Does not broadcast after simulation exception or clean simulation rejection.
- Locally estimates gas and applies a buffer instead of trusting low API hints.
- Waits for approval receipts and caches approvals only after `status == 1`.
- For receipt-waited orders, records `logs_count` and emits `order_receipt_empty_logs` when `status == 1` but no logs are present.

## Report 3: Startup friction for first-time bot authors

### Finding 1: SIWE domain must match the API host

The SIWE flow is documented, and the auth error enum includes `domain_mismatch`. The practical integration detail is that the SIWE message domain must be derived from the active API base URL.

For staging, the domain is `stg.api.dreamdex.io`; for production, it is `api.dreamdex.io`. Hardcoding `dreamdex.io` caused authentication failure in early integration.

Recommendation: add a worked SIWE example for both staging and production, including the exact `domain` value.

### Finding 2: No single wallet balance endpoint is documented

The Vault docs document vault balance endpoints, but a wallet-funded bot also needs wallet balances:

- Wallet base token balance
- Wallet quote token balance
- Vault base balance
- Vault quote balance

The practical solution is on-chain reads: `eth_getBalance` for native SOMI and ERC-20 `balanceOf` for tokens discovered from `/v0/markets`. That is workable, but it should be explicit.

Recommendation: either document wallet balance reads as on-chain responsibility, or add an account balances endpoint that returns wallet and vault balances together.

### Finding 3: Market and orderbook response wrappers matter

The Market Data docs show wrapped response shapes:

- `/v0/markets` returns `{ "markets": [...] }`
- `/v0/orderbooks` returns `{ "orderbooks": [...] }`

This is correct in the docs, but bot authors need to rely on exact response shape, not inference. Keeping canonical examples complete will reduce first-run parsing issues.

### Finding 4: Native SOMI and ERC-20 paths need one consolidated bot-author guide

The docs mention native-token behavior in multiple places:

- Native token approval can return JSON `null`.
- Native SOMI uses `value`/payable calls rather than ERC-20 approval.
- Wallet-funded buys and sells differ by which token must be approved.

Recommendation: add one `Native vs ERC-20 Markets` page that walks through complete bot flows for native base, ERC-20 base, quote approvals, wallet funding, and vault funding.

### Finding 5: Fees page is useful but still WIP

The Fees page states 0% maker and 0% taker fees, while gas is paid in SOMI. It is marked Work in Progress and still has TODOs for estimated gas costs.

Recommendation: before the competition, publish practical gas estimates for approval, place order, cancel order, and vault deposit/withdrawal. For volume bots, gas budget is the difference between a good dry run and a failed deployment.

## Implementation changes made in the bot after these findings

- Global `orders`/`fills` WebSocket subscriptions are no longer used.
- `WsClient` supports documented dynamic `order` subscribe/unsubscribe by `orderId`.
- The engine subscribes to per-order updates only for resting orders (`gtc`/`post_only`) to avoid IOC hot-path churn.
- Terminal order statuses now include `filled`, `closed`, `cancelled`, `canceled`, `expired`, and `rejected`.
- Receipt-waited orders now report `logs_count`, `placed`, and `order_receipt_empty_logs` when logs are empty.
- The generated session summary now distinguishes submitted orders from receipt-confirmed waited paths and receipt-confirmed orders with log evidence.

## Day 1 readiness note

This bot is suitable for a conservative supervised Day 1 start, not an unsupervised claim that all mainnet conditions are known. The first competition run should still include:

1. Read-only preflight on the competition wallet.
2. Confirm exactly 50 USDso and enough native SOMI for gas.
3. Confirm zero unexpected starting base inventory.
4. Start with VolumeMill only; keep YieldMaker disabled.
5. Watch the first bootstrap buy and first full buy/sell round-trip.
6. Keep post-tx balance refresh as the inventory source of truth.
7. Export `session.jsonl` and regenerate the summary after the first hour.
