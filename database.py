import sqlite3
import os
import shutil
from config import DB_PATH, BACKUP_DB_PATH, logger

class DatabaseManager:
    def __init__(self, db_path=DB_PATH):
        self.db_path = db_path
        self.init_database()

    def get_connection(self):
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON;")
        return conn

    def init_database(self):
        try:
            conn = self.get_connection()
            cursor = conn.cursor()
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS schema_version (
                    version INTEGER PRIMARY KEY,
                    applied_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
            cursor.execute("SELECT MAX(version) FROM schema_version")
            row = cursor.fetchone()
            current_version = row[0] if row and row[0] else 0

            if current_version < 1:
                cursor.execute("""
                    CREATE TABLE IF NOT EXISTS matches (
                        match_id TEXT PRIMARY KEY,
                        league TEXT NOT NULL,
                        home_team TEXT NOT NULL,
                        away_team TEXT NOT NULL,
                        match_date TEXT NOT NULL,
                        ft_home_score INTEGER,
                        ft_away_score INTEGER,
                        ht_home_score INTEGER,
                        ht_away_score INTEGER,
                        source TEXT NOT NULL,
                        verified INTEGER DEFAULT 0,
                        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                    )
                """)
                cursor.execute("""
                    CREATE TABLE IF NOT EXISTS odds_history (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        match_id TEXT NOT NULL,
                        market_type TEXT NOT NULL,
                        odds_1 REAL,
                        odds_x REAL,
                        odds_2 REAL,
                        timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        source TEXT NOT NULL,
                        verified INTEGER DEFAULT 0,
                        FOREIGN KEY (match_id) REFERENCES matches(match_id) ON DELETE CASCADE
                    )
                """)
                cursor.execute("""
                    CREATE TABLE IF NOT EXISTS audit_findings (
                        finding_id INTEGER PRIMARY KEY AUTOINCREMENT,
                        category TEXT NOT NULL,
                        severity TEXT NOT NULL,
                        title TEXT NOT NULL,
                        description TEXT NOT NULL,
                        evidence TEXT,
                        status TEXT DEFAULT 'Open',
                        detected_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                    )
                """)
                cursor.execute("INSERT INTO schema_version (version) VALUES (1)")
                conn.commit()
                logger.info("Veritabanı şeması başarıyla oluşturuldu ve v1 uygulandı.")
            conn.close()
        except Exception as e:
            logger.error(f"Veritabanı başlatma hatası: {e}")
            raise

    def backup_database(self):
        try:
            if os.path.exists(self.db_path):
                shutil.copy2(self.db_path, BACKUP_DB_PATH)
                logger.info(f"Veritabanı yedeği alındı: {BACKUP_DB_PATH}")
                return True
        except Exception as e:
            logger.error(f"Yedekleme başarısız: {e}")
        return False
