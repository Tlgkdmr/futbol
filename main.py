from __future__ import annotations

import io
from datetime import datetime
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

APP_VERSION = "1.0.0"
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
    "İY 1": {"result_col": "İY Sonuç", "target": "1", "odds": ["B365CH", "AvgCH", "PSCH", "WHCH"]},
    "İY X": {"result_col": "İY Sonuç", "target": "X", "odds": ["B365CD", "AvgCD", "PSCD", "WHCD"]},
    "İY 2": {"result_col": "İY Sonuç", "target": "2", "odds": ["B365CA", "AvgCA", "PSCA", "WHCA"]},
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
    st.warning("Seçilen lig ve sezonda kullanılabilir veri bulunamadı. Sezonu değiştirin veya veri kaynağı durum raporunu kontrol edin.")
    st.stop()

finished = matches.copy()
if "MS Sonuç" in finished.columns:
    finished = finished[finished["MS Sonuç"].notna()].copy()

c1, c2, c3, c4 = st.columns(4)
c1.metric("İndirilen maç kaydı", f"{len(matches):,}")
c2.metric("Tamamlanan maç", f"{len(finished):,}")
c3.metric("Lig sayısı", str(matches["Lig"].nunique()))
c4.metric("Tarih aralığı", f"{matches['Date'].min():%d.%m.%Y} – {matches['Date'].max():%d.%m.%Y}")

tabs = st.tabs([
    "Genel görünüm", "Oran analizi", "Benzer oranlı maçlar",
    "Gol ve KG", "Maç listesi", "İndir / dışa aktar"
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

with tabs[2]:
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

with tabs[3]:
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

with tabs[4]:
    st.subheader("Filtrelenmiş maç kayıtları")
    league_filter = st.multiselect("Maç listesindeki ligler", sorted(matches["Lig"].dropna().unique().tolist()), default=sorted(matches["Lig"].dropna().unique().tolist()), key="match_leagues")
    date_min = matches["Date"].min().date()
    date_max = matches["Date"].max().date()
    date_range = st.date_input("Tarih aralığı", value=(date_min, date_max), min_value=date_min, max_value=date_max)
    listing = matches[matches["Lig"].isin(league_filter)].copy()
    if isinstance(date_range, tuple) and len(date_range) == 2:
        start_date, end_date = date_range
        listing = listing[listing["Date"].dt.date.between(start_date, end_date)]
    cols = [c for c in ["Date", "Lig", "HomeTeam", "AwayTeam", "FTHG", "FTAG", "HTHG", "HTAG", "MS Sonuç", "KG Sonuç", "2.5 Sonuç"] if c in listing.columns]
    st.dataframe(listing[cols].sort_values("Date", ascending=False), use_container_width=True, hide_index=True)

with tabs[5]:
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
