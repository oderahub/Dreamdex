# DreamDEX Alpha Competition - Consolidated Integration Feedback

## Scope

This report consolidates feedback gathered while building and running an automated DreamDEX trading bot across Somnia testnet and mainnet during May 2026.

The integration used:

- DreamDEX REST market, orderbook, authentication, balance, and order endpoints.
- The public WebSocket endpoint for book updates.
- Direct contract reads and transaction receipts.
- Local `eth_estimateGas` before broadcast.
- Wallet-funded IOC orders.
- A read-only market watcher and an unattended mainnet worker.

Mainnet wallet used for the final session:

```text
0x4258950186a12492Bf805f2B9D7facd202921F34
```

The observations below separate DreamDEX product and documentation feedback from operational lessons learned while hardening the bot.

## Executive Summary

| Priority | Area | Feedback |
| --- | --- | --- |
| P0 | Order success semantics | A transaction receipt with `status=1` is not sufficient evidence that an IOC order filled. Document the required receipt-log and balance checks. |
| P0 | Preflight simulation | Encourage simulation and local gas estimation immediately before broadcast. This caught real failures on both testnet and mainnet. |
| P1 | Gas guidance | Prepared transaction gas hints were not sufficient for every approval and order path. Document local estimation with a safety buffer. |
| P1 | Orderbook freshness | REST orderbooks can lag direct contract book reads by a few ticks. Document freshness expectations and expose timestamps consistently. |
| P1 | WebSocket lifecycle | Own-order lifecycle tracking requires an `orderId` subscription. Document the discovery and subscription flow clearly. |
| P1 | Native SOMI handling | SOMI is both gas token and tradable inventory. Add explicit reserve guidance for bots. |
| P1 | Market metadata | Expose tick size, lot size, and minimum notional clearly. Small WETH orders can repeatedly skip as below minimum quantity. |
| P1 | Availability | REST-prepared bots depend on the API. Add a lightweight status endpoint and document fallback behavior. |
| P2 | Account balances | Document wallet balances alongside vault balances for wallet-funded bots. |
| P2 | Leaderboard PnL | Publish the exact PnL formula, including treatment of open inventory and gas. |
| P2 | Bot guidance | Add a bot-author guide covering quote floors, native gas floors, allowance reuse, graceful shutdown, and process-manager restarts. |

## Validated Product And Documentation Feedback

### 1. IOC receipt status does not prove a fill

#### Observation

On testnet, IOC order transactions could complete successfully at the chain level while producing no fill. The no-fill transactions had:

- `status=1`
- Empty logs
- No placed or filled order
- No inventory change
- Gas charged
- Refunded `msg.value`

Representative transactions:

```text
Filled control:
0xf6cad0fea122fbc3c052f9f1d836b8b988c7514a3ef56581f0966c283f0ab1f6

No-fill examples:
0xd4a0deeec2600a65d13d5c88b636b7bd124d71972c7787d1e3f59b626656b211
0x0e07d732d074335a70f35f78c4b269f6934f84ed57599e487905c0866cc7fa2f
```

#### Impact

A bot that treats `status=1` as a fill can drift out of sync with actual balances and submit the wrong next action.

#### Recommendation

Document that integrations should verify fills using receipt logs and post-transaction balances. If possible, return a normalized fill result from the API or expose an order lookup that makes the distinction explicit.

### 2. Simulate and estimate immediately before broadcast

#### Observation

Prepared transaction payloads were useful, but local `eth_estimateGas` remained necessary. It caught failures before spending gas and allowed a consistent safety buffer.

Representative approval estimates from testnet:

```text
SOMI:USDso approval  estimated=1,391,074  buffered=1,738,842
WETH:USDso approval  estimated=1,091,074  buffered=1,363,842
WBTC:USDso approval  estimated=1,391,074  buffered=1,738,842
```

Mainnet order broadcasts commonly used buffered gas limits around:

```text
2,005,108 to 2,122,933
```

During the first mainnet day, the bot submitted 91 orders and rejected 6 cleanly during preflight simulation.

#### Impact

Bots that broadcast prepared calls without simulation can pay for avoidable reverts or fail unexpectedly when state changes.

#### Recommendation

Document a standard broadcast flow:

1. Prepare the order.
2. Simulate or estimate against the latest state.
3. Add a documented gas buffer.
4. Broadcast.
5. Verify receipt logs and balances.

### 3. REST orderbooks can lag direct contract reads

#### Observation

Short testnet samples showed temporary mismatches between REST orderbooks and direct `getBookLevels` reads over 2.3 to 3.4 second windows.

```text
SOMI:USDso  1 mismatch / 5 samples
WETH:USDso  2 mismatches / 5 samples
WBTC:USDso  2 mismatches / 5 samples
```

The direct book method used during investigation was:

```text
getBookLevels(bool,uint64) -> 0x4f1ce9a7
```

#### Impact

A taker bot using stale REST levels may choose an invalid or less favorable price. A maker bot may quote against a book that has already moved.

#### Recommendation

Document:

- REST orderbook timestamp semantics.
- Expected relationship between REST and chain state.
- The direct contract book-read method.
- The need to simulate against current state before broadcast.

### 4. WebSocket own-order tracking needs clearer documentation

#### Observation

The public WebSocket book stream was useful. Tracking a specific order requires a per-order subscription:

```json
{"operation":"subscribe","channel":"order","params":{"orderId":"..."}}
```

#### Impact

Integrators can assume there is an account-wide orders or fills stream and miss lifecycle events.

#### Recommendation

Document the complete lifecycle:

1. Submit an order.
2. Extract the returned order identifier.
3. Subscribe to the `order` channel with `orderId`.
4. Reconcile with receipt logs and balances.

An account-wide authenticated orders or fills channel would simplify bot integrations.

### 5. Native SOMI needs explicit gas-versus-inventory guidance

#### Observation

SOMI is both:

- The native gas token.
- Tradable base inventory in `SOMI:USDso`.

An unattended bot can accidentally trade away the token required to continue operating.

#### Impact

The bot may stop submitting transactions even while it still has quote inventory.

#### Recommendation

Add a native-token integration note with:

- A minimum gas reserve recommendation.
- Wallet balance examples.
- Guidance for excluding reserved native SOMI from sellable inventory.
- A warning for SOMI markets.

### 6. Expose minimum order metadata clearly

#### Observation

Small WETH targets produced repeated skips:

```text
volume_mill.skip market=WETH:USDso min_qty=0.001 reason=buy_qty_too_small target_usd=2.00
```

#### Impact

A bot may repeatedly evaluate an order that cannot satisfy the market minimum, generating noisy logs and no volume.

#### Recommendation

Expose and document:

- Tick size.
- Quantity step.
- Minimum quantity.
- Minimum notional.
- Rounding rules.

Return field-specific validation errors where possible.

### 7. API availability is a hard dependency for REST-prepared bots

#### Observation

The integration relied on REST for market discovery, orderbooks, authentication, balances, and prepared order payloads.

#### Impact

A REST outage prevents a REST-prepared bot from operating even if the chain remains available.

#### Recommendation

Add:

- A lightweight API health endpoint.
- Status-page guidance.
- Retry and backoff recommendations.
- Direct-contract fallback documentation for critical reads and order submission.

### 8. Document wallet balances alongside vault balances

#### Observation

The bot was wallet funded. Vault balances alone did not represent spendable wallet inventory, so direct wallet reads were also required.

#### Recommendation

Document the intended relationship between:

- Wallet ERC-20 balances.
- Native SOMI balance.
- Vault balances.
- Spendable balances for prepared orders.

If practical, expose a normalized account balances endpoint.

### 9. SIWE domain selection should be explicit

#### Observation

Authentication depends on the SIWE domain matching the API host. Staging and production hosts require the corresponding domain.

#### Recommendation

Include environment-specific SIWE examples and call out the domain requirement directly.

### 10. Publish the leaderboard PnL formula

#### Observation

During live runs, liquid `USDso`, open WETH inventory, native SOMI gas spend, and leaderboard PnL could move independently.

#### Recommendation

Document whether leaderboard PnL includes:

- Marked-to-market open inventory.
- Realized PnL only.
- Native SOMI gas spend.
- Vault balances and wallet balances.
- The pricing source and refresh cadence.

## Market Structure Observations

Liquidity conditions differed materially by pair. This affected which strategies were practical.

### Final-session watcher summary

| Market | Observed condition | Practical implication |
| --- | --- | --- |
| `WETH:USDso` | Usually about 2.05 bps spread with roughly $1.58k top-five bid depth | Most suitable pair for controlled IOC testing |
| `WBTC:USDso` | Often tight when active, but less consistently usable with the available quote reserve | Suitable only when inventory and quote-floor checks pass |
| `SOMI:USDso` | Wider, often around 6.5 to 13 bps, with native-gas inventory coupling | Requires stronger reserve handling and more selective entries |

These conditions are not protocol defects. Documentation for bot authors should recommend rejecting:

- Empty bids or asks.
- Spreads above a configured threshold.
- Insufficient side depth.
- Orders that consume too much visible depth.
- Entries without an external fair-price check when running maker logic.

## Mainnet Operating Findings

### Day 1 summary

```text
Rank:                  1
Transactions:          290
Volume:                930.47 USDso
PnL:                   -0.22 USDso
After flatten USDso:   49.778875
After flatten WETH:    0
After flatten WBTC:    0
Native SOMI:           9.992409452
```

### Final WETH-only session

The final controlled configuration ran `WETH:USDso` only, with a `$9` target per side and a `$25` projected liquid `USDso` floor.

Representative cycle:

```text
Before buy:
USDso=36.141748  WETH=0

After buy:
USDso=27.285076  WETH=0.0044

After sell:
USDso=36.136864  WETH=0
```

Approximate cycle result:

```text
Quote loss:             0.004884 USDso
Native gas spent:       0.005209740 SOMI
Temporary WETH holding: 0.0044 WETH
Minimum quote headroom: 2.285076 USDso above the $25 floor
```

This behavior is expected for an IOC volume strategy: it buys from the best ask and later sells into the best bid. It normally pays spread, possible slippage, and native SOMI gas. It should not be presented as a profit strategy.

At a one-minute transaction cadence, the `$9` configuration generated approximately:

```text
17.7 USDso volume per completed buy/sell cycle
530 USDso volume per hour before skips
0.16 SOMI gas per hour at the observed gas rate
```

Actual throughput depends on spread checks, depth, minimum order rules, API availability, and chain conditions.

## Bot Safeguards Implemented

The following are bot-side controls, not protocol fixes. They may be useful as reference patterns in a bot-author guide.

### Projected quote-floor enforcement

The bot checks the post-buy quote balance, not only the current balance:

```python
def _buy_block_reason(self, projected_quote_spend: Decimal = Decimal(0)) -> str | None:
    if self._safe_exit_requested:
        return self._safe_exit_reason or "safe_exit_requested"
    if self._drawdown_pending:
        return "drawdown_pending_confirmation"

    projected_quote = wallet_quote - projected_quote_spend
    if quote_floor > 0 and projected_quote < quote_floor:
        return (
            f"projected_liquid_usdso_below_floor:"
            f"{wallet_quote}-{projected_quote_spend}={projected_quote}<{quote_floor}"
        )
```

This prevented a buy from passing the reserve check and then dropping liquid `USDso` below the configured floor.

### Confirmed drawdown handling

The worker requires repeated drawdown confirmations before firing its kill switch and latches the handled event:

```python
def _confirm_drawdown_events(self, events: list[RiskEvent]) -> list[RiskEvent]:
    drawdown_events = [ev for ev in events if ev.rule_name == "max_drawdown"]
    if self._max_drawdown_handled:
        return [ev for ev in events if ev.rule_name != "max_drawdown"]
```

When confirmed, it flattens inventory and stays alive in a paused state:

```python
if ev.rule_name == "max_drawdown":
    self._max_drawdown_handled = True
    self._request_safe_exit("max_drawdown", stop_when_flat=False)
```

This matters under managed deployment platforms. Exiting after a drawdown can trigger an automatic restart and allow new trades before the condition is reconfirmed.

### Allowance reuse

The worker reuses sufficient on-chain allowances and supports a maximum-allowance mode:

```python
allowance = await self._wallet_allowance(token, spender)
if allowance >= required_amount:
    self._submitted_approvals[approval_key] = allowance

if self.approval_config.get("mode", "exact") == "max":
    approve_amount = Decimal(2**256 - 1)
```

This removed repeated approval transactions from the normal cycle.

### Graceful deployment shutdown

The worker requests a safe exit when it receives a shutdown signal:

```python
async def shutdown_watcher():
    await stop_evt.wait()
    log.warning("bot.shutdown_requested")
    engine.request_safe_exit("signal_received")
```

This allowed deployment restarts to sell temporary WETH inventory before process exit.

### Representative unattended configuration

```yaml
wallet_approvals:
  mode: max

unattended:
  min_native_somi: "3.00"
  min_liquid_usdso: "25.00"
  drawdown_confirmations: 10
```

## Profit-Oriented Strategy Guidance

The IOC volume loop used for the competition is intentionally simple and gas intensive. A profit-oriented strategy would normally differ:

1. Place resting bids below fair value and resting asks above fair value.
2. Earn spread when other participants trade against those orders.
3. Continuously cancel or reprice stale quotes.
4. Limit inventory imbalance and adverse selection.
5. Include gas, fees, and expected fill probability in every decision.
6. Use an external fair-price source where appropriate.

DreamDEX documentation would benefit from a short section distinguishing:

- Taker IOC volume generation.
- Maker quoting.
- Inventory risk.
- Gas economics.
- Cancel and replace behavior.

## Prioritized Recommendations

### P0

1. Document that receipt `status=1` does not prove an IOC fill.
2. Recommend simulation, local gas estimation, buffered broadcast, log checks, and balance reconciliation.

### P1

1. Document REST freshness expectations and direct book reads.
2. Document per-order WebSocket subscription flow.
3. Add native SOMI reserve guidance.
4. Expose minimum quantity, notional, tick, and rounding metadata clearly.
5. Add a REST health endpoint and fallback guidance.

### P2

1. Add a normalized account balances endpoint or clearer wallet-versus-vault examples.
2. Add an authenticated account-wide orders or fills stream.
3. Publish the leaderboard PnL calculation.
4. Add an unattended bot-author guide with reserve, allowance, shutdown, and restart patterns.
5. Add a maker-strategy guide with fair-price and inventory-risk guidance.

## Source Reports

This file consolidates:

```text
reports/submission-ready-feedback.md
reports/day-1-mainnet-feedback.md
reports/day-3-report.md
reports/rest-chain-orderbook-staleness.md
reports/testnet-day-1.md
reports/draft.md
```

## References

```text
DreamDEX docs:
https://docs.dreamdex.io/uK9H3quGFeuU9dyKOiCH

Contract specifications:
https://docs.dreamdex.io/ld25g222WKDrLlJMcR41/trading/readme-1/contract-specifications#testnet-somnia-shannon-chain-id-50312
```
