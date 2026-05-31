# DreamDEX Alpha Day 1 Mainnet Report

Generated: 2026-05-26T19:54:43Z  
Participant: trader-4  
Wallet: `0x4258950186a12492Bf805f2B9D7facd202921F34`  
Network: DreamDEX Alpha mainnet  
API host: `https://api.dreamdex.io`

## Summary

I ran an automated wallet-funded trading bot on Day 1 using REST order
preparation, WebSocket connectivity, smart-contract simulation, local gas
estimation, and receipt/balance reconciliation.

The bot generated real activity across:

- `SOMI:USDso`
- `WETH:USDso`
- `WBTC:USDso`

Latest leaderboard state observed during the run:

```text
Rank: 1
Tx: 290
Volume: 930.47 USDso
PnL: -0.22 USDso
```

After stopping and flattening inventory, the wallet state was approximately:

```text
USDso: 49.778875
WETH:  0
WBTC:  0
Native SOMI: 9.992409452
```

## Trading System Tested

The bot used an IOC volume-cycling strategy:

1. Read market/orderbook state through REST.
2. Prepare orders through DreamDEX REST endpoints.
3. Simulate prepared calldata before broadcast.
4. Broadcast only if simulation passed.
5. Wait for receipt confirmation.
6. Refresh balances after each transaction.
7. Treat balances, not local assumptions, as the inventory source of truth.

YieldMaker / resting maker mode was intentionally left disabled for Day 1. The
goal was first to validate high-throughput taker flow safely.

## Mainnet Run Evidence

Local bot log window:

```text
Started: 2026-05-26T16:05:32Z
Stopped: 2026-05-26T16:41:05Z
Events recorded in local log: 1404
Orders submitted by bot: 91
Clean order simulation rejections: 6
Markets traded: SOMI:USDso, WETH:USDso, WBTC:USDso
```

Order submissions by market/side:

```text
WETH:USDso buy:  16
WETH:USDso sell: 15
WBTC:USDso buy:  16
WBTC:USDso sell: 16
SOMI:USDso buy:  14
SOMI:USDso sell: 14
```

Representative submitted tx hashes:

```text
WETH buy:  0xf2bfe22a4efe6dbe2c73fae5a20cddab07623d4bd768d23d10e5570882f70938
SOMI buy:  0xfd857a0480370815f12701fa9cb9570c550368a230deaa076b3dd0416eddddb5
WBTC buy:  0xcc6cb1bbc96a73ca4f9161c189171f9c0cbd08a85468d011944ac56e7d45e468
SOMI sell: 0x4feb9367d06fd754c872d7f722259aba9defd7d6d2a6aa9db3d0e5c65fe17090
WETH sell: 0x03685ca6cc8ec090527bb7186ab13087af07c0a58e87b6897c202182e4c20cba
WBTC sell: 0x37a7b3b77029ff3d9ca37b37da1561dc1e78338d64b2f86e2f46fb78633d7b30
```

## Finding 1: REST API availability is a hard dependency for REST-prepared bots

### What happened

At kickoff, the team announced:

```text
REST APIs are not working right now.
(https://api.dreamdex.io/v0)
I'll let you know when indexer is fixed.
```

The API later recovered and the bot was able to trade normally. During the
mainnet run, REST market, orderbook, auth, approval, and order-prepare endpoints
returned successful responses.

### Why this matters

Bots using the documented REST prepare flow are blocked when REST/indexer access
is unavailable, even if pool contracts are live. Bot authors need to know which
paths remain usable during REST/indexer incidents:

- direct contract reads/writes
- REST order preparation
- REST orderbook reads
- WebSocket orderbook streams

### Suggested fix

Document an operational fallback model:

1. Which endpoints depend on the indexer.
2. Which endpoints remain usable if the indexer is delayed or down.
3. Whether bots should fall back to direct contract calls during REST incidents.
4. A status page or health endpoint for REST/indexer readiness.

## Finding 2: Native SOMI gas balance can look like tradable inventory

### Claim

`SOMI:USDso` is a special market for bot authors because SOMI is both:

- the native gas token, and
- the tradable base asset of `SOMI:USDso`.

A bot that treats native SOMI exactly like an ERC-20 base balance can make bad
decisions.

### What I observed

During testing, native SOMI caused two concrete bot-integration issues:

1. A quote-only wallet with USDso plus SOMI gas could appear to already have
   base inventory, causing bootstrap logic to skip the first intended purchase.
2. Risk/inventory checks could treat reserved gas SOMI as strategy inventory.

During the mainnet flatten/stop phase, the local risk log repeatedly emitted:

```text
Inventory drift 12.50000 > cap 10.00 on SOMI:USDso
```

This was caused by native SOMI gas being visible to the inventory/risk layer.
The funds were not lost, but it is a real integration footgun: without explicit
reserve handling, a strategy could sell its own gas or pause for the wrong
reason.

### Suggested fix

Add docs guidance for native-token markets:

```text
tradable_somi = max(0, native_somi_balance - gas_reserve)
```

Recommended docs section:

```text
Native token markets: gas balance vs trading inventory
```

It should explicitly tell bot authors to:

1. Reserve native SOMI for gas.
2. Exclude that reserve from strategy inventory.
3. Treat only native balance above reserve as sellable base.
4. Use `msg.value` for native SOMI sells.
5. Use approval flow for ERC-20 quote/base assets.

## Finding 3: Simulate-before-broadcast remains necessary on mainnet

### Claim

Even when REST is working and liquidity exists, IOC orders can become invalid
between orderbook read, prepare, simulation, and broadcast. Bot examples should
treat simulation rejection as normal control flow, not an exceptional crash.

### What I observed

During the Day 1 mainnet run:

```text
Orders submitted: 91
Clean simulation rejections: 6
```

The bot did not broadcast those rejected orders. It logged and skipped them.
This is the correct behavior, but it only works if bot authors simulate prepared
transactions before sending.

### Why this matters

If a bot broadcasts every prepared transaction without simulation, it can create
unnecessary failed/no-fill transactions, waste gas, and corrupt local inventory
assumptions.

### Suggested fix

Keep the existing docs guidance prominent and add a full bot-style example:

```text
prepare order -> eth_call simulate -> if success, estimate gas -> broadcast ->
wait receipt -> verify logs/balances -> refresh inventory
```

Also show the negative path:

```text
if simulation returns false or throws, do not broadcast; refresh book and retry
later
```

## Finding 4: Market trend logging is useful for deciding when to enable maker logic

### What I added

After the trading run, I added a read-only watcher that samples REST orderbooks
and logs:

- best bid/ask
- mid price
- spread bps
- top-5 bid/ask depth
- 60s and 300s trend

Command used:

```bash
NETWORK=mainnet .venv/bin/python -m dreamdex_bot.market_watch \
  --duration-sec 600 \
  --interval-sec 10 \
  --output logs/market-watch-mainnet.jsonl
```

### 10-minute sample result

```text
SOMI:USDso
mid: 0.16385 -> 0.16370 (-9.15 bps)
avg spread: 8.25 bps
latest spread: 12.22 bps
avg top-5 depth: ~1688 USDso bid / ~1687 USDso ask

WETH:USDso
mid: 2079.54 -> 2081.15 (+7.74 bps)
avg spread: 2.05 bps
latest spread: 2.02 bps
avg top-5 depth: ~1616 USDso bid / ~1723 USDso ask

WBTC:USDso
mid: 76551.90 -> 76543.50 (-1.10 bps)
avg spread: 2.01 bps
latest spread: 2.01 bps
avg top-5 depth: ~1595 USDso bid / ~1894 USDso ask
```

### Why this matters

The bot intentionally kept YieldMaker disabled on Day 1. Based on the watcher,
SOMI had the widest spread but was choppy; WETH/WBTC were tighter but more
stable. This kind of telemetry is useful for deciding when maker quoting is
worth the risk.

## Notes for Day 2

1. Keep VolumeMill available for controlled volume generation.
2. Add more conservative market-condition gates before enabling YieldMaker.
3. Only enable maker logic with small inventory caps and explicit native SOMI
   reserve handling.
4. Continue logging REST orderbook trend/depth so strategy changes are based on
   measured conditions rather than guesses.

