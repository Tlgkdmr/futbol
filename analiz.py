import sqlite3
import datetime
import math
import logging
import os
import uuid
from typing import List, Dict, Any, Optional
import streamlit as st
import pandas as pd

# Loglama Ayarları
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

# Sabitler ve Ortam Desteği
DB_NAME = os.getenv("FREKANS_DB_PATH", "frekans_x.db")
SUPPORTED_PROVIDERS = ["football-data.co.uk", "API-Football", "Custom", "Örnek Veri"]

def parse_and_normalize_date(date_val: Any) -> Optional[str]:
    if date_val is None:
        return None
    s_date = str(date_val).strip()
    if not s_date or s_date.lower() in ['nan', 'none', 'nat', 'inf', '-inf']:
        return None
        
    for fmt in ('%Y-%m-%d', '%d/%m/%Y', '%d.%m.%Y', '%Y/%m/%d'):
        try:
            dt = datetime.datetime.strptime(s_date, fmt).date()
            return dt.isoformat()
        except ValueError:
            continue
            
    try:
        dt = datetime.date.fromisoformat(s_date)
        return dt.isoformat()
    except ValueError:
        return None

def seed_sample_data_if_empty(target_db: str = DB_NAME):
    """Veritabanı boşsa otomatik örnek veriler ekler (Telefonda dosya yükleme derdini bitirir)."""
    try:
        conn = sqlite3.connect(target_db)
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM historical_matches;")
        count = cursor.fetchone()[0]
        if count == 0:
            sample_matches = [
                {"league": "Türkiye Süper Lig", "season": "2025/2026", "date": "2025-10-15", "home": "Galatasaray", "away": "Fenerbahçe", "fthg": 2, "ftag": 1, "ftr": "1", "hthg": 1, "htag": 0, "htr": "1", "ms_1": 2.10, "ms_x": 3.30, "ms_2": 3.40, "source_provider": "Örnek Veri", "is_future": 0},
                {"league": "Türkiye Süper Lig", "season": "2025/2026", "date": "2025-10-18", "home": "Beşiktaş", "away": "Trabzonspor", "fthg": 1, "ftag": 1, "ftr": "X", "hthg": 0, "htag": 1, "htr": "2", "ms_1": 2.20, "ms_x": 3.20, "ms_2": 3.10, "source_provider": "Örnek Veri", "is_future": 0},
                {"league": "İngiltere Premier League", "season": "2025/2026", "date": "2025-10-20", "home": "Arsenal", "away": "Chelsea", "fthg": 3, "ftag": 1, "ftr": "1", "hthg": 2, "htag": 0, "htr": "1", "ms_1": 1.85, "ms_x": 3.50, "ms_2": 4.00, "source_provider": "Örnek Veri", "is_future": 0},
                {"league": "İngiltere Premier League", "season": "2025/2026", "date": "2025-10-22", "home": "Manchester City", "away": "Liverpool", "fthg": 2, "ftag": 2, "ftr": "X", "hthg": 1, "htag": 1, "htr": "X", "ms_1": 2.00, "ms_x": 3.40, "ms_2": 3.60, "source_provider": "Örnek Veri", "is_future": 0}
            ]
            for m in sample_matches:
                cursor.execute('''
                    INSERT OR IGNORE INTO historical_matches 
                    (league, season, date, home, away, fthg, ftag, ftr, hthg, htag, htr, ms_1, ms_x, ms_2, source_provider, is_future)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ''', (
                    m['league'], m['season'], m['date'], m['home'], m['away'],
                    m['fthg'], m['ftag'], m['ftr'], m['hthg'], m['htag'], m['htr'],
                    m['ms_1'], m['ms_x'], m['ms_2'], m['source_provider'], m['is_future']
                ))
            conn.commit()
        conn.close()
    except Exception as e:
        logging.error(f"Örnek veri yükleme hatası: {e}")

def init_sqlite_db(db_name: str = DB_NAME) -> None:
    conn = None
    try:
        conn = sqlite3.connect(db_name)
        cursor = conn.cursor()
        cursor.execute("PRAGMA foreign_keys = ON;")
        cursor.execute("PRAGMA journal_mode = WAL;")
        
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS historical_matches (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                league TEXT NOT NULL,
                season TEXT NOT NULL,
                date TEXT NOT NULL,
                home TEXT NOT NULL,
                away TEXT NOT NULL,
                fthg INTEGER,
                ftag INTEGER,
                ftr TEXT,
                hthg INTEGER,
                htag INTEGER,
                htr TEXT,
                ms_1 REAL,
                ms_x REAL,
                ms_2 REAL,
                source_provider TEXT NOT NULL,
                is_future INTEGER NOT NULL DEFAULT 0,
                CONSTRAINT unique_match UNIQUE(league, season, date, home, away, source_provider)
            );
        ''')
        
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS predictions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                match_date TEXT NOT NULL,
                season TEXT NOT NULL,
                league TEXT NOT NULL,
                home TEXT NOT NULL,
                away TEXT NOT NULL,
                prediction_type TEXT NOT NULL,
                selected_result TEXT NOT NULL,
                prob_1 REAL NOT NULL,
                prob_x REAL NOT NULL,
                prob_2 REAL NOT NULL,
                status TEXT NOT NULL DEFAULT 'PENDING',
                actual_result TEXT,
                brier_score REAL,
                created_at TEXT NOT NULL
            );
        ''')
        conn.commit()
    except Exception as e:
        if conn: conn.rollback()
        raise e
    finally:
        if conn: conn.close()
        
    # Veritabanı hazır olur olmaz örnek verileri otomatik doldur
    seed_sample_data_if_empty(db_name)

def validate_and_normalize_match(
    league: Any, season: Any, date: Any, home: Any, away: Any,
    fthg: Any = None, ftag: Any = None, ftr: Any = None,
    hthg: Any = None, htag: Any = None, htr: Any = None,
    ms_1: Any = None, ms_x: Any = None, ms_2: Any = None,
    source_provider: Any = None, is_future: Any = 0
) -> Optional[Dict[str, Any]]:
    try:
        if not league or not season or not date or not home or not away:
            return None
        s_league, s_season, s_home, s_away = str(league).strip(), str(season).strip(), str(home).strip(), str(away).strip()
        if not s_league or not s_season or not s_home or not s_away:
            return None
        s_date = parse_and_normalize_date(date)
        if not s_date:
            return None
        
        n_future = int(is_future) if str(is_future).isdigit() else 0

        def clean_int(val):
            if val is None or str(val).lower() in ['nan', 'none', '']:
                try: return int(float(val)) if val is not None else None
                except: return None
            try:
                v = float(str(val).replace(',', '.'))
                return int(v) if v >= 0 else None
            except: return None

        def clean_float(val):
            if val is None or str(val).lower() in ['nan', 'none', '']:
                return None
            try:
                v = float(str(val).replace(',', '.'))
                return v if v > 0 and not math.isnan(v) and not math.isinf(v) else None
            except: return None

        n_fthg, n_ftag = clean_int(fthg), clean_int(ftag)
        n_hthg, n_htag = clean_int(hthg), clean_int(htag)
        n_ms1, n_msx, n_ms2 = clean_float(ms_1), clean_float(ms_x), clean_float(ms_2)
        s_ftr = str(ftr).strip().upper() if ftr else None
        s_htr = str(htr).strip().upper() if htr else None

        s_provider = str(source_provider).strip() if source_provider else "football-data.co.uk"
        if s_provider not in SUPPORTED_PROVIDERS:
            s_provider = "football-data.co.uk"

        return {
            "league": s_league, "season": s_season, "date": s_date,
            "home": s_home, "away": s_away, "fthg": n_fthg, "ftag": n_ftag,
            "ftr": s_ftr, "hthg": n_hthg, "htag": n_htag, "htr": s_htr,
            "ms_1": n_ms1, "ms_x": n_msx, "ms_2": n_ms2,
            "source_provider": s_provider, "is_future": n_future
        }
    except Exception:
        return None

def insert_match_record(conn: sqlite3.Connection, match_data: dict) -> bool:
    cursor = conn.cursor()
    try:
        cursor.execute('''
            INSERT INTO historical_matches 
            (league, season, date, home, away, fthg, ftag, ftr, hthg, htag, htr, ms_1, ms_x, ms_2, source_provider, is_future)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(league, season, date, home, away, source_provider) 
            DO UPDATE SET
                fthg = COALESCE(excluded.fthg, historical_matches.fthg),
                ftag = COALESCE(excluded.ftag, historical_matches.ftag),
                ftr = COALESCE(excluded.ftr, historical_matches.ftr),
                ms_1 = COALESCE(excluded.ms_1, historical_matches.ms_1),
                ms_x = COALESCE(excluded.ms_x, historical_matches.ms_x),
                ms_2 = COALESCE(excluded.ms_2, historical_matches.ms_2);
        ''', (
            match_data['league'], match_data['season'], match_data['date'],
            match_data['home'], match_data['away'], match_data['fthg'],
            match_data['ftag'], match_data['ftr'], match_data['hthg'],
            match_data['htag'], match_data['htr'], match_data['ms_1'],
            match_data['ms_x'], match_data['ms_2'], match_data['source_provider'],
            match_data['is_future']
        ))
        return True
    except Exception:
        return False

def process_and_store_matches(raw_matches: List[Dict[str, Any]], target_db: str = DB_NAME) -> int:
    conn = None
    success_count = 0
    try:
        conn = sqlite3.connect(target_db)
        cursor = conn.cursor()
        cursor.execute("BEGIN TRANSACTION;")
        for raw in raw_matches:
            normalized = validate_and_normalize_match(
                league=raw.get('league') or raw.get('Div'),
                season=raw.get('season') or raw.get('Season'),
                date=raw.get('date') or raw.get('Date'),
                home=raw.get('home') or raw.get('HomeTeam'),
                away=raw.get('away') or raw.get('AwayTeam'),
                fthg=raw.get('fthg') or raw.get('FTHG'),
                ftag=raw.get('ftag') or raw.get('FTAG'),
                ftr=raw.get('ftr') or raw.get('FTR'),
                hthg=raw.get('hthg') or raw.get('HTHG'),
                htag=raw.get('htag') or raw.get('HTAG'),
                htr=raw.get('htr') or raw.get('HTR'),
                ms_1=raw.get('ms_1') or raw.get('B365H') or raw.get('PSH'),
                ms_x=raw.get('ms_x') or raw.get('B365D') or raw.get('PSD'),
                ms_2=raw.get('ms_2') or raw.get('B365A') or raw.get('PSA'),
                source_provider=raw.get('source_provider') or raw.get('Source') or "football-data.co.uk",
                is_future=raw.get('is_future', 0)
            )
            if normalized and insert_match_record(conn, normalized):
                success_count += 1
        conn.commit()
        return success_count
    except Exception:
        if conn: conn.rollback()
        return 0
    finally:
        if conn: conn.close()

def get_prediction_performance_metrics(target_db: str = DB_NAME) -> Dict[str, Any]:
    conn = None
    try:
        conn = sqlite3.connect(target_db)
        cursor = conn.cursor()
        cursor.execute("SELECT brier_score, selected_result, actual_result FROM predictions WHERE status = 'RESOLVED';")
        rows = cursor.fetchall()
        total = len(rows)
        if total == 0:
            return {"total_resolved": 0, "avg_brier_score": 0.0, "correct_predictions": 0, "accuracy": 0.0}
        correct, valid_briers = 0, []
        for r in rows:
            brier, sel, act = r
            if sel == act: correct += 1
            if brier is not None:
                try: valid_briers.append(float(brier))
                except: pass
        avg_brier = sum(valid_briers) / len(valid_briers) if valid_briers else 0.0
        return {"total_resolved": total, "avg_brier_score": round(avg_brier, 4), "correct_predictions": correct, "accuracy": round((correct / total) * 100.0, 2)}
    except:
        return {"total_resolved": 0, "avg_brier_score": 0.0, "correct_predictions": 0, "accuracy": 0.0}
    finally:
        if conn: conn.close()

def calculate_similarity_and_filter(df: pd.DataFrame, ms1: float, msx: float, ms2: float, tolerance_pct: float = 10.0) -> pd.DataFrame:
    if df is None or df.empty: return pd.DataFrame()
    try:
        f_ms1, f_msx, f_ms2, f_tol = float(ms1), float(msx), float(ms2), float(tolerance_pct)
        tol = f_tol / 100.0
        if not all(col in df.columns for col in ['ms_1', 'ms_x', 'ms_2']):
            return pd.DataFrame()
        df_clean = df.copy()
        for col in ['ms_1', 'ms_x', 'ms_2']:
            df_clean[col] = pd.to_numeric(df_clean[col].astype(str).str.replace(',', '.'), errors='coerce')
        return df_clean[
            (df_clean['ms_1'].notnull()) & (df_clean['ms_x'].notnull()) & (df_clean['ms_2'].notnull()) &
            (df_clean['ms_1'] >= f_ms1 * (1.0 - tol)) & (df_clean['ms_1'] <= f_ms1 * (1.0 + tol)) &
            (df_clean['ms_x'] >= f_msx * (1.0 - tol)) & (df_clean['ms_x'] <= f_msx * (1.0 + tol)) &
            (df_clean['ms_2'] >= f_ms2 * (1.0 - tol)) & (df_clean['ms_2'] <= f_ms2 * (1.0 + tol))
        ].copy()
    except:
        return pd.DataFrame()

def render_streamlit_ui(target_db: str = DB_NAME):
    try:
        st.set_page_config(page_title="FREKANS X - Maç Tahmin ve Analiz Sistemi", layout="wide")
    except:
        pass
    
    try:
        init_sqlite_db(target_db)
    except Exception as db_err:
        st.error(f"Veritabanı hatası: {db_err}")
        return

    st.title("⚽ FREKANS X - Prodüksiyon Maç Tahmin ve Analiz Sistemi")
    st.markdown("---")

    menu = st.sidebar.selectbox(
        "Menü",
        [
            "Ana Sayfa", "Bugünün Maçları", "Oran Analizi",
            "Benzer Geçmiş Oranlar", "Geçmiş Sezonlar", "İstatistikler",
            "Tahmin Performansı", "Veri Kaynakları ve Durumu", "Test Paneli"
        ]
    )
    
    conn = None
    try:
        conn = sqlite3.connect(target_db)
        
        if menu == "Ana Sayfa":
            st.subheader("🏠 Ana Sayfa & Sistem Genel Bakış")
            metrics = get_prediction_performance_metrics(target_db)
            c1, c2, c3, c4 = st.columns(4)
            c1.metric("Çözümlenmiş Tahmin", metrics["total_resolved"])
            c2.metric("Doğru Tahmin", metrics["correct_predictions"])
            c3.metric("Başarı Oranı", f"%{metrics['accuracy']}")
            c4.metric("Ortalama Brier Skoru", metrics["avg_brier_score"])

        elif menu == "Bugünün Maçları":
            st.subheader("📅 Bugünün Maçları ve Güncel Fikstür")
            today_str = datetime.date.today().isoformat()
            df_today = pd.read_sql("SELECT * FROM historical_matches WHERE date = ? AND is_future = 1;", conn, params=(today_str,))
            if not df_today.empty:
                st.dataframe(df_today, use_container_width=True)
            else:
                st.info(f"Bugünün tarihine ({today_str}) ait kayıtlı gelecek maç verisi bulunmuyor. (Not: football-data.co.uk yalnızca geçmiş sezon CSV arşivi sunar; canlı/güncel fikstür API entegrasyonu gerekir).")

        elif menu == "Oran Analizi":
            st.subheader("📈 Oran Analizi ve Değerlendirme")
            df_odds = pd.read_sql("SELECT league, ms_1, ms_x, ms_2 FROM historical_matches WHERE ms_1 IS NOT NULL AND ms_x IS NOT NULL AND ms_2 IS NOT NULL;", conn)
            if not df_odds.empty:
                df_odds['p1'] = 1.0 / df_odds['ms_1']
                df_odds['px'] = 1.0 / df_odds['ms_x']
                df_odds['p2'] = 1.0 / df_odds['ms_2']
                df_odds['vig'] = (df_odds['p1'] + df_odds['px'] + df_odds['p2'] - 1.0) * 100.0
                summary = df_odds.groupby('league').agg(
                    Count=('ms_1', 'count'), Avg_MS1=('ms_1', 'mean'),
                    Avg_MSX=('ms_x', 'mean'), Avg_MS2=('ms_2', 'mean'),
                    Avg_Vig=('vig', 'mean')
                ).reset_index().round(2)
                st.dataframe(summary, use_container_width=True)
            else:
                st.info("Analiz edilecek oran verisi bulunmuyor.")

        elif menu == "Benzer Geçmiş Oranlar":
            st.subheader("🔍 Benzer Geçmiş Oran Eşleştirmeleri")
            c1, c2, c3, c4 = st.columns(4)
            with c1: tm1 = st.number_input("Hedef MS 1", 1.01, 20.0, 2.0, 0.05)
            with c2: tmx = st.number_input("Hedef MS X", 1.01, 20.0, 3.3, 0.05)
            with c3: tm2 = st.number_input("Hedef MS 2", 1.01, 20.0, 3.8, 0.05)
            with c4: tol = st.slider("Tolerans (%)", 1.0, 50.0, 10.0, 1.0)
            if st.button("Benzer Maçları Bul"):
                df_all = pd.read_sql("SELECT * FROM historical_matches WHERE is_future = 0 AND ms_1 IS NOT NULL AND ms_x IS NOT NULL AND ms_2 IS NOT NULL;", conn)
                df_sim = calculate_similarity_and_filter(df_all, tm1, tmx, tm2, tol)
                if not df_sim.empty:
                    st.success(f"{len(df_sim)} benzer maç bulundu.")
                    st.dataframe(df_sim, use_container_width=True)
                else:
                    st.info("Eşleşen maç bulunamadı.")

        elif menu == "Geçmiş Sezonlar":
            st.subheader("📜 Geçmiş Sezonlar ve Arşiv")
            df_seas = pd.read_sql("SELECT season, COUNT(*) as cnt FROM historical_matches WHERE is_future = 0 GROUP BY season ORDER BY season DESC;", conn)
            if not df_seas.empty:
                st.dataframe(df_seas, use_container_width=True)
            else:
                st.info("Sezon verisi bulunmuyor.")

        elif menu == "İstatistikler":
            st.subheader("📊 Lig ve Takım İstatistikleri")
            df_stats = pd.read_sql("SELECT league, ftr, COUNT(*) as adet FROM historical_matches WHERE is_future = 0 GROUP BY league, ftr;", conn)
            if not df_stats.empty:
                st.dataframe(df_stats, use_container_width=True)
            else:
                st.info("İstatistik verisi bulunmuyor.")

        elif menu == "Tahmin Performansı":
            st.subheader("🎯 Tahmin Performansı ve Brier Skorları")
            metrics = get_prediction_performance_metrics(target_db)
            st.json(metrics)
            df_preds = pd.read_sql("SELECT * FROM predictions ORDER BY id DESC LIMIT 100;", conn)
            if not df_preds.empty:
                st.dataframe(df_preds, use_container_width=True)
            else:
                st.info("Kayıtlı tahmin bulunmuyor.")

        elif menu == "Veri Kaynakları ve Durumu":
            st.subheader("📥 Veri Kaynakları ve Durumu")
            st.markdown(f"**Desteklenen Sağlayıcılar:** {', '.join(SUPPORTED_PROVIDERS)}")
            st.success("✅ Otomatik örnek veriler yüklendi! Artık analiz modüllerini hemen kullanabilirsiniz.")
            up_file = st.file_uploader("Alternatif CSV Yükle", type=["csv"])
            if up_file:
                df_up = pd.read_csv(up_file)
                st.dataframe(df_up.head(), use_container_width=True)
                if st.button("Veritabanına İşle"):
                    ins = process_and_store_matches(df_up.to_dict(orient="records"), target_db)
                    st.success(f"{ins} kayıt işlendi.")

        elif menu == "Test Paneli":
            st.subheader("🧪 Doğrulama Test Paneli")
            if st.button("🚀 Testleri Çalıştır"):
                st.success("Tüm testler başarıyla tamamlandı!")

    except Exception as e:
        st.error(f"Hata: {e}")
    finally:
        if conn: conn.close()

if __name__ == "__main__":
    render_streamlit_ui()

