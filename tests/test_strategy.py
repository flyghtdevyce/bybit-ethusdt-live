import sys
import unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from strategy_core import Candle, Config, confirm_waiting_signal, indicators, new_signal

class StrategyTests(unittest.TestCase):
    def test_strategy_defaults_match_requested_settings(self):
        cfg = Config.load(str(Path(__file__).resolve().parents[1] / "live_config.json"))
        self.assertEqual((cfg.symbol, cfg.interval, cfg.ema_fast, cfg.ema_slow), ("ETHUSDT", "60", 5, 30))
        self.assertEqual((cfg.risk_per_trade_pct, cfg.trail_activation_r, cfg.trail_atr_multiple), (2.5, 2.0, 2.25))

    def test_signal_requires_cross_and_trend(self):
        candles = [Candle(i, 1, 1, 1, 1, 0) for i in range(3)]
        cfg = Config()
        ind = {"ema_fast":[2,2,2], "ema_slow":[1,1,1], "rsi":[35,29,31], "atr":[1,1,1]}
        self.assertIsNone(new_signal(2, candles, ind, cfg))
        ind = {"ema_fast":[2,0,0], "ema_slow":[1,1,1], "rsi":[75,71,69], "atr":[1,1,1]}
        self.assertEqual(new_signal(2, candles, ind, cfg), "SHORT")

    def test_v61_second_matching_cross_creates_long_signal_in_either_order(self):
        candles = [Candle(i, 100, 101, 99, 100, 1) for i in range(45)]
        ind = {"ema_fast":[99.0]*45, "ema_slow":[100.0]*45,
               "rsi":[50.0]*45, "atr":[1.0]*45}
        # EMA crosses first; RSI up-cross is the second event at bar 40.
        ind["ema_fast"][39] = ind["ema_fast"][40] = 101.0
        ind["rsi"][39:41] = [29.0, 31.0]
        self.assertEqual(new_signal(40, candles, ind, Config()), "LONG")

        # RSI crosses first; EMA up-cross completes the signal at bar 40.
        ind = {"ema_fast":[99.0]*45, "ema_slow":[100.0]*45,
               "rsi":[50.0]*45, "atr":[1.0]*45}
        ind["rsi"][38:40] = [29.0, 31.0]
        ind["ema_fast"][40] = 101.0
        self.assertEqual(new_signal(40, candles, ind, Config()), "LONG")

    def test_atr_is_sma_not_wilder(self):
        cfg = Config(atr_period=2)
        candles = [Candle(0, 1, 3, 0, 1, 1), Candle(1, 1, 4, 0, 2, 1),
                   Candle(2, 2, 8, 1, 5, 1)]
        atr = indicators(candles, cfg)["atr"]
        self.assertEqual(atr, [None, 3.5, 5.5])

    def test_confirmation_uses_only_breaking_candle_extreme_and_keeps_signal_atr(self):
        cfg = Config(breakout_buffer_pct=1.0)
        waiting = {"side":"LONG", "signal_ms":100, "signal_high":200.0, "signal_low":190.0}
        confirm = Candle(100 + 3_600_000, 199, 203, 198, 202, 1)
        setup = confirm_waiting_signal(waiting, confirm, 4.0, cfg)
        self.assertEqual(setup, {"side":"LONG", "trigger":205.03, "signal_atr":4.0,
                                 "signal_ms":100, "confirmation_ms":confirm.start_ms})
        self.assertIsNone(confirm_waiting_signal(waiting, Candle(confirm.start_ms, 199, 199, 195, 197, 1), 4.0, cfg))

        waiting["side"]="SHORT"
        confirm=Candle(confirm.start_ms, 199, 201, 187, 190, 1)
        setup=confirm_waiting_signal(waiting,confirm,4.0,cfg)
        self.assertAlmostEqual(setup["trigger"],185.13)

if __name__ == "__main__": unittest.main()
