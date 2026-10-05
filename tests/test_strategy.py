import sys
import unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from strategy_core import Candle, Config, new_signal

class StrategyTests(unittest.TestCase):
    def test_strategy_defaults_match_requested_settings(self):
        cfg = Config.load(str(Path(__file__).resolve().parents[1] / "live_config.json"))
        self.assertEqual((cfg.symbol, cfg.interval, cfg.ema_fast, cfg.ema_slow), ("ETHUSDT", "60", 5, 30))
        self.assertEqual((cfg.risk_per_trade_pct, cfg.trail_activation_r, cfg.trail_atr_multiple), (2.5, 2.0, 2.25))

    def test_signal_requires_cross_and_trend(self):
        candles = [Candle(i, 1, 1, 1, 1, 0) for i in range(3)]
        cfg = Config()
        ind = {"ema_fast":[2,2,2], "ema_slow":[1,1,1], "rsi":[35,29,31], "atr":[1,1,1]}
        self.assertEqual(new_signal(2, candles, ind, cfg), "LONG")
        ind["rsi"] = [75,71,69]
        ind["ema_fast"] = [0,0,0]
        ind["ema_slow"] = [1,1,1]
        self.assertEqual(new_signal(2, candles, ind, cfg), "SHORT")

if __name__ == "__main__": unittest.main()
