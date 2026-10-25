# -*- coding: utf-8 -*-
"""
Futbol geçmiş maç / oran analizi — yalnızca yerel SQLite veritabanı.
Çalıştırma: streamlit run app.py
Bu uygulama dış API çağrısı yapmaz ve veritabanına maç indirmez.
"""
from pathlib import Path
import sqlite3
import math
import numpy as np
import pandas as pd
import streamlit as st

st.set_page_config(page_title="Futbol — Yerel Maç ve Oran Analizi", layout="wide")

APP_DIR = Path(__file__).resolve().parent
DB_CANDIDATES = [
    APP_DIR / "data" / "frekans_x.db",
    APP_DIR / "data" / "matches.db",
    APP_DIR / "frekans_x.db",
    APP_DIR / "matches.db",
    APP_DIR / "data.db",
]

ALIASES = {
    "league": ["league", "lig", "competition", "country_league"],
    "season": ["season", "sezon"],
    "date": ["date", "match_date", "fixture_date", "tarih"],
    "home_team": ["home_team", "hometeam", "home", "ev_sahibi", "ev_takimi"],
    "away_team": ["away_team", "awayteam", "away", "deplasman", "deplasman_takimi"],
    "ft_home_goals": ["ft_home_goals", "fthg", "home_goals", "home_score", "ev_gol", "ev_skor"],
    "ft_away_goals": ["ft_away_goals", "ftag", "away_goals", "away_score", "dep_gol", "dep_skor"],
    "ft_result": ["ft_result", "ftr", "result", "full_time_result", "ms_sonuc"],
    "odds_h": ["odds_h", "oddsh", "b365h", "psh", "home_odds", "ms1", "oran1"],
    "odds_d": ["odds_d", "oddsd", "b365d", "psd", "draw_odds", "msx", "oranx"],
    "odds_a": ["odds_a", "oddsa", "b365a", "psa", "away_odds", "ms2", "oran2"],
    "odds_over25": ["odds_over25", "b365>2.5", "b365o25", "over25_odds"],
    "odds_under25": ["odds_under25", "b365<2.5", "b365u25", "under25_odds"],
}

def norm_col(s):
    return str(s).strip().lower().replace(" ", "_").replace("-", "_")

def find_db():
    for path in DB_CANDIDATES:
        if path.exists():
            return path
    # Avoid silently opening/creating a blank database; let user know candidates.
    return None

def read_matches(db_path):
    with sqlite3.connect(str(db_path)) as conn:
        tables = pd.read_sql_query(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name", conn
        )["name"].tolist()
        if "matches" not in tables:
            raise ValueError("Veritabanında 'matches' tablosu bulunamadı. Bulunan tablolar: " + (", ".join(tables) if tables else "hiç tablo yok"))
        return pd.read_sql_query('SELECT * FROM "matches"', conn)

def resolve_columns(df):
    lookup = {norm_col(c): c for c in df.columns}
    out = {}
    for standard, options in ALIASES.items():
        for option in options:
            key = norm_col(option)
            if key in lookup:
                out[standard] = lookup[key]
                break
    return out

def numeric(series):
    return pd.to_numeric(series, errors="coerce")

def add_standard_columns(df):
    cols = resolve_columns(df)
    result = df.copy()
    for standard, original in cols.items():
        if standard != original:
            result[standard] = result[original]
    for c in ["ft_home_goals", "ft_away_goals", "odds_h", "odds_d", "odds_a", "odds_over25", "odds_under25"]:
        if c in result.columns:
            result[c] = numeric(result[c])
    if "date" in result.columns:
        result["date"] = result["date"].astype(str)
    if "ft_result" not in result.columns and {"ft_home_goals", "ft_away_goals"}.issubset(result.columns):
        h, a = result["ft_home_goals"], result["ft_away_goals"]
        result["ft_result"] = np.where(h > a, "H", np.where(h < a, "A", np.where(h == a, "D", None)))
    return result, cols

def has_results(df):
    return {"ft_home_goals", "ft_away_goals"}.issubset(df.columns) and df[["ft_home_goals", "ft_away_goals"]].notna().any(axis=None)

def odds_similarity(target, row):
    vals = []
    for key in ("odds_h", "odds_d", "odds_a"):
        x = target.get(key)
        y = row.get(key)
        if pd.notna(x) and pd.notna(y) and float(x) > 1 and float(y) > 1:
            # Log odds distance is less distorted by large odds.
            vals.append(abs(math.log(float(x)) - math.log(float(y))))
        else:
            return 0.0
    # Score ranges 0..1; 1 means same odds. Tuned for a readable ranking, not a probability.
    distance = sum(vals) / len(vals)
    return float(math.exp(-2.2 * distance))

def result_label(value):
    return {"H": "MS 1", "D": "MS X", "A": "MS 2", "1": "MS 1", "X": "MS X", "2": "MS 2"}.get(str(value).upper(), str(value))

def result_distribution(matches):
    if "ft_result" not in matches.columns:
        return pd.DataFrame()
    res = matches["ft_result"].astype(str).str.upper().map(
        lambda x: {"1": "MS 1", "H": "MS 1", "X": "MS X", "D": "MS X", "2": "MS 2", "A": "MS 2"}.get(x, None)
    ).dropna()
    if res.empty:
        return pd.DataFrame()
    counts = res.value_counts().reindex(["MS 1", "MS X", "MS 2"], fill_value=0)
    return pd.DataFrame({"Sonuç": counts.index, "Maç": counts.values, "Oran (%)": (counts.values / counts.sum() * 100).round(1)})

def safe_float(value):
    try:
        x = float(value)
        return x if math.isfinite(x) else np.nan
    except (TypeError, ValueError):
        return np.nan

st.title("⚽ Futbol — Yerel Geçmiş Maç ve Oran Analizi")
st.caption("Bu sürüm dış API çağırmaz. Yalnızca seçtiğiniz yerel SQLite veritabanındaki kayıtları okur; gelecekteki fikstürü otomatik çekmez.")

db_path = find_db()
with st.sidebar:
    st.header("Menü")
    menu = st.radio("Bölüm seçin", [
        "Veritabanı durumu",
        "Oran benzerliği ve olasılık",
        "Takım geçmişi",
        "Oran filtresi",
        "Anomali kontrolü",
    ])
    st.divider()
    st.caption("Veritabanı konumu")
    st.code(str(db_path) if db_path else "Veritabanı bulunamadı")

if db_path is None:
    st.error("SQLite veritabanı bulunamadı. Bu app.py dosyasının yanında `data/frekans_x.db` dosyası veya proje kökünde `frekans_x.db` bulunmalı.")
    st.info("GitHub deposunda SQLite veritabanı dosyası yoksa uygulama geçmiş maç gösteremez. Bu uygulama dışarıdan veri indirmez.")
    st.stop()

try:
    raw = read_matches(db_path)
except Exception as exc:
    st.error(f"Veritabanı okunamadı: {exc}")
    st.stop()

df, mapped_cols = add_standard_columns(raw)
if df.empty:
    st.warning("matches tablosu var ancak içinde kayıt yok.")
    st.stop()

required_names = ["home_team", "away_team"]
missing_names = [c for c in required_names if c not in df.columns]
if missing_names:
    st.warning("Takım sütunları otomatik tanınamadı. Mevcut sütunlar: " + ", ".join(map(str, raw.columns)))
    st.stop()

# Clean standard fields without dropping original columns.
for c in ["league", "season", "date", "home_team", "away_team", "ft_result"]:
    if c in df.columns:
        df[c] = df[c].replace({"None": np.nan, "nan": np.nan, "": np.nan})

if menu == "Veritabanı durumu":
    st.subheader("Veritabanı ve veri kapsamı")
    c1, c2, c3 = st.columns(3)
    c1.metric("Toplam kayıt", f"{len(df):,}")
    c2.metric("Lig sayısı", str(df["league"].nunique()) if "league" in df.columns else "Bilinmiyor")
    c3.metric("Skoru olan maç", f"{int(df[['ft_home_goals','ft_away_goals']].dropna().shape[0]):,}" if {"ft_home_goals","ft_away_goals"}.issubset(df.columns) else "Skor sütunu yok")
    if "date" in df.columns:
        dates = pd.to_datetime(df["date"], errors="coerce", dayfirst=True)
        if dates.notna().any():
            st.write(f"Veri tarih aralığı: **{dates.min().date()} – {dates.max().date()}**")
            if dates.max().date() < pd.Timestamp.today().date():
                st.warning("Veritabanındaki en yeni tarih geçmişte. Bu uygulama yeni fikstür indirmediği için yalnızca kayıtlı maçları analiz eder.")
    st.write("Tanımlanan sütun eşleşmeleri")
    st.json({k: str(v) for k, v in mapped_cols.items()})
    st.write("Örnek kayıtlar")
    show_cols = [c for c in ["date","league","season","home_team","away_team","ft_home_goals","ft_away_goals","ft_result","odds_h","odds_d","odds_a"] if c in df.columns]
    st.dataframe(df[show_cols].head(100), use_container_width=True)
    with st.expander("Veritabanındaki tüm sütun adları"):
        st.write(list(raw.columns))

elif menu == "Oran benzerliği ve olasılık":
    st.subheader("Benzer geçmiş oran kombinasyonları")
    if not {"odds_h", "odds_d", "odds_a"}.issubset(df.columns):
        st.error("MS 1 / MS X / MS 2 oran sütunları bulunamadı. Veritabanı sütunları bu analizi desteklemiyor.")
        st.write("Mevcut sütunlar:", list(raw.columns))
        st.stop()
    st.write("Aşağıdaki oranları girin. Sistem sadece veritabanındaki geçmiş maçları karşılaştırır.")
    c1, c2, c3 = st.columns(3)
    with c1: target_h = st.number_input("MS 1 oranı", min_value=1.01, value=2.10, step=0.05)
    with c2: target_d = st.number_input("MS X oranı", min_value=1.01, value=3.40, step=0.05)
    with c3: target_a = st.number_input("MS 2 oranı", min_value=1.01, value=3.50, step=0.05)
    leagues = sorted(df["league"].dropna().astype(str).unique()) if "league" in df.columns else []
    league_choice = st.selectbox("Lig filtresi", ["Tüm ligler"] + leagues)
    min_sim = st.slider("En düşük benzerlik", min_value=0.30, max_value=0.95, value=0.70, step=0.05)
    if st.button("Benzer maçları analiz et", type="primary"):
        work = df.copy()
        if league_choice != "Tüm ligler" and "league" in work.columns:
            work = work[work["league"].astype(str) == league_choice]
        valid = work[["odds_h","odds_d","odds_a"]].notna().all(axis=1)
        valid &= (work[["odds_h","odds_d","odds_a"]] > 1).all(axis=1)
        work = work[valid].copy()
        target = {"odds_h": target_h, "odds_d": target_d, "odds_a": target_a}
        work["Benzerlik"] = work.apply(lambda r: odds_similarity(target, r), axis=1)
        matches = work[work["Benzerlik"] >= min_sim].sort_values("Benzerlik", ascending=False)
        st.metric("Benzer geçmiş maç", f"{len(matches):,}")
        if matches.empty:
            st.info("Bu eşikte benzer maç bulunamadı. Benzerlik eşiğini düşürüp tekrar deneyin.")
        else:
            if has_results(matches):
                dist = result_distribution(matches)
                if not dist.empty:
                    st.markdown("#### Benzer maçların gerçek sonuç dağılımı")
                    st.dataframe(dist, hide_index=True, use_container_width=True)
                    st.caption("Bu yüzdeler benzer geçmiş maçların frekansıdır; gelecek maç için garanti veya doğrulanmış model olasılığı değildir.")
                valid_score = matches.dropna(subset=["ft_home_goals","ft_away_goals"])
                if not valid_score.empty:
                    st.write("**Ortalama goller:** Ev sahibi", round(valid_score["ft_home_goals"].mean(), 2), "— Deplasman", round(valid_score["ft_away_goals"].mean(), 2))
                    over = ((valid_score["ft_home_goals"] + valid_score["ft_away_goals"]) > 2.5).mean() * 100
                    btts = ((valid_score["ft_home_goals"] > 0) & (valid_score["ft_away_goals"] > 0)).mean() * 100
                    x1, x2 = st.columns(2)
                    x1.metric("2,5 üst frekansı", f"%{over:.1f}")
                    x2.metric("Karşılıklı gol frekansı", f"%{btts:.1f}")
            display = [c for c in ["Benzerlik","date","league","home_team","away_team","odds_h","odds_d","odds_a","ft_home_goals","ft_away_goals","ft_result"] if c in matches.columns]
            st.dataframe(matches[display].head(100), use_container_width=True)
            st.download_button("Sonuçları CSV indir", matches[display].to_csv(index=False).encode("utf-8-sig"), "benzer_maclar.csv", "text/csv")

elif menu == "Takım geçmişi":
    st.subheader("Takımın kayıtlı geçmiş performansı")
    teams = sorted(set(df["home_team"].dropna().astype(str)) | set(df["away_team"].dropna().astype(str)))
    team = st.selectbox("Takım seçin", teams)
    n = st.slider("Son kaç kayıtlı maç", 5, 30, 10)
    home = df[df["home_team"].astype(str) == team].copy()
    away = df[df["away_team"].astype(str) == team].copy()
    home["Takım tarafı"] = "Ev sahibi"
    away["Takım tarafı"] = "Deplasman"
    team_games = pd.concat([home, away], ignore_index=True)
    if "date" in team_games.columns:
        team_games["_sort_date"] = pd.to_datetime(team_games["date"], errors="coerce", dayfirst=True)
        team_games = team_games.sort_values("_sort_date", ascending=False, na_position="last")
    team_games = team_games.head(n)
    if {"ft_home_goals","ft_away_goals"}.issubset(team_games.columns):
        team_games["Atılan gol"] = np.where(team_games["Takım tarafı"] == "Ev sahibi", team_games["ft_home_goals"], team_games["ft_away_goals"])
        team_games["Yenilen gol"] = np.where(team_games["Takım tarafı"] == "Ev sahibi", team_games["ft_away_goals"], team_games["ft_home_goals"])
        team_games["Performans"] = np.where(team_games["Atılan gol"] > team_games["Yenilen gol"], "Galibiyet", np.where(team_games["Atılan gol"] < team_games["Yenilen gol"], "Mağlubiyet", "Beraberlik"))
        if not team_games.empty:
            st.metric("İncelenen maç", len(team_games))
            st.write("Galibiyet %", round((team_games["Performans"] == "Galibiyet").mean()*100, 1),
                     " | Beraberlik %", round((team_games["Performans"] == "Beraberlik").mean()*100, 1),
                     " | Maç başı gol", round(team_games["Atılan gol"].mean(), 2))
    show = [c for c in ["date","league","home_team","away_team","ft_home_goals","ft_away_goals","ft_result","odds_h","odds_d","odds_a","Takım tarafı","Performans"] if c in team_games.columns]
    st.dataframe(team_games[show], use_container_width=True)

elif menu == "Oran filtresi":
    st.subheader("Geçmiş maçları oran aralığına göre filtrele")
    if not {"odds_h","odds_d","odds_a"}.intersection(df.columns):
        st.warning("Veritabanında tanınan oran sütunu yok.")
    else:
        available = [c for c in ["odds_h","odds_d","odds_a"] if c in df.columns]
        odds_col = st.selectbox("Oran sütunu", available)
        values = numeric(df[odds_col]).dropna()
        if values.empty:
            st.warning("Bu sütunda sayısal oran bulunamadı.")
        else:
            low_default = max(1.0, float(values.quantile(0.1)))
            high_default = max(low_default + 0.1, float(values.quantile(0.9)))
            low = st.number_input("En düşük oran", min_value=1.0, value=round(low_default, 2), step=0.1)
            high = st.number_input("En yüksek oran", min_value=1.0, value=round(high_default, 2), step=0.1)
            filtered = df[(numeric(df[odds_col]) >= low) & (numeric(df[odds_col]) <= high)]
            st.write(f"Eşleşen maç: {len(filtered):,}")
            show = [c for c in ["date","league","home_team","away_team","odds_h","odds_d","odds_a","ft_home_goals","ft_away_goals","ft_result"] if c in filtered.columns]
            st.dataframe(filtered[show], use_container_width=True)

elif menu == "Anomali kontrolü":
    st.subheader("Oran kombinasyonu / piyasa marjı kontrolü")
    if not {"odds_h","odds_d","odds_a"}.issubset(df.columns):
        st.warning("Üçlü maç sonucu oran sütunları bulunamadı.")
    else:
        odds = df[["odds_h","odds_d","odds_a"]].apply(pd.to_numeric, errors="coerce")
        valid = odds.notna().all(axis=1) & (odds > 1).all(axis=1)
        margin = (1 / odds["odds_h"]) + (1 / odds["odds_d"]) + (1 / odds["odds_a"])
        result = df.loc[valid].copy()
        result["İma edilen toplam olasılık"] = margin[valid]
        result["Marj (%)"] = ((margin[valid] - 1) * 100).round(2)
        result["Kontrol"] = np.where((margin[valid] < 1.0) | (margin[valid] > 1.20), "İncele", "Genel aralıkta")
        unusual = result[(result["İma edilen toplam olasılık"] < 1.0) | (result["İma edilen toplam olasılık"] > 1.20)]
        st.metric("İncelenecek kayıt", f"{len(unusual):,}")
        st.caption("Bu kontrol tek başına şike veya manipülasyon kanıtı değildir; eksik/hatalı oranları ve alışılmadık piyasa marjlarını işaretler.")
        show = [c for c in ["date","league","home_team","away_team","odds_h","odds_d","odds_a","İma edilen toplam olasılık","Marj (%)","Kontrol"] if c in result.columns]
        st.dataframe(unusual[show].head(500), use_container_width=True)
