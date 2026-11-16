from config import logger

class OddsIntelligenceEngine:
    @staticmethod
    def calculate_implied_probability(odds_1, odds_x, odds_2):
        try:
            p1 = 1.0 / odds_1
            px = 1.0 / odds_x
            p2 = 1.0 / odds_2
            overround = (p1 + px + p2) - 1.0
            return {
                "prob_1": round(p1 * 100, 2),
                "prob_x": round(px * 100, 2),
                "prob_2": round(p2 * 100, 2),
                "overround": round(overround * 100, 2)
            }
        except ZeroDivisionError:
            logger.error("Oranlar sıfır olamaz!")
            return None
