"""Validate the ledger against explicit physical shell orders, not its internals."""
import importlib.util
import itertools
import unittest
from pathlib import Path

spec = importlib.util.spec_from_file_location("probability", Path(__file__).parents[1] / "buckshot" / "probability.py")
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
AmmoLedger = module.AmmoLedger


class LedgerTests(unittest.TestCase):
    def test_unknown_consumption_keeps_marginal_but_reveal_updates_counts(self):
        ledger = AmmoLedger()
        ledger.reset(2, 1)
        snapshot = ledger.consume("unknown")
        self.assertAlmostEqual(snapshot["p_live"], 2 / 3)
        snapshot = ledger.reveal("blank")
        self.assertEqual(snapshot["expected_live"], 1)
        self.assertEqual(snapshot["expected_blank"], 1)
        self.assertEqual(ledger.consume("unknown")["p_live"], 1)

    def test_exhaustive_physical_orders(self):
        # Every sequence below has the same initial probability. Compare after
        # mixed observations, unknown shots, and repeated inversion events.
        sequences = set(itertools.permutations(("live", "live", "blank", "blank", "blank")))
        events = [("invert", None), ("consume", "unknown"), ("reveal", "live"),
                  ("invert", None), ("consume", "blank"), ("consume", "unknown")]
        ledger = AmmoLedger()
        ledger.reset(2, 3)
        worlds = list(sequences)
        for event, value in events:
            if event == "invert":
                worlds = [("blank" if world[0] == "live" else "live",) + world[1:] for world in worlds]
                actual = ledger.invert()
            elif event == "reveal":
                worlds = [world for world in worlds if world[0] == value]
                actual = ledger.reveal(value)
            else:
                worlds = [world[1:] for world in worlds if value == "unknown" or world[0] == value]
                actual = ledger.consume(value)
            self.assertTrue(worlds)
            self.assertAlmostEqual(actual["p_live"], sum(world[0] == "live" for world in worlds) / len(worlds))
            self.assertAlmostEqual(actual["expected_live"], sum(world.count("live") for world in worlds) / len(worlds))

    def test_impossible_observation_is_atomic(self):
        ledger = AmmoLedger()
        ledger.reset(1, 2)
        before = ledger.reveal("live")
        with self.assertRaises(ValueError):
            ledger.consume("blank")
        self.assertEqual(ledger.snapshot(), before)
        with self.assertRaises(ValueError):
            ledger.reveal("blank")
        self.assertEqual(ledger.snapshot(), before)

    def test_double_invert_and_exhaustion(self):
        ledger = AmmoLedger()
        ledger.reset(1, 3)
        self.assertAlmostEqual(ledger.invert()["p_live"], 0.75)
        self.assertAlmostEqual(ledger.invert()["p_live"], 0.25)
        ledger.consume("live")
        for _ in range(3):
            snapshot = ledger.consume("blank")
        self.assertIsNone(snapshot["p_live"])
        with self.assertRaises(ValueError):
            ledger.consume("unknown")

    def test_bad_counts_and_empty_ledger(self):
        ledger = AmmoLedger()
        for counts in ((0, 0), (True, 2), (-1, 2), (17, 0), (1.0, 2)):
            with self.assertRaises(ValueError):
                ledger.reset(*counts)
        with self.assertRaises(ValueError):
            ledger.invert()
        self.assertFalse(ledger.clear()["initialized"])


if __name__ == "__main__":
    unittest.main()
