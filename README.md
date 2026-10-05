# Bybit ETHUSDT Frozen-v3 Live Bot

Standalone repository and systemd service for Bybit Linear ETHUSDT on closed 60-minute candles. This repository contains its own signal engine; it does not import from or deploy the paper bot.

## Strategy settings

| Setting | Value |
|---|---:|
| EMA fast / slow | 5 / 30 |
| RSI period and cross thresholds | 14; long 30, short 70 |
| Signal window | 5 candles |
| ATR period / initial stop | 14 / 0.5 ATR |
| Risk budget | 2.5% of current Bybit Unified equity |
| Breakout buffer | 1% |
| Trail | activate at +2R, trail by 2.25 ATR |
| Fees / slippage model | 0.055% / 0.02% per side (backtest settings; live exchange fills are actual) |

Entry uses a conditional market order with an attached exchange stop. Position quantity is rounded to Bybit's instrument step and capped by available margin. After +2R, the bot tightens the exchange stop using the 2.25 ATR trail. EMA reversal submits a reduce-only market close. The bot halts on unclear order/position state or a missed hourly candle instead of guessing.

## Before live activation

The signal/indicator functions were bundled from the local paper implementation so this repository is self-contained. The original deployed `bot_frozen_v3.py` and the historical trade CSV are not available here, so exact source parity and reproduction of the screenshot's backtest metrics have not been verified. Review and compare those before enabling orders.

The screenshot's $10,000 is a backtest starting balance. Live position risk is calculated from current Bybit Unified account equity. This is an automated trading system; test the service on a demo account first.

## VPS setup

The deployment workflow expects an Ubuntu VPS with `python3`, `rsync`, and `sudo`; SSH access for the selected deploy user; and `/home/trader` writable by that user. The deploy user must be allowed to install the systemd unit and control this one service with non-interactive sudo. Create the `trader` account if needed and make it owner of `/home/trader/bybit-ethusdt-live/data`.

1. Create `/etc/bybit/ethusdt-live.env` on the VPS using `ethusdt-live.env.example` as a template. Set owner `root:trader`, mode `0640`. Keep `LIVE_TRADING_ENABLED=NO` initially. The actual Bybit key and secret belong only in this VPS file.
2. Use a Bybit HMAC key limited to read and derivatives trading, restrict it to the VPS IP, and disable withdrawals/transfers.
3. Add GitHub Actions repository secrets: `VPS_HOST`, `VPS_USER`, `VPS_SSH_KEY` (private deploy key), and `VPS_KNOWN_HOSTS` (pinned host key line). Do not add Bybit keys to GitHub.
4. Push to `main` or run **Test and deploy live bot** manually. The workflow runs unit tests and deploys this repository alone. It installs the service but leaves it stopped unless the VPS env file contains the exact line `LIVE_TRADING_ENABLED=YES`.
5. After verifying code, configuration, account mode (one-way), exchange key permissions, and demo behavior, change the VPS flag deliberately and run the deployment workflow again. Inspect with `sudo systemctl status bybit-ethusdt-live` and `sudo journalctl -u bybit-ethusdt-live -f`.

The workflow's SSH deploy user needs non-interactive sudo for the systemd install/reload/service commands. Restrict that sudo policy to the required commands on the VPS.

## Local checks

```sh
python3 -m unittest discover -s tests -v
python3 live_bot.py --once --config live_config.json
```

`--once` still requires `LIVE_TRADING_ENABLED=YES` because it uses the live executor. Do not use it as a dry run. There is no order-placement dry-run flag in this live service.
