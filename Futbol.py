# -*- coding: utf-8 -*-
"""
FREKANS X — Tarihsel Futbol Verisi, Oran Analizi ve Tahmin Platformu
Kurulum:
    python -m pip install -r requirements.txt
Çalıştırma:
    streamlit run frekans_x.py

Notlar:
- football-data.co.uk verilerinin kapsamı lig/sezon bazında değişebilir.
- Bu uygulama istatistiksel analiz aracıdır; bahis kazancı garanti etmez.
- Backtest, seçilen oran aralığında ev sahibi galibiyeti bahis stratejisinin
  basit tarihsel simülasyonudur; tek başına tahmin modelinin kanıtı değildir.
"""

from __future__ import annotations

import datetime as dt
import math
from io import StringIO
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

import numpy as np
import pandas as pd
import requests
import streamlit as st
from sqlalchemy import (
    Column, Date, DateTime, Float, Integer, String, UniqueConstraint,
    create_engine, inspect
)
from sqlalchemy.orm import declarative_base, sessionmaker

# ---------------------------------------------------------------------
# 1. CONFIGURATION
# ---------------------------------------------------------------------

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
DATA_DIR.mkdir(parents=True, exist_ok=True)
DB_PATH = BASE_DIR / "frekans_x.db"
DATABASE_URL = f"sqlite:///{DB_PATH.as_posix()}"

SUPPORTED_LEAGUES: Dict[str, Dict[str, str]] = {
    "TUR_SL": {"name": "Süper Lig", "country": "Türkiye", "code": "T1"},
    "TUR_1L": {"name": "1. Lig", "country": "Türkiye", "code": "T2"},
    "ENG_PL": {"name": "Premier League", "country": "İngiltere", "code": "E0"},
    "ENG_CH": {"name": "Championship", "country": "İngiltere", "code": "E1"},
    "ESP_PD": {"name": "La Liga", "country": "İspanya", "code": "SP1"},
    "ESP_SD": {"name": "La Liga 2", "country": "İspanya", "code": "SP2"},
    "ITA_SA": {"name": "Serie A", "country": "İtalya", "code": "I1"},
    "ITA_SB": {"name": "Serie B", "country": "İtalya", "code": "I2"},
    "GER_BL1": {"name": "Bundesliga", "country": "Almanya", "code": "D1"},
    "GER_BL2": {"name": "2. Bundesliga", "country": "Almanya", "code": "D2"},
    "FRA_L1": {"name": "Ligue 1", "country": "Fransa", "code": "F1"},
    "FRA_L2": {"name": "Ligue 2", "country": "Fransa", "code": "F2"},
    "NED_ED": {"name": "Eredivisie", "country": "Hollanda", "code": "N1"},
    "POR_PL": {"name": "Primeira Liga", "country": "Portekiz", "code": "P1"},
    "BEL_PL": {"name": "Pro League", "country": "Belçika", "code": "B1"},
}
# Season format used by football-data.co.uk: 2526 = 2025/26.
# Last three completed seasons as of October 2026.
SUPPORTED_SEASONS = ["2526", "2425", "2324"]

MARKET_METADATA = {
    "MS_1": "Maç Sonu 1", "MS_X": "Maç Sonu X", "MS_2": "Maç Sonu 2",
    "CS_1X": "Çifte Şans 1X", "CS_X2": "Çifte Şans X2", "CS_12": "Çifte Şans 12",
    "BTTS_YES": "KG Var", "BTTS_NO": "KG Yok",
    "OU_05": "0.5 Üst", "OU_15": "1.5 Üst", "OU_25": "2.5 Üst",
    "OU_35": "3.5 Üst", "OU_45": "4.5 Üst",
    "HT_1": "İlk Yarı 1", "HT_X": "İlk Yarı X", "HT_2": "İlk Yarı 2",
    "HT_OU_15": "İlk Yarı 1.5 Üst", "TG_46": "Toplam Gol 4–6",
    "TG_7PLUS": "Toplam Gol 7+", "EXACT_SCORE": "Doğru Skor",
    "CORNER_KICKS": "Korner bahisleri", "YELLOW_CARDS": "Kart bahisleri",
    "PLAYER_PROPS": "Oyuncu bahisleri",
}

engine = create_engine(
    DATABASE_URL, echo=False, future=True,
    connect_args={"check_same_thread": False, "timeout": 30}
)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)
Base = declarative_base()


class MatchModel(Base):
    __tablename__ = "matches"
    id = Column(Integer, primary_key=True)
    league_code = Column(String, nullable=False, index=True)
    season = Column(String, nullable=False, index=True)
    date = Column(Date, nullable=False, index=True)
    home_team = Column(String, nullable=False, index=True)
    away_team = Column(String, nullable=False, index=True)
    fthg = Column(Integer)
    ftag = Column(Integer)
    ftr = Column(String)
    hthg = Column(Integer)
    htag = Column(Integer)
    htr = Column(String)
    odds_h_open = Column(Float)
    odds_d_open = Column(Float)
    odds_a_open = Column(Float)
    odds_h_close = Column(Float)
    odds_d_close = Column(Float)
    odds_a_close = Column(Float)
    odds_over_25_open = Column(Float)
    odds_under_25_open = Column(Float)
    odds_over_25_close = Column(Float)
    odds_under_25_close = Column(Float)
    odds_btts_yes = Column(Float)
    odds_btts_no = Column(Float)
    created_at = Column(DateTime, default=dt.datetime.utcnow)
    __table_args__ = (
        UniqueConstraint("league_code", "date", "home_team", "away_team",
                         name="uq_match_identity"),
    )


class PredictionLogModel(Base):
    __tablename__ = "prediction_logs"
    id = Column(Integer, primary_key=True)
    match_id = Column(Integer, index=True, nullable=True)
    prediction_date = Column(DateTime, default=dt.datetime.utcnow)
    league_code = Column(String)
    home_team = Column(String)
    away_team = Column(String)
    predicted_market = Column(String)
    predicted_pick = Column(String)
    prob_estimated = Column(Float)
    odds_val = Column(Float)
    actual_outcome = Column(String, nullable=True)
    is_correct = Column(Integer, nullable=True)
    brier_score = Column(Float, nullable=True)
    profit_loss = Column(Float, nullable=True)


def init_db() -> None:
    Base.metadata.create_all(bind=engine)
    # Future schema changes should use a migration tool. create_all does not
    # add columns to old SQLite tables, so warn rather than silently failing.
    required = {c.name for c in MatchModel.__table__.columns}
    existing = {c["name"] for c in inspect(engine).get_columns("matches")}
    missing = required - existing
    if missing:
        raise RuntimeError(
            "Mevcut veritabanı eski şemada ve şu sütunlar eksik: "
            + ", ".join(sorted(missing))
            + ". Uygulamayı kapatıp veritabanını yedekleyin; ardından "
              "frekans_x.db dosyasını yeniden adlandırın veya migration uygulayın."
        )


# ---------------------------------------------------------------------
# 2. DATA INGESTION
# ---------------------------------------------------------------------

def safe_float(value: Any) -> Optional[float]:
    if value is None or pd.isna(value):
        return None
    try:
        result = float(value)
        return result if math.isfinite(result) and result > 1.0 else None
    except (TypeError, ValueError):
        return None


def safe_int(value: Any) -> Optional[int]:
    if value is None or pd.isna(value):
        return None
    try:
        return int(float(value))
    except (TypeError, ValueError, OverflowError):
        return None


def parse_match_date(value: Any) -> Optional[dt.date]:
    if value is None or pd.isna(value):
        return None
    if isinstance(value, (dt.date, dt.datetime, pd.Timestamp)):
        return value.date() if isinstance(value, (dt.datetime, pd.Timestamp)) else value
    raw = str(value).strip()
    if not raw:
        return None
    for fmt in ("%d/%m/%Y", "%d/%m/%y", "%Y-%m-%d", "%d-%m-%Y"):
        try:
            return dt.datetime.strptime(raw, fmt).date()
        except ValueError:
            pass
    parsed = pd.to_datetime(raw, dayfirst=True, errors="coerce")
    return None if pd.isna(parsed) else parsed.date()


def row_value(row: pd.Series, *names: str) -> Any:
    for name in names:
        if name in row.index and not pd.isna(row[name]):
            return row[name]
    return None


class DataLoader:
    def fetch_and_sync_league_season(
        self, league_key: str, season: str
    ) -> Tuple[bool, str, int, int]:
        if league_key not in SUPPORTED_LEAGUES:
            return False, f"Geçersiz lig kodu: {league_key}", 0, 0
        if season not in SUPPORTED_SEASONS:
            return False, f"Desteklenmeyen sezon: {season}", 0, 0

        league = SUPPORTED_LEAGUES[league_key]
        url = f"https://www.football-data.co.uk/mmz4281/{season}/{league['code']}.csv"
        try:
            response = requests.get(
                url, timeout=(5, 25),
                headers={"User-Agent": "FREKANS-X/1.0 (historical football analysis)"}
            )
            if response.status_code != 200:
                return False, (
                    f"Kaynak HTTP {response.status_code} döndürdü. "
                    "Bu lig/sezon dosyası kaynakta olmayabilir. URL: " + url
                ), 0, 0
            if not response.text.strip():
                return False, "Kaynak boş CSV döndürdü.", 0, 0
            try:
                df = pd.read_csv(StringIO(response.text), low_memory=False)
            except Exception as exc:
                return False, f"CSV okunamadı: {exc}", 0, 0
            df.columns = [str(c).strip() for c in df.columns]
            if df.empty or not {"Date"}.issubset(df.columns):
                return False, "CSV boş veya Date sütunu bulunamadı.", 0, 0
            if not ({"HomeTeam", "Home"} & set(df.columns)) or not ({"AwayTeam", "Away"} & set(df.columns)):
                return False, "CSV içinde ev/deplasman takım sütunları bulunamadı.", 0, 0
            inserted, updated, skipped = self._upsert_dataframe(df, league_key, season)
            return True, (
                f"Tamamlandı: {inserted} yeni maç, {updated} güncellenen maç, "
                f"{skipped} atlanan satır. Kaynak: football-data.co.uk"
            ), inserted, updated
        except requests.RequestException as exc:
            return False, f"İnternet/istek hatası: {exc}", 0, 0
        except Exception as exc:
            return False, f"Veri indirme/işleme hatası: {type(exc).__name__}: {exc}", 0, 0

    def _upsert_dataframe(
        self, df: pd.DataFrame, league_key: str, season: str
    ) -> Tuple[int, int, int]:
        inserted = updated = skipped = 0
        session = SessionLocal()
        try:
            for _, row in df.iterrows():
                date_value = parse_match_date(row_value(row, "Date", "DATE"))
                home = row_value(row, "HomeTeam", "Home")
                away = row_value(row, "AwayTeam", "Away")
                home = str(home).strip() if home is not None else ""
                away = str(away).strip() if away is not None else ""
                if not date_value or not home or not away or home.lower() == "nan" or away.lower() == "nan":
                    skipped += 1
                    continue

                fthg = safe_int(row_value(row, "FTHG"))
                ftag = safe_int(row_value(row, "FTAG"))
                hthg = safe_int(row_value(row, "HTHG"))
                htag = safe_int(row_value(row, "HTAG"))
                ftr = row_value(row, "FTR")
                htr = row_value(row, "HTR")
                ftr = str(ftr).strip().upper() if ftr is not None else None
                htr = str(htr).strip().upper() if htr is not None else None
                if ftr not in {"H", "D", "A"}:
                    if fthg is not None and ftag is not None:
                        ftr = "H" if fthg > ftag else "A" if fthg < ftag else "D"
                    else:
                        ftr = None
                if htr not in {"H", "D", "A"}:
                    if hthg is not None and htag is not None:
                        htr = "H" if hthg > htag else "A" if hthg < htag else "D"
                    else:
                        htr = None

                # Explicit aliases; opening and closing columns are kept separate.
                values = {
                    "season": season, "fthg": fthg, "ftag": ftag, "ftr": ftr,
                    "hthg": hthg, "htag": htag, "htr": htr,
                    "odds_h_open": safe_float(row_value(row, "B365H", "AvgH", "PSH")),
                    "odds_d_open": safe_float(row_value(row, "B365D", "AvgD", "PSD")),
                    "odds_a_open": safe_float(row_value(row, "B365A", "AvgA", "PSA")),
                    "odds_h_close": safe_float(row_value(row, "B365CH", "AvgCH", "PSCH")),
                    "odds_d_close": safe_float(row_value(row, "B365CD", "AvgCD", "PSCD")),
                    "odds_a_close": safe_float(row_value(row, "B365CA", "AvgCA", "PSCA")),
                    "odds_over_25_open": safe_float(row_value(row, "B365>2.5", "Avg>2.5")),
                    "odds_under_25_open": safe_float(row_value(row, "B365<2.5", "Avg<2.5")),
                    "odds_over_25_close": safe_float(row_value(row, "B365C>2.5", "AvgC>2.5")),
                    "odds_under_25_close": safe_float(row_value(row, "B365C<2.5", "AvgC<2.5")),
                    # These columns are not consistently available in this source.
                    "odds_btts_yes": safe_float(row_value(row, "B365CBBTSH", "B365BBTSH")),
                    "odds_btts_no": safe_float(row_value(row, "B365CBBTSN", "B365BBTSN")),
                }
                existing = session.query(MatchModel).filter_by(
                    league_code=league_key, date=date_value,
                    home_team=home, away_team=away
                ).one_or_none()
                if existing:
                    for key, value in values.items():
                        # Preserve stored odds when this CSV has no value for a field.
                        if value is not None or key in {"ftr", "fthg", "ftag", "htr", "hthg", "htag"}:
                            setattr(existing, key, value)
                    updated += 1
                else:
                    session.add(MatchModel(
                        league_code=league_key, date=date_value,
                        home_team=home, away_team=away, **values
                    ))
                    inserted += 1
            session.commit()
            return inserted, updated, skipped
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()


# ---------------------------------------------------------------------
# 3. DATAFRAME AND HISTORICAL ODDS FREQUENCIES
# ---------------------------------------------------------------------

MATCH_COLUMNS = [
    "id", "league", "season", "date", "home_team", "away_team",
    "fthg", "ftag", "ftr", "hthg", "htag", "htr",
    "odds_h_open", "odds_d_open", "odds_a_open",
    "odds_h_close", "odds_d_close", "odds_a_close",
    "odds_over_25_open", "odds_under_25_open",
    "odds_over_25_close", "odds_under_25_close",
    "odds_btts_yes", "odds_btts_no",
]


def get_matches_dataframe(league_code: Optional[str] = None) -> pd.DataFrame:
    session = SessionLocal()
    try:
        query = session.query(MatchModel)
        if league_code and league_code != "Tüm Ligler":
            query = query.filter(MatchModel.league_code == league_code)
        records = query.order_by(MatchModel.date.asc()).all()
        if not records:
            return pd.DataFrame(columns=MATCH_COLUMNS)
        result = []
        for m in records:
            result.append({
                "id": m.id, "league": m.league_code, "season": m.season,
                "date": m.date, "home_team": m.home_team, "away_team": m.away_team,
                "fthg": m.fthg, "ftag": m.ftag, "ftr": m.ftr,
                "hthg": m.hthg, "htag": m.htag, "htr": m.htr,
                "odds_h_open": m.odds_h_open, "odds_d_open": m.odds_d_open,
                "odds_a_open": m.odds_a_open, "odds_h_close": m.odds_h_close,
                "odds_d_close": m.odds_d_close, "odds_a_close": m.odds_a_close,
                "odds_over_25_open": m.odds_over_25_open,
                "odds_under_25_open": m.odds_under_25_open,
                "odds_over_25_close": m.odds_over_25_close,
                "odds_under_25_close": m.odds_under_25_close,
                "odds_btts_yes": m.odds_btts_yes, "odds_btts_no": m.odds_btts_no,
            })
        return pd.DataFrame(result, columns=MATCH_COLUMNS)
    finally:
        session.close()


def find_similar_odds_matches(
    df: pd.DataFrame, target_h: float, target_d: float, target_a: float,
    margin_percent: float = 0.05, use_closing_odds: bool = True,
    league_filter: Optional[str] = None
) -> pd.DataFrame:
    if df.empty or min(target_h, target_d, target_a) <= 0:
        return pd.DataFrame()
    work = df.copy()
    if league_filter and league_filter != "Tüm Ligler":
        work = work[work["league"] == league_filter]
    suffix = "close" if use_closing_odds else "open"
    cols = [f"odds_{x}_{suffix}" for x in ("h", "d", "a")]
    work = work.dropna(subset=cols).copy()
    if work.empty:
        return work
    targets = np.array([target_h, target_d, target_a], dtype=float)
    odds = work[cols].astype(float).to_numpy()
    # Symmetric relative error, averaged across the three odds.
    diffs = np.abs(odds - targets) / targets
    work["diff_h"] = diffs[:, 0]
    work["diff_d"] = diffs[:, 1]
    work["diff_a"] = diffs[:, 2]
    work["total_diff"] = diffs.mean(axis=1)
    return work[work["total_diff"] <= margin_percent].sort_values("total_diff")


def calculate_market_frequencies(df: pd.DataFrame) -> Dict[str, Any]:
    if df.empty:
        return {"sample_size": 0, "status": "Eşleşen geçmiş maç bulunamadı."}
    out: Dict[str, Any] = {"sample_size": len(df)}
    # Use each market's own valid denominator, not the total sample.
    result_rows = df.dropna(subset=["ftr"])
    n_result = len(result_rows)
    for key, code in (("MS_1", "H"), ("MS_X", "D"), ("MS_2", "A")):
        out[key] = round((result_rows["ftr"].eq(code).mean() * 100), 1) if n_result else None
    out["CS_1X"] = round(out.get("MS_1", 0) + out.get("MS_X", 0), 1) if n_result else None
    out["CS_X2"] = round(out.get("MS_X", 0) + out.get("MS_2", 0), 1) if n_result else None
    out["CS_12"] = round(out.get("MS_1", 0) + out.get("MS_2", 0), 1) if n_result else None

    goals = df.dropna(subset=["fthg", "ftag"]).copy()
    n_goals = len(goals)
    if n_goals:
        total = goals["fthg"] + goals["ftag"]
        for threshold, key in ((0, "OU_05"), (1, "OU_15"), (2, "OU_25"), (3, "OU_35"), (4, "OU_45")):
            out[key] = round((total > threshold).mean() * 100, 1)
        btts = (goals["fthg"] > 0) & (goals["ftag"] > 0)
        out["BTTS_YES"] = round(btts.mean() * 100, 1)
        out["BTTS_NO"] = round((~btts).mean() * 100, 1)
        out["TG_46"] = round(total.between(4, 6).mean() * 100, 1)
        out["TG_7PLUS"] = round((total >= 7).mean() * 100, 1)
    else:
        for key in ("OU_05", "OU_15", "OU_25", "OU_35", "OU_45", "BTTS_YES", "BTTS_NO", "TG_46", "TG_7PLUS"):
            out[key] = None

    ht = df.dropna(subset=["htr"]).copy()
    n_ht = len(ht)
    for key, code in (("HT_1", "H"), ("HT_X", "D"), ("HT_2", "A")):
        out[key] = round(ht["htr"].eq(code).mean() * 100, 1) if n_ht else None
    ht_goals = ht.dropna(subset=["hthg", "htag"])
    out["HT_OU_15"] = round((((ht_goals["hthg"] + ht_goals["htag"]) > 1).mean() * 100), 1) if not ht_goals.empty else None
    # Approximate 95% maximum margin of error for a proportion, in percentage points.
    out["std_error"] = round(1.96 * 50 / math.sqrt(n_goals), 1) if n_goals else None
    out["status"] = "Örneklem daha anlamlı (n≥100)" if n_goals >= 100 else (
        "Orta örneklem (30≤n<100)" if n_goals >= 30 else "Düşük örneklem; belirsizlik yüksek"
    )
    return out


# ---------------------------------------------------------------------
# 4. POISSON PREDICTION
# ---------------------------------------------------------------------

class MatchPredictorEngine:
    def __init__(self, df_all: pd.DataFrame):
        self.df = df_all.copy()
        if not self.df.empty:
            self.df["date"] = pd.to_datetime(self.df["date"], errors="coerce")
            self.df = self.df.dropna(subset=["date"]).sort_values("date")
            self.df = self.df.dropna(subset=["fthg", "ftag"]).copy()
            self.df["fthg"] = pd.to_numeric(self.df["fthg"], errors="coerce")
            self.df["ftag"] = pd.to_numeric(self.df["ftag"], errors="coerce")
            self.df = self.df.dropna(subset=["fthg", "ftag"])

    @staticmethod
    def _poisson_probs(lam: float, max_goals: int = 10) -> np.ndarray:
        lam = max(0.01, min(float(lam), 8.0))
        probs = np.array([
            math.exp(-lam) * (lam ** k) / math.factorial(k)
            for k in range(max_goals)
        ], dtype=float)
        # Include residual probability in the last bucket to keep total near 1.
        probs[-1] += max(0.0, 1.0 - probs.sum())
        return probs

    def predict_match(self, home_team: str, away_team: str, league_code: Optional[str] = None) -> Dict[str, Any]:
        if self.df.empty:
            return {"error": "Tahmin için tamamlanmış maç verisi bulunamadı."}
        if home_team == away_team:
            return {"error": "Ev sahibi ve deplasman takımı aynı seçilemez."}
        df = self.df
        if league_code and league_code != "Tüm Ligler":
            df = df[df["league"] == league_code]
        if df.empty:
            return {"error": "Seçilen ligde tamamlanmış maç bulunamadı."}

        # Team recent matches are date-sorted; prefer venue-specific samples,
        # falling back to all matches if there are fewer than 4 venue matches.
        home_all = df[(df["home_team"] == home_team) | (df["away_team"] == home_team)].tail(12)
        away_all = df[(df["home_team"] == away_team) | (df["away_team"] == away_team)].tail(12)
        home_venue = df[df["home_team"] == home_team].tail(8)
        away_venue = df[df["away_team"] == away_team].tail(8)
        home_sample = home_venue if len(home_venue) >= 4 else home_all
        away_sample = away_venue if len(away_venue) >= 4 else away_all
        if len(home_sample) < 4 or len(away_sample) < 4:
            return {"error": "Takımlardan biri için en az 4 tamamlanmış geçmiş maç bulunamadı."}

        # Goals scored/conceded by the selected team regardless of venue.
        def team_for_metrics(sample: pd.DataFrame, team: str):
            scored = np.where(sample["home_team"].eq(team), sample["fthg"], sample["ftag"])
            conceded = np.where(sample["home_team"].eq(team), sample["ftag"], sample["fthg"])
            return np.asarray(scored, dtype=float), np.asarray(conceded, dtype=float)

        hs, hc = team_for_metrics(home_sample, home_team)
        aws, awc = team_for_metrics(away_sample, away_team)
        league_home_avg = float(df["fthg"].mean())
        league_away_avg = float(df["ftag"].mean())
        if league_home_avg <= 0 or league_away_avg <= 0:
            return {"error": "Lig gol ortalamaları hesaplanamadı."}

        # Smoothed ratios reduce extreme estimates from tiny samples.
        home_attack = (float(hs.mean()) + league_home_avg) / (2 * league_home_avg)
        away_defence = (float(awc.mean()) + league_home_avg) / (2 * league_home_avg)
        away_attack = (float(aws.mean()) + league_away_avg) / (2 * league_away_avg)
        home_defence = (float(hc.mean()) + league_away_avg) / (2 * league_away_avg)
        lam_h = max(0.15, min(4.5, league_home_avg * home_attack * away_defence))
        lam_a = max(0.15, min(4.5, league_away_avg * away_attack * home_defence))

        p_home_goals = self._poisson_probs(lam_h)
        p_away_goals = self._poisson_probs(lam_a)
        matrix = np.outer(p_home_goals, p_away_goals)
        p1 = float(np.tril(matrix, -1).sum())
        px = float(np.trace(matrix))
        p2 = float(np.triu(matrix, 1).sum())
        over25 = float(sum(matrix[i, j] for i in range(10) for j in range(10) if i + j >= 3))
        under25 = 1.0 - over25
        btts = float(sum(matrix[i, j] for i in range(1, 10) for j in range(1, 10)))
        def fair(p: float) -> Optional[float]:
            return round(1 / p, 2) if p > 0 else None
        score_probs = sorted(
            [(float(matrix[i, j]), f"{i}-{j}") for i in range(7) for j in range(7)],
            reverse=True
        )[:5]
        picks = []
        for label, prob in (("MS 1", p1), ("MS X", px), ("MS 2", p2), ("2.5 Üst", over25), ("KG Var", btts)):
            if prob >= 0.58:
                picks.append({"market": label, "prob": round(prob * 100, 1), "fair_odds": fair(prob)})
        return {
            "home_team": home_team, "away_team": away_team,
            "expected_goals_home": round(lam_h, 2), "expected_goals_away": round(lam_a, 2),
            "prob_ms1": round(p1 * 100, 1), "prob_msx": round(px * 100, 1),
            "prob_ms2": round(p2 * 100, 1), "prob_over_25": round(over25 * 100, 1),
            "prob_under_25": round(under25 * 100, 1), "prob_btts": round(btts * 100, 1),
            "fair_odds_ms1": fair(p1), "fair_odds_msx": fair(px), "fair_odds_ms2": fair(p2),
            "top_scores": [{"score": score, "prob": round(prob * 100, 2)} for prob, score in score_probs],
            "recommended_picks": picks,
            "warning": "Model yalnızca tarihsel gol verisine dayanır; kadro, sakatlık, fikstür ve güncel oranları içermez."
        }


# ---------------------------------------------------------------------
# 5. ODDS MOVEMENT AND BACKTEST
# ---------------------------------------------------------------------

class OddsMovementAnalyzer:
    @staticmethod
    def analyze_movements(df: pd.DataFrame, min_drop_percent: float = 0.08) -> pd.DataFrame:
        if df.empty:
            return pd.DataFrame()
        needed = ["odds_h_open", "odds_h_close", "odds_d_open", "odds_d_close", "odds_a_open", "odds_a_close"]
        valid = df.dropna(subset=needed).copy()
        if valid.empty:
            return pd.DataFrame()
        for outcome in ("h", "d", "a"):
            opening, closing = valid[f"odds_{outcome}_open"], valid[f"odds_{outcome}_close"]
            valid[f"drop_{outcome}"] = (opening - closing) / opening
        drop_cols = ["drop_h", "drop_d", "drop_a"]
        valid["largest_drop"] = valid[drop_cols].max(axis=1)
        valid = valid[valid["largest_drop"] >= min_drop_percent].copy()
        if valid.empty:
            return pd.DataFrame()
        def market(row):
            mapping = {"drop_h": "MS 1 düşüş", "drop_d": "MS X düşüş", "drop_a": "MS 2 düşüş"}
            return mapping[max(drop_cols, key=lambda c: row[c])]
        valid["significant_market"] = valid.apply(market, axis=1)
        valid["largest_drop_pct"] = (valid["largest_drop"] * 100).round(1)
        cols = ["league", "season", "date", "home_team", "away_team", "ftr",
                "odds_h_open", "odds_h_close", "drop_h", "odds_d_open", "odds_d_close",
                "drop_d", "odds_a_open", "odds_a_close", "drop_a",
                "significant_market", "largest_drop_pct"]
        return valid.sort_values("largest_drop", ascending=False)[cols]


class BacktestValidator:
    @staticmethod
    def run_historical_backtest(
        df: pd.DataFrame, min_odds: float = 1.50, max_odds: float = 3.50,
        commission_rate: float = 0.02, stake: float = 100.0
    ) -> Dict[str, Any]:
        if df.empty:
            return {"status": "Test için veri yok."}
        if min_odds <= 1 or max_odds < min_odds or not (0 <= commission_rate < 1) or stake <= 0:
            return {"status": "Oran, komisyon veya bahis tutarı geçersiz."}
        # This tests a transparent baseline: bet on home win whenever closing odds
        # lie in the chosen band. It is not a model-generated pick backtest.
        valid = df.dropna(subset=["fthg", "ftag", "ftr", "odds_h_close"]).copy()
        valid["odds_h_close"] = pd.to_numeric(valid["odds_h_close"], errors="coerce")
        valid = valid.dropna(subset=["odds_h_close"])
        valid = valid[valid["odds_h_close"].between(min_odds, max_odds)]
        if valid.empty:
            return {"status": "Seçilen oran aralığında tamamlanmış maç bulunamadı."}
        valid = valid.sort_values("date")
        outcomes = valid["ftr"].astype(str).str.upper()
        wins = outcomes.eq("H")
        losses = ~wins
        # Commission is applied to net winnings only, not stake returned.
        pnl_per_bet = np.where(
            wins,
            stake * (valid["odds_h_close"].astype(float) - 1) * (1 - commission_rate),
            -stake
        )
        total_bets = len(valid)
        total_stake = total_bets * stake
        net_pnl = float(pnl_per_bet.sum())
        total_return = total_stake + net_pnl
        # Brier score for market-implied probability, normalized for the 1X2 overround
        # cannot be accurately computed from only home odds; this is a binary proxy.
        implied = 1.0 / valid["odds_h_close"].astype(float).to_numpy()
        actual = wins.astype(float).to_numpy()
        brier = float(np.mean((implied - actual) ** 2))
        return {
            "total_bets": int(total_bets), "winning_bets": int(wins.sum()),
            "losing_bets": int(losses.sum()), "win_rate": round(float(wins.mean() * 100), 2),
            "total_stake": round(total_stake, 2), "total_return": round(total_return, 2),
            "net_pnl": round(net_pnl, 2), "roi_percent": round(net_pnl / total_stake * 100, 2),
            "brier_score": round(brier, 4),
            "first_date": str(valid["date"].min()), "last_date": str(valid["date"].max()),
            "note": "Basit kapanış-oranı filtresi; model tahminleriyle yapılan walk-forward test değildir. Brier, yalnızca ev sahibi olasılığı için yaklaşık ikili skordur."
        }


class WalkForwardBacktest:
    """Chronological model test: every prediction uses matches strictly before its date."""

    @staticmethod
    def run(df: pd.DataFrame, league_code: str, test_matches: int = 100,
            min_history: int = 30) -> Tuple[Dict[str, Any], pd.DataFrame]:
        required = {"date", "league", "home_team", "away_team", "fthg", "ftag"}
        if df.empty or not required.issubset(df.columns):
            return {"status": "Walk-forward testi için yeterli veri/sütun yok."}, pd.DataFrame()
        work = df.copy()
        work["date"] = pd.to_datetime(work["date"], errors="coerce")
        work["fthg"] = pd.to_numeric(work["fthg"], errors="coerce")
        work["ftag"] = pd.to_numeric(work["ftag"], errors="coerce")
        work = work.dropna(subset=["date", "fthg", "ftag", "home_team", "away_team"])
        if league_code and league_code != "Tüm Ligler":
            work = work[work["league"] == league_code]
        work = work.sort_values("date").reset_index(drop=True)
        if len(work) <= min_history:
            return {"status": f"Test için daha fazla veri gerekli (en az {min_history + 1} maç)."}, pd.DataFrame()
        test = work.tail(max(1, min(int(test_matches), 500))).copy()
        records = []
        for _, match in test.iterrows():
            # Exclude the whole matchday to prevent same-day result leakage.
            history = work[work["date"] < match["date"]]
            if len(history) < min_history:
                continue
            league_history = history
            if league_code == "Tüm Ligler":
                league_history = history[history["league"] == match["league"]]
            if len(league_history) < min_history:
                continue
            pred = MatchPredictorEngine(league_history).predict_match(
                str(match["home_team"]), str(match["away_team"])
            )
            if "error" in pred:
                continue
            actual_result = "H" if match["fthg"] > match["ftag"] else "A" if match["fthg"] < match["ftag"] else "D"
            probs = {"H": pred["prob_ms1"] / 100, "D": pred["prob_msx"] / 100, "A": pred["prob_ms2"] / 100}
            predicted_result = max(probs, key=probs.get)
            actual_over25 = int(match["fthg"] + match["ftag"] >= 3)
            actual_btts = int(match["fthg"] > 0 and match["ftag"] > 0)
            records.append({
                "date": match["date"].date().isoformat(), "league": match["league"],
                "home_team": match["home_team"], "away_team": match["away_team"],
                "actual_score": f"{int(match['fthg'])}-{int(match['ftag'])}",
                "actual_result": actual_result, "predicted_result": predicted_result,
                "prob_ms1_pct": round(probs["H"] * 100, 1),
                "prob_msx_pct": round(probs["D"] * 100, 1),
                "prob_ms2_pct": round(probs["A"] * 100, 1),
                "result_correct": int(predicted_result == actual_result),
                "over25_prob_pct": pred["prob_over_25"], "actual_over25": actual_over25,
                "btts_prob_pct": pred["prob_btts"], "actual_btts": actual_btts,
                "brier_1x2": round(sum((probs[k] - float(actual_result == k)) ** 2 for k in ("H", "D", "A")), 4),
                "brier_over25": round((pred["prob_over_25"] / 100 - actual_over25) ** 2, 4),
                "brier_btts": round((pred["prob_btts"] / 100 - actual_btts) ** 2, 4),
            })
        details = pd.DataFrame(records)
        if details.empty:
            return {"status": "Bu test aralığında takımlar için yeterli geçmiş bulunamadı."}, details
        summary = {
            "tested_matches": int(len(details)),
            "result_accuracy_pct": round(float(details["result_correct"].mean() * 100), 2),
            "brier_1x2": round(float(details["brier_1x2"].mean()), 4),
            "brier_over25": round(float(details["brier_over25"].mean()), 4),
            "brier_btts": round(float(details["brier_btts"].mean()), 4),
            "first_date": details["date"].min(), "last_date": details["date"].max(),
            "note": "Zamansal testtir: her maç tahmini yalnızca maç tarihinden önceki sonuçlarla üretilir. Geçmiş performans geleceği garanti etmez."
        }
        return summary, details


# ---------------------------------------------------------------------
# 6. STREAMLIT UI
# ---------------------------------------------------------------------

def league_label(key: str) -> str:
    item = SUPPORTED_LEAGUES[key]
    return f"{item['country']} — {item['name']} ({key})"


def main() -> None:
    st.set_page_config(
        page_title="FREKANS X — Futbol Analiz Sistemi",
        page_icon="⚽", layout="wide"
    )
    try:
        init_db()
    except Exception as exc:
        st.error(f"Veritabanı başlatılamadı: {exc}")
        st.stop()

    st.title("⚽ FREKANS X")
    st.caption("Tarihsel oran kombinasyonu • Gol frekansları • Poisson tahmini • Oran hareketi • Backtest")
    menu = st.sidebar.selectbox("Sistem Menüsü", [
        "1. Veri Güncelleme", "2. Oran Kombinasyonu Analizi",
        "3. Gelecek Maç Tahmini", "4. Oran Hareketleri",
        "5. Backtest", "6. Veri Kapsamı ve Tanımlar"
    ])

    if menu == "1. Veri Güncelleme":
        st.header("Veri İndirme ve Senkronizasyon")
        st.info("Veri kaynağı: football-data.co.uk. Her lig/sezon dosyası kaynakta bulunmayabilir.")
        c1, c2 = st.columns(2)
        with c1:
            league = st.selectbox("Lig", list(SUPPORTED_LEAGUES), format_func=league_label)
        with c2:
            season = st.selectbox("Sezon", SUPPORTED_SEASONS, format_func=lambda s: f"20{s[:2]}/20{s[2:]}")
        download_col1, download_col2 = st.columns(2)
        if download_col1.button("Seçili sezonu indir", type="primary"):
            with st.spinner("CSV indiriliyor, doğrulanıyor ve SQLite'a kaydediliyor..."):
                ok, msg, inserted, updated = DataLoader().fetch_and_sync_league_season(league, season)
            (st.success if ok else st.error)(msg)
            if ok:
                st.metric("Yeni kayıt", inserted)
                st.metric("Güncellenen kayıt", updated)
                st.rerun()
        if download_col2.button("Seçili lig için 3 sezonu indir"):
            progress = st.progress(0)
            messages = []
            total_inserted = total_updated = 0
            loader = DataLoader()
            for idx, season_code in enumerate(SUPPORTED_SEASONS, start=1):
                ok, msg, inserted, updated = loader.fetch_and_sync_league_season(league, season_code)
                messages.append(f"20{season_code[:2]}/20{season_code[2:]}: {msg}")
                total_inserted += inserted
                total_updated += updated
                progress.progress(idx / len(SUPPORTED_SEASONS))
            for message in messages:
                st.write(message)
            st.metric("Toplam yeni kayıt", total_inserted)
            st.metric("Toplam güncellenen kayıt", total_updated)
            if any("Tamamlandı" in message for message in messages):
                st.rerun()
        df = get_matches_dataframe()
        st.divider()
        st.subheader("Veritabanı Durumu")
        st.metric("Toplam maç kaydı", len(df))
        if not df.empty:
            counts = df.groupby("league").agg(
                Mac=("id", "count"), Ilk_Tarih=("date", "min"), Son_Tarih=("date", "max")
            ).reset_index().rename(columns={"league": "Lig"})
            counts["Lig Adı"] = counts["Lig"].map(lambda x: SUPPORTED_LEAGUES.get(x, {}).get("name", x))
            st.dataframe(counts[["Lig", "Lig Adı", "Mac", "Ilk_Tarih", "Son_Tarih"]], use_container_width=True)
            st.download_button("Veritabanı maçlarını CSV indir", df.to_csv(index=False).encode("utf-8-sig"),
                               "frekans_x_maclar.csv", "text/csv")

    elif menu == "2. Oran Kombinasyonu Analizi":
        st.header("Tarihsel Oran Kombinasyonu ve Frekans Analizi")
        df = get_matches_dataframe()
        if df.empty:
            st.warning("Veritabanı boş. Önce Veri Güncelleme bölümünden maç verisi indirin.")
            st.stop()
        c1, c2, c3 = st.columns(3)
        h = c1.number_input("MS 1 oranı", min_value=1.01, value=2.10, step=0.05)
        d = c2.number_input("MS X oranı", min_value=1.01, value=3.20, step=0.05)
        a = c3.number_input("MS 2 oranı", min_value=1.01, value=3.10, step=0.05)
        c4, c5, c6 = st.columns(3)
        margin = c4.slider("Ortalama göreli oran toleransı (%)", 1, 30, 5) / 100
        league = c5.selectbox("Lig filtresi", ["Tüm Ligler"] + list(SUPPORTED_LEAGUES), format_func=lambda x: "Tüm Ligler" if x == "Tüm Ligler" else league_label(x))
        closing = c6.radio("Oran türü", ["Kapanış", "Açılış"]) == "Kapanış"
        if st.button("Benzer maçları bul ve hesapla", type="primary"):
            similar = find_similar_odds_matches(df, h, d, a, margin, closing, league)
            metrics = calculate_market_frequencies(similar)
            if metrics["sample_size"] == 0:
                st.warning("Bu toleransla eşleşen maç bulunamadı. Toleransı artırın veya açılış/kapanış seçimini değiştirin.")
            else:
                st.subheader(f"Sonuçlar — {metrics['sample_size']} eşleşme")
                st.caption(f"{metrics['status']} • Yaklaşık %95 hata payı: ±{metrics.get('std_error')} yüzde puan (gol örneklemi için)")
                keys = [("MS_1", "MS 1"), ("MS_X", "MS X"), ("MS_2", "MS 2"), ("OU_25", "2.5 Üst"),
                        ("BTTS_YES", "KG Var"), ("CS_1X", "1X"), ("HT_OU_15", "İY 1.5 Üst"), ("TG_46", "4–6 gol")]
                cols = st.columns(4)
                for i, (key, label) in enumerate(keys):
                    value = metrics.get(key)
                    cols[i % 4].metric(label, "Veri yok" if value is None else f"%{value}")
                show_cols = ["league", "season", "date", "home_team", "away_team", "fthg", "ftag", "ftr",
                             "odds_h_close", "odds_d_close", "odds_a_close", "total_diff"]
                show_cols = [c for c in show_cols if c in similar.columns]
                st.dataframe(similar[show_cols], use_container_width=True)
                st.download_button("Eşleşen maçları CSV indir", similar.to_csv(index=False).encode("utf-8-sig"),
                                   "frekans_x_benzer_maclar.csv", "text/csv")

    elif menu == "3. Gelecek Maç Tahmini":
        st.header("Poisson Tabanlı Gol ve Sonuç Tahmini")
        df = get_matches_dataframe()
        if df.empty:
            st.warning("Önce maç verisi indirin.")
            st.stop()
        league = st.selectbox("Tahmin için lig", ["Tüm Ligler"] + list(SUPPORTED_LEAGUES),
                              format_func=lambda x: "Tüm Ligler" if x == "Tüm Ligler" else league_label(x))
        df_l = df if league == "Tüm Ligler" else df[df["league"] == league]
        teams = sorted(set(df_l["home_team"].dropna()) | set(df_l["away_team"].dropna()))
        if len(teams) < 2:
            st.warning("Bu filtrede yeterli takım bulunamadı.")
            st.stop()
        c1, c2 = st.columns(2)
        home = c1.selectbox("Ev sahibi", teams, index=0)
        away_default = 1 if len(teams) > 1 else 0
        away = c2.selectbox("Deplasman", teams, index=away_default)
        if st.button("Tahmini hesapla", type="primary"):
            result = MatchPredictorEngine(df_l).predict_match(home, away)
            if "error" in result:
                st.error(result["error"])
            else:
                st.subheader(f"{home} — {away}")
                x1, x2 = st.columns(2)
                x1.metric("Beklenen ev sahibi golü", result["expected_goals_home"])
                x2.metric("Beklenen deplasman golü", result["expected_goals_away"])
                st.subheader("Sonuç olasılıkları")
                m = st.columns(5)
                for col, label, key in zip(m, ["MS 1", "MS X", "MS 2", "2.5 Üst", "KG Var"],
                                           ["prob_ms1", "prob_msx", "prob_ms2", "prob_over_25", "prob_btts"]):
                    col.metric(label, f"%{result[key]}")
                st.write("**Modelin en olası skorları**")
                st.dataframe(pd.DataFrame(result["top_scores"]), use_container_width=True, hide_index=True)
                st.write("**Model filtresini geçen olasılıklar (en az %58):**")
                if result["recommended_picks"]:
                    st.dataframe(pd.DataFrame(result["recommended_picks"]), use_container_width=True, hide_index=True)
                else:
                    st.info("Hiçbir pazar %58 eşiğine ulaşmadı.")
                st.warning(result["warning"])

    elif menu == "4. Oran Hareketleri":
        st.header("Açılış ve Kapanış Oran Hareketleri")
        df = get_matches_dataframe()
        if df.empty:
            st.warning("Önce maç verisi indirin.")
            st.stop()
        drop = st.slider("Minimum oran düşüşü (%)", 3, 30, 8) / 100
        league = st.selectbox("Lig filtresi", ["Tüm Ligler"] + list(SUPPORTED_LEAGUES),
                              format_func=lambda x: "Tüm Ligler" if x == "Tüm Ligler" else league_label(x))
        if league != "Tüm Ligler":
            df = df[df["league"] == league]
        result = OddsMovementAnalyzer.analyze_movements(df, drop)
        if result.empty:
            st.info("Bu eşik için yeterli açılış ve kapanış oranı bulunan belirgin düşüşlü maç yok.")
        else:
            st.caption("Oran düşüşü tek başına şike, sakatlık veya garanti kazanç göstergesi değildir.")
            st.dataframe(result, use_container_width=True)
            st.download_button("Oran hareketlerini CSV indir", result.to_csv(index=False).encode("utf-8-sig"),
                               "frekans_x_oran_hareketleri.csv", "text/csv")

    elif menu == "5. Backtest":
        st.header("Backtest")
        df = get_matches_dataframe()
        if df.empty:
            st.warning("Önce maç verisi indirin.")
            st.stop()
        test_type = st.radio("Test türü", ["Model walk-forward (zamansal)", "Kapanış oranı filtresi (basit)"])
        if test_type == "Model walk-forward (zamansal)":
            st.caption("Tahminler yalnızca maç tarihinden önce oynanmış maçlarla oluşturulur; aynı günün sonuçları kullanılmaz.")
            league = st.selectbox("Test ligi", ["Tüm Ligler"] + list(SUPPORTED_LEAGUES),
                                  format_func=lambda x: "Tüm Ligler" if x == "Tüm Ligler" else league_label(x))
            test_count = st.number_input("Test edilecek son maç sayısı", min_value=20, max_value=500, value=100, step=20)
            min_history = st.number_input("Gerekli minimum geçmiş maç", min_value=10, max_value=300, value=30, step=10)
            if st.button("Walk-forward testini çalıştır", type="primary"):
                with st.spinner("Geçmiş maçlar sırayla test ediliyor; veri miktarına göre biraz sürebilir..."):
                    summary, details = WalkForwardBacktest.run(df, league, int(test_count), int(min_history))
                if "status" in summary:
                    st.warning(summary["status"])
                else:
                    st.caption(f"Test dönemi: {summary['first_date']} — {summary['last_date']}")
                    c = st.columns(4)
                    c[0].metric("Test edilen maç", summary["tested_matches"])
                    c[1].metric("MS 1X2 doğruluğu", f"%{summary['result_accuracy_pct']}")
                    c[2].metric("1X2 Brier", summary["brier_1x2"])
                    c[3].metric("2.5 Üst Brier", summary["brier_over25"])
                    st.metric("KG Var Brier", summary["brier_btts"])
                    st.caption(summary["note"])
                    st.dataframe(details, use_container_width=True, hide_index=True)
                    st.download_button("Walk-forward sonuçlarını CSV indir", details.to_csv(index=False).encode("utf-8-sig"),
                                       "frekans_x_walk_forward.csv", "text/csv")
        else:
            st.warning("Bu strateji, kapanış MS 1 oranı seçilen aralıkta olan her maça ev sahibi galibiyeti bahsi varsayar; model tahmini değildir.")
            c1, c2, c3 = st.columns(3)
            min_o = c1.number_input("Minimum kapanış oranı", min_value=1.01, value=1.50, step=0.10)
            max_o = c2.number_input("Maksimum kapanış oranı", min_value=1.01, value=2.80, step=0.10)
            commission = c3.number_input("Kazanç üzerinden komisyon (%)", min_value=0.0, max_value=99.0, value=2.0, step=0.5) / 100
            stake = st.number_input("Maç başı sabit bahis tutarı (₺)", min_value=1.0, value=100.0, step=10.0)
            if st.button("Oran filtresi backtest'ini çalıştır", type="primary"):
                result = BacktestValidator.run_historical_backtest(df, min_o, max_o, commission, stake)
                if "status" in result:
                    st.warning(result["status"])
                else:
                    st.caption(f"Dönem: {result['first_date']} — {result['last_date']}")
                    c = st.columns(4)
                    c[0].metric("Bahis sayısı", result["total_bets"])
                    c[1].metric("Kazanma oranı", f"%{result['win_rate']}")
                    c[2].metric("Net kâr/zarar", f"{result['net_pnl']:,.2f} ₺")
                    c[3].metric("ROI", f"%{result['roi_percent']}")
                    c2x = st.columns(3)
                    c2x[0].metric("Toplam yatırılan", f"{result['total_stake']:,.2f} ₺")
                    c2x[1].metric("Toplam geri dönüş", f"{result['total_return']:,.2f} ₺")
                    c2x[2].metric("Brier (yaklaşık)", result["brier_score"])
                    st.caption(result["note"])

    else:
        st.header("Veri Kapsamı ve Önemli Sınırlamalar")
        st.markdown("""
**Veri kaynağı:** football-data.co.uk CSV dosyaları. Kaynakta bulunmayan lig/sezon dosyaları indirilemez.
**Oran alanları:** B365/ortalama oran sütunları varsa alınır; bulunmayan oranlar boş kalır.
**KG Var/KG Yok oranları:** Kaynakta standart ve tutarlı sütunlar bulunmadığından bazı sezonlarda boş olabilir. Gerçekleşme frekansı skor verilerinden hesaplanır.
**Tahmin:** Poisson modeli tarihsel gollere dayanır; güncel sakatlık, kadro, fikstür yoğunluğu ve canlı piyasa bilgisi kullanmaz.
**Backtest:** Basit kapanış oranı aralığı filtresidir; modelin ileriye dönük başarısını ispatlamaz.
**İstatistik:** Küçük örneklem yüzdeleri yüksek oynaklık taşır. Geçmiş frekanslar gelecek sonucu garanti etmez.
        """)
        support = []
        for key, name in MARKET_METADATA.items():
            if key in {"CORNER_KICKS", "YELLOW_CARDS", "PLAYER_PROPS", "EXACT_SCORE"}:
                status = "Kısıtlı / özel veri gerekli"
            else:
                status = "Skor/oran alanları varsa hesaplanır"
            support.append({"Market": name, "Durum": status})
        st.dataframe(pd.DataFrame(support), use_container_width=True, hide_index=True)


if __name__ == "__main__":
    main()
