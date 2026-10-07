# Bybit ETHUSDT Strategy Lab V6.1 Live Bot

Standalone repository and systemd service for real Bybit Linear ETHUSDT orders on closed 60-minute candles. Long and short trades are enabled. The strategy core is self-contained and mirrors the local V6.1 paper bot. Its mainnet state is stored separately in `data/live_mainnet.sqlite3`.

## Strategy settings

| Setting | Value |
|---|---:|
| EMA fast / slow | 5 / 30 |
| RSI period and long / short thresholds | 14; 30 / 70 |
| Signal window | 5 candles |
| ATR period / initial stop | 14 / 0.5 ATR |
| Risk budget | 2.5% of current Bybit Unified equity |
| Breakout buffer | 1% |
| Trail | activate at +2R, trail by 2.25 SMA-ATR |
| Fee / slippage sizing estimate | 0.055% / 0.02% per side |
| Directions | LONG and SHORT enabled |

EMA 5/30 and matching SMA-RSI(14) threshold crossovers can happen in either order within five candles; the second cross creates a setup. Only the next candle may confirm it by breaking the signal candle high/low. After confirmation closes, a buffered conditional market order is placed at the confirmation candle high/low, with an exchange stop attached at 0.5 SMA-ATR(14) from the trigger. Opposite signals cancel unfilled bot entries. One-way mode and one position at a time are required.

Quantity uses 2.5% of current Unified equity as the risk budget, including estimated entry/stop fees and slippage, then rounds down to Bybit's quantity step and caps notional to 90% of available margin at configured leverage. Exchange fills, fees and slippage will differ from the replay assumptions. The $10,000 backtest start is not used for live sizing. After price reaches +2R, the stop is tightened using the closed candle and 2.25 SMA-ATR. Stop orders are managed at the exchange; EMA reversal submits a reduce-only market close as soon as the closed-candle reversal is detected (typically shortly after the next candle opens, with polling/fill deviation). The bot halts on unclear order/position state or a missed hourly candle instead of guessing.

The signal and indicator rules mirror the local V6.1 paper implementation, including first-close EMA seed, SMA-RSI, SMA-ATR, crossover order/window, next-candle confirmation, signal-candle ATR stop, opposite-signal cancellation and close-based ATR trail. The replay still differs from the Strategy Lab screenshot, so exact parity with the website's full implementation is not established. Compare exported Strategy Lab trades before treating replay performance as reproduced.

## Credentials and activation

Create a Bybit HMAC API key for read and derivatives trading access, restrict it to the VPS IP, and leave withdrawal/transfer permissions disabled. Store it only in `/etc/bybit/ethusdt-live.env`, owned by `root:trader` with mode `0640`. Never commit credentials or put the Bybit key in GitHub Actions. The example environment file defaults `LIVE_TRADING_ENABLED=NO`.

The live executor exits unless the protected VPS environment has the exact setting `LIVE_TRADING_ENABLED=YES`. Keep it `NO` while installing, reviewing, and testing. Starting balance $10,000 is only for historical replay; live sizing uses current account equity.

## Forced entry

`--force-entry LONG` or `--force-entry SHORT` bypasses signal and next-candle confirmation, then submits a market entry immediately with a stop attached at 0.5 of the latest closed H1 SMA-ATR. Quantity uses the configured risk percentage of current Unified equity, includes configured fee/slippage estimates, rounds down to Bybit's lot step, and observes the same available-margin cap as strategy entries. The resulting open position is recorded in the same SQLite state and then managed by the usual EMA exit and ATR trail. The command refuses existing ETHUSDT positions/orders or nonempty local bot state, requires an exact interactive confirmation, and refuses to run while the systemd strategy service is active. SQLite events record the sizing snapshot, order request/response, confirmed fill, stop, trail/exit events and when the position is no longer open; consult Bybit order/execution history for final fill and PnL details.

Stop the service before invoking a forced entry, then load the protected environment in the current shell and run the command from the repository directory. After the command confirms the position and stop, restart the strategy service so it can manage the position:

```sh
set -e
sudo systemctl stop bybit-ethusdt-live.service
cd /home/trader/bybit-ethusdt-live
set -a
. /etc/bybit/ethusdt-live.env
set +a
python3 live_bot.py --config /home/trader/bybit-ethusdt-live/live_config.json --force-entry SHORT
sudo systemctl start bybit-ethusdt-live.service
```

Replace `SHORT` with `LONG` if desired. Do not restart the service if the command reports an unclear submission or no confirmed position; inspect Bybit first. View the persistent SQLite event history with:

```sh
python3 live_bot.py --config /home/trader/bybit-ethusdt-live/live_config.json --history
```

## VPS deployment

The GitHub Actions workflow deploys this separate repository to `/home/trader/bybit-ethusdt-live`. It expects Ubuntu with `python3`, `rsync`, and `sudo`; the deploy user needs narrowly scoped non-interactive sudo for installing/reloading the systemd unit and managing this service.

1. On the VPS, create `/etc/bybit/ethusdt-live.env` from `ethusdt-live.env.example`, set `LIVE_TRADING_ENABLED=NO`, and install with owner `root:trader`, mode `0640`.
2. Add GitHub Actions secrets `VPS_HOST`, `VPS_USER`, `VPS_SSH_KEY`, and `VPS_KNOWN_HOSTS`. These are deployment SSH details only.
3. Push this repository to `main` or run **Test and deploy live bot** manually. The workflow deploys the code and leaves the unit stopped unless the VPS flag is already `YES`.
4. The current code defaults to Bybit mainnet. For a Demo Trading shakeout, set `BYBIT_API_URL=https://api-demo.bybit.com` and use a separate Demo API key. Bybit documents this endpoint for its isolated Demo Trading account. Do not use a mainnet key for that test. The executor remains stopped unless `LIVE_TRADING_ENABLED=YES`.
5. Inspect using `sudo systemctl status bybit-ethusdt-live` and `sudo journalctl -u bybit-ethusdt-live -f`.

## Local checks

```sh
python3 -m unittest discover -s tests -v
python3 -m py_compile strategy_core.py live_bot.py
```

Tests use mocked exchange calls and do not submit orders. `live_bot.py --once` is not a dry run; the live executor remains disabled unless the explicit environment flag is set.
