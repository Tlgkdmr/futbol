# -*- coding: utf-8 -*-
"""Futbol Maç Analiz Sistemi - otomatik fikstür + Poisson olasılık modeli.
Fikstür/sonuçlar ESPN'in herkese açık scoreboard uç noktasından alınır.
Bu uygulama bahis tavsiyesi veya kazanç garantisi vermez.
"""
from __future__ import annotations
import datetime as dt
import math
from zoneinfo import ZoneInfo
from typing import Any

import pandas as pd
import requests
import streamlit as st

st.set_page_config(page_title="Futbol Analiz Sistemi", page_icon="⚽", layout="wide")
TZ = ZoneInfo("Europe/Istanbul")
BASE = "https://site.api.espn.com/apis/site/v2/sports/soccer"

LEAGUES = {
    "Türkiye Süper Lig": "tur.1",
    "İngiltere Premier League": "eng.1",
    "İngiltere Championship": "eng.2",
    "İspanya La Liga": "esp.1",
    "İspanya La Liga 2": "esp.2",
    "İtalya Serie A": "ita.1",
    "İtalya Serie B": "ita.2",
    "Almanya Bundesliga": "ger.1",
    "Almanya 2. Bundesliga": "ger.2",
    "Fransa Ligue 1": "fra.1",
    "Fransa Ligue 2": "fra.2",
    "Hollanda Eredivisie": "ned.1",
    "Portekiz Primeira Liga": "por.1",
    "Belçika Pro League": "bel.1",
    "İskoçya Premiership": "sco.1",
    "ABD MLS": "usa.1",
    "UEFA Şampiyonlar Ligi": "uefa.champions",
    "UEFA Avrupa Ligi": "uefa.europa",
    "UEFA Konferans Ligi": "uefa.europa.conf",
}
HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; FutbolAnaliz/1.0)"}


def date_key(d: dt.date) -> str:
    return d.strftime("%Y%m%d")


def safe_score(value: Any) -> float | None:
    try:
        if value is None or str(value).strip() == "":
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


@st.cache_data(ttl=3600, show_spinner=False)
def fetch_scoreboard(league_code: str, start: dt.date, end: dt.date) -> tuple[list[dict], str | None]:
    """Fetch schedule/results for one league and date range."""
    url = f"{BASE}/{league_code}/scoreboard"
    params = {"dates": f"{date_key(start)}-{date_key(end)}", "limit": 500}
    try:
        response = requests.get(url, params=params, headers=HEADERS, timeout=20)
        if response.status_code != 200:
            return [], f"HTTP {response.status_code}"
        payload = response.json()
        events = payload.get("events", []) or []
        rows: list[dict] = []
        for event in events:
            competitions = event.get("competitions") or []
            if not competitions:
                continue
            competition = competitions[0]
            competitors = competition.get("competitors") or []
            home = next((x for x in competitors if x.get("homeAway") == "home"), None)
            away = next((x for x in competitors if x.get("homeAway") == "away"), None)
            if not home or not away:
                continue
            raw_date = event.get("date")
            try:
                kickoff = dt.datetime.fromisoformat(str(raw_date).replace("Z", "+00:00")).astimezone(TZ)
            except Exception:
                kickoff = None
            status_type = ((event.get("status") or {}).get("type") or {})
            completed = bool(status_type.get("completed"))
            rows.append({
                "event_id": str(event.get("id", "")),
                "league_code": league_code,
                "league": (event.get("season") or {}).get("name") or event.get("name") or league_code,
                "kickoff": kickoff,
                "home_team": (home.get("team") or {}).get("displayName", "Bilinmeyen takım"),
                "away_team": (away.get("team") or {}).get("displayName", "Bilinmeyen takım"),
                "home_score": safe_score(home.get("score")),
                "away_score": safe_score(away.get("score")),
                "completed": completed,
                "status": status_type.get("detail") or status_type.get("description") or "Programda",
                "venue": ((competition.get("venue") or {}).get("fullName")) or "",
            })
        return rows, None
    except Exception as exc:
        return [], f"{type(exc).__name__}: {exc}"


def get_events(league_names: list[str], start: dt.date, end: dt.date) -> tuple[pd.DataFrame, list[str]]:
    rows: list[dict] = []
    errors: list[str] = []
    for name in league_names:
        code = LEAGUES[name]
        data, error = fetch_scoreboard(code, start, end)
        rows.extend(data)
        if error:
            errors.append(f"{name}: {error}")
    if not rows:
        return pd.DataFrame(), errors
    df = pd.DataFrame(rows).drop_duplicates(subset=["event_id"])
    df["league"] = df["league_code"].map({v: k for k, v in LEAGUES.items()}).fillna(df["league"])
    df = df.sort_values("kickoff", na_position="last").reset_index(drop=True)
    return df, errors


def poisson_probs(lam: float, max_goals: int = 9) -> list[float]:
    lam = max(0.05, min(float(lam), 5.0))
    probs = [math.exp(-lam)]
    for k in range(1, max_goals + 1):
        probs.append(probs[-1] * lam / k)
    total = sum(probs)
    return [p / total for p in probs]


def estimate_team_rates(history: pd.DataFrame, team: str, venue: str, fallback_home: float, fallback_away: float) -> tuple[float, float, int]:
    if history.empty:
        return fallback_home, fallback_away, 0
    home_rows = history[(history.home_team == team) & (history.completed)]
    away_rows = history[(history.away_team == team) & (history.completed)]
    if venue == "home":
        rows = home_rows.tail(10)
        if len(rows) >= 3:
            scored = rows.home_score.mean()
            conceded = rows.away_score.mean()
            return float(scored), float(conceded), len(rows)
    else:
        rows = away_rows.tail(10)
        if len(rows) >= 3:
            scored = rows.away_score.mean()
            conceded = rows.home_score.mean()
            return float(scored), float(conceded), len(rows)
    rows = history[((history.home_team == team) | (history.away_team == team)) & history.completed].tail(10)
    if len(rows) < 2:
        return fallback_home, fallback_away, len(rows)
    scored_vals, conceded_vals = [], []
    for _, r in rows.iterrows():
        if r.home_team == team:
            scored_vals.append(r.home_score); conceded_vals.append(r.away_score)
        else:
            scored_vals.append(r.away_score); conceded_vals.append(r.home_score)
    return float(sum(scored_vals)/len(scored_vals)), float(sum(conceded_vals)/len(conceded_vals)), len(rows)


def predict_fixture(fixture: pd.Series, history: pd.DataFrame) -> dict:
    if history.empty:
        return {"λ Ev": None, "λ Dep": None, "MS 1 %": None, "X %": None, "MS 2 %": None,
                "2,5 Üst %": None, "KG Var %": None, "Model Skoru": "Veri yok", "Veri": "Yetersiz"}
    completed = history[history.completed & history.home_score.notna() & history.away_score.notna()].copy()
    if completed.empty:
        return {"λ Ev": None, "λ Dep": None, "MS 1 %": None, "X %": None, "MS 2 %": None,
                "2,5 Üst %": None, "KG Var %": None, "Model Skoru": "Veri yok", "Veri": "Yetersiz"}
    # League averages, used to normalize team attack/defence rates.
    home_avg = max(0.3, float(completed.home_score.mean()))
    away_avg = max(0.3, float(completed.away_score.mean()))
    hs, hc, hn = estimate_team_rates(completed, str(fixture.home_team), "home", home_avg, away_avg)
    aws, awc, an = estimate_team_rates(completed, str(fixture.away_team), "away", away_avg, home_avg)
    # Blend team rates with league baseline to avoid extreme forecasts on small samples.
    home_attack = 0.65 * hs + 0.35 * home_avg
    away_defence = 0.65 * awc + 0.35 * home_avg
    away_attack = 0.65 * aws + 0.35 * away_avg
    home_defence = 0.65 * hc + 0.35 * away_avg
    lam_h = max(0.15, min(4.0, home_avg * (home_attack / home_avg) * (away_defence / home_avg)))
    lam_a = max(0.15, min(4.0, away_avg * (away_attack / away_avg) * (home_defence / away_avg)))
    ph, pa = poisson_probs(lam_h), poisson_probs(lam_a)
    p_home = p_draw = p_away = p_over25 = p_btts = 0.0
    best = (0, 0, -1.0)
    for h, hp in enumerate(ph):
        for a, ap in enumerate(pa):
            p = hp * ap
            if h > a: p_home += p
            elif h == a: p_draw += p
            else: p_away += p
            if h + a >= 3: p_over25 += p
            if h >= 1 and a >= 1: p_btts += p
            if p > best[2]: best = (h, a, p)
    samples = min(hn, 10) + min(an, 10)
    confidence = "İyi" if hn >= 6 and an >= 6 else ("Orta" if hn >= 3 and an >= 3 else "Düşük")
    return {"λ Ev": round(lam_h, 2), "λ Dep": round(lam_a, 2),
            "MS 1 %": round(p_home*100, 1), "X %": round(p_draw*100, 1), "MS 2 %": round(p_away*100, 1),
            "2,5 Üst %": round(p_over25*100, 1), "KG Var %": round(p_btts*100, 1),
            "Model Skoru": f"{best[0]}-{best[1]}", "Veri": f"{hn} ev / {an} dep. maç ({confidence})"}


def main() -> None:
    st.title("⚽ Futbol Maç Analiz Sistemi")
    st.caption("Fikstür otomatik alınır; takımın geçmiş skorlarından Poisson olasılıkları hesaplanır. Tahminler kesin sonuç değildir.")
    with st.sidebar:
        st.header("Fikstür ayarları")
        today = dt.datetime.now(TZ).date()
        mode = st.radio("Tarih aralığı", ["Yarın", "Bugün", "Önümüzdeki 7 gün", "Tarih seç"], index=0)
        if mode == "Bugün":
            start = end = today
        elif mode == "Yarın":
            start = end = today + dt.timedelta(days=1)
        elif mode == "Önümüzdeki 7 gün":
            start, end = today, today + dt.timedelta(days=6)
        else:
            start = st.date_input("Başlangıç", value=today + dt.timedelta(days=1))
            end = st.date_input("Bitiş", value=today + dt.timedelta(days=7))
            if end < start:
                st.error("Bitiş tarihi başlangıçtan önce olamaz.")
                st.stop()
        defaults = ["Türkiye Süper Lig", "İngiltere Premier League", "İspanya La Liga", "İtalya Serie A", "Almanya Bundesliga", "UEFA Şampiyonlar Ligi"]
        selected = st.multiselect("Ligler", list(LEAGUES.keys()), default=defaults)
        run = st.button("Fikstürü getir ve analiz et", type="primary", use_container_width=True)
    if "fixtures_df" not in st.session_state:
        st.session_state.fixtures_df = pd.DataFrame()
        st.session_state.history_df = pd.DataFrame()
        st.session_state.errors = []
    if run:
        if not selected:
            st.warning("En az bir lig seç.")
            st.stop()
        with st.spinner("Fikstür ve son maç sonuçları alınıyor. Biraz sürebilir..."):
            fixtures, errors = get_events(selected, start, end)
            history_start = max(dt.date(2025, 7, 1), start - dt.timedelta(days=365))
            history_end = min(today, start - dt.timedelta(days=1))
            hist, hist_errors = get_events(selected, history_start, history_end) if history_end >= history_start else (pd.DataFrame(), [])
            st.session_state.fixtures_df = fixtures
            st.session_state.history_df = hist
            st.session_state.errors = errors + ["Geçmiş veri: " + e for e in hist_errors]
            st.session_state.range_start = start
            st.session_state.range_end = end
            st.session_state.selected_leagues = selected
    fixtures = st.session_state.fixtures_df
    history = st.session_state.history_df
    if st.session_state.errors:
        with st.expander("Veri kaynağı uyarıları"):
            for error in st.session_state.errors:
                st.write("- " + error)
    if fixtures.empty:
        st.info("Henüz fikstür alınmadı. Soldan ligleri seçip **Fikstürü getir ve analiz et** düğmesine bas. ESPN kaynağı bazı ligleri veya tarihleri kapsamıyor olabilir.")
        st.markdown("**Veri kaynağı:** ESPN herkese açık futbol skor/fikstür servisi. Kaynakta olmayan maçlar listelenemez.")
        return
    # Show upcoming scheduled fixtures only (no completed events).
    now = dt.datetime.now(TZ)
    upcoming = fixtures[(~fixtures.completed) & fixtures.kickoff.notna() & (fixtures.kickoff >= now)].copy()
    if upcoming.empty:
        st.warning("Seçilen tarih aralığında gelecekteki maç bulunamadı. Ligleri veya tarih aralığını değiştir.")
        st.dataframe(fixtures[["league", "kickoff", "home_team", "away_team", "status"]], use_container_width=True, hide_index=True)
        return
    st.subheader(f"Bulunan yaklaşan maç: {len(upcoming)}")
    outputs = []
    for _, fixture in upcoming.iterrows():
        same_league_history = history[history.league_code == fixture.league_code] if not history.empty else pd.DataFrame()
        prediction = predict_fixture(fixture, same_league_history)
        outputs.append({
            "Tarih/Saat (Türkiye)": fixture.kickoff.strftime("%d.%m.%Y %H:%M"),
            "Lig": fixture.league,
            "Ev sahibi": fixture.home_team,
            "Deplasman": fixture.away_team,
            **prediction,
        })
    result = pd.DataFrame(outputs)
    st.dataframe(result, use_container_width=True, hide_index=True)
    csv = result.to_csv(index=False).encode("utf-8-sig")
    st.download_button("Analiz tablosunu indir (CSV)", data=csv, file_name="futbol_mac_analizi.csv", mime="text/csv")
    st.warning("Bu olasılıklar yalnızca geçmiş skor verilerinden üretilen istatistiksel tahminlerdir. Sakatlık, ilk 11, fikstür yoğunluğu ve güncel bahis oranları dahil değildir; bahis kazancı garanti edilmez.")
    st.markdown("**Kaynak bilgisi:** Fikstür ve skorlar ESPN'in halka açık scoreboard servisi üzerinden alınır. Bazı ligler veya maçlar kaynakta bulunmayabilir. Güncel bahis oranları bu sürümde otomatik çekilmez.")

if __name__ == "__main__":
    main()
