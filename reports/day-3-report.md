# Thin / One-Sided Market Conditions Affect Bot Behavior During Alpha
  Competition

  ## Context

  Testing performed on DreamDEX mainnet during the alpha trading
  competition using an automated bot connected through REST, WebSocket, and
  prepared transaction flows.

  Because the alpha cohort has only a small number of active traders,
  liquidity appears to be mostly participant-provided. In practice, this
  means traders are both creating the available orderbook liquidity and
  consuming it through swaps/trades. When only a few bots are active, the
  book can quickly become one-sided, thin, or temporarily empty.

  This is understandable for an alpha competition, but it materially
  affects bot behavior and should be documented clearly for bot authors.

  ## What I Observed

  During live trading, market quality differed significantly across pairs:

  - `WETH:USDso` was the most consistently tradeable market.
  - `WBTC:USDso` was sometimes tradeable, but liquidity was intermittent.
  - `SOMI:USDso` was often one-sided, with only asks or no usable bid side.
  - Maker-style logic such as `YieldMaker` could not operate effectively
  when the target market had no valid two-sided book.
  - Taker-style IOC logic worked better, but still needed strong checks for
  both bid and ask depth before preparing orders.

  Example bot behavior:

  - When `SOMI:USDso` had no bid, the bot could not calculate a valid mid
  price.
  - Without a valid mid price, maker quoting logic could not place sensible
  bid/ask quotes.
  - When `WBTC:USDso` liquidity was thin or absent, the bot skipped the
  market or hit simulation rejection.
  - The most reliable volume generation came from cycling small trades on
  `WETH:USDso` and sometimes `WBTC:USDso`.

  ## Why This Matters

  A new bot author may assume all listed markets are continuously two-sided
  and tradeable. In the alpha environment, that assumption is not always
  true.

  This affects several bot strategies:

  - Market makers need a valid bid and ask to calculate mid price and quote
  safely.
  - IOC/taker bots need enough opposite-side depth before preparing orders.
  - Grid bots need a stable two-sided book.
  - Inventory-management bots may get stuck holding base inventory if the
  exit side disappears.
  - Volume bots may unintentionally concentrate on only the most liquid
  pair.

  This is not necessarily a protocol bug. It is a market-condition and
  documentation issue caused by limited active participants and
  participant-provided liquidity.

  ## Suggested Documentation Improvement

  Add a short “Alpha Market Conditions / Bot Author Guidance” section to
  the docs.

  Suggested points:

  1. During alpha or low-liquidity periods, listed markets may be thin,
  one-sided, or temporarily empty.
  3. IOC bots should check available opposite-side depth before preparing
  orders.
  4. Maker bots should avoid quoting if the reference book is one-sided
  unless they have an external fair-price source.
  5. Bots should handle these cases explicitly:
     - no bids
     - no asks
     - spread too wide
     - depth below minimum order size
     - simulation succeeds but broadcast does not fill due to book movement
  6. Docs should clarify which markets are expected to have reliable
  liquidity during competitions or testing windows.

  ## Suggested Bot-Side Mitigation

  Bot authors should implement liquidity gates before order preparation:

  ```ts
  if (!bestBid || !bestAsk) {
    skip("one_sided_or_empty_book");
  }

  const mid = (bestBid + bestAsk) / 2;
  const spreadBps = ((bestAsk - bestBid) / mid) * 10_000;

  if (spreadBps > maxSpreadBps) {
    skip("spread_too_wide");
  }

  if (oppositeSideDepthUsd < minDepthUsd) {
    skip("insufficient_depth");
  }

  For maker strategies:

  if (!bestBid || !bestAsk) {
    // Do not quote from this market alone.
    // Either skip, or use an external oracle/reference price.
    return;
  }

  ## Impact

  This would help bot authors avoid confusing failures and reduce
  unnecessary support/debugging during alpha testing.

  It would also make it clearer that some “bot is not trading” situations
  are caused by market conditions, not necessarily API failures.

  ## Summary

  The alpha markets are functioning, but because liquidity is mostly
  provided by a small group of participant bots, books can become thin or
  one-sided. This directly affects strategy design. Clear documentation
  around low-liquidity market handling would make DreamDEX easier for bot
  authors to integrate with and would reduce false bug reports during
  future testing rounds.
