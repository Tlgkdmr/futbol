
import sqlite3
from pathlib import Path

DB_PATH = Path("data/frekans_x.db")

def initialize_database():
    """Veritabanı yoksa oluşturur ve gerekli tabloları hazırlar."""
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)

    with sqlite3.connect(DB_PATH) as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS matches (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                Date TEXT,
                League TEXT,
                HomeTeam TEXT,
                AwayTeam TEXT,
                FTHG INTEGER,
                FTAG INTEGER,
                B365H REAL,
                B365D REAL,
                B365A REAL
            )
        """)

        conn.execute("""
            CREATE TABLE IF NOT EXISTS predictions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                match_id INTEGER,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                market TEXT,
                predicted_value TEXT,
                probability REAL,
                notes TEXT
            )
        """)

        conn.commit()

    return DB_PATH
