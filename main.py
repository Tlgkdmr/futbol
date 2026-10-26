from __future__ import annotations

import io
import math
import sqlite3
from pathlib import Path
from datetime import datetime, timezone
from typing import Dict, List, Optional, Tuple

import pandas as pd
import requests
import streamlit as st

st.set_page_config(
    page_title="FREKANS X",
    page_icon="⚽",
    layout="wide",
    initial_sidebar_state="expanded",
)

APP_VERSION = "1.1.1"
BASE_URL = "https://www.football-data.co.uk/mmz4281"
REQUEST_TIMEOUT = 20
USER_AGENT = "FREKANS-X/1.0 (historical football data analysis)"

LEAGUES: Dict[str, str] = {
    "Türkiye Süper Lig": "T1",
    "İngiltere Premier League": "E0",
    "İngiltere Championship": "E1",
    "İspanya La Liga": "SP1",
    "İspanya La Liga 2": "SP2",
    "İtalya Serie A": "I1",
    "İtalya Serie B": "I2",
    "Fransa Ligue 1": "F1",
    "Almanya Bundesliga": "D1",
    "Almanya 2. Bundesliga": "D2",
    "Hollanda Eredivisie": "N1",
    "Portekiz Primeira Liga": "P1",
    "Belçika Pro League": "B1",
}

# Candidate columns are checked in order. Missing markets are omitted, never fabricated.
MARKETS: Dict[str, dict] = {
    "MS 1": {"result_col": "MS Sonuç", "target": "1", "odds": ["B365H", "AvgH", "PSH", "WHH", "BWH", "IWH", "VCH", "MaxH"]},
    "MS X": {"result_col": "MS Sonuç", "target": "X", "odds": ["B365D", "AvgD", "PSD", "WHD", "BWD", "IWD", "VCD", "MaxD"]},
    "MS 2": {"result_col": "MS Sonuç", "target": "2", "odds": ["B365A", "AvgA", "PSA", "WHA", "BWA", "IWA", "VCA", "MaxA"]},
    "2.5 Üst": {"result_col": "2.5 Sonuç", "target": "Üst", "odds": ["B365>2.5", "Avg>2.5", "P>2.5", "Max>2.5"]},
    "2.5 Alt": {"result_col": "2.5 Sonuç", "target": "Alt", "odds": ["B365<2.5", "Avg<2.5", "P<2.5", "Max<2.5"]},
}

ODDS_BINS = [1.0, 1.25, 1.50, 1.75, 2.0, 2.25, 2.50, 3.0, 3.5, 4.0, 5.0, 7.0, 10.0, 20.0, 100.0]
MATCH_COLUMNS = ["Date", "Lig", "Sezon", "HomeTeam", "AwayTeam"]


def parse_csv_response(content: bytes) -> pd.DataFrame:
    """Read Football-Data CSV with resilient encoding and malformed-line handling."""
    last_error: Optional[Exception] = None
    for encoding in ("latin1", "utf-8-sig", "cp1252"):
        try:
            frame = pd.read_csv(io.BytesIO(content), encoding=encoding, on_bad_lines="skip", low_memory=False)
            if len(frame.columns) > 1:
                return frame
        except Exception as exc:  # noqa: BLE001 - try alternate encodings
            last_error = exc
    raise ValueError(f"CSV okunamadı: {last_error or 'dosya biçimi tanınmadı'}")


@st.cache_data(ttl=3600, show_spinner=False)
def fetch_season(season: str) -> Tuple[pd.DataFrame, List[dict]]:
    frames: List[pd.DataFrame] = []
    statuses: List[dict] = []
    session = requests.Session()
    session.headers.update({"User-Agent": USER_AGENT})

    for league, code in LEAGUES.items():
        url = f"{BASE_URL}/{season}/{code}.csv"
        try:
            response = session.get(url, timeout=REQUEST_TIMEOUT)
            response.raise_for_status()
            if not response.content.strip():
                raise ValueError("Kaynak boş dosya döndürdü")
            frame = parse_csv_response(response.content)
            required = {"Date", "HomeTeam", "AwayTeam"}
            if not required.issubset(frame.columns):
                raise ValueError("Gerekli Date/HomeTeam/AwayTeam sütunları yok")
            frame["Date"] = pd.to_datetime(frame["Date"], dayfirst=True, errors="coerce")
            frame["HomeTeam"] = frame["HomeTeam"].astype("string").str.strip()
            frame["AwayTeam"] = frame["AwayTeam"].astype("string").str.strip()
            frame = frame.dropna(subset=["Date", "HomeTeam", "AwayTeam"]).copy()
            frame = frame[(frame["HomeTeam"] != "") & (frame["AwayTeam"] != "")]
            frame["Lig"] = league
            frame["Sezon"] = season
            frame["Kaynak"] = url
            frames.append(frame)
            statuses.append({"Lig": league, "Durum": "Veri alındı", "Maç": len(frame), "Not": ""})
        except requests.RequestException as exc:
            statuses.append({"Lig": league, "Durum": "İstek başarısız", "Maç": 0, "Not": str(exc)[:160]})
        except Exception as exc:  # noqa: BLE001 - report per-league source errors
            statuses.append({"Lig": league, "Durum": "Veri kullanılamadı", "Maç": 0, "Not": str(exc)[:160]})

    session.close()
    data = pd.concat(frames, ignore_index=True, sort=False) if frames else pd.DataFrame()
    return data, statuses


def prepare_matches(data: pd.DataFrame) -> pd.DataFrame:
    df = data.copy()
    numeric_cols = ["FTHG", "FTAG", "HTHG", "HTAG"]
    for col in numeric_cols:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")

    if {"FTHG", "FTAG"}.issubset(df.columns):
        full_valid = df["FTHG"].notna() & df["FTAG"].notna()
        df["MS Sonuç"] = pd.Series(pd.NA, index=df.index, dtype="string")
        df.loc[full_valid & (df["FTHG"] > df["FTAG"]), "MS Sonuç"] = "1"
        df.loc[full_valid & (df["FTHG"] == df["FTAG"]), "MS Sonuç"] = "X"
        df.loc[full_valid & (df["FTHG"] < df["FTAG"]), "MS Sonuç"] = "2"
        df["Toplam Gol"] = df["FTHG"] + df["FTAG"]
        df["2.5 Sonuç"] = pd.Series(pd.NA, index=df.index, dtype="string")
        df.loc[full_valid & (df["Toplam Gol"] > 2.5), "2.5 Sonuç"] = "Üst"
        df.loc[full_valid & (df["Toplam Gol"] <= 2.5), "2.5 Sonuç"] = "Alt"
        df["KG Sonuç"] = pd.Series(pd.NA, index=df.index, dtype="string")
        df.loc[full_valid & (df["FTHG"] > 0) & (df["FTAG"] > 0), "KG Sonuç"] = "Var"
        df.loc[full_valid & ((df["FTHG"] == 0) | (df["FTAG"] == 0)), "KG Sonuç"] = "Yok"

    if {"HTHG", "HTAG"}.issubset(df.columns):
        half_valid = df["HTHG"].notna() & df["HTAG"].notna()
        df["İY Sonuç"] = pd.Series(pd.NA, index=df.index, dtype="string")
        df.loc[half_valid & (df["HTHG"] > df["HTAG"]), "İY Sonuç"] = "1"
        df.loc[half_valid & (df["HTHG"] == df["HTAG"]), "İY Sonuç"] = "X"
        df.loc[half_valid & (df["HTHG"] < df["HTAG"]), "İY Sonuç"] = "2"
    return df


def find_odds_column(df: pd.DataFrame, market: str) -> Optional[str]:
    for col in MARKETS[market]["odds"]:
        if col in df.columns and pd.to_numeric(df[col], errors="coerce").notna().any():
            return col
    return None


def build_market_frame(df: pd.DataFrame, market: str) -> Tuple[pd.DataFrame, Optional[str]]:
    odds_col = find_odds_column(df, market)
    result_col = MARKETS[market]["result_col"]
    target = MARKETS[market]["target"]
    if odds_col is None or result_col not in df.columns:
        return pd.DataFrame(), odds_col

    work = df.copy()
    work["Oran"] = pd.to_numeric(work[odds_col], errors="coerce")
    work = work[work["Oran"].between(1.01, 100, inclusive="both")]
    work = work[work[result_col].notna()].copy()
    if work.empty:
        return work, odds_col
    work["Kazandı"] = work[result_col].astype(str).eq(target)
    work["Net birim getiri"] = work["Kazandı"].astype(int) * work["Oran"] - 1.0
    work["Oran aralığı"] = pd.cut(work["Oran"], bins=ODDS_BINS, right=False, include_lowest=True)
    return work, odds_col


def band_summary(work: pd.DataFrame) -> pd.DataFrame:
    if work.empty:
        return pd.DataFrame()
    summary = work.groupby("Oran aralığı", observed=True).agg(
        Mac_sayisi=("Kazandı", "size"),
        Kazanan=("Kazandı", "sum"),
        Ortalama_oran=("Oran", "mean"),
        Net_birim_getiri=("Net birim getiri", "sum"),
    ).reset_index()
    summary["Başarı %"] = (100 * summary["Kazanan"] / summary["Mac_sayisi"]).round(2)
    summary["Getiri %"] = (100 * summary["Net_birim_getiri"] / summary["Mac_sayisi"]).round(2)
    summary["Ortalama_oran"] = summary["Ortalama_oran"].round(3)
    summary["Net_birim_getiri"] = summary["Net_birim_getiri"].round(2)
    return summary.rename(columns={"Oran aralığı": "Oran aralığı"})


def fmt_pct(numerator: int, denominator: int) -> str:
    return f"{100 * numerator / denominator:.1f}%" if denominator else "—"



FIXTURES_URL = "https://www.football-data.co.uk/matches/fixtures.csv"
SNAPSHOT_DB = Path(__file__).with_name("frekans_x_snapshots.sqlite3")
DIV_TO_LEAGUE = {code: name for name, code in LEAGUES.items()}

@st.cache_data(ttl=1800, show_spinner=False)
def fetch_upcoming_fixtures() -> Tuple[pd.DataFrame, str]:
    """Download the provider's separate upcoming-fixtures file; no fixtures are fabricated."""
    response = requests.get(
        FIXTURES_URL,
        timeout=REQUEST_TIMEOUT,
        headers={"User-Agent": USER_AGENT},
    )
    response.raise_for_status()
    frame = parse_csv_response(response.content)
    # Normalize provider header variations while preserving original columns.
    rename = {}
    aliases = {
        "Div": "Div", "div": "Div",
        "Date": "Date", "date": "Date",
        "Time": "Time", "time": "Time",
        "HomeTeam": "HomeTeam", "home_team": "HomeTeam", "Home Team": "HomeTeam",
        "AwayTeam": "AwayTeam", "away_team": "AwayTeam", "Away Team": "AwayTeam",
        "B365H": "B365H", "b365_h": "B365H",
        "B365D": "B365D", "b365_d": "B365D",
        "B365A": "B365A", "b365_a": "B365A",
        "B365>2.5": "B365>2.5", "b365_o25": "B365>2.5", "B365O25": "B365>2.5",
        "B365<2.5": "B365<2.5", "b365_u25": "B365<2.5", "B365U25": "B365<2.5",
        "AvgH": "AvgH", "avgh": "AvgH", "avg_h": "AvgH",
        "AvgD": "AvgD", "avgd": "AvgD", "avg_d": "AvgD",
        "AvgA": "AvgA", "avga": "AvgA", "avg_a": "AvgA",
    }
    for col in frame.columns:
        clean = str(col).strip().replace("\ufeff", "")
        if clean in aliases:
            rename[col] = aliases[clean]
    frame = frame.rename(columns=rename)
    required = {"Date", "HomeTeam", "AwayTeam"}
    if not required.issubset(frame.columns):
        raise ValueError("Fikstür CSV'sinde tarih/takım sütunları bulunamadı: " + ", ".join(map(str, frame.columns[:20])))
    frame["Date"] = pd.to_datetime(frame["Date"], dayfirst=True, errors="coerce")
    frame["HomeTeam"] = frame["HomeTeam"].astype("string").str.strip()
    frame["AwayTeam"] = frame["AwayTeam"].astype("string").str.strip()
    frame = frame.dropna(subset=["Date", "HomeTeam", "AwayTeam"]).copy()
    frame = frame[(frame["HomeTeam"] != "") & (frame["AwayTeam"] != "")]
    if "Div" in frame.columns:
        frame["Lig"] = frame["Div"].astype(str).map(DIV_TO_LEAGUE).fillna(frame["Div"].astype(str))
    else:
        frame["Lig"] = "Lig bilgisi kaynakta yok"
    frame["Kaynak"] = FIXTURES_URL
    return frame, "Fikstür kaynağı: Football-Data.co.uk"


def poisson_probabilities(lam: float, max_goals: int = 10) -> list:
    """Poisson probability mass through max_goals, with residual tail folded into the last bucket."""
    lam = max(0.05, min(float(lam), 6.0))
    probs = [math.exp(-lam)]
    for k in range(1, max_goals + 1):
        probs.append(probs[-1] * lam / k)
    probs[-1] += max(0.0, 1.0 - sum(probs))
    return probs


def predict_fixture(home: str, away: str, league: str, history: pd.DataFrame) -> dict:
    """Estimate markets from available completed scores; league averages backstop sparse team data."""
    hist = history.copy()
    if hist.empty or not {"FTHG", "FTAG", "HomeTeam", "AwayTeam"}.issubset(hist.columns):
        hist = pd.DataFrame(columns=["FTHG", "FTAG", "HomeTeam", "AwayTeam", "Lig"])
    hist["FTHG"] = pd.to_numeric(hist.get("FTHG"), errors="coerce")
    hist["FTAG"] = pd.to_numeric(hist.get("FTAG"), errors="coerce")
    hist = hist.dropna(subset=["FTHG", "FTAG"])
    if "Lig" in hist.columns and league in set(hist["Lig"].astype(str)):
        lh = hist[hist["Lig"].astype(str) == str(league)]
    else:
        lh = hist
    if lh.empty:
        lh = hist
    if lh.empty:
        return {"Maç başına ev golü": None, "Maç başına deplasman golü": None,
                "MS 1 %": None, "MS X %": None, "MS 2 %": None,
                "2.5 Üst %": None, "2.5 Alt %": None, "KG Var %": None,
                "Tahmin notu": "Yeterli tamamlanmış maç verisi yok; tahmin üretilemedi."}

    league_home_avg = float(lh["FTHG"].mean())
    league_away_avg = float(lh["FTAG"].mean())
    league_home_avg = max(0.2, min(league_home_avg, 4.0))
    league_away_avg = max(0.2, min(league_away_avg, 4.0))

    # Use venue-specific team records when available; shrink small samples toward league average.
    home_home = lh[lh["HomeTeam"].astype(str).str.casefold() == str(home).casefold()]
    away_away = lh[lh["AwayTeam"].astype(str).str.casefold() == str(away).casefold()]
    home_away = lh[lh["AwayTeam"].astype(str).str.casefold() == str(home).casefold()]
    away_home = lh[lh["HomeTeam"].astype(str).str.casefold() == str(away).casefold()]

    def smoothed_mean(frame: pd.DataFrame, col: str, baseline: float) -> float:
        values = pd.to_numeric(frame[col], errors="coerce").dropna() if col in frame else pd.Series(dtype=float)
        n = len(values)
        # Four pseudo-observations stabilize estimates for teams with little history.
        return float((values.sum() + 4.0 * baseline) / (n + 4.0)) if n else baseline

    home_attack = smoothed_mean(home_home, "FTHG", league_home_avg)
    home_defence = smoothed_mean(home_away, "FTAG", league_away_avg)
    away_attack = smoothed_mean(away_away, "FTAG", league_away_avg)
    away_defence = smoothed_mean(away_home, "FTHG", league_home_avg)
    exp_home = max(0.15, min(4.5, (home_attack / league_home_avg) * (away_defence / league_home_avg) * league_home_avg))
    exp_away = max(0.15, min(4.5, (away_attack / league_away_avg) * (home_defence / league_away_avg) * league_away_avg))
    ph = poisson_probabilities(exp_home)
    pa = poisson_probabilities(exp_away)
    p1 = px = p2 = p_over = p_btts = 0.0
    for i, pi in enumerate(ph):
        for j, pj in enumerate(pa):
            prob = pi * pj
            if i > j:
                p1 += prob
            elif i == j:
                px += prob
            else:
                p2 += prob
            if i + j >= 3:
                p_over += prob
            if i > 0 and j > 0:
                p_btts += prob
    return {
        "Maç başına ev golü": round(exp_home, 2),
        "Maç başına deplasman golü": round(exp_away, 2),
        "MS 1 %": round(p1 * 100, 1), "MS X %": round(px * 100, 1), "MS 2 %": round(p2 * 100, 1),
        "2.5 Üst %": round(p_over * 100, 1), "2.5 Alt %": round((1 - p_over) * 100, 1),
        "KG Var %": round(p_btts * 100, 1), "KG Yok %": round((1 - p_btts) * 100, 1),
        "Tahmin notu": f"Model: mevcut geçmiş skorlar ve lig ortalaması; beklenen gol {exp_home:.2f}-{exp_away:.2f}. Kadro/sakatlık/xG bilgisi içermez."
    }


def add_odds_value(row: dict, fixture: pd.Series, odds_col: str, probability_key: str) -> None:
    value = pd.to_numeric(pd.Series([fixture.get(odds_col)]), errors="coerce").iloc[0]
    prob_pct = row.get(probability_key)
    if pd.notna(value) and value > 1 and prob_pct is not None and prob_pct > 0:
        row[odds_col + " gerçek oran"] = round(float(value), 2)
        row[probability_key.replace(" %", " adil oran")] = round(100.0 / float(prob_pct), 2)
        row[probability_key.replace(" %", " beklenen getiri %")] = round((float(value) * float(prob_pct) / 100.0 - 1.0) * 100.0, 1)

def save_odds_snapshot(fixtures: pd.DataFrame) -> int:
    """Persist available fixture odds snapshots for later movement comparisons.

    On hosting platforms with ephemeral storage, snapshots may be lost after a restart.
    No opening price is invented: a drop is calculated only when two valid observations exist.
    """
    odds_cols = [c for c in ["B365H", "B365D", "B365A", "AvgH", "AvgD", "AvgA",
                              "B365>2.5", "B365<2.5", "Avg>2.5", "Avg<2.5"] if c in fixtures.columns]
    if fixtures.empty or not odds_cols:
        return 0
    saved = 0
    try:
        with sqlite3.connect(str(SNAPSHOT_DB), timeout=10) as con:
            con.execute("""CREATE TABLE IF NOT EXISTS odds_snapshots (
                match_key TEXT NOT NULL, captured_at TEXT NOT NULL, league TEXT, match_date TEXT,
                home_team TEXT NOT NULL, away_team TEXT NOT NULL, bookmaker_market TEXT NOT NULL, odds REAL NOT NULL
            )""")
            for _, r in fixtures.iterrows():
                home = str(r.get("HomeTeam", "")).strip()
                away = str(r.get("AwayTeam", "")).strip()
                date_value = r.get("Date")
                if not home or not away or pd.isna(date_value):
                    continue
                date_text = pd.Timestamp(date_value).isoformat()
                league = str(r.get("Lig", ""))
                match_key = "|".join([date_text[:10], league, home.casefold(), away.casefold()])
                captured = datetime.now(timezone.utc).isoformat(timespec="microseconds")
                for col in odds_cols:
                    value = pd.to_numeric(pd.Series([r.get(col)]), errors="coerce").iloc[0]
                    if pd.notna(value) and 1.01 <= float(value) <= 100:
                        # Do not treat repeated reruns of the same cached quote as new observations.
                        previous = con.execute(
                            "SELECT odds FROM odds_snapshots WHERE match_key = ? AND bookmaker_market = ? ORDER BY captured_at DESC LIMIT 1",
                            (match_key, col),
                        ).fetchone()
                        if previous is not None and abs(float(previous[0]) - float(value)) < 1e-9:
                            continue
                        con.execute(
                            "INSERT INTO odds_snapshots (match_key,captured_at,league,match_date,home_team,away_team,bookmaker_market,odds) VALUES (?,?,?,?,?,?,?,?)",
                            (match_key, captured, league, date_text, home, away, col, float(value)),
                        )
                        saved += 1
            con.commit()
    except (sqlite3.Error, OSError) as exc:
        st.warning(f"Oran geçmişi bu oturumda kaydedilemedi: {str(exc)[:180]}")
    return saved


def load_odds_movement() -> pd.DataFrame:
    """Return opening-to-latest movements only for matches with at least two snapshots."""
    if not SNAPSHOT_DB.exists():
        return pd.DataFrame()
    try:
        with sqlite3.connect(str(SNAPSHOT_DB), timeout=10) as con:
            raw = pd.read_sql_query(
                "SELECT * FROM odds_snapshots ORDER BY captured_at ASC", con
            )
    except (sqlite3.Error, OSError, pd.errors.DatabaseError):
        return pd.DataFrame()
    if raw.empty:
        return raw
    raw["captured_at"] = pd.to_datetime(raw["captured_at"], utc=True, errors="coerce")
    raw["odds"] = pd.to_numeric(raw["odds"], errors="coerce")
    raw = raw.dropna(subset=["captured_at", "odds"])
    if raw.empty:
        return raw
    grouped = raw.groupby(["match_key", "league", "match_date", "home_team", "away_team", "bookmaker_market"], dropna=False)
    rows = []
    for keys, group in grouped:
        group = group.sort_values("captured_at")
        first = group.iloc[0]
        last = group.iloc[-1]
        if len(group) < 2 or first["odds"] <= 0:
            continue
        opening = float(first["odds"])
        latest = float(last["odds"])
        change = (latest - opening) / opening * 100.0
        rows.append({
            "Lig": keys[1], "Tarih": keys[2], "Ev sahibi": keys[3], "Deplasman": keys[4],
            "Pazar/Oran sütunu": keys[5], "İlk kaydedilen oran": round(opening, 3),
            "Son kaydedilen oran": round(latest, 3), "Değişim %": round(change, 2),
            "Gözlem sayısı": len(group), "İlk kayıt (UTC)": first["captured_at"],
            "Son kayıt (UTC)": last["captured_at"],
            "Sinyal": "Belirgin düşüş" if change <= -10 else ("Belirgin yükseliş" if change >= 10 else "Normal aralık")
        })
    return pd.DataFrame(rows)


def historical_open_close_analysis(df: pd.DataFrame) -> pd.DataFrame:
    """Compare historical opening and closing odds where both columns exist."""
    pairs = [("MS 1", "B365H", "B365CH"), ("MS X", "B365D", "B365CD"),
             ("MS 2", "B365A", "B365CA"), ("Ortalama MS 1", "AvgH", "AvgCH"),
             ("Ortalama MS X", "AvgD", "AvgCD"), ("Ortalama MS 2", "AvgA", "AvgCA")]
    rows = []
    for label, opening_col, closing_col in pairs:
        if opening_col not in df.columns or closing_col not in df.columns:
            continue
        opening = pd.to_numeric(df[opening_col], errors="coerce")
        closing = pd.to_numeric(df[closing_col], errors="coerce")
        valid = opening.between(1.01, 100) & closing.between(1.01, 100)
        if not valid.any():
            continue
        changes = ((closing[valid] - opening[valid]) / opening[valid]) * 100.0
        rows.append({
            "Pazar": label, "Eşleşen maç": int(valid.sum()),
            "Ortalama açılış oranı": round(float(opening[valid].mean()), 3),
            "Ortalama kapanış oranı": round(float(closing[valid].mean()), 3),
            "Ortalama değişim %": round(float(changes.mean()), 2),
            "Düşüş ≥ %10": int((changes <= -10).sum()),
            "Yükseliş ≥ %10": int((changes >= 10).sum()),
        })
    return pd.DataFrame(rows)


st.title("⚽ FREKANS X")
st.caption(f"Futbol verileri • Tarihsel oran analizi • Sürüm {APP_VERSION}")
st.info(
    "Kaynak: Football-Data.co.uk. Kaynakta bulunmayan oranlar oluşturulmaz. "
    "Geçmiş performans gelecekteki sonucu garanti etmez."
)

with st.sidebar:
    st.header("Veri ayarları")
    current_year = datetime.now().year
    default_seasons = [f"{str(y)[-2:]}{str(y + 1)[-2:]}" for y in range(current_year, current_year - 4, -1)]
    selected_season = st.selectbox(
        "Sezon",
        default_seasons,
        index=0,
        format_func=lambda s: f"20{s[:2]}–20{s[2:]}"
    )
    selected_leagues = st.multiselect("Ligler", list(LEAGUES.keys()), default=list(LEAGUES.keys()))
    st.caption("Veriler, düğmeye bastığında kaynaktan çekilir. Önbellek 1 saat geçerlidir.")
    refresh = st.button("Verileri yeniden çek", type="primary", use_container_width=True)

if "raw_matches" not in st.session_state:
    st.session_state["raw_matches"] = pd.DataFrame()
    st.session_state["source_statuses"] = []
    st.session_state["loaded_season"] = None

if refresh or st.session_state["loaded_season"] != selected_season:
    with st.spinner("Lig dosyaları kontrol ediliyor ve indiriliyor..."):
        all_data, statuses = fetch_season(selected_season)
    st.session_state["raw_matches"] = all_data
    st.session_state["source_statuses"] = statuses
    st.session_state["loaded_season"] = selected_season

raw_matches = st.session_state["raw_matches"]
if not raw_matches.empty and selected_leagues:
    raw_matches = raw_matches[raw_matches["Lig"].isin(selected_leagues)].copy()
elif not selected_leagues:
    raw_matches = pd.DataFrame()

matches = prepare_matches(raw_matches) if not raw_matches.empty else pd.DataFrame()

with st.expander("Veri kaynağı kontrol raporu", expanded=False):
    statuses = st.session_state.get("source_statuses", [])
    if statuses:
        st.dataframe(pd.DataFrame(statuses), use_container_width=True, hide_index=True)
    else:
        st.warning("Henüz kaynak kontrolü yapılmadı.")

if matches.empty:
    st.warning(
        "Seçilen lig ve sezonda geçmiş maç verisi bulunamadı. "
        "Gelecek fikstürü bölümü yine açılabilir; tahminler için geçmiş skor "
        "bulunmuyorsa model tahmin üretemez. Kaynak durum raporunu kontrol edin."
    )
    # Keep the app alive so the upcoming-fixtures tab can still be used.
    matches = pd.DataFrame(columns=[
        "Date", "Lig", "Sezon", "HomeTeam", "AwayTeam", "FTHG", "FTAG",
        "HTHG", "HTAG", "MS Sonuç", "2.5 Sonuç", "KG Sonuç", "İY Sonuç",
        "Toplam Gol"
    ])

finished = matches.copy()
if "MS Sonuç" in finished.columns:
    finished = finished[finished["MS Sonuç"].notna()].copy()

c1, c2, c3, c4 = st.columns(4)
c1.metric("İndirilen maç kaydı", f"{len(matches):,}")
c2.metric("Tamamlanan maç", f"{len(finished):,}")
c3.metric("Lig sayısı", str(matches["Lig"].nunique()))
if not matches.empty and matches["Date"].notna().any():
    min_date = matches["Date"].min()
    max_date = matches["Date"].max()
    c4.metric("Tarih aralığı", f"{min_date:%d.%m.%Y} – {max_date:%d.%m.%Y}")
else:
    c4.metric("Tarih aralığı", "Veri yok")

tabs = st.tabs([
    "Genel görünüm", "Gelecek maç tahminleri", "Oran analizi", "Benzer oranlı maçlar",
    "Gol ve KG", "Maç listesi", "Oran düşüşü", "İndir / dışa aktar"
])

with tabs[0]:
    st.subheader("Lig bazında sonuç dağılımı")
    if not finished.empty and "MS Sonuç" in finished.columns:
        result_counts = pd.crosstab(finished["Lig"], finished["MS Sonuç"])
        for result in ["1", "X", "2"]:
            if result not in result_counts.columns:
                result_counts[result] = 0
        result_counts = result_counts[["1", "X", "2"]]
        result_counts["Toplam"] = result_counts.sum(axis=1)
        result_counts["1 %"] = (100 * result_counts["1"] / result_counts["Toplam"]).round(1)
        result_counts["X %"] = (100 * result_counts["X"] / result_counts["Toplam"]).round(1)
        result_counts["2 %"] = (100 * result_counts["2"] / result_counts["Toplam"]).round(1)
        st.dataframe(result_counts, use_container_width=True)
    else:
        st.info("Bu veride tamamlanmış maç sonucu bulunamadı.")

    if not finished.empty:
        st.subheader("Son oynanan maçlar")
        cols = [c for c in ["Date", "Lig", "HomeTeam", "AwayTeam", "FTHG", "FTAG", "MS Sonuç"] if c in finished.columns]
        st.dataframe(finished[cols].sort_values("Date", ascending=False).head(100), use_container_width=True, hide_index=True)

with tabs[1]:
    st.subheader("Bugün ve önümüzdeki 7 gün: fikstür ve tahminler")
    st.caption("Gelecek fikstür ayrı kaynaktan çekilir. Olasılıklar basit Poisson modelidir; kesin tahmin değildir.")
    force_fixtures = st.button("Gelecek maçları güncelle", key="refresh_fixtures")
    try:
        if force_fixtures:
            fetch_upcoming_fixtures.clear()
        upcoming, fixtures_note = fetch_upcoming_fixtures()
        st.caption(fixtures_note)
        # Use Türkiye local date rather than assuming the cloud server timezone.
        today = pd.Timestamp.now(tz="Europe/Istanbul").tz_localize(None).normalize()
        end_date = today + pd.Timedelta(days=7)
        upcoming = upcoming[(upcoming["Date"] >= today) & (upcoming["Date"] < end_date + pd.Timedelta(days=1))].copy()
        if selected_leagues and "Lig" in upcoming.columns:
            # Respect the user's league filter even when none of the selected leagues has a fixture.
            upcoming = upcoming[upcoming["Lig"].isin(selected_leagues)].copy()
        if upcoming.empty:
            st.warning("Kaynakta bugün ile önümüzdeki 7 gün arasında fikstür bulunamadı. Kaynak henüz güncellenmemiş olabilir; bu durum maç olmadığı anlamına gelmez.")
        else:
            saved_count = save_odds_snapshot(upcoming)
            if saved_count:
                st.caption(f"Bu yenilemede {saved_count} gerçek oran gözlemi kaydedildi. Oran hareketi için aynı maç/pazarın farklı zamanlarda en az iki kez alınması gerekir.")
            predictions = []
            for _, fixture in upcoming.iterrows():
                league_name = str(fixture.get("Lig", ""))
                row = {
                    "Tarih": fixture["Date"].strftime("%d.%m.%Y"),
                    "Saat": str(fixture.get("Time", "")) if pd.notna(fixture.get("Time", pd.NA)) else "",
                    "Lig": league_name,
                    "Ev sahibi": str(fixture["HomeTeam"]),
                    "Deplasman": str(fixture["AwayTeam"]),
                }
                pred = predict_fixture(str(fixture["HomeTeam"]), str(fixture["AwayTeam"]), league_name, matches)
                row.update(pred)
                for odds_col, prob_key in [("B365H", "MS 1 %"), ("B365D", "MS X %"), ("B365A", "MS 2 %"), ("B365>2.5", "2.5 Üst %"), ("B365<2.5", "2.5 Alt %")]:
                    add_odds_value(row, fixture, odds_col, prob_key)
                predictions.append(row)
            pred_df = pd.DataFrame(predictions)
            st.metric("Önümüzdeki 7 gündeki fikstür", len(pred_df))
            display_cols = [c for c in ["Tarih", "Saat", "Lig", "Ev sahibi", "Deplasman", "Maç başına ev golü", "Maç başına deplasman golü", "MS 1 %", "MS X %", "MS 2 %", "2.5 Üst %", "2.5 Alt %", "KG Var %", "KG Yok %", "B365H gerçek oran", "B365D gerçek oran", "B365A gerçek oran", "B365>2.5 gerçek oran", "B365<2.5 gerçek oran"] if c in pred_df.columns]
            st.dataframe(pred_df[display_cols], use_container_width=True, hide_index=True)
            st.download_button("Gelecek maç tahminlerini CSV indir", pred_df.to_csv(index=False).encode("utf-8-sig"), file_name="frekans_x_gelecek_tahminleri.csv", mime="text/csv")
            st.warning("Model tahminleri yalnızca geçmiş skor verileriyle hesaplanır. Gerçek oran sütunu boşsa kaynak o maç/pazar için oran sağlamamıştır. Beklenen getiri tek başına bahis önerisi değildir.")
    except Exception as exc:
        st.error(f"Gelecek fikstürü alınamadı: {str(exc)[:300]}")
        st.info("İnternet erişimini ve veri kaynağını kontrol edip 'Gelecek maçları güncelle' düğmesini yeniden deneyin.")

with tabs[2]:
    st.subheader("Gerçek oranlara göre tarihsel analiz")
    available_markets = [m for m in MARKETS if find_odds_column(matches, m) is not None and MARKETS[m]["result_col"] in matches.columns]
    if not available_markets:
        st.warning("Seçili veri dosyalarında desteklenen gerçek oran sütunu bulunamadı. Oran uydurulmadı.")
    else:
        market = st.selectbox("Analiz pazarı", available_markets, key="analysis_market")
        work, odds_col = build_market_frame(matches, market)
        if work.empty:
            st.warning("Bu pazar için oranı ve sonucu birlikte bulunan yeterli kayıt yok.")
        else:
            wins = int(work["Kazandı"].sum())
            bets = len(work)
            net = float(work["Net birim getiri"].sum())
            a, b, c, d = st.columns(4)
            a.metric("Değerlendirilen maç", f"{bets:,}")
            b.metric("Tarihsel başarı", fmt_pct(wins, bets))
            c.metric("Net birim getiri", f"{net:+.2f}")
            d.metric("Birim getiri", f"{100 * net / bets:+.2f}%")
            st.caption(f"Kullanılan gerçek oran sütunu: {odds_col}. Her kayıt için 1 birim bahis varsayılır; komisyon ve vergiler dahil değildir.")
            summary = band_summary(work)
            st.markdown("**Oran aralıklarına göre sonuç**")
            st.dataframe(summary, use_container_width=True, hide_index=True)
            min_sample = st.slider("Öne çıkarma için asgari maç sayısı", 5, 100, 20, 5)
            eligible = summary[summary["Mac_sayisi"] >= min_sample].copy()
            if eligible.empty:
                st.info(f"Hiçbir oran aralığında {min_sample} maçlık örneklem yok. Güvenilir bir karşılaştırma için daha fazla veri gerekir.")
            else:
                st.markdown("**Geçmiş örneklemde en yüksek birim getiri gösteren aralıklar**")
                st.dataframe(eligible.sort_values("Getiri %", ascending=False).head(5), use_container_width=True, hide_index=True)
                st.caption("Bu sıralama yalnızca geçmiş örneklemi tarif eder; geleceğe yönelik garanti veya kesin bahis önerisi değildir.")

with tabs[3]:
    st.subheader("Benzer oranlı geçmiş maçlar")
    market_options = [m for m in MARKETS if find_odds_column(matches, m) is not None and MARKETS[m]["result_col"] in matches.columns]
    if not market_options:
        st.warning("Benzer oran araması için gerekli oran sütunları mevcut değil.")
    else:
        market2 = st.selectbox("Pazar", market_options, key="similar_market")
        odds_col2 = find_odds_column(matches, market2)
        odds_values = pd.to_numeric(matches[odds_col2], errors="coerce")
        valid_odds = odds_values[odds_values.between(1.01, 100)]
        if valid_odds.empty:
            st.warning("Bu pazar için geçerli oran bulunamadı.")
        else:
            default_odds = float(valid_odds.median())
            target_odds = st.number_input("Karşılaştırılacak oran", min_value=1.01, max_value=100.0, value=round(default_odds, 2), step=0.05)
            tolerance = st.slider("Oran toleransı (±)", 0.01, 0.50, 0.10, 0.01)
            work2, _ = build_market_frame(matches, market2)
            similar = work2[(work2["Oran"] >= target_odds - tolerance) & (work2["Oran"] <= target_odds + tolerance)].copy()
            if similar.empty:
                st.info("Bu aralıkta oranı ve sonucu bilinen geçmiş maç bulunamadı.")
            else:
                wins2 = int(similar["Kazandı"].sum())
                st.metric("Benzer geçmiş maç", len(similar))
                st.metric("Bu pazardaki tarihsel başarı", fmt_pct(wins2, len(similar)))
                display_cols = [c for c in ["Date", "Lig", "HomeTeam", "AwayTeam", "FTHG", "FTAG", "Oran", MARKETS[market2]["result_col"]] if c in similar.columns]
                st.dataframe(similar[display_cols].sort_values("Date", ascending=False), use_container_width=True, hide_index=True)
                st.caption("Benzer oranlı geçmiş maçlar yalnızca karşılaştırma içindir; maçlar birbirinden bağımsız olmayabilir.")

with tabs[4]:
    st.subheader("Gol ve KG Var/Yok istatistikleri")
    if "Toplam Gol" not in matches.columns:
        st.info("Tam maç skoru sütunları kaynakta bulunamadı.")
    else:
        goal_data = matches[matches["Toplam Gol"].notna()].copy()
        if goal_data.empty:
            st.info("Tamamlanmış maç skoru bulunamadı.")
        else:
            total = len(goal_data)
            over = int((goal_data["Toplam Gol"] > 2.5).sum())
            btts = int((goal_data["KG Sonuç"] == "Var").sum())
            g1, g2, g3 = st.columns(3)
            g1.metric("Maç başı ortalama gol", f"{goal_data['Toplam Gol'].mean():.2f}")
            g2.metric("2.5 Üst oranı", fmt_pct(over, total))
            g3.metric("KG Var oranı", fmt_pct(btts, total))
            by_league = goal_data.groupby("Lig").agg(
                Mac=("Toplam Gol", "size"),
                Ortalama_gol=("Toplam Gol", "mean"),
                KG_Var=("KG Sonuç", lambda s: int((s == "Var").sum())),
                Ust_2_5=("Toplam Gol", lambda s: int((s > 2.5).sum())),
            ).reset_index()
            by_league["KG Var %"] = (100 * by_league["KG_Var"] / by_league["Mac"]).round(1)
            by_league["2.5 Üst %"] = (100 * by_league["Ust_2_5"] / by_league["Mac"]).round(1)
            by_league["Ortalama_gol"] = by_league["Ortalama_gol"].round(2)
            st.dataframe(by_league, use_container_width=True, hide_index=True)
            st.caption("KG Var/Yok için burada sonuç yüzdesi hesaplanır; kaynakta gerçek KG oranı yoksa oran analizi veya getiri hesabı yapılmaz.")

with tabs[5]:
    st.subheader("Filtrelenmiş maç kayıtları")
    if matches.empty or "Date" not in matches.columns or matches["Date"].dropna().empty:
        st.info("Seçili sezon/ligler için listelenecek geçmiş maç kaydı yok.")
    else:
        available_leagues = sorted(matches["Lig"].dropna().unique().tolist())
        league_filter = st.multiselect(
            "Maç listesindeki ligler", available_leagues,
            default=available_leagues, key="match_leagues"
        )
        date_min = matches["Date"].min().date()
        date_max = matches["Date"].max().date()
        date_range = st.date_input(
            "Tarih aralığı", value=(date_min, date_max),
            min_value=date_min, max_value=date_max
        )
        listing = matches[matches["Lig"].isin(league_filter)].copy()
        if isinstance(date_range, (tuple, list)) and len(date_range) == 2:
            start_date, end_date = date_range
            listing = listing[listing["Date"].dt.date.between(start_date, end_date)]
        cols = [c for c in ["Date", "Lig", "HomeTeam", "AwayTeam", "FTHG", "FTAG", "HTHG", "HTAG", "MS Sonuç", "KG Sonuç", "2.5 Sonuç"] if c in listing.columns]
        st.dataframe(listing[cols].sort_values("Date", ascending=False), use_container_width=True, hide_index=True)

with tabs[6]:
    st.subheader("Oran düşüşü ve şüpheli hareket kontrolü")
    st.caption("Bir oran düşüşü tek başına şike kanıtı değildir. Sinyal yalnızca sayısal hareketi gösterir.")
    st.markdown("**Gelecek maçlar: bu uygulamada kaydedilen oran gözlemleri**")
    movement = load_odds_movement()
    if movement.empty:
        st.info("Henüz aynı maç/pazar için farklı zamanlarda kaydedilmiş en az iki oran gözlemi yok. Gelecek maçları farklı zamanlarda güncelle; ilk kayıt tek başına düşüş analizi oluşturmaz.")
    else:
        st.dataframe(movement.sort_values("Değişim %"), use_container_width=True, hide_index=True)
        drops = movement[movement["Değişim %"] <= -10]
        if drops.empty:
            st.success("Kaydedilmiş gözlemlerde %10 veya daha büyük oran düşüşü bulunmadı.")
        else:
            st.warning(f"%10 veya daha büyük düşüş gösteren {len(drops)} oran hareketi var. Bu, şike kanıtı değildir; kaynak/örneklem doğrulanmalıdır.")
            st.dataframe(drops, use_container_width=True, hide_index=True)
    st.markdown("**Tamamlanmış geçmiş maçlar: açılış ve kapanış oranı karşılaştırması**")
    hist_movement = historical_open_close_analysis(matches)
    if hist_movement.empty:
        st.info("Seçilen sezon dosyasında eşleştirilebilen açılış/kapanış oran sütunları bulunmuyor.")
    else:
        st.dataframe(hist_movement, use_container_width=True, hide_index=True)
    st.caption("Gelecek maçlarda kaynak yalnızca tek bir oran görüntüsü sunuyorsa açılış oranı varsayılmaz. SQLite kayıtları barındırma ortamı yeniden başlatıldığında silinebilir.")

with tabs[7]:
    st.subheader("Verileri dışa aktar")
    st.write("Seçili sezon ve ligler için alınan ham kayıtları veya maç sonuçları eklenmiş verileri CSV olarak indirebilirsin.")
    st.download_button(
        "Ham veriyi CSV indir",
        data=matches.to_csv(index=False).encode("utf-8-sig"),
        file_name=f"frekans_x_{selected_season}_ham_veri.csv",
        mime="text/csv",
        use_container_width=True,
    )
    st.download_button(
        "Analiz sütunlarıyla CSV indir",
        data=prepare_matches(matches).to_csv(index=False).encode("utf-8-sig"),
        file_name=f"frekans_x_{selected_season}_analiz.csv",
        mime="text/csv",
        use_container_width=True,
    )
