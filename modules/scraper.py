import datetime
from config import logger

class NesineScraper:
    def __init__(self, use_mock=True):
        self.use_mock = use_mock

    def fetch_live_odds(self):
        logger.info("Maç bültenleri taranıyor...")
        if self.use_mock:
            return self._get_mock_data()
        return []

    def _get_mock_data(self):
        return [
            {
                "match_id": "NESINE-2026-001",
                "league": "Türkiye Süper Ligi",
                "home_team": "Galatasaray",
                "away_team": "Fenerbahçe",
                "match_date": str(datetime.date.today()),
                "market_type": "1X2",
                "odds_1": 2.10,
                "odds_x": 3.30,
                "odds_2": 3.10,
                "source": "Nesine_Mock_Feed",
                "verified": 1
            },
            {
                "match_id": "NESINE-2026-002",
                "league": "İngiltere Premier Lig",
                "home_team": "Arsenal",
                "away_team": "Manchester City",
                "match_date": str(datetime.date.today()),
                "market_type": "1X2",
                "odds_1": 2.50,
                "odds_x": 3.20,
                "odds_2": 2.70,
                "source": "Nesine_Mock_Feed",
                "verified": 1
            }
        ]
