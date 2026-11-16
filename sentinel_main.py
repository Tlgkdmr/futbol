from database import DatabaseManager
from modules.scraper import NesineScraper
from modules.odds_intelligence import OddsIntelligenceEngine
from modules.consistency_audit import ConsistencyAuditor
from modules.security_audit import EthicalSecurityAuditor
from modules.reporter import ReportGenerator
from config import logger

def main():
    logger.info("NESİNE SENTİNEL X Başlatılıyor...")
    
    db_mgr = DatabaseManager()
    db_mgr.backup_database()
    
    scraper = NesineScraper(use_mock=True)
    raw_matches = scraper.fetch_live_odds()
    
    conn = db_mgr.get_connection()
    cursor = conn.cursor()
    
    for match in raw_matches:
        cursor.execute("""
            INSERT OR IGNORE INTO matches (match_id, league, home_team, away_team, match_date, source, verified)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (
            match["match_id"], match["league"], match["home_team"], match["away_team"],
            match["match_date"], match["source"], match["verified"]
        ))
        
        cursor.execute("""
            INSERT INTO odds_history (match_id, market_type, odds_1, odds_x, odds_2, source, verified)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (
            match["match_id"], match["market_type"], match["odds_1"], match["odds_x"],
            match["odds_2"], match["source"], match["verified"]
        ))
    conn.commit()
    
    consistency_findings = ConsistencyAuditor.check_market_consistency(raw_matches)
    security_findings = EthicalSecurityAuditor.passive_security_check()
    
    all_findings = consistency_findings + security_findings
    
    for f in all_findings:
        cursor.execute("""
            INSERT INTO audit_findings (category, severity, title, description, evidence, status)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (
            f["category"], f["severity"], f["title"], f["description"], f["evidence"], "Open"
        ))
    conn.commit()
    conn.close()
    
    ReportGenerator.generate_reports(raw_matches, all_findings)
    logger.info("NESİNE SENTİNEL X görev döngüsünü başarıyla tamamladı.")

if __name__ == "__main__":
    main()
