from config import logger

class ConsistencyAuditor:
    @staticmethod
    def check_market_consistency(matches):
        findings = []
        logger.info("Piyasa tutarlılık denetimi başlatılıyor...")
        
        for match in matches:
            odds_1 = match.get("odds_1")
            odds_x = match.get("odds_x")
            odds_2 = match.get("odds_2")
            
            if any(o is None or o <= 1.0 for o in [odds_1, odds_x, odds_2]):
                findings.append({
                    "category": "Odds_Consistency",
                    "severity": "High",
                    "title": f"Geçersiz Oran Tespit Edildi: {match.get('match_id')}",
                    "description": "Oranlar 1.0'ın altında veya eksik.",
                    "evidence": f"Maç: {match.get('home_team')} vs {match.get('away_team')} | Oranlar: {odds_1}, {odds_x}, {odds_2}"
                })
        
        return findings
