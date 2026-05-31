# DreamDEX Bot Runbook

Commands assume you are in:

```sh
cd /Users/mac/Desktop/dreamdex-bot
```

## 1. Check Current Bot Status

Default macOS-safe command:

```sh
ps -fA | grep 'bots.main\|python -m bots.main' | grep -v grep
```

If this prints nothing, no trading bot is running.

If `rg` is installed, this also works:

```sh
ps -fA | rg 'bots.main|python -m bots.main|PID'
```

If only the `rg` command appears, no trading bot is running.

## 2. Stop/Pause The Bot

Find the bot PID:

```sh
ps -fA | grep 'bots.main\|python -m bots.main' | grep -v grep
```

Or, if `rg` is installed:

```sh
ps -fA | rg 'bots.main|python -m bots.main|PID'
```

Stop it gracefully:

```sh
kill -TERM <PID>
```

Example:

```sh
kill -TERM 12345
```

Then confirm it stopped:

```sh
ps -fA | grep 'bots.main\|python -m bots.main' | grep -v grep
```

If this prints nothing, the bot is stopped.

Or, if `rg` is installed:

```sh
ps -fA | rg 'bots.main|python -m bots.main|PID'
```

## 3. Watch Logs

Main bot logs:

```sh
tail -f logs/bot.jsonl
```

Errors/warnings:

```sh
tail -f logs/errors.jsonl
```

Recent submitted orders:

Default macOS-safe command:

```sh
grep 'order_submitted\|order_confirmed\|order_receipt_empty_logs\|order_simulation_rejected\|fill' logs/bot.jsonl
```

If `rg` is installed:

```sh
rg 'order_submitted|order_confirmed|order_receipt_empty_logs|order_simulation_rejected|fill' logs/bot.jsonl
```

## 4. Run Read-Only Market Watcher

This does not trade. It only samples public orderbooks.

5 minutes:

```sh
env NETWORK=mainnet .venv/bin/python -m dreamdex_bot.market_watch --duration-sec 300 --interval-sec 10 --output logs/market-watch-mainnet-live.jsonl
```

10 minutes:

```sh
env NETWORK=mainnet .venv/bin/python -m dreamdex_bot.market_watch --duration-sec 600 --interval-sec 10 --output logs/market-watch-mainnet-live.jsonl
```

WETH only:

```sh
env NETWORK=mainnet .venv/bin/python -m dreamdex_bot.market_watch --markets WETH:USDso --duration-sec 600 --interval-sec 10 --output logs/market-watch-weth.jsonl
```

Summarize watcher output:

```sh
.venv/bin/python -c "import json;from decimal import Decimal; rows=[json.loads(l) for l in open('logs/market-watch-mainnet-live.jsonl') if l.strip()];\
for m in ['SOMI:USDso','WETH:USDso','WBTC:USDso']:\n vals=[r for r in rows if r.get('market')==m and r.get('mid') is not None]; errs=[r for r in rows if r.get('market')==m and 'error' in r]; print(m,'samples',len(vals),'errors',len(errs));\n if vals:\n  mids=[Decimal(str(r['mid'])) for r in vals]; spreads=[Decimal(str(r['spread_bps'])) for r in vals if r.get('spread_bps') is not None]; print(' first',vals[0]['ts'],mids[0],'last',vals[-1]['ts'],mids[-1],'net_bps',((mids[-1]-mids[0])/mids[0]*Decimal('10000')).quantize(Decimal('0.01')),'min',min(mids),'max',max(mids),'avg_spread_bps',(sum(spreads)/len(spreads)).quantize(Decimal('0.01')) if spreads else None,'last_trend_60s',vals[-1].get('trend_60s_bps'),'last_trend_300s',vals[-1].get('trend_300s_bps'))"
```

## 5. Start Mainnet Bot

Use this only after checking config.

```sh
env NETWORK=mainnet .venv/bin/python -m bots.main --config configs/mainnet.yaml
```

Leave it running in the terminal. Stop with `Ctrl+C`, or from another terminal with `kill -TERM <PID>`.

## 6. Run Flatten Mode

Use this when you want to return close to quote-only / no WETH or WBTC exposure.

```sh
env NETWORK=mainnet .venv/bin/python -m bots.main --config configs/mainnet-flatten.yaml
```

Watch logs until balances show:

- `WETH wallet_base=0`
- `WBTC wallet_base=0`
- quote balance near your remaining USDso
- open orders `0`

Then stop it:

```sh
ps -fA | grep 'bots.main\|python -m bots.main' | grep -v grep
kill -TERM <PID>
```

## 7. Switch Back To VolumeMill

Edit `configs/mainnet.yaml`.

Set:

```yaml
strategies:
  volume_mill:
    enabled: true
```

Set:

```yaml
  yield_maker:
    enabled: false
```

Current conservative sizes should be:

```yaml
size_per_cycle_usd_by_market:
  WETH:USDso: "4.00"
  WBTC:USDso: "12.00"
```

Start:

```sh
env NETWORK=mainnet .venv/bin/python -m bots.main --config configs/mainnet.yaml
```

## 8. Switch To YieldMaker

Warning: current `YieldMaker` is hard-coded to `SOMI:USDso`. It does not make WETH. If SOMI has no bid and ask, it will probably sit idle.

Edit `configs/mainnet.yaml`.

Set:

```yaml
strategies:
  volume_mill:
    enabled: false
```

Set:

```yaml
  yield_maker:
    enabled: true
```

Start:

```sh
env NETWORK=mainnet .venv/bin/python -m bots.main --config configs/mainnet.yaml
```

Look for:

```sh
grep 'yield_maker\|order_submitted\|order_simulation_rejected\|execute_failed' logs/bot.jsonl logs/errors.jsonl
```

If `rg` is installed:

```sh
rg 'yield_maker|order_submitted|order_simulation_rejected|execute_failed' logs/bot.jsonl logs/errors.jsonl
```

If there are no `yield_maker` or `order_submitted` events, it is probably waiting for a valid two-sided SOMI book.

## 9. Check DreamDEX Public Trades

WETH trades:

```sh
curl -s 'https://api.dreamdex.io/v0/markets/WETH:USDso/trades'
```

WBTC trades:

```sh
curl -s 'https://api.dreamdex.io/v0/markets/WBTC:USDso/trades'
```

SOMI trades:

```sh
curl -s 'https://api.dreamdex.io/v0/markets/SOMI:USDso/trades'
```

Markets:

```sh
curl -s 'https://api.dreamdex.io/v0/markets'
```

Orderbooks:

```sh
curl -s 'https://api.dreamdex.io/v0/orderbooks?symbols=WETH:USDso&depth=10'
curl -s 'https://api.dreamdex.io/v0/orderbooks?symbols=WBTC:USDso&depth=10'
curl -s 'https://api.dreamdex.io/v0/orderbooks?symbols=SOMI:USDso&depth=10'
```

## 10. Recommended Operating Rules

- If you want volume, use `VolumeMill`.
- If you want lower PnL bleed, use smaller sizes or profit-aware/patient logic.
- Current `YieldMaker` is not useful for WETH unless the code is changed.
- Do not leave the bot running if `errors.jsonl` shows repeated `failed_tx_streak`.
- Do not trade SOMI aggressively unless native gas reserve is protected.
- Stop or flatten before sleeping if you do not want unmanaged exposure.

## 11. Sunday Render Worker

The Render blueprint is `render.yaml`. It starts one background worker:

```sh
python -m bots.main --config configs/mainnet.yaml
```

Create the worker from the blueprint and set these Render secrets manually:

```text
WALLET_ADDRESS
PRIVATE_KEY
```

`NETWORK=mainnet` is already set by the blueprint. Keep exactly one worker
instance running for this wallet.

The unattended mainnet profile in `configs/mainnet.yaml`:

```yaml
unattended:
  min_native_somi: "3.00"
  min_liquid_usdso: "25.00"
  max_runtime_sec: 72000
  max_submitted_orders: 2000
  drawdown_confirmations: 10
```

Behavior:

- New buys pause below the SOMI or USDso floors.
- Sells remain enabled so held WETH/WBTC can flatten.
- Runtime/order caps trigger flatten-only mode, then stop the worker.
- `SIGTERM` also requests flatten-only mode before shutdown.
- ERC-20 pool allowances use reusable max approvals to reduce gas and tx count.

Before deploying, run:

```sh
env NETWORK=mainnet .venv/bin/python -m dreamdex_bot.preflight --config configs/mainnet.yaml --expected-usdso 0
```

## 12. Useful Files

- Main config: `configs/mainnet.yaml`
- Flatten config: `configs/mainnet-flatten.yaml`
- Main bot entry: `bots/main.py`
- Market watcher: `src/dreamdex_bot/market_watch.py`
- Volume strategy: `src/dreamdex_bot/strategies/volume_mill.py`
- Yield strategy: `src/dreamdex_bot/strategies/yield_maker.py`
- Main logs: `logs/bot.jsonl`
- Error logs: `logs/errors.jsonl`

## 13. If `rg` Is Missing

Some commands online use `rg`, also called ripgrep. If your terminal says:

```sh
zsh: command not found: rg
```

use `grep` instead.

Examples:

```sh
ps -fA | grep 'bots.main\|python -m bots.main' | grep -v grep
```

```sh
grep 'order_submitted\|order_confirmed' logs/bot.jsonl
```

Optional install:

```sh
brew install ripgrep
```
