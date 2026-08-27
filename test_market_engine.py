import unittest

import market_engine as me


def pick(market, odd, prob):
    return {
        "market": market,
        "bookmaker_odds": odd,
        "probability": prob * 100,
        "ticket_probability": prob * 100,
        "suspicious": False,
        "recommendation_score": prob * 100,
        "ev": 0,
    }


class MarketEngineTests(unittest.TestCase):
    def test_asian_quarter_lines_are_split_correctly(self):
        self.assertEqual(me._quarter_split(2.25), (2.0, 2.5))
        self.assertEqual(me._quarter_split(2.75), (2.5, 3.0))
        self.assertEqual(me._quarter_split(-0.25), (-0.5, 0.0))
        self.assertIsNone(me._quarter_split(2.5))

    def test_optimizer_prefers_safer_ticket_over_exact_odds(self):
        rows = [
            {
                "home": "A",
                "away": "B",
                "markets": [pick("safe", 1.40, 0.80), pick("exact", 1.4286, 0.60)],
            },
            {"home": "C", "away": "D", "markets": [pick("base", 1.40, 0.80)]},
        ]
        combo, _ = me.build_combo(rows, 2.0)
        self.assertIsNotNone(combo)
        self.assertAlmostEqual(combo["combined_odds"], 1.96, places=2)
        self.assertGreater(combo["estimated_joint_probability"], 60)

    def test_one_selection_per_fixture(self):
        rows = []
        for i in range(8):
            rows.append(
                {
                    "home": f"H{i}",
                    "away": f"A{i}",
                    "markets": [pick("one", 1.30, 0.78), pick("two", 1.55, 0.63)],
                }
            )
        combo, _ = me.build_combo(rows, 5.0)
        self.assertIsNotNone(combo)
        matches = [(x["home"], x["away"]) for x in combo["matches"]]
        self.assertEqual(len(matches), len(set(matches)))

    def test_cota_100_has_no_fixed_leg_count(self):
        rows = []
        for i in range(20):
            rows.append(
                {
                    "home": f"H{i}",
                    "away": f"A{i}",
                    "markets": [pick("low", 1.36, 0.75), pick("higher", 1.80, 0.57)],
                }
            )
        combo, diag = me.build_combo(rows, 100.0)
        self.assertIsNotNone(combo)
        self.assertTrue(98 <= combo["combined_odds"] <= 102)
        self.assertTrue(combo["target_met"])
        self.assertGreaterEqual(diag["candidate_matches"], 15)


if __name__ == "__main__":
    unittest.main()
