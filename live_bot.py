#!/usr/bin/env python3
"""Fail-closed live executor for the ETHUSDT Frozen-v3-style 60m strategy.

Live orders require LIVE_TRADING_ENABLED=YES and a trade-enabled Bybit HMAC key.
The standalone signal engine is bundled in strategy_core.py.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os
import time
import urllib.parse
import urllib.request
from decimal import Decimal, ROUND_DOWN, ROUND_UP
from pathlib import Path
from typing import Any

from strategy_core import (
    Config, Candle, INTERVAL_MS, bootstrap, event, get_closed_candles,
    get_state, indicators, init_db, new_signal, set_state,
)

API = "https://api.bybit.com"
WINDOW = "5000"
PREFIX = "ethf3"


class BybitAPI:
    def __init__(self, key: str, secret: str):
        if not key or not secret:
            raise RuntimeError("BYBIT_LIVE_API_KEY and BYBIT_LIVE_API_SECRET are required.")
        self.key, self.secret = key, secret.encode()

    @staticmethod
    def _ok(raw: bytes) -> dict[str, Any]:
        data = json.loads(raw)
        if data.get("retCode") != 0:
            raise RuntimeError(f"Bybit error {data.get('retCode')}: {data.get('retMsg')}")
        return data.get("result", {})

    def request(self, method: str, path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        params = params or {}
        timestamp = str(int(time.time() * 1000))
        if method == "GET":
            query = urllib.parse.urlencode(sorted((k, str(v)) for k, v in params.items()))
            payload = query
            url = f"{API}{path}?{query}" if query else f"{API}{path}"
            data = None
        else:
            payload = json.dumps(params, separators=(",", ":"), ensure_ascii=False)
            url = f"{API}{path}"
            data = payload.encode()
        signed = timestamp + self.key + WINDOW + payload
        signature = hmac.new(self.secret, signed.encode(), hashlib.sha256).hexdigest()
        headers = {
            "X-BAPI-API-KEY": self.key,
            "X-BAPI-TIMESTAMP": timestamp,
            "X-BAPI-RECV-WINDOW": WINDOW,
            "X-BAPI-SIGN": signature,
            "Content-Type": "application/json",
            "User-Agent": "ethusdt-frozenv3-live/1.0",
        }
        req = urllib.request.Request(url, data=data, headers=headers, method=method)
        with urllib.request.urlopen(req, timeout=15) as response:
            return self._ok(response.read())

    def positions(self) -> list[dict[str, Any]]:
        return self.request("GET", "/v5/position/list", {"category":"linear","symbol":"ETHUSDT"}).get("list", [])

    def open_orders(self) -> list[dict[str, Any]]:
        return self.request("GET", "/v5/order/realtime", {"category":"linear","symbol":"ETHUSDT"}).get("list", [])

    def account(self) -> tuple[float, float]:
        result = self.request("GET", "/v5/account/wallet-balance", {"accountType":"UNIFIED","coin":"USDT"})
        accounts = result.get("list", [])
        if not accounts:
            raise RuntimeError("Bybit returned no Unified account balance.")
        row = accounts[0]
        equity = float(row.get("totalEquity") or 0)
        available = float(row.get("totalAvailableBalance") or 0)
        if equity <= 0 or available <= 0:
            raise RuntimeError("Equity or available balance is zero; refusing to size an order.")
        return equity, available

    def instrument(self) -> dict[str, Any]:
        rows = self.request("GET", "/v5/market/instruments-info", {"category":"linear","symbol":"ETHUSDT"}).get("list", [])
        if not rows or rows[0].get("status") != "Trading":
            raise RuntimeError("ETHUSDT Linear is not in Trading status.")
        return rows[0]

    def cancel_order(self, order_id: str) -> None:
        self.request("POST", "/v5/order/cancel", {"category":"linear","symbol":"ETHUSDT","orderId":order_id})

    def place_breakout(self, side: str, qty: str, trigger: str, stop: str, order_link_id: str) -> dict[str, Any]:
        is_long = side == "LONG"
        body = {
            "category":"linear", "symbol":"ETHUSDT", "side":"Buy" if is_long else "Sell",
            "orderType":"Market", "qty":qty,
            "triggerPrice":trigger, "triggerDirection":1 if is_long else 2,
            "triggerBy":"LastPrice", "timeInForce":"GTC", "positionIdx":0,
            "orderLinkId":order_link_id, "reduceOnly":False,
            "stopLoss":stop, "slTriggerBy":"LastPrice", "tpslMode":"Full", "slOrderType":"Market",
        }
        return self.request("POST", "/v5/order/create", body)

    def set_stop(self, price: str) -> None:
        self.request("POST", "/v5/position/trading-stop", {
            "category":"linear","symbol":"ETHUSDT","tpslMode":"Full",
            "positionIdx":0,"stopLoss":price,"slTriggerBy":"LastPrice","slOrderType":"Market",
        })

    def close_market(self, side: str, qty: str, order_link_id: str) -> dict[str, Any]:
        return self.request("POST", "/v5/order/create", {
            "category":"linear","symbol":"ETHUSDT","side":"Sell" if side=="LONG" else "Buy",
            "orderType":"Market","qty":qty,"positionIdx":0,"reduceOnly":True,
            "closeOnTrigger":True,"orderLinkId":order_link_id,
        })


def round_to_step(value: float, step: str, up: bool = False) -> str:
    d, s = Decimal(str(value)), Decimal(step)
    units = (d / s).to_integral_value(rounding=ROUND_UP if up else ROUND_DOWN)
    return format(units * s, "f")


def active_position(rows: list[dict[str, Any]]) -> dict[str, Any] | None:
    live = [r for r in rows if float(r.get("size") or 0) > 0]
    if len(live) > 1:
        raise RuntimeError("Multiple ETHUSDT positions detected; refusing automated management.")
    if not live:
        return None
    if int(live[0].get("positionIdx", -1)) != 0:
        raise RuntimeError("ETHUSDT account is not in one-way position mode (positionIdx=0 required).")
    return live[0]


class LiveRunner:
    def __init__(self, cfg: Config, api: BybitAPI):
        self.cfg, self.api = cfg, api
        self.db = init_db(cfg.database_path, cfg.starting_balance_usd)
        self.spec = api.instrument()
        self.qty_filter = self.spec["lotSizeFilter"]
        self.price_tick = self.spec["priceFilter"]["tickSize"]

    def qty_for_risk(self, trigger: float, atr: float, available: float) -> tuple[str, float]:
        stop_distance = atr * self.cfg.atr_stop_multiple
        if stop_distance <= 0:
            raise RuntimeError("Invalid stop distance.")
        equity, _ = self.api.account()
        qty_by_risk = equity * self.cfg.risk_per_trade_pct / 100 / stop_distance
        leverage_rows = self.api.positions()
        leverage_values = [float(r["leverage"]) for r in leverage_rows if r.get("leverage")]
        if not leverage_values:
            raise RuntimeError("Bybit did not return configured ETHUSDT leverage; refusing to size live order.")
        max_notional = available * min(leverage_values) * 0.90
        qty_by_margin = max_notional / trigger
        qty = min(qty_by_risk, qty_by_margin, float(self.qty_filter.get("maxMktOrderQty") or qty_by_risk))
        qty_text = round_to_step(qty, self.qty_filter["qtyStep"])
        if Decimal(qty_text) < Decimal(self.qty_filter["minOrderQty"]):
            raise RuntimeError("Calculated quantity is below Bybit's minOrderQty; signal skipped.")
        notional = float(qty_text) * trigger
        if notional < float(self.qty_filter.get("minNotionalValue") or 0):
            raise RuntimeError("Calculated order is below Bybit's minNotionalValue; signal skipped.")
        return qty_text, stop_distance

    def submit_signal(self, side: str, candle: Candle, atr: float) -> None:
        equity, available = self.api.account()
        del equity
        is_long = side == "LONG"
        raw_trigger = candle.high * (1 + self.cfg.breakout_buffer_pct / 100) if is_long else candle.low * (1 - self.cfg.breakout_buffer_pct / 100)
        trigger = float(round_to_step(raw_trigger, self.price_tick, up=is_long))
        stop_distance = atr * self.cfg.atr_stop_multiple
        raw_stop = trigger - stop_distance if is_long else trigger + stop_distance
        stop = float(round_to_step(raw_stop, self.price_tick, up=not is_long))
        qty, _ = self.qty_for_risk(trigger, atr, available)
        order_link = f"{PREFIX}-{side.lower()}-{candle.start_ms}"
        result = self.api.place_breakout(side, qty, f"{trigger:.12g}", f"{stop:.12g}", order_link)
        pending = {"side":side,"trigger":trigger,"signal_atr":atr,"signal_ms":candle.start_ms,
                   "expires_after_ms":candle.start_ms+self.cfg.signal_window_bars*INTERVAL_MS,
                   "order_id":result.get("orderId"),"order_link_id":order_link,"stop":stop,"qty":qty}
        set_state(self.db,"pending",pending)
        event(self.db,"LIVE_ENTRY_ORDER",f"{side} conditional market entry qty={qty} trigger={trigger}; initial exchange stop={stop}",candle.start_ms+INTERVAL_MS)

    def manage_position(self, position: dict[str, Any], candle: Candle, i: int, ind: dict[str, list[float | None]]) -> None:
        tracked = get_state(self.db,"position")
        side = "LONG" if position.get("side")=="Buy" else "SHORT"
        entry = float(position["avgPrice"])
        qty = str(position["size"])
        pending = get_state(self.db,"pending")
        if tracked is None:
            if not pending:
                raise RuntimeError("Untracked ETHUSDT position found; manual intervention required.")
            tracked = {"side":side,"entry_price":entry,"quantity":qty,
                       "initial_risk":float(pending["signal_atr"])*self.cfg.atr_stop_multiple,
                       "stop":float(position.get("stopLoss") or pending["stop"]),"order_link_id":pending.get("order_link_id")}
            set_state(self.db,"position",tracked)
            set_state(self.db,"pending",None)
            event(self.db,"LIVE_POSITION_RECONCILED",f"Exchange confirms {side} position at {entry}; size={qty}",candle.start_ms+INTERVAL_MS)
        exchange_stop = float(position.get("stopLoss") or 0)
        if exchange_stop <= 0:
            initial = entry-tracked["initial_risk"] if side=="LONG" else entry+tracked["initial_risk"]
            stop = float(round_to_step(initial,self.price_tick,up=side=="SHORT"))
            self.api.set_stop(f"{stop:.12g}")
            tracked["stop"]=stop
            set_state(self.db,"position",tracked)
            event(self.db,"LIVE_STOP_RESTORED",f"Missing exchange stop; restored at {stop}",candle.start_ms+INTERVAL_MS)
        sign = 1 if side=="LONG" else -1
        reversed_trend = (sign==1 and ind["ema_fast"][i] < ind["ema_slow"][i]) or (sign==-1 and ind["ema_fast"][i] > ind["ema_slow"][i])
        if reversed_trend:
            if tracked.get("closing_order_link_id"):
                logging.info("EMA exit already submitted as %s; waiting for exchange position to become flat.", tracked["closing_order_link_id"])
                return
            link = f"{PREFIX}-exit-{candle.start_ms}"
            self.api.close_market(side,qty,link)
            event(self.db,"LIVE_EXIT_ORDER",f"{side} reduce-only market close submitted on EMA reversal",candle.start_ms+INTERVAL_MS)
            tracked["closing_order_link_id"]=link
            set_state(self.db,"position",tracked)
            return
        favorable = candle.high-entry if side=="LONG" else entry-candle.low
        if favorable >= tracked["initial_risk"]*self.cfg.trail_activation_r and ind["atr"][i] is not None:
            raw = candle.high-self.cfg.trail_atr_multiple*float(ind["atr"][i]) if side=="LONG" else candle.low+self.cfg.trail_atr_multiple*float(ind["atr"][i])
            proposed = float(round_to_step(raw,self.price_tick,up=side=="SHORT"))
            tighter = proposed > exchange_stop if side=="LONG" else proposed < exchange_stop
            if tighter:
                self.api.set_stop(f"{proposed:.12g}")
                tracked["stop"]=proposed
                set_state(self.db,"position",tracked)
                event(self.db,"LIVE_TRAIL_UPDATED",f"{side} stop moved from {exchange_stop} to {proposed}",candle.start_ms+INTERVAL_MS)

    def cycle(self) -> None:
        candles=get_closed_candles(self.cfg)
        bootstrap(self.db,candles)
        last_ms=int(get_state(self.db,"last_processed_ms"))
        fresh=[c for c in candles if c.start_ms>last_ms]
        positions=active_position(self.api.positions())
        orders=self.api.open_orders()
        pending=get_state(self.db,"pending")
        tracked_position=get_state(self.db,"position")
        if positions is None and tracked_position is not None:
            event(self.db,"LIVE_POSITION_CLOSED","Exchange reports the managed ETHUSDT position is flat; clearing local position state.")
            set_state(self.db,"position",None)
            tracked_position=None
        if len(fresh)>1:
            raise RuntimeError(f"{len(fresh)} hourly bars accumulated; live catch-up is disabled. Reconcile manually.")
        if positions is None and pending is None:
            if orders:
                raise RuntimeError("Unmanaged ETHUSDT open orders found; refusing new signal.")
        elif pending:
            tracked_id=pending.get("order_id")
            if positions is None and tracked_id and not any(o.get("orderId")==tracked_id for o in orders):
                raise RuntimeError("Tracked entry order is absent but no position is visible; check Bybit order history before resuming.")
        if not fresh:
            logging.info("No new closed candle; exchange state checked.")
            return
        c=fresh[0]
        ind=indicators(candles,self.cfg)
        i=next(i for i,x in enumerate(candles) if x.start_ms==c.start_ms)
        if positions:
            self.manage_position(positions,c,i,ind)
        else:
            if pending and c.start_ms>pending["expires_after_ms"]:
                self.api.cancel_order(str(pending["order_id"]))
                set_state(self.db,"pending",None)
                event(self.db,"LIVE_ENTRY_EXPIRED",f"Canceled {pending['side']} unfilled breakout order",c.start_ms+INTERVAL_MS)
                pending=None
            signal=new_signal(i,candles,ind,self.cfg)
            if signal:
                if pending:
                    self.api.cancel_order(str(pending["order_id"]))
                    event(self.db,"LIVE_ENTRY_CANCELLED",f"New {signal} signal canceled prior {pending['side']} order",c.start_ms+INTERVAL_MS)
                    set_state(self.db,"pending",None)
                    pending=None
                if ind["atr"][i] is not None:
                    self.submit_signal(signal,c,float(ind["atr"][i]))
        set_state(self.db,"last_processed_ms",c.start_ms)


def main() -> None:
    import argparse
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config",default="live_config.json")
    parser.add_argument("--once",action="store_true")
    args=parser.parse_args()
    logging.basicConfig(level=logging.INFO,format="%(asctime)s %(levelname)s %(message)s")
    if os.getenv("LIVE_TRADING_ENABLED")!="YES":
        raise SystemExit("Live trading is disabled. Set LIVE_TRADING_ENABLED=YES in the protected service environment to enable order placement.")
    cfg=Config.load(args.config)
    api=BybitAPI(os.getenv("BYBIT_LIVE_API_KEY",""),os.getenv("BYBIT_LIVE_API_SECRET",""))
    runner=LiveRunner(cfg,api)
    if args.once:
        runner.cycle()
        return
    while True:
        try:
            runner.cycle()
        except Exception:
            logging.exception("Live engine stopped on an error; investigate before restarting.")
            raise
        time.sleep(cfg.poll_seconds)


if __name__=="__main__":
    main()
