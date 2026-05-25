# REST orderbook snapshots can lag on-chain `getBookLevels` by a few ticks

## Doc URLs

- https://docs.dreamdex.io/uK9H3quGFeuU9dyKOiCH
- https://docs.dreamdex.io/ld25g222WKDrLlJMcR41/trading/readme-1/contract-specifications#testnet-somnia-shannon-chain-id-50312

## What docs imply

The Market Data docs describe `GET /v0/orderbooks` as returning aggregated
order book data for one or more market symbols. They say the response includes
open bids and asks sorted by price, quantities at each price level, and a Unix
timestamp in milliseconds.

The Contract Specifications page lists the testnet pool addresses and says raw
on-chain values are stored in token units. It does not appear to document
`getBookLevels`, its exact signature, or the expected freshness relationship
between REST orderbook snapshots and on-chain pool book levels.

For IOC/taker bots, the exact best bid/ask still matters because an order
prepared against a stale touch may no longer cross when simulated or submitted.

## What actually happens

In testnet sampling on May 25, 2026, REST `/v0/orderbooks` and on-chain
`getBookLevels` usually matched, but sometimes disagreed at top of book within a
2.3-3.4 second REST-to-chain sampling window.

This was not a persistent "REST empty while chain has liquidity" failure in this
run. It looked like short-lived snapshot/indexer drift during moving books:
prices differed by a small number of ticks, and the direction of the difference
was not consistent.

## How we confirmed

I added a read-only probe to the bot:

```bash
.venv/bin/python -m dreamdex_bot.probes.run --probe book_source_compare --market SOMI:USDso
.venv/bin/python -m dreamdex_bot.probes.run --probe book_source_compare --market WETH:USDso
.venv/bin/python -m dreamdex_bot.probes.run --probe book_source_compare --market WBTC:USDso
```

The probe:

1. Calls REST `/v0/orderbooks?symbols={market}&depth=5`.
2. Immediately calls the pool contract with `getBookLevels(bool,uint64)` for bids and asks.
3. Decodes the result as `(uint128 priceRaw, uint128 quantityRaw)[]`.
4. Converts raw units back to decimal price/quantity.
5. Logs both snapshots and timestamps to `logs/probes.jsonl`.

Observed latest run:

- `SOMI:USDso`: 1 mismatch / 5 samples
- `WETH:USDso`: 2 mismatches / 5 samples
- `WBTC:USDso`: 2 mismatches / 5 samples

Testnet pools:

- `SOMI:USDso`: `0x259fD6559214dd5aD3752322426eA9F9fABEFff4`
- `WETH:USDso`: `0xD180195da5459C7a0DEA188ed61216ec43682b50`
- `WBTC:USDso`: `0x3605f28aA7C50e7441211e77Cb0762d49539326C`

The discovered book call selector was:

```text
getBookLevels(bool,uint64) -> 0x4f1ce9a7
```

## Evidence samples

`SOMI:USDso`, sample 0, 2.543s REST-to-chain window:

```text
REST bid:  0.1709 x 478.5
Chain bid: 0.1710 x 478

REST ask:  0.1710 x 521.5
Chain ask: 0.1713 x 522
```

`WETH:USDso`, sample 3, 2.767s REST-to-chain window:

```text
REST bid:  2117.99 x 0.063
Chain bid: 2117.85 x 0.063

REST ask:  2118.43 x 0.037
Chain ask: 2118.05 x 0.037
```

`WBTC:USDso`, sample 1, 3.009s REST-to-chain window:

```text
REST bid:  77425.0 x 0.00187
Chain bid: 77423.9 x 0.00187

REST ask:  77440.6 x 0.00092
Chain ask: 77439.5 x 0.00092
```

Full raw evidence:

```text
logs/probes.jsonl
probe="book_source_compare_sample"
probe="book_source_compare"
```

## Impact

For UI display this may be acceptable. For automated IOC trading it is material:
the bot may prepare an order against a REST touch that has already moved on
chain. The subsequent `eth_call` correctly returns `(false, 0)`, so a safe bot
does not broadcast, but throughput falls.

This likely explains part of the supervised testnet session where the bot saw:

- 78 order submissions
- 18 clean `order_simulation_rejected` events
- 12 `order_simulation_failed` events

## Suggested fix

1. Document expected freshness for REST `/v0/orderbooks` versus pool
   `getBookLevels`.
2. Clarify the existing REST `timestamp` field's semantics: whether it is API
   response time, indexer update time, matching-engine event time, or source
   block time.
3. Add bot-author guidance showing how to use that timestamp to detect stale
   snapshots before preparing IOC orders.
4. Document the pool-level book read method, including the observed
   `getBookLevels(bool,uint64)` signature and raw-unit scaling for returned
   price and quantity, if this method is intended for bot authors.
5. For bot examples, recommend `eth_call` simulation before broadcast and a
   configurable IOC cross buffer to absorb small book drift.

## Bot-side handling

I am not switching the bot to chain-book reads for Day 1. Chain reads are more
authoritative but slower, and the measured issue is short-lived drift rather
than persistent REST emptiness. The bot already defends by simulating prepared
orders before broadcast and refusing to send when the contract returns
`success=false`.
