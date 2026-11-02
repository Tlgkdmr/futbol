# ==============================================================================
# 0. PYTHON IMPORT SIRALAMASI VE STREAMLIT YAPILANDIRMASI
# ==============================================================================
from __future__ import annotations

import streamlit as st

st.set_page_config(
    page_title="FREKANS X – Profesyonel Futbol Analiz Platformu",
    page_icon="⚽",
    layout="wide",
    initial_sidebar_state="expanded",
)

import io
import math
import logging
import sqlite3
import tempfile
import os
from datetime import datetime
from typing import Dict, List, Optional, Tuple

import pandas as pd
import requests

# ==============================================================================
# 0.1 TEMEL LOGLAMA VE GÜVENLİ/ATOMİK SQLITE MIGRATION & ŞEMA YÖNETİMİ
# ==============================================================================
logging.basicConfig(level=logging.INFO)
DB_NAME = "football_cache.db"
SUPPORTED_PROVIDERS = {"Bet365", "Pinnacle", "Ortalama (Avg)"}

def parse_and_normalize_date(date_val) -> Optional[str]:
    """
    DD/MM/YYYY ve YYYY-MM-DD formatlarını dayfirst=True ile destekler.
    Geçersiz veya boş tarihleri None olarak döner.
    """
    if date_val is None or pd.isna(date_val):
        return None
    s = str(date_val).strip()
    if not s or s.lower() in ['nan', 'nat', 'none']:
        return None
    
    try:
        parsed = datetime.strptime(s[:10], '%Y-%m-%d')
        return parsed.strftime('%Y-%m-%d')
    except ValueError:
        pass

    parsed_pd = pd.to_datetime(s, dayfirst=True, errors='coerce')
    if pd.isnull(parsed_pd):
        return None
    return parsed_pd.strftime('%Y-%m-%d')

def validate_and_normalize_match(
    league, season, date_val, home, away, 
    fthg, ftag, ftr, hthg, htag, htr, 
    ms_1, ms_x, ms_2, source_provider
) -> Optional[Dict]:
    """
    Katı ve Güvenli Ortak Doğrulama Motoru:
    - Asla varsayılan/sahte değer atamaz (eksik skor=0, eksik oran=1.0, eksik FTR=D YOK).
    - Desteklenmeyen veya bilinmeyen veri sağlayıcılarını reddeder.
    - Negatif, kesirli, sonsuz skor/oranları reddeder.
    - Devre skorunun maç sonu skorunu aşmasını engeller.
    - FTR ve HTR sonuçlarının gerçek skorlarla uyumunu doğrular.
    """
    if league is None or season is None or date_val is None or home is None or away is None:
        return None
    if fthg is None or ftag is None or ftr is None or hthg is None or htag is None or htr is None:
        return None
    if ms_1 is None or ms_x is None or ms_2 is None or source_provider is None:
        return None
        
    if (pd.isna(league) or pd.isna(season) or pd.isna(date_val) or pd.isna(home) or pd.isna(away) or
        pd.isna(fthg) or pd.isna(ftag) or pd.isna(ftr) or pd.isna(hthg) or pd.isna(htag) or pd.isna(htr) or
        pd.isna(ms_1) or pd.isna(ms_x) or pd.isna(ms_2) or pd.isna(source_provider)):
        return None
        
    lg = str(league).strip()
    seas = str(season).strip()
    hm = str(home).strip()
    aw = str(away).strip()
    sp = str(source_provider).strip()
    
    if not lg or not seas or not hm or not aw or not sp:
        return None
    if hm.lower() in ['nan', 'nat', 'none'] or aw.lower() in ['nan', 'nat', 'none']:
        return None
        
    if sp not in SUPPORTED_PROVIDERS:
        return None
        
    norm_date = parse_and_normalize_date(date_val)
    if not norm_date:
        return None
        
    try:
        fth = float(fthg)
        fta = float(ftag)
        hth = float(hthg)
        hta = float(htag)
        m1 = float(ms_1)
        mx = float(ms_x)
        m2 = float(ms_2)
        
        for v in [fth, fta, hth, hta, m1, mx, m2]:
            if math.isnan(v) or math.isinf(v):
                return None
                
        if fth < 0 or fta < 0 or hth < 0 or hta < 0:
            return None
        if not (fth.is_integer() and fta.is_integer() and hth.is_integer() and hta.is_integer()):
            return None
            
        if hth > fth or hta > fta:
            return None
            
        if m1 <= 0 or mx <= 0 or m2 <= 0:
            return None
            
        f_res = str(ftr).strip().upper()
        h_res = str(htr).strip().upper()
        
        if f_res not in ['H', 'D', 'A'] or h_res not in ['H', 'D', 'A']:
            return None
            
        actual_ftr = 'H' if fth > fta else ('A' if fta > fth else 'D')
        if f_res != actual_ftr:
            return None
            
        actual_htr = 'H' if hth > hta else ('A' if hta > hth else 'D')
        if h_res != actual_htr:
            return None
            
        return {
            'league': lg,
            'season': seas,
            'date': norm_date,
            'home': hm,
            'away': aw,
            'fthg': int(fth),
            'ftag': int(fta),
            'ftr': f_res,
            'hthg': int(hth),
            'htag': int(hta),
            'htr': h_res,
            'ms_1': m1,
            'ms_x': mx,
            'ms_2': m2,
            'source_provider': sp
        }
    except Exception:
        return None

# Test 8 için gerçek rollback enjeksiyon hook'u (Geliştirici kontrolü)
_migration_hook_error: Optional[Exception] = None

def init_sqlite_db(target_db: str = DB_NAME):
    """
    Güvenli Staging SQLite Migration Sistemi:
    - Eksik sütun, fazladan sütun ve yanlış primary key yapısını eksiksiz tespit eder.
    - Tablo yapısı doğru olsa bile içindeki bozuk kayıtları tarar ve eler.
    - Kaynak tablo kontrolünde hem 15 sütun sayısı hem de exact sütun isimleri eşleşmesini doğrular.
    - ROLLBACK sonrasında önceden var olan historical_matches_staging_new tablosu kesinlikle DROP edilmez, korunur.
    """
    conn = None
    try:
        conn = sqlite3.connect(target_db)
        cursor = conn.cursor()
        
        expected_columns = [
            'league', 'season', 'date', 'home', 'away', 
            'fthg', 'ftag', 'ftr', 'hthg', 'htag', 'htr', 
            'ms_1', 'ms_x', 'ms_2', 'source_provider'
        ]
        expected_pk_list = ['league', 'season', 'date', 'home', 'away', 'source_provider']
        
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='historical_matches'")
        table_exists = cursor.fetchone() is not None
        
        if not table_exists:
            cursor.execute('''
                CREATE TABLE historical_matches (
                    league TEXT,
                    season TEXT,
                    date TEXT,
                    home TEXT,
                    away TEXT,
                    fthg REAL,
                    ftag REAL,
                    ftr TEXT,
                    hthg REAL,
                    htag REAL,
                    htr TEXT,
                    ms_1 REAL,
                    ms_x REAL,
                    ms_2 REAL,
                    source_provider TEXT,
                    PRIMARY KEY (league, season, date, home, away, source_provider)
                )
            ''')
            conn.commit()
        else:
            cursor.execute("PRAGMA table_info(historical_matches)")
            columns_info = cursor.fetchall()
            existing_cols = [col[1] for col in columns_info]
            pk_columns = [col[1] for col in columns_info if col[5] > 0]
            
            missing_cols = [c for c in expected_columns if c not in existing_cols]
            extra_cols = [c for c in existing_cols if c not in expected_columns]
            is_pk_correct = pk_columns == expected_pk_list
            
            cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name IN ('historical_matches_new', 'historical_matches_staging')")
            partial_tables = [row[0] for row in cursor.fetchall()]
            
            has_corrupt_data = False
            if not missing_cols and not extra_cols and is_pk_correct and not partial_tables:
                cursor.execute("SELECT * FROM historical_matches")
                all_old_rows = cursor.fetchall()
                for row in all_old_rows:
                    row_dict = {existing_cols[i]: row[i] for i in range(len(existing_cols))}
                    if validate_and_normalize_match(
                        row_dict.get('league'), row_dict.get('season'), row_dict.get('date'),
                        row_dict.get('home'), row_dict.get('away'), row_dict.get('fthg'),
                        row_dict.get('ftag'), row_dict.get('ftr'), row_dict.get('hthg'),
                        row_dict.get('htag'), row_dict.get('htr'), row_dict.get('ms_1'),
                        row_dict.get('ms_x'), row_dict.get('ms_2'), row_dict.get('source_provider')
                    ) is None:
                        has_corrupt_data = True
                        break

            if missing_cols or extra_cols or not is_pk_correct or partial_tables or has_corrupt_data:
                logging.info("SQLite şema uyumsuzluğu veya bozuk kayıt tespit edildi. Güvenli staging migration başlatılıyor...")
                
                # 1. Önce verileri güvenle oku ve doğrula
                tables_to_harvest = ['historical_matches'] + partial_tables
                seen = set()
                tables_to_harvest = [x for x in tables_to_harvest if not (x in seen or seen.add(x))]
                
                harvested_records = []
                invalid_skipped_count = 0
                
                for t_name in tables_to_harvest:
                    cursor.execute(f"SELECT name FROM sqlite_master WHERE type='table' AND name='{t_name}'")
                    if not cursor.fetchone():
                        continue
                        
                    cursor.execute(f"PRAGMA table_info({t_name})")
                    t_cols = [c[1] for c in cursor.fetchall()]
                    
                    if not (len(t_cols) == 15 and set(t_cols) == set(expected_columns)):
                        continue
                        
                    cursor.execute(f"SELECT * FROM {t_name}")
                    rows = cursor.fetchall()
                    
                    for r in rows:
                        if len(r) != len(t_cols):
                            continue
                        r_dict = {t_cols[i]: r[i] for i in range(len(t_cols))}
                        norm = validate_and_normalize_match(
                            r_dict.get('league'), r_dict.get('season'), r_dict.get('date'),
                            r_dict.get('home'), r_dict.get('away'), r_dict.get('fthg'),
                            r_dict.get('ftag'), r_dict.get('ftr'), r_dict.get('hthg'),
                            r_dict.get('htag'), r_dict.get('htr'), r_dict.get('ms_1'),
                            r_dict.get('ms_x'), r_dict.get('ms_2'), r_dict.get('source_provider')
                        )
                        if norm is None:
                            invalid_skipped_count += 1
                            continue
                        harvested_records.append(norm)

                # Migration öncesi target new tablonun önceden var olup olmadığını kontrol edelim
                cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='historical_matches_staging_new'")
                staging_new_existed_before = cursor.fetchone() is not None

                cursor.execute("BEGIN TRANSACTION;")
                try:
                    if not staging_new_existed_before:
                        cursor.execute("DROP TABLE IF EXISTS historical_matches_staging_new;")
                    
                    cursor.execute('''
                        CREATE TABLE IF NOT EXISTS historical_matches_staging_new (
                            league TEXT,
                            season TEXT,
                            date TEXT,
                            home TEXT,
                            away TEXT,
                            fthg REAL,
                            ftag REAL,
                            ftr TEXT,
                            hthg REAL,
                            htag REAL,
                            htr TEXT,
                            ms_1 REAL,
                            ms_x REAL,
                            ms_2 REAL,
                            source_provider TEXT,
                            PRIMARY KEY (league, season, date, home, away, source_provider)
                        )
                    ''')
                    
                    migrated_count = 0
                    for norm in harvested_records:
                        cursor.execute('''
                            INSERT OR IGNORE INTO historical_matches_staging_new 
                            (league, season, date, home, away, fthg, ftag, ftr, hthg, htag, htr, ms_1, ms_x, ms_2, source_provider)
                            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        ''', (
                            norm['league'], norm['season'], norm['date'], norm['home'], norm['away'],
                            norm['fthg'], norm['ftag'], norm['ftr'], norm['hthg'], norm['htag'], norm['htr'],
                            norm['ms_1'], norm['ms_x'], norm['ms_2'], norm['source_provider']
                        ))
                        if cursor.rowcount > 0:
                            migrated_count += 1

                    global _migration_hook_error
                    if _migration_hook_error is not None:
                        raise _migration_hook_error
                            
                    cursor.execute("PRAGMA table_info(historical_matches_staging_new)")
                    st_info = cursor.fetchall()
                    st_cols = [c[1] for c in st_info]
                    st_pk = [c[1] for c in st_info if c[5] > 0]
                    
                    if len(st_cols) != 15 or st_pk != expected_pk_list:
                        raise Exception("Staging şema veya Primary Key doğrulaması başarısız!")
                        
                    for t_name in tables_to_harvest:
                        cursor.execute(f"DROP TABLE IF EXISTS {t_name};")
                        
                    cursor.execute("ALTER TABLE historical_matches_staging_new RENAME TO historical_matches;")
                    conn.commit()
                    logging.info(f"Migration başarılı. Taşınan: {migrated_count}, Geçersiz Atlanan: {invalid_skipped_count}")
                except Exception as inner_e:
                    cursor.execute("ROLLBACK;")
                    # 1. ROLLBACK sonrasında önceden var olan historical_matches_staging_new tablosu kesinlikle silinmez (korunur).
                    if not staging_new_existed_before:
                        cursor.execute("DROP TABLE IF EXISTS historical_matches_staging_new;")
                    logging.error(f"Migration hatası, ROLLBACK uygulandı: {inner_e}")
                    raise inner_e
    except Exception as e:
        logging.critical(f"Kritik SQLite Migration Hatası: {e}")
        if target_db == DB_NAME:
            st.error(f"Veritabanı şema güncellemesi sırasında hata oluştu: {e}")
            st.stop()
        else:
            raise e
    finally:
        if conn:
            conn.close()

init_sqlite_db()

# ==============================================================================
# 1. TEMA VE STİL YAPILANDIRMASI (GLASSMORPHISM & MOBİL UYUMLU)
# ==============================================================================
st.markdown("""
    <style>
    .stApp {
        background-color: #0b0e14;
        color: #e2e8f0;
        font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
    }
    [data-testid="stSidebar"] {
        background-color: #111622 !important;
        border-right: 1px solid rgba(255, 255, 255, 0.08);
    }
    .glass-card {
        background: rgba(22, 30, 46, 0.65);
        backdrop-filter: blur(12px);
        -webkit-backdrop-filter: blur(12px);
        border: 1px solid rgba(255, 255, 255, 0.08);
        border-radius: 12px;
        padding: 16px;
        margin-bottom: 12px;
        box-shadow: 0 8px 32px 0 rgba(0, 0, 0, 0.37);
    }
    .gradient-text {
        background: linear-gradient(135deg, #00f2fe 0%, #4facfe 50%, #00c6ff 100%);
        -webkit-background-clip: text;
        -webkit-text-fill-color: transparent;
        font-weight: 800;
    }
    .metric-box {
        background: rgba(30, 41, 59, 0.5);
        border: 1px solid rgba(56, 189, 248, 0.2);
        border-radius: 10px;
        padding: 10px;
        text-align: center;
    }
    .metric-value {
        font-size: 1.3rem;
        font-weight: 700;
        color: #38bdf8;
    }
    .metric-label {
        font-size: 0.75rem;
        color: #94a3b8;
        text-transform: uppercase;
        letter-spacing: 0.05em;
    }
    .dataframe {
        color: #f8fafc !important;
        background-color: #0f172a !important;
        font-size: 0.85rem !important;
    }
    </style>
""", unsafe_allow_html=True)

# ==============================================================================
# 2. VERİ YAPISI VE GÜVENLİ İSTEK / İZOLE SQLITE YÖNETİMİ
# ==============================================================================
LEAGUES = {
    "Türkiye Süper Lig": {"code": "T1", "country": "Turkey"},
    "İngiltere Premier League": {"code": "E0", "country": "England"},
    "İngiltere Championship": {"code": "E1", "country": "England"},
    "İspanya La Liga": {"code": "SP1", "country": "Spain"},
    "İspanya Segunda": {"code": "SP2", "country": "Spain"},
    "İtalya Serie A": {"code": "I1", "country": "Italy"},
    "İtalya Serie B": {"code": "I2", "country": "Italy"},
    "Almanya Bundesliga": {"code": "D1", "country": "Germany"},
    "Almanya 2. Bundesliga": {"code": "D2", "country": "Germany"},
    "Fransa Ligue 1": {"code": "F1", "country": "France"},
    "Hollanda Eredivisie": {"code": "N1", "country": "Netherlands"},
    "Portekiz Primeira Liga": {"code": "P1", "country": "Portugal"},
    "Belçika Pro League": {"code": "B1", "country": "Belgium"}
}

SEASONS = ["2122", "2223", "2324", "2425", "2526", "2627"]

def load_from_sqlite(league_name: str, season_str: str, odds_provider: str, target_db: str = DB_NAME) -> pd.DataFrame:
    conn = None
    try:
        conn = sqlite3.connect(target_db)
        query = """
            SELECT league, season, date, home, away, fthg, ftag, ftr, hthg, htag, htr, ms_1, ms_x, ms_2, source_provider 
            FROM historical_matches 
            WHERE league = ? AND season = ? AND source_provider = ?
        """
        df = pd.read_sql(query, conn, params=(league_name, season_str, odds_provider))
        if not df.empty:
            df = df.rename(columns={
                'date': 'Date', 'home': 'Home', 'away': 'Away',
                'fthg': 'FTHG', 'ftag': 'FTAG', 'ftr': 'FTR',
                'hthg': 'HTHG', 'htag': 'HTAG', 'htr': 'HTR',
                'ms_1': 'MS_1', 'ms_x': 'MS_X', 'ms_2': 'MS_2',
                'source_provider': 'Source'
            })
            return df
    except Exception as e:
        logging.error(f"SQLite okuma hatası ({league_name} - {season_str} - {odds_provider}): {e}")
    finally:
        if conn:
            conn.close()
    return pd.DataFrame()

def save_to_sqlite(df: pd.DataFrame, league_name: str, season_str: str, odds_provider: str, target_db: str = DB_NAME):
    """
    Kayıt Motoru:
    - Desteklenmeyen sağlayıcıları reddeder.
    - Hata durumunda rollback yapar ve hatayı tekrar yükseltir (asla kısmi başarı gizlemez).
    - Yinelenen (mükerrer) kayıtları ve işlem bütünlüğünü tam doğrular.
    """
    if df.empty:
        return {'new_added': 0, 'already_exists': 0, 'invalid_skipped': 0}
        
    if odds_provider not in SUPPORTED_PROVIDERS:
        raise ValueError(f"Desteklenmeyen oran sağlayıcı: {odds_provider}")
        
    conn = None
    new_added = 0
    already_exists = 0
    invalid_skipped = 0
    
    try:
        conn = sqlite3.connect(target_db)
        cursor = conn.cursor()
        cursor.execute("BEGIN TRANSACTION;")
        
        for _, row in df.iterrows():
            norm = validate_and_normalize_match(
                league_name, season_str,
                row.get('Date'), row.get('Home'), row.get('Away'),
                row.get('FTHG'), row.get('FTAG'), row.get('FTR'),
                row.get('HTHG'), row.get('HTAG'), row.get('HTR'),
                row.get('MS_1'), row.get('MS_X'), row.get('MS_2'),
                odds_provider
            )
            
            if norm is None:
                invalid_skipped += 1
                continue
                
            cursor.execute('''
                SELECT 1 FROM historical_matches 
                WHERE league = ? AND season = ? AND date = ? AND home = ? AND away = ? AND source_provider = ?
            ''', (
                norm['league'], norm['season'], norm['date'],
                norm['home'], norm['away'], norm['source_provider']
            ))
            exists = cursor.fetchone()
            
            if exists:
                already_exists += 1
            else:
                cursor.execute('''
                    INSERT OR IGNORE INTO historical_matches 
                    (league, season, date, home, away, fthg, ftag, ftr, hthg, htag, htr, ms_1, ms_x, ms_2, source_provider)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ''', (
                    norm['league'], norm['season'], norm['date'], norm['home'], norm['away'],
                    norm['fthg'], norm['ftag'], norm['ftr'], norm['hthg'], norm['htag'], norm['htr'],
                    norm['ms_1'], norm['ms_x'], norm['ms_2'], norm['source_provider']
                ))
                if cursor.rowcount > 0:
                    new_added += 1
                else:
                    already_exists += 1
                    
        conn.commit()
        return {'new_added': new_added, 'already_exists': already_exists, 'invalid_skipped': invalid_skipped}
    except Exception as e:
        if conn:
            conn.rollback()
        logging.error(f"SQLite kayıt işleminde hata, ROLLBACK yapıldı: {e}")
        raise e
    finally:
        if conn:
            conn.close()

# ==============================================================================
# 3. VERİ TEMİZLİĞİ VE DOĞRULAMA MOTORU
# ==============================================================================
def get_clean_valid_matches(df: pd.DataFrame) -> pd.DataFrame:
    """
    Eksik metadata içeren veya varsayılan atanan kayıtları kesinlikle kabul etmez.
    """
    if df.empty:
        return df.copy()
        
    valid_rows = []
    for _, row in df.iterrows():
        norm = validate_and_normalize_match(
            row.get('League'),
            row.get('Season'),
            row.get('Date'),
            row.get('Home'),
            row.get('Away'),
            row.get('FTHG'),
            row.get('FTAG'),
            row.get('FTR'),
            row.get('HTHG'),
            row.get('HTAG'),
            row.get('HTR'),
            row.get('MS_1'),
            row.get('MS_X'),
            row.get('MS_2'),
            row.get('Source')
        )
        if norm:
            valid_rows.append({
                'League': norm['league'],
                'Season': norm['season'],
                'Date': norm['date'],
                'Home': norm['home'],
                'Away': norm['away'],
                'FTHG': norm['fthg'],
                'FTAG': norm['ftag'],
                'FTR': norm['ftr'],
                'HTHG': norm['hthg'],
                'HTAG': norm['htag'],
                'HTR': norm['htr'],
                'MS_1': norm['ms_1'],
                'MS_X': norm['ms_x'],
                'MS_2': norm['ms_2'],
                'Source': norm['source_provider']
            })
            
    return pd.DataFrame(valid_rows)

@st.cache_data(ttl=86400, show_spinner=False)
def fetch_single_league_season(league_code: str, season_code: str, league_name: str, odds_pref: str = "Bet365") -> Tuple[pd.DataFrame, str]:
    if odds_pref not in SUPPORTED_PROVIDERS:
        return pd.DataFrame(), f"unsupported_provider_{odds_pref}"
        
    season_str = f"20{season_code[:2]}-20{season_code[2:]}"
    
    cached_df = load_from_sqlite(league_name, season_str, odds_pref)
    if not cached_df.empty:
        cleaned_cached = get_clean_valid_matches(cached_df)
        if not cleaned_cached.empty:
            return cleaned_cached, "success"

    url = f"https://www.football-data.co.uk/mmz4281/{season_code}/{league_code}.csv"
    try:
        res = requests.get(url, timeout=6)
        if res.status_code == 404:
            return pd.DataFrame(), "not_published"
        if res.status_code != 200:
            return pd.DataFrame(), f"HTTP {res.status_code}"
            
        df = pd.read_csv(io.StringIO(res.text), on_bad_lines='skip', encoding_errors='replace')
        if df.empty:
            return pd.DataFrame(), "empty_data"
            
        if odds_pref == "Pinnacle":
            h_col, x_col, a_col = 'PSH', 'PSD', 'PSA'
        elif odds_pref == "Ortalama (Avg)":
            h_col, x_col, a_col = 'AvgH', 'AvgD', 'AvgA'
        elif odds_pref == "Bet365":
            h_col, x_col, a_col = 'B365H', 'B365D', 'B365A'
        else:
            return pd.DataFrame(), f"invalid_provider_{odds_pref}"
            
        if h_col not in df.columns or x_col not in df.columns or a_col not in df.columns:
            return pd.DataFrame(), f"missing_provider_columns_{odds_pref}"
            
        df['League'] = league_name
        df['Season'] = season_str
        df['Source'] = odds_pref
        
        col_map = {h_col: 'MS_1', x_col: 'MS_X', a_col: 'MS_2'}
        for c in df.columns:
            clean_l = c.strip().lower()
            if clean_l == 'date': col_map[c] = 'Date'
            elif clean_l == 'hometeam': col_map[c] = 'Home'
            elif clean_l == 'awayteam': col_map[c] = 'Away'
            elif clean_l == 'fthg': col_map[c] = 'FTHG'
            elif clean_l == 'ftag': col_map[c] = 'FTAG'
            elif clean_l == 'ftr': col_map[c] = 'FTR'
            elif clean_l == 'hthg': col_map[c] = 'HTHG'
            elif clean_l == 'htag': col_map[c] = 'HTAG'
            elif clean_l == 'htr': col_map[c] = 'HTR'
            
        df = df.rename(columns=col_map)
        
        cleaned_df = get_clean_valid_matches(df)
        if cleaned_df.empty:
            return pd.DataFrame(), "empty_valid_data"
            
        if not cleaned_df.empty:
            save_to_sqlite(cleaned_df, league_name, season_str, odds_pref)
            
        return cleaned_df, "success"
    except Exception as e:
        logging.error(f"Veri çekme hatası ({league_name} - {season_code}): {e}")
        return pd.DataFrame(), str(e)

def fetch_selected_dataset(selected_leagues: List[str], selected_seasons: List[str], odds_pref: str = "Bet365") -> Tuple[pd.DataFrame, Dict[str, str]]:
    frames = []
    status_report = {}
    for lg in selected_leagues:
        code = LEAGUES[lg]["code"]
        for s in selected_seasons:
            sub_df, status = fetch_single_league_season(code, s, lg, odds_pref)
            status_report[f"{lg} ({s})"] = status
            if not sub_df.empty:
                frames.append(sub_df)
    if not frames:
        return pd.DataFrame(), status_report
    return pd.concat(frames, ignore_index=True), status_report

# ==============================================================================
# 4. ANALİZ MOTORU & YÜZDE BAZLI MESAFE BENZERLİK HESABI
# ==============================================================================
def calculate_similarity_and_filter(df: pd.DataFrame, ms1: float, msx: float, ms2: float, tolerance_pct: float) -> pd.DataFrame:
    if df.empty or ms1 <= 0 or msx <= 0 or ms2 <= 0:
        return pd.DataFrame()
        
    d = df.copy()
    
    required_odds = ['MS_1', 'MS_X', 'MS_2']
    for col in required_odds:
        if col not in d.columns:
            return pd.DataFrame()
        d[col] = pd.to_numeric(d[col], errors='coerce')
        
    d = d.dropna(subset=required_odds)
    d = d[(d['MS_1'] > 0) & (d['MS_X'] > 0) & (d['MS_2'] > 0)].copy()
    if d.empty:
        return d
        
    tol = tolerance_pct / 100.0
    cond_1 = (d['MS_1'] >= ms1 * (1 - tol)) & (d['MS_1'] <= ms1 * (1 + tol))
    cond_x = (d['MS_X'] >= msx * (1 - tol)) & (d['MS_X'] <= msx * (1 + tol))
    cond_2 = (d['MS_2'] >= ms2 * (1 - tol)) & (d['MS_2'] <= ms2 * (1 + tol))
    
    filtered = d[cond_1 & cond_x & cond_2].copy()
    if filtered.empty:
        return filtered
        
    def calc_percentage_distance_score(row):
        diff_1 = abs(row['MS_1'] - ms1) / ms1
        diff_x = abs(row['MS_X'] - msx) / msx
        diff_2 = abs(row['MS_2'] - ms2) / ms2
        avg_deviation = (diff_1 + diff_x + diff_2) / 3.0
        score = max(0.0, 100.0 - (avg_deviation * 100.0 * 2.0))
        return round(score, 1)
        
    filtered['Geçmiş Oran Benzerliği %'] = filtered.apply(calc_percentage_distance_score, axis=1)
    if 'Date' in filtered.columns:
        filtered = filtered.sort_values(by=['Geçmiş Oran Benzerliği %', 'Date'], ascending=[False, False])
    else:
        filtered = filtered.sort_values(by='Geçmiş Oran Benzerliği %', ascending=False)
        
    return filtered

# ==============================================================================
# 5. ARAYÜZ VE 8+1 MENÜ YAPILANDIRMASI
# ==============================================================================
st.markdown("<h1 class='gradient-text'>FREKANS X</h1>", unsafe_allow_html=True)
st.markdown("<p style='color: #94a3b8; font-size: 1rem; margin-top: -15px;'>Profesyonel Futbol Veri & Oran Analiz Platformu</p>", unsafe_allow_html=True)

st.sidebar.markdown("### 📌 MENÜ")
menu_choice = st.sidebar.radio(
    "Modül Seçin:",
    [
        "Ana Sayfa",
        "Bugünün Maçları",
        "Oran Analizi",
        "Benzer Geçmiş Oranlar",
        "Geçmiş Sezonlar",
        "İstatistikler",
        "Tahmin Performansı",
        "Veri Kaynakları ve Durumu",
        "Test Paneli (Geliştirici Modülü)"
    ]
)

st.sidebar.markdown("---")
st.sidebar.markdown("### ⚙️ FİLTRELER & AYARLAR")
odds_provider = st.sidebar.selectbox("Oran Sağlayıcı Kaynağı:", ["Bet365", "Pinnacle", "Ortalama (Avg)"])
sel_leagues = st.sidebar.multiselect(
    "Aktif Ligler:",
    options=list(LEAGUES.keys()),
    default=["Türkiye Süper Lig", "İngiltere Premier League"]
)
sel_seasons = st.sidebar.multiselect(
    "Aktif Sezonlar:",
    options=SEASONS,
    default=["2324", "2425", "2526"]
)
global_tolerance = st.sidebar.select_slider(
    "Oran Toleransı (%)",
    options=[3.0, 5.0, 8.0, 10.0, 15.0, 20.0],
    value=5.0
)

# ==============================================================================
# MODÜL 1: ANA SAYFA
# ==============================================================================
if menu_choice == "Ana Sayfa":
    st.markdown("<div class='glass-card'>", unsafe_allow_html=True)
    st.markdown("### 📊 Platform Durum Özeti")
    col1, col2, col3 = st.columns(3)
    with col1:
        st.markdown(f"<div class='metric-box'><div class='metric-value'>{len(sel_leagues)}</div><div class='metric-label'>Seçili Lig</div></div>", unsafe_allow_html=True)
    with col2:
        st.markdown(f"<div class='metric-box'><div class='metric-value'>{len(sel_seasons)}</div><div class='metric-label'>Seçili Sezon</div></div>", unsafe_allow_html=True)
    with col3:
        st.markdown(f"<div class='metric-box'><div class='metric-value'>{odds_provider}</div><div class='metric-label'>Aktif Sağlayıcı</div></div>", unsafe_allow_html=True)
    st.markdown("</div>", unsafe_allow_html=True)
    st.info("💡 Bilgilendirme: Bu platform, geçmiş maç oranlarını arşiv verileriyle karşılaştıran istatistiksel bir analiz aracıdır. Kesin kazanma garantisi veya yatırım tavsiyesi sunmaz.")
    st.markdown("""
    > ⚠️ **Kalıcı Depolama Uyarısı:** Streamlit Cloud sunucuları geçici (ephemeral) ortamlardır; uygulama yeniden başlatıldığında yerel dosya sistemindeki `football_cache.db` dosyası sıfırlanabilir. Verilerin kalıcı olması için veritabanının harici bir bulut veritabanına bağlanması önerilir.
    """)

# ==============================================================================
# MODÜL 2: BUGÜNÜN MAÇLARI
# ==============================================================================
elif menu_choice == "Bugünün Maçları":
    st.markdown("### 📅 Günün Maçları ve Oran Girişi")
    st.warning("⚠️ Bu uygulamada bu platformlara bağlı doğrulanmış/resmi bir veri bağlantısı yapılandırılmamıştır. Sistemde otomatik canlı güncel maç fikstürü veya oranı çekilmemektedir; maç uydurulmaz.")
    
    st.markdown("#### Yasal Platform Bağlantı Durumu")
    platforms = ["Nesine", "Misli", "Bilyoner", "Tuttur", "Birebin"]
    p_cols = st.columns(len(platforms))
    for idx, p in enumerate(platforms):
        with p_cols[idx]:
            st.markdown(f"<div class='metric-box'><div class='metric-value' style='font-size:0.8rem; color:#f59e0b;'>Bağlantı Yok</div><div class='metric-label'>{p}</div></div>", unsafe_allow_html=True)
            
    st.markdown("---")
    st.markdown("#### ⚽ Manuel Maç Oranı Analiz Paneli")
    st.caption("Güncel bir maçın oranlarını analiz etmek için aşağıdaki alanları manuel doldurabilirsiniz.")
    
    c1, c2, c3 = st.columns(3)
    with c1:
        m_home = st.text_input("Ev Sahibi Takım", value="")
    with c2:
        m_away = st.text_input("Deplasman Takım", value="")
    with c3:
        m_league = st.selectbox("Lig Seçiniz", list(LEAGUES.keys()))
        
    oc1, oc2, oc3 = st.columns(3)
    with oc1:
        rate1 = st.number_input("MS 1 Oranı", value=0.0, step=0.05, format="%.2f")
    with oc2:
        ratex = st.number_input("MS X Oranı", value=0.0, step=0.05, format="%.2f")
    with oc3:
        rate2 = st.number_input("MS 2 Oranı", value=0.0, step=0.05, format="%.2f")
        
    if st.button("Arşivi Tara ve Benzer Maçları Bul"):
        if rate1 <= 0 or ratex <= 0 or rate2 <= 0:
            st.error("Lütfen geçerli maç oranları giriniz (0'dan büyük olmalıdır).")
        elif not sel_leagues or not sel_seasons:
            st.error("Lütfen sol menüden en az bir lig ve sezon seçiniz.")
        else:
            with st.spinner("Seçili veriler taranıyor..."):
                db_df, _ = fetch_selected_dataset(sel_leagues, sel_seasons, odds_provider)
                clean_db = get_clean_valid_matches(db_df)
                res = calculate_similarity_and_filter(clean_db, rate1, ratex, rate2, global_tolerance)
                if res.empty:
                    st.info("Belirtilen tolerans aralığında geçmiş benzer oranlı maç bulunamadı.")
                else:
                    st.success(f"Toplam {len(res)} benzer geçmiş maç bulundu.")
                    st.markdown("> ℹ️ **Not:** Listelenen benzerlik yüzdesi **kesin kazanma olasılığı değildir**, yalnızca geçmiş oranlar arasındaki matematiksel sapma mesafesini gösterir.")
                    st.dataframe(res[['Date', 'Season', 'League', 'Home', 'Away', 'MS_1', 'MS_X', 'MS_2', 'HTHG', 'HTAG', 'FTHG', 'FTAG', 'FTR', 'Geçmiş Oran Benzerliği %', 'Source']], use_container_width=True)

# ==============================================================================
# MODÜL 3 & 4: ORAN ANALİZİ VE BENZER GEÇMİŞ ORANLAR
# ==============================================================================
elif menu_choice in ["Oran Analizi", "Benzer Geçmiş Oranlar"]:
    st.markdown("### 🔍 Oran Toleransı ile Geçmiş Maç Taraması")
    col1, col2, col3 = st.columns(3)
    with col1:
        in_ms1 = st.number_input("Hedef MS 1 Oranı", value=2.10, step=0.01, format="%.2f")
    with col2:
        in_msx = st.number_input("Hedef MS X Oranı", value=3.25, step=0.01, format="%.2f")
    with col3:
        in_ms2 = st.number_input("Hedef MS 2 Oranı", value=3.40, step=0.01, format="%.2f")
        
    top_n = st.selectbox("Gösterilecek Karşılaşma Sayısı:", [20, 50, 100], index=1)

    if st.button("Arşivi Tara ve Detaylı İstatistik Çıkar"):
        if not sel_leagues or not sel_seasons:
            st.error("Lütfen sol menüden en az bir lig ve sezon seçiniz.")
        else:
            with st.spinner(f"[{odds_provider}] kaynaklı veriler yükleniyor..."):
                db_df, _ = fetch_selected_dataset(sel_leagues, sel_seasons, odds_provider)
                clean_db = get_clean_valid_matches(db_df)
                res = calculate_similarity_and_filter(clean_db, in_ms1, in_msx, in_ms2, global_tolerance)
                
                if res.empty:
                    st.warning("Seçilen tolerans seviyesinde eşleşen maç bulunamadı. Tolerans oranını artırmayı deneyebilirsiniz.")
                else:
                    total_matched = len(res)
                    if total_matched < 15:
                        st.warning(f"⚠️ Örneklem Uyarısı: Eşleşen maç sayısı az ({total_matched} adet). İstatistiksel güvenilirlik düşük olabilir.")
                        
                    st.markdown(f"#### 📈 Bulunan Eşleşmeler ({total_matched} Maç - İlk {top_n} Gösteriliyor)")
                    st.markdown("> ℹ️ **Önemli Hatırlatma:** Benzerlik yüzdesi kazanma olasılığı veya tahmini başarı oranı değildir; oranların birbirine olan yakınlık derecesini ifade eder.")
                    
                    disp = res.head(top_n)[['Date', 'Season', 'League', 'Home', 'Away', 'MS_1', 'MS_X', 'MS_2', 'HTHG', 'HTAG', 'FTHG', 'FTAG', 'FTR', 'Geçmiş Oran Benzerliği %', 'Source']]
                    disp.columns = ['Tarih', 'Sezon', 'Lig', 'Ev Sahibi', 'Deplasman', 'MS1', 'MSX', 'MS2', 'İY Ev', 'İY Dep', 'MS Ev', 'MS Dep', 'Sonuç', 'Benzerlik %', 'Kaynak']
                    st.dataframe(disp, use_container_width=True)

                    st.markdown("### 📊 Gerçekleşen Detaylı İstatistikler ve Örneklem Dağılımı")
                    
                    sc1, sc2, sc3 = st.columns(3)
                    
                    with sc1:
                        st.markdown("**Maç Sonu (MS) Sonuç Sayıları**")
                        ftr_counts = res['FTR'].value_counts()
                        ftr_norm = res['FTR'].value_counts(normalize=True) * 100
                        st.write(f"• MS 1 (H): {ftr_counts.get('H', 0)} maç (%{ftr_norm.get('H', 0):.1f})")
                        st.write(f"• MS X (D): {ftr_counts.get('D', 0)} maç (%{ftr_norm.get('D', 0):.1f})")
                        st.write(f"• MS 2 (A): {ftr_counts.get('A', 0)} maç (%{ftr_norm.get('A', 0):.1f})")
                        st.caption(f"Hesaplanan Maç Sayısı: {len(res)}")
                            
                        st.markdown("**İlk Yarı (İY) Sonuç Sayıları**")
                        htr_counts = res['HTR'].value_counts()
                        htr_norm = res['HTR'].value_counts(normalize=True) * 100
                        st.write(f"• İY 1 (H): {htr_counts.get('H', 0)} maç (%{htr_norm.get('H', 0):.1f})")
                        st.write(f"• İY X (D): {htr_counts.get('D', 0)} maç (%{htr_norm.get('D', 0):.1f})")
                        st.write(f"• İY 2 (A): {htr_counts.get('A', 0)} maç (%{htr_norm.get('A', 0):.1f})")
                        st.caption(f"Hesaplanan Maç Sayısı: {len(res)}")

                    with sc2:
                        st.markdown("**Alt / Üst Detaylı Dağılım**")
                        res['Total_Goals'] = res['FTHG'] + res['FTAG']
                        
                        c_o15 = int((res['Total_Goals'] > 1.5).sum())
                        c_u15 = int((res['Total_Goals'] <= 1.5).sum())
                        p_o15 = (c_o15 / len(res)) * 100
                        p_u15 = (c_u15 / len(res)) * 100
                        
                        c_o25 = int((res['Total_Goals'] > 2.5).sum())
                        c_u25 = int((res['Total_Goals'] <= 2.5).sum())
                        p_o25 = (c_o25 / len(res)) * 100
                        p_u25 = (c_u25 / len(res)) * 100
                        
                        c_o35 = int((res['Total_Goals'] > 3.5).sum())
                        c_u35 = int((res['Total_Goals'] <= 3.5).sum())
                        p_o35 = (c_o35 / len(res)) * 100
                        p_u35 = (c_u35 / len(res)) * 100
                        
                        st.write(f"• 1.5 Üst: {c_o15} maç (%{p_o15:.1f}) | Alt: {c_u15} maç (%{p_u15:.1f})")
                        st.write(f"• 2.5 Üst: {c_o25} maç (%{p_o25:.1f}) | Alt: {c_u25} maç (%{p_u25:.1f})")
                        st.write(f"• 3.5 Üst: {c_o35} maç (%{p_o35:.1f}) | Alt: {c_u35} maç (%{p_u35:.1f})")
                        st.caption(f"Hesaplanan Maç Sayısı: {len(res)}")
                            
                        st.markdown("**Gol Ortalamaları (Aynı Satır Doğrulamalı)**")
                        res['HT_Goals'] = res['HTHG'] + res['HTAG']
                        res['2H_Goals'] = (res['FTHG'] - res['HTHG']) + (res['FTAG'] - res['HTAG'])
                        
                        ht_avg = res['HT_Goals'].mean()
                        sh_avg = res['2H_Goals'].mean()
                        ft_avg = res['Total_Goals'].mean()
                        sample_cnt = len(res)
                        
                        st.write(f"• İlk Yarı Gol Ort: **{ht_avg:.2f}** ({sample_cnt} maç)")
                        st.write(f"• İkinci Yarı Gol Ort: **{sh_avg:.2f}** ({sample_cnt} maç)")
                        st.write(f"• Maç Toplam Gol Ort: **{ft_avg:.2f}** ({sample_cnt} maç)")

                    with sc3:
                        st.markdown("**Karşılıklı Gol (KG Var / Yok)**")
                        res['BTTS_Yes'] = (res['FTHG'] > 0) & (res['FTAG'] > 0)
                        btts_yes_count = int(res['BTTS_Yes'].sum())
                        btts_no_count = int(len(res) - btts_yes_count)
                        btts_yes_pct = (btts_yes_count / len(res)) * 100
                        btts_no_pct = (btts_no_count / len(res)) * 100
                        
                        st.write(f"• KG Var: {btts_yes_count} maç (%{btts_yes_pct:.1f})")
                        st.write(f"• KG Yok: {btts_no_count} maç (%{btts_no_pct:.1f})")
                        st.caption(f"Hesaplanan Maç Sayısı: {len(res)}")
                            
                        st.markdown("**Popüler İY/MS Kombinasyonları**")
                        res['IY_MS'] = res['HTR'] + "/" + res['FTR']
                        iyms_counts = res['IY_MS'].value_counts()
                        iyms_top = iyms_counts.head(3)
                        for combo, count in iyms_top.items():
                            pct = (count / len(res)) * 100
                            st.write(f"• {combo}: {count} maç (%{pct:.1f})")
                        st.caption(f"Hesaplanan Maç Sayısı: {len(res)}")

# ==============================================================================
# MODÜL 5: GEÇMİŞ SEZONLAR
# ==============================================================================
elif menu_choice == "Geçmiş Sezonlar":
    st.markdown("### 📚 Sezon Filtreli Veri Arşivi İnceleme")
    if not sel_leagues or not sel_seasons:
        st.error("Lütfen sol panelden lig ve sezon seçimi yapınız.")
    else:
        with st.spinner("Seçili veriler yükleniyor..."):
            db_df, _ = fetch_selected_dataset(sel_leagues, sel_seasons, odds_provider)
            filtered_db = get_clean_valid_matches(db_df)
            if filtered_db.empty:
                st.warning("Seçilen kriterlere uygun temiz veri bulunamadı veya ilgili sezon dosyası henüz yayınlanmamış.")
            else:
                st.markdown(f"Filtrelere Uyan Toplam **{len(filtered_db):,}** Geçerli Maç Bulundu.")
                st.dataframe(filtered_db[['Season', 'League', 'Date', 'Home', 'Away', 'MS_1', 'MS_X', 'MS_2', 'HTHG', 'HTAG', 'FTHG', 'FTAG', 'FTR', 'Source']], use_container_width=True)

# ==============================================================================
# MODÜL 6: İSTATİSTİKLER (GENEL İSTATİSTİK RAPORU)
# ==============================================================================
elif menu_choice == "İstatistikler":
    st.markdown("### 📉 Gelişmiş Arşiv İstatistik Raporu")
    with st.spinner("İstatistikler derleniyor..."):
        db_df, _ = fetch_selected_dataset(sel_leagues, sel_seasons, odds_provider)
        valid_stats = get_clean_valid_matches(db_df)
        
        if valid_stats.empty:
            st.warning("İstatistik oluşturmak için geçerli ve temiz veri bulunamadı.")
        else:
            st.markdown(f"#### Seçilen Arşiv Boyutu: Toplam {len(valid_stats):,} Geçerli Maç İnceleniyor")
            
            c1, c2 = st.columns(2)
            with c1:
                st.markdown("#### Maç Sonu (MS) Sonuç Sayıları")
                st.bar_chart(valid_stats['FTR'].value_counts())
                st.caption(f"Gerçek Maç Sayısı: {len(valid_stats)}")
                
                st.markdown("#### İlk Yarı (İY) Sonuç Sayıları")
                st.bar_chart(valid_stats['HTR'].value_counts())
                st.caption(f"Gerçek Maç Sayısı: {len(valid_stats)}")
                
                st.markdown("#### Popüler İY/MS Kombinasyonları")
                valid_stats['IY_MS'] = valid_stats['HTR'] + "/" + valid_stats['FTR']
                iyms_gen_counts = valid_stats['IY_MS'].value_counts().head(5)
                for combo, count in iyms_gen_counts.items():
                    pct = (count / len(valid_stats)) * 100
                    st.write(f"• {combo}: {count} maç (%{pct:.1f})")
                    
            with c2:
                st.markdown("#### Liglere Göre Maç Dağılımı")
                st.bar_chart(valid_stats['League'].value_counts())
                
                st.markdown("#### Detaylı Gol, Alt/Üst ve KG Oranları")
                valid_stats['Total_FT'] = valid_stats['FTHG'] + valid_stats['FTAG']
                valid_stats['Total_HT'] = valid_stats['HTHG'] + valid_stats['HTAG']
                valid_stats['Total_2H'] = (valid_stats['FTHG'] - valid_stats['HTHG']) + (valid_stats['FTAG'] - valid_stats['HTAG'])
                
                ft_avg = valid_stats['Total_FT'].mean()
                ht_avg = valid_stats['Total_HT'].mean()
                sh_avg = valid_stats['Total_2H'].mean()
                sample_all = len(valid_stats)
                
                o15 = (valid_stats['Total_FT'] > 1.5).mean() * 100
                o25 = (valid_stats['Total_FT'] > 2.5).mean() * 100
                o35 = (valid_stats['Total_FT'] > 3.5).mean() * 100
                btts_gen = ((valid_stats['FTHG'] > 0) & (valid_stats['FTAG'] > 0)).mean() * 100
                btts_no_gen = 100.0 - btts_gen
                
                st.write(f"• Maç Toplam Gol Ortalaması: **{ft_avg:.2f}** ({sample_all} maç)")
                st.write(f"• İlk Yarı Gol Ortalaması: **{ht_avg:.2f}** ({sample_all} maç)")
                st.write(f"• İkinci Yarı Gol Ortalaması: **{sh_avg:.2f}** ({sample_all} maç)")
                st.write(f"• 1.5 Üst Oranı: **%{o15:.1f}** | Alt: **%{100-o15:.1f}**")
                st.write(f"• 2.5 Üst Oranı: **%{o25:.1f}** | Alt: **%{100-o25:.1f}**")
                st.write(f"• 3.5 Üst Oranı: **%{o35:.1f}** | Alt: **%{100-o35:.1f}**")
                st.write(f"• Karşılıklı Gol Var: **%{btts_gen:.1f}** | KG Yok: **%{btts_no_gen:.1f}**")

# ==============================================================================
# MODÜL 7: TAHMİN PERFORMANSI
# ==============================================================================
elif menu_choice == "Tahmin Performansı":
    st.markdown("### 🎯 Tahmin Geçmişi ve Performans Analizi")
    st.info("⚠️ Gerçekleştirilmiş kayıtlı tahmin verisi veya Brier skoru ölçümü için veritabanında onaylanmış geçmiş tahmin kaydı bulunmamaktadır. Uydurma başarı yüzdesi gösterilmez.")
    
    st.markdown("<div class='glass-card'>", unsafe_allow_html=True)
    st.write("• Kayıtlı Tahmin Sayısı: 0")
    st.write("• Sonuçlanan Kupon / Maç: Bulunmuyor")
    st.write("• Brier Skoru / Ortalama Hata: Ölçülemedi (Veri Yetersiz)")
    st.markdown("</div>", unsafe_allow_html=True)

# ==============================================================================
# MODÜL 8: VERİ KAYNAKLARI VE DURUMU
# ==============================================================================
elif menu_choice == "Veri Kaynakları ve Durumu":
    st.markdown("### 🌐 Veri Kaynakları ve Sağlayıcı Durumu")
    st.markdown(f"""
    * **Ana Veri Sağlayıcı:** Football-Data.co.uk (Geçmiş maç arşivi)
    * **Seçilen Aktif Oran Sağlayıcı:** `{odds_provider}` (Veriler diğer sağlayıcılarla kesinlikle karıştırılmaz).
    * **Yerel Önbellek:** SQLite veritabanı (`football_cache.db`) ile tekrar eden indirmeler engellenmektedir.
    * **Kalıcı Depolama Uyarısı:** Streamlit sunucularının geçici yapısı nedeniyle uygulama yeniden başlatıldığında yerel SQLite veritabanı sıfırlanabilir. Bulut kalıcılığı için harici veritabanı entegrasyonu gereklidir.
    * **Yasal Platformlar:** Bu uygulamada bu platformlara bağlı doğrulanmış/resmi bir veri bağlantısı yapılandırılmamıştır.
    """)
    
    st.markdown("#### Lig, Sezon ve Sağlayıcı Bazlı Gerçek Durum Raporu")
    if sel_leagues and sel_seasons:
        _, report = fetch_selected_dataset(sel_leagues, sel_seasons, odds_provider)
        rep_data = []
        for k, v in report.items():
            rep_data.append({"Kriter / Sağlayıcı Kombinasyonu": k, "Gerçek Yanıt / Durum": v})
        st.table(rep_data)
    else:
        st.info("Durum raporunu görmek için lütfen sol menüden lig ve sezon seçiniz.")

# ==============================================================================
# MODÜL 9: TEST PANELİ (tempfile.TemporaryDirectory İle İzole Geliştirici Modülü)
# ==============================================================================
elif menu_choice == "Test Paneli (Geliştirici Modülü)":
    st.markdown("### 🧪 İzole Test ve Otomatik Doğrulama Paneli")
    st.info("Bu panel; eksik sütun, fazla sütun, yanlış PK, boş tablo, bozuk kayıt, yinelenen kayıt, yarım kalmış staging tablosu ve gerçek migration rollback testlerini tamamen izole tempfile.TemporaryDirectory() ortamında assertion kontrolleriyle çalıştırır.")
    
    if st.button("Tüm Test Senaryolarını Otomatik Assert ile Çalıştır"):
        test_success = False
        test_results_log = []
        try:
            with tempfile.TemporaryDirectory() as tmpdir:
                test_db_path = os.path.join(tmpdir, "test_football_cache.db")
                
                expected_cols = [
                    'league', 'season', 'date', 'home', 'away', 
                    'fthg', 'ftag', 'ftr', 'hthg', 'htag', 'htr', 
                    'ms_1', 'ms_x', 'ms_2', 'source_provider'
                ]
                expected_pk = ['league', 'season', 'date', 'home', 'away', 'source_provider']

                # --- TEST 1: Eksik Sütunlu Eski Tablo ---
                conn = sqlite3.connect(test_db_path)
                cur = conn.cursor()
                cur.execute("CREATE TABLE historical_matches (league TEXT, season TEXT, date TEXT)")
                cur.execute("INSERT INTO historical_matches VALUES ('TestLig', '2324', '2023-08-15')")
                conn.commit()
                conn.close()

                init_sqlite_db(target_db=test_db_path)

                conn = sqlite3.connect(test_db_path)
                cur = conn.cursor()
                cur.execute("PRAGMA table_info(historical_matches)")
                info = cur.fetchall()
                cols = [c[1] for c in info]
                pk = [c[1] for c in info if c[5] > 0]
                conn.close()

                assert cols == expected_cols, f"Test 1 Sütunlar hatalı: {cols}"
                assert pk == expected_pk, f"Test 1 PK hatalı: {pk}"
                test_results_log.append("✅ Test 1 (Eksik Sütunlu Eski Tablo): BAŞARILI")
                os.remove(test_db_path)

                # --- TEST 2: Fazla Sütunlu Eski Tablo ---
                conn = sqlite3.connect(test_db_path)
                cur = conn.cursor()
                cur.execute("""
                    CREATE TABLE historical_matches (
                        league TEXT, season TEXT, date TEXT, home TEXT, away TEXT,
                        fthg REAL, ftag REAL, ftr TEXT, hthg REAL, htag REAL, htr TEXT,
                        ms_1 REAL, ms_x REAL, ms_2 REAL, source_provider TEXT,
                        extra_col TEXT
                    )
                """)
                cur.execute("INSERT INTO historical_matches VALUES ('TestLig', '2324', '2023-08-15', 'GS', 'FB', 2, 1, 'H', 1, 0, 'H', 1.8, 3.4, 4.2, 'Bet365', 'FazlaData')")
                conn.commit()
                conn.close()

                init_sqlite_db(target_db=test_db_path)

                conn = sqlite3.connect(test_db_path)
                cur = conn.cursor()
                cur.execute("PRAGMA table_info(historical_matches)")
                info = cur.fetchall()
                cols = [c[1] for c in info]
                pk = [c[1] for c in info if c[5] > 0]
                cur.execute("SELECT COUNT(*) FROM historical_matches")
                rc = cur.fetchone()[0]
                conn.close()

                assert cols == expected_cols, f"Test 2 Sütunlar hatalı: {cols}"
                assert pk == expected_pk, f"Test 2 PK hatalı: {pk}"
                assert rc == 1, f"Test 2 Kayıt sayısı hatalı: {rc}"
                test_results_log.append("✅ Test 2 (Fazla Sütunlu Eski Tablo): BAŞARILI")
                os.remove(test_db_path)

                # --- TEST 3: Yanlış PK ---
                conn = sqlite3.connect(test_db_path)
                cur = conn.cursor()
                cur.execute("""
                    CREATE TABLE historical_matches (
                        league TEXT PRIMARY KEY, season TEXT, date TEXT, home TEXT, away TEXT,
                        fthg REAL, ftag REAL, ftr TEXT, hthg REAL, htag REAL, htr TEXT,
                        ms_1 REAL, ms_x REAL, ms_2 REAL, source_provider TEXT
                    )
                """)
                cur.execute("INSERT INTO historical_matches VALUES ('TestLig', '2324', '2023-08-15', 'GS', 'FB', 2, 1, 'H', 1, 0, 'H', 1.8, 3.4, 4.2, 'Bet365')")
                conn.commit()
                conn.close()

                init_sqlite_db(target_db=test_db_path)

                conn = sqlite3.connect(test_db_path)
                cur = conn.cursor()
                cur.execute("PRAGMA table_info(historical_matches)")
                info = cur.fetchall()
                pk = [c[1] for c in info if c[5] > 0]
                conn.close()

                assert pk == expected_pk, f"Test 3 PK hatalı: {pk}"
                test_results_log.append("✅ Test 3 (Yanlış PK): BAŞARILI")
                os.remove(test_db_path)

                # --- TEST 4: Boş Tablo ---
                conn = sqlite3.connect(test_db_path)
                cur = conn.cursor()
                cur.execute("CREATE TABLE historical_matches (league TEXT)")
                conn.commit()
                conn.close()

                init_sqlite_db(target_db=test_db_path)

                conn = sqlite3.connect(test_db_path)
                cur = conn.cursor()
                cur.execute("SELECT COUNT(*) FROM historical_matches")
                cnt = cur.fetchone()[0]
                conn.close()

                assert cnt == 0, "Test 4 Boş tablo hatası"
                test_results_log.append("✅ Test 4 (Boş Tablo Migration): BAŞARILI")
                os.remove(test_db_path)

                # --- TEST 5: Bozuk Kayıt Filtreleme ---
                conn = sqlite3.connect(test_db_path)
                cur = conn.cursor()
                cur.execute("""
                    CREATE TABLE historical_matches (
                        league TEXT, season TEXT, date TEXT, home TEXT, away TEXT,
                        fthg REAL, ftag REAL, ftr TEXT, hthg REAL, htag REAL, htr TEXT,
                        ms_1 REAL, ms_x REAL, ms_2 REAL, source_provider TEXT,
                        PRIMARY KEY (league, season, date, home, away, source_provider)
                    )
                """)
                cur.execute("INSERT INTO historical_matches VALUES ('Lig', '2324', '2023-08-15', 'GS', 'FB', 2, 1, 'H', 1, 0, 'H', 1.8, 3.4, 4.2, 'Bet365')")
                cur.execute("INSERT INTO historical_matches VALUES ('Lig', '2324', '2023-08-16', 'BJK', 'TS', -1, 1, 'A', 0, 1, 'A', 2.1, 3.2, 3.1, 'Bilinmeyen')")
                conn.commit()
                conn.close()

                init_sqlite_db(target_db=test_db_path)

                conn = sqlite3.connect(test_db_path)
                cur = conn.cursor()
                cur.execute("SELECT COUNT(*) FROM historical_matches")
                cnt = cur.fetchone()[0]
                conn.close()

                assert cnt == 1, f"Test 5 Bozuk kayıt filtrelenemedi: {cnt}"
                test_results_log.append("✅ Test 5 (Bozuk Kayıt Filtreleme): BAŞARILI")
                os.remove(test_db_path)

                # --- TEST 6: Yinelenen Kayıt ve Transaction Bütünlüğü ---
                init_sqlite_db(target_db=test_db_path)
                df_dup = pd.DataFrame([
                    {"Date": "2023-08-15", "Home": "GS", "Away": "FB", "FTHG": 2, "FTAG": 1, "FTR": "H", "HTHG": 1, "HTAG": 0, "HTR": "H", "MS_1": 1.8, "MS_X": 3.4, "MS_2": 4.2},
                    {"Date": "2023-08-15", "Home": "GS", "Away": "FB", "FTHG": 2, "FTAG": 1, "FTR": "H", "HTHG": 1, "HTAG": 0, "HTR": "H", "MS_1": 1.8, "MS_X": 3.4, "MS_2": 4.2}
                ])
                rep = save_to_sqlite(df_dup, "Lig", "2324", "Bet365", target_db=test_db_path)
                assert rep['new_added'] == 1, f"Test 6 Yeni eklenen: {rep['new_added']}"
                assert rep['already_exists'] == 1, f"Test 6 Yinelenen: {rep['already_exists']}"
                test_results_log.append("✅ Test 6 (Yinelenen Kayıt Sayımı): BAŞARILI")
                os.remove(test_db_path)

                # --- TEST 7: Yarım Kalmış Staging Tablosu (PK Olmayan 15 Sütunlu) ve Tablo Yapısı Doğrulaması ---
                conn = sqlite3.connect(test_db_path)
                cur = conn.cursor()
                cur.execute("CREATE TABLE historical_matches (league TEXT)")
                cur.execute("CREATE TABLE historical_matches_staging (league TEXT, season TEXT, date TEXT, home TEXT, away TEXT, fthg REAL, ftag REAL, ftr TEXT, hthg REAL, htag REAL, htr TEXT, ms_1 REAL, ms_x REAL, ms_2 REAL, source_provider TEXT)")
                cur.execute("INSERT INTO historical_matches_staging VALUES ('Lig', '2324', '2023-08-15', 'GS', 'FB', 2, 1, 'H', 1, 0, 'H', 1.8, 3.4, 4.2, 'Bet365')")
                cur.execute("CREATE TABLE irrelevant_table (data TEXT)")
                cur.execute("INSERT INTO irrelevant_table VALUES ('ignore_me')")
                conn.commit()
                conn.close()

                init_sqlite_db(target_db=test_db_path)

                conn = sqlite3.connect(test_db_path)
                cur = conn.cursor()
                cur.execute("SELECT COUNT(*) FROM historical_matches")
                cnt = cur.fetchone()[0]
                cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='irrelevant_table'")
                irr_exists = cur.fetchone() is not None
                cur.execute("PRAGMA table_info(historical_matches)")
                info = cur.fetchall()
                pk = [c[1] for c in info if c[5] > 0]
                conn.close()

                assert cnt == 1, f"Test 7 Staging veri alınamadı: {cnt}"
                assert irr_exists, "Test 7 İlgisiz tablo silinmemeli"
                assert pk == expected_pk, f"Test 7 Hedef tablonun PK yapısı doğru olmalı: {pk}"
                test_results_log.append("✅ Test 7 (Yarım Staging Tablosu ve Tablo Yapısı Doğrulaması): BAŞARILI")
                os.remove(test_db_path)

                # --- TEST 8: Genişletilmiş Gerçek Transaction Rollback Testi (Staging & Staging_New Koruması) ---
                conn = sqlite3.connect(test_db_path)
                cur = conn.cursor()
                # 1. Eski ana tablo (fazladan sütunlu)
                cur.execute("""
                    CREATE TABLE historical_matches (
                        league TEXT, season TEXT, date TEXT, home TEXT, away TEXT,
                        fthg REAL, ftag REAL, ftr TEXT, hthg REAL, htag REAL, htr TEXT,
                        ms_1 REAL, ms_x REAL, ms_2 REAL, source_provider TEXT,
                        legacy_extra TEXT,
                        PRIMARY KEY (league, season, date, home, away, source_provider)
                    )
                """)
                cur.execute("INSERT INTO historical_matches VALUES ('OldLig', '2324', '2023-08-15', 'GS', 'FB', 2, 1, 'H', 1, 0, 'H', 1.8, 3.4, 4.2, 'Bet365', 'ExtraVal')")
                
                # 2. Önceden var olan historical_matches_staging tablosu ve kaydı
                cur.execute("CREATE TABLE historical_matches_staging (league TEXT, season TEXT, date TEXT, home TEXT, away TEXT, fthg REAL, ftag REAL, ftr TEXT, hthg REAL, htag REAL, htr TEXT, ms_1 REAL, ms_x REAL, ms_2 REAL, source_provider TEXT)")
                cur.execute("INSERT INTO historical_matches_staging VALUES ('StgLig', '2324', '2023-08-15', 'BJK', 'TS', 1, 1, 'D', 1, 1, 'D', 2.0, 3.0, 3.0, 'Bet365')")
                
                # 3. Önceden var olan historical_matches_staging_new tablosu ve kaydı
                cur.execute("CREATE TABLE historical_matches_staging_new (league TEXT, season TEXT, date TEXT, home TEXT, away TEXT, fthg REAL, ftag REAL, ftr TEXT, hthg REAL, htag REAL, htr TEXT, ms_1 REAL, ms_x REAL, ms_2 REAL, source_provider TEXT)")
                cur.execute("INSERT INTO historical_matches_staging_new VALUES ('StgNewLig', '2324', '2023-08-15', 'TS', 'BJK', 0, 0, 'D', 0, 0, 'D', 2.5, 3.1, 2.8, 'Bet365')")
                conn.commit()
                conn.close()

                global _migration_hook_error
                _migration_hook_error = RuntimeError("TEST: Genişletilmiş gerçek transaction rollback tetiklendi")
                rollback_occurred = False

                try:
                    init_sqlite_db(target_db=test_db_path)
                except RuntimeError as re:
                    if "TEST: Genişletilmiş gerçek transaction rollback tetiklendi" in str(re):
                        rollback_occurred = True
                    else:
                        raise
                finally:
                    _migration_hook_error = None

                assert rollback_occurred, "Test 8: Gerçek migration hatasında rollback gerçekleşmedi"

                conn = sqlite3.connect(test_db_path)
                cur = conn.cursor()
                
                # Eski ana tablo şema ve kayıt doğrulaması
                cur.execute("PRAGMA table_info(historical_matches)")
                info = cur.fetchall()
                cols = [c[1] for c in info]
                pk = [c[1] for c in info if c[5] > 0]
                cur.execute("SELECT league, season, date, home, away, legacy_extra FROM historical_matches")
                row_data = cur.fetchone()

                # Önceden var olan historical_matches_staging tablosu ve kaydı doğrulaması
                cur.execute("PRAGMA table_info(historical_matches_staging)")
                stg_info = cur.fetchall()
                stg_cols = [c[1] for c in stg_info]
                cur.execute("SELECT league, home FROM historical_matches_staging")
                stg_row = cur.fetchone()

                # Önceden var olan historical_matches_staging_new tablosu ve kaydı doğrulaması
                cur.execute("PRAGMA table_info(historical_matches_staging_new)")
                stg_new_info = cur.fetchall()
                stg_new_cols = [c[1] for c in stg_new_info]
                cur.execute("SELECT league, home FROM historical_matches_staging_new")
                stg_new_row = cur.fetchone()
                
                conn.close()

                # Assertions
                assert 'legacy_extra' in cols, f"Test 8 Rollback sonrası eski ana şema (legacy_extra) korunamadı: {cols}"
                assert pk == expected_pk, f"Test 8 Rollback sonrası eski Primary Key korunmalı: {pk}"
                assert row_data == ('OldLig', '2324', '2023-08-15', 'GS', 'FB', 'ExtraVal'), f"Test 8 Ana kayıtlar korunamadı: {row_data}"
                
                assert len(stg_cols) == 15, "Test 8 Staging şeması korunamadı"
                assert stg_row == ('StgLig', 'BJK'), f"Test 8 Staging kayıt korunamadı: {stg_row}"

                assert len(stg_new_cols) == 15, "Test 8 Staging_new şeması korunamadı"
                assert stg_new_row == ('StgNewLig', 'TS'), f"Test 8 Staging_new kayıt korunamadı: {stg_new_row}"

                test_results_log.append("✅ Test 8 (Genişletilmiş Gerçek Transaction Rollback ve Staging/Staging_New/Ana Tablo Koruması): BAŞARILI")

            test_success = True
            st.success("🎉 Tüm test senaryoları (Test 1-8) `tempfile.TemporaryDirectory()` içinde başarıyla tamamlandı!")
            for log in test_results_log:
                st.write(log)
        except AssertionError as ae:
            test_success = False
            st.error(f"❌ Test Doğrulama Hatası (Assert Başarısız): {ae}")
        except Exception as e:
            test_success = False
            st.error(f"❌ Beklenmeyen Test Hatası: {e}")

