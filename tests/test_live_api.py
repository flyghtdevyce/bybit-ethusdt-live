import sys
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from live_bot import BybitAPI, LiveRunner, active_position, round_to_step
from strategy_core import Candle, Config


class FakeAPI(BybitAPI):
    def __init__(self):
        self.captured = None

    def request(self, method, path, params=None):
        self.captured = (method, path, params)
        return {"orderId": "sample-order-id"}


class LiveSafetyTests(unittest.TestCase):
    def test_rounds_quantity_down_and_stop_outward(self):
        self.assertEqual(round_to_step(1.239, "0.01"), "1.23")
        self.assertEqual(round_to_step(100.001, "0.1", up=True), "100.1")

    def test_conditional_order_is_one_way_and_carries_protective_stop(self):
        api = FakeAPI()
        api.place_breakout("LONG", "0.2", "2500", "2480", "ethf3-long-1")
        method, path, body = api.captured
        self.assertEqual((method, path), ("POST", "/v5/order/create"))
        self.assertEqual(body["triggerDirection"], 1)
        self.assertEqual(body["positionIdx"], 0)
        self.assertEqual(body["stopLoss"], "2480")
        self.assertEqual(body["slOrderType"], "Market")
        self.assertFalse(body["reduceOnly"])

    def test_market_entry_is_one_way_and_carries_protective_stop(self):
        api = FakeAPI()
        api.place_market_entry("SHORT", "0.2", "2520", "ethv61-force-short-1")
        method, path, body = api.captured
        self.assertEqual((method, path), ("POST", "/v5/order/create"))
        self.assertEqual(body["side"], "Sell")
        self.assertEqual(body["orderType"], "Market")
        self.assertEqual(body["positionIdx"], 0)
        self.assertEqual(body["stopLoss"], "2520")
        self.assertEqual(body["slOrderType"], "Market")
        self.assertFalse(body["reduceOnly"])

    def test_unmanaged_position_and_hedge_mode_fail_closed(self):
        self.assertIsNone(active_position([]))
        self.assertEqual(active_position([{"size":"0","positionIdx":0}]), None)
        with self.assertRaises(RuntimeError):
            active_position([{"size":"0.1","side":"Buy","positionIdx":1}])

    def test_live_risk_size_includes_fee_and_slippage_estimates(self):
        class SizingAPI:
            def account(self):
                return 10_000.0, 10_000.0
            def positions(self):
                return [{"leverage":"2"}]

        runner = LiveRunner.__new__(LiveRunner)
        runner.cfg = Config()
        runner.api = SizingAPI()
        runner.qty_filter = {"qtyStep":"0.01", "minOrderQty":"0.01",
                             "minNotionalValue":"5", "maxMktOrderQty":"100000"}
        qty, stop_distance = runner.qty_for_risk("LONG", 100.0, 2.0, 20_000.0)
        unit_risk = 1.0 + (100.0 + 99.0) * (0.001 + 0.0002)
        self.assertEqual(qty, "217.53")
        self.assertAlmostEqual(stop_distance, 1.0)
        self.assertAlmostEqual(float(qty), 250.0 / unit_risk, delta=0.01)

    def test_forced_entry_uses_live_equity_and_records_full_lifecycle(self):
        class ForceAPI:
            key = "test-key"
            secret = b"test-secret"

            def __init__(self):
                self.order = None

            def instrument(self):
                return {"lotSizeFilter": {"qtyStep":"0.01", "minOrderQty":"0.01",
                                          "minNotionalValue":"5", "maxMktOrderQty":"100000"},
                        "priceFilter": {"tickSize":"0.1"}}

            def positions(self):
                if self.order:
                    return [{"size":self.order["qty"], "side":"Buy", "positionIdx":0,
                             "leverage":"2", "avgPrice":"100", "stopLoss":self.order["stop"]}]
                return [{"size":"0", "positionIdx":0, "leverage":"2"}]

            def open_orders(self):
                return []

            def account(self):
                return 10_000.0, 10_000.0

            def last_price(self):
                return 100.0

            def place_market_entry(self, side, qty, stop, order_link_id):
                self.order = {"qty":qty, "stop":stop, "order_link_id":order_link_id}
                return {"orderId":"forced-order-1"}

        with tempfile.TemporaryDirectory() as tmp:
            cfg = Config(database_path=str(Path(tmp) / "live.sqlite3"))
            api = ForceAPI()
            runner = LiveRunner(cfg, api)
            candles = [Candle(i * 3_600_000, 100, 101, 99, 100, 1) for i in range(20)]
            with patch("live_bot.get_closed_candles", return_value=candles), \
                 patch("live_bot.indicators", return_value={"atr":[2.0] * len(candles)}), \
                 patch("builtins.input", return_value="FORCE LIVE ETHUSDT LONG"):
                runner.force_entry("LONG")

            self.assertEqual(api.order["stop"], "99")
            tracked = json.loads(runner.db.execute(
                "SELECT value FROM state WHERE key='position'").fetchone()[0])
            self.assertTrue(tracked["forced"])
            kinds = [r[0] for r in runner.db.execute("SELECT kind FROM events")]
            self.assertIn("FORCE_ENTRY_REQUESTED", kinds)
            self.assertIn("FORCE_ENTRY_ORDER_ACCEPTED", kinds)
            self.assertIn("FORCED_POSITION_OPENED", kinds)
            self.assertIsNone(json.loads(
                runner.db.execute("SELECT value FROM state WHERE key='pending'").fetchone()[0]))

    def test_forced_entry_refuses_existing_exchange_orders(self):
        class BusyAPI:
            key = "test-key"
            secret = b"test-secret"

            def positions(self):
                return [{"size":"0", "positionIdx":0}]

            def open_orders(self):
                return [{"orderId":"existing"}]

        runner = LiveRunner.__new__(LiveRunner)
        runner.cfg = Config()
        runner.api = BusyAPI()
        with self.assertRaisesRegex(RuntimeError, "open order"):
            runner.force_entry("SHORT")


if __name__ == "__main__":
    unittest.main()
