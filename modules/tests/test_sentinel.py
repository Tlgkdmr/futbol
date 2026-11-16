import unittest
from modules.odds_intelligence import OddsIntelligenceEngine
from modules.consistency_audit import ConsistencyAuditor

class TestSentinelSystem(unittest.TestCase):
    def test_implied_probability(self):
        result = OddsIntelligenceEngine.calculate_implied_probability(2.0, 3.0, 4.0)
        self.assertIsNotNone(result)
        self.assertIn("prob_1", result)
        self.assertGreater(result["overround"], 0)

    def test_consistency_auditor(self):
        mock_bad_match = [{
            "match_id": "TEST-001",
            "home_team": "Team A",
            "away_team": "Team B",
            "odds_1": 0.9, # Geçersiz oran
            "odds_x": 3.0,
            "odds_2": 3.0
        }]
        findings = ConsistencyAuditor.check_market_consistency(mock_bad_match)
        self.assertTrue(len(findings) > 0)
        self.assertEqual(findings[0]["severity"], "High")

if __name__ == "__main__":
    unittest.main()
