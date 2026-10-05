"""Standalone 60m signal engine bundled with the live repository."""
from __future__ import annotations
import json
import logging
import sqlite3
from datetime import datetime, timezone
import time
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any

API = "https://api.bybit.com/v5/market/kline"
INTERVAL_MS = 60 * 60 * 1000


@dataclass
class Candle:
    start_ms: int
    open: float
    high: float
    low: float
    close: float
    volume: float


@dataclass
class Config:
    symbol: str = "ETHUSDT"
    category: str = "linear"
    interval: str = "60"
    poll_seconds: int = 30
    ema_fast: int = 5
    ema_slow: int = 30
    rsi_period: int = 14
    rsi_long_cross: float = 30.0
    rsi_short_cross: float = 70.0
    signal_window_bars: int = 5
    atr_period: int = 14
    atr_stop_multiple: float = 0.5
    risk_per_trade_pct: float = 2.5
    breakout_buffer_pct: float = 1.0
    fee_per_side_pct: float = 0.055
    slippage_per_side_pct: float = 0.02
    starting_balance_usd: float = 10000.0
    trail_activation_r: float = 2.0
    trail_atr_multiple: float = 2.25
    allow_long: bool = True
    allow_short: bool = True
    database_path: str = "data/live.sqlite3"

    @classmethod
    def load(cls, path: str) -> "Config":
        raw = json.loads(Path(path).read_text())
        cfg = cls(**raw)
        if cfg.symbol != "ETHUSDT" or cfg.category != "linear" or cfg.interval != "60":
            raise ValueError("This live bot is pinned to ETHUSDT Linear 60m.")
        if cfg.ema_fast >= cfg.ema_slow or cfg.risk_per_trade_pct <= 0:
            raise ValueError("Invalid EMA or risk settings.")
        return cfg


def get_closed_candles(cfg: Config, limit: int = 1000) -> list[Candle]:
    qs = urllib.parse.urlencode({"category": cfg.category, "symbol": cfg.symbol,
                                 "interval": cfg.interval, "limit": limit})
    req = urllib.request.Request(f"{API}?{qs}", headers={"User-Agent": "bybit-ethusdt-live-bot/1.0"})
    with urllib.request.urlopen(req, timeout=15) as response:
        body = json.loads(response.read())
    if body.get("retCode") != 0:
        raise RuntimeError(f"Bybit API error: {body.get('retCode')} {body.get('retMsg')}")
    now_ms = int(time.time() * 1000)
    rows = body["result"]["list"]
    candles = [Candle(int(r[0]), float(r[1]), float(r[2]), float(r[3]), float(r[4]), float(r[5]))
                for r in rows if int(r[0]) + INTERVAL_MS <= now_ms]
    candles.sort(key=lambda c: c.start_ms)
    unique = {c.start_ms: c for c in candles}
    return list(unique.values())


def ema(values: list[float], period: int) -> list[float | None]:
    result: list[float | None] = [None] * len(values)
    if len(values) < period:
        return result
    alpha = 2.0 / (period + 1)
    current = sum(values[:period]) / period
    result[period - 1] = current
    for i in range(period, len(values)):
        current = alpha * values[i] + (1 - alpha) * current
        result[i] = current
    return result


def sma_rsi(values: list[float], period: int) -> list[float | None]:
    out: list[float | None] = [None] * len(values)
    changes = [values[i] - values[i - 1] for i in range(1, len(values))]
    for i in range(period, len(values)):
        w = changes[i - period:i]
        gains = sum(max(x, 0.0) for x in w) / period
        losses = sum(max(-x, 0.0) for x in w) / period
        out[i] = 100.0 if losses == 0 else 100.0 - 100.0 / (1.0 + gains / losses)
    return out


def atr_wilder(candles: list[Candle], period: int) -> list[float | None]:
    out: list[float | None] = [None] * len(candles)
    tr: list[float] = []
    for i, c in enumerate(candles):
        tr.append(c.high - c.low if i == 0 else max(c.high - c.low, abs(c.high - candles[i-1].close), abs(c.low - candles[i-1].close)))
    if len(tr) < period:
        return out
    value = sum(tr[:period]) / period
    out[period - 1] = value
    for i in range(period, len(tr)):
        value = ((period - 1) * value + tr[i]) / period
        out[i] = value
    return out


def indicators(candles: list[Candle], cfg: Config) -> dict[str, list[float | None]]:
    closes = [c.close for c in candles]
    return {"ema_fast": ema(closes, cfg.ema_fast), "ema_slow": ema(closes, cfg.ema_slow),
            "rsi": sma_rsi(closes, cfg.rsi_period), "atr": atr_wilder(candles, cfg.atr_period)}



@dataclass
class Config:
    symbol: str = "ETHUSDT"
    category: str = "linear"
    interval: str = "60"
    poll_seconds: int = 30
    ema_fast: int = 5
    ema_slow: int = 30
    rsi_period: int = 14
    rsi_long_cross: float = 30.0
    rsi_short_cross: float = 70.0
    signal_window_bars: int = 5
    atr_period: int = 14
    atr_stop_multiple: float = 0.5
    risk_per_trade_pct: float = 2.5
    breakout_buffer_pct: float = 1.0
    fee_per_side_pct: float = 0.055
    slippage_per_side_pct: float = 0.02
    starting_balance_usd: float = 10000.0
    trail_activation_r: float = 2.0
    trail_atr_multiple: float = 2.25
    allow_long: bool = True
    allow_short: bool = True
    database_path: str = "data/live.sqlite3"

    @classmethod
    def load(cls, path: str) -> "Config":
        cfg = cls(**json.loads(Path(path).read_text()))
        if (cfg.symbol, cfg.category, cfg.interval) != ("ETHUSDT", "linear", "60"):
            raise ValueError("This live bot is pinned to ETHUSDT Linear 60m.")
        if cfg.ema_fast >= cfg.ema_slow or cfg.risk_per_trade_pct <= 0:
            raise ValueError("Invalid EMA or risk settings.")
        if cfg.trail_activation_r <= 0 or cfg.trail_atr_multiple <= 0:
            raise ValueError("Trail activation and ATR multiple must be positive.")
        return cfg


def init_db(path: str, starting_balance: float) -> sqlite3.Connection:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(path)
    db.row_factory = sqlite3.Row
    db.executescript("""
      CREATE TABLE IF NOT EXISTS state (key TEXT PRIMARY KEY, value TEXT NOT NULL);
      CREATE TABLE IF NOT EXISTS events (id INTEGER PRIMARY KEY AUTOINCREMENT, time TEXT NOT NULL, kind TEXT NOT NULL, detail TEXT NOT NULL);
    """)
    return db

def get_state(db: sqlite3.Connection, key: str, default: Any = None) -> Any:
    row = db.execute("SELECT value FROM state WHERE key=?", (key,)).fetchone()
    return default if row is None else json.loads(row[0])

def set_state(db: sqlite3.Connection, key: str, value: Any) -> None:
    db.execute("INSERT INTO state(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, json.dumps(value)))
    db.commit()

def event(db: sqlite3.Connection, kind: str, detail: str, at_ms: int | None = None) -> None:
    stamp = datetime.fromtimestamp((at_ms or int(time.time()*1000))/1000, timezone.utc).isoformat()
    db.execute("INSERT INTO events(time,kind,detail) VALUES(?,?,?)", (stamp, kind, detail))
    db.commit()
    logging.info("%s %s", kind, detail)

def bootstrap(db: sqlite3.Connection, candles: list[Candle]) -> None:
    if get_state(db, "last_processed_ms") is not None:
        return
    if not candles:
        raise RuntimeError("No closed candles returned; cannot bootstrap.")
    last = candles[-1]
    set_state(db, "last_processed_ms", last.start_ms)
    event(db, "BOOTSTRAP", f"anchored at latest closed 60m candle {datetime.fromtimestamp(last.start_ms/1000, timezone.utc).isoformat()}; no historical orders simulated", last.start_ms + INTERVAL_MS)

def new_signal(i: int, candles: list[Candle], ind: dict[str, list[float | None]], cfg: Config) -> str | None:
    if i < 1:
        return None
    ef, es, rsi = ind["ema_fast"], ind["ema_slow"], ind["rsi"]
    if any(x is None for x in (ef[i], es[i], rsi[i], rsi[i-1])):
        return None
    if cfg.allow_long and ef[i] > es[i] and rsi[i-1] <= cfg.rsi_long_cross < rsi[i]:
        return "LONG"
    if cfg.allow_short and ef[i] < es[i] and rsi[i-1] >= cfg.rsi_short_cross > rsi[i]:
        return "SHORT"
    return None

