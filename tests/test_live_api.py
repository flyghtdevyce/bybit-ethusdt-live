import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from live_bot import BybitAPI, LiveRunner, active_position, round_to_step
from strategy_core import Config


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
        unit_risk = 1.0 + (100.0 + 99.0) * (0.00055 + 0.0002)
        self.assertEqual(qty, "217.53")
        self.assertAlmostEqual(stop_distance, 1.0)
        self.assertAlmostEqual(float(qty), 250.0 / unit_risk, delta=0.01)


if __name__ == "__main__":
    unittest.main()
