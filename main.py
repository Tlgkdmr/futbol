
import io
from datetime import datetime

import pandas as pd
import requests
import streamlit as st

st.set_page_config(
    page_title="FREKANS X",
    page_icon="⚽",
    layout="wide"
)

st.title("⚽ FREKANS X")
st.caption("Futbol verileri ve geçmiş oran analizleri")

LEAGUES = {
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

st.info(
    "Veri kaynağı: Football-Data.co.uk. "
    "Kaynakta bulunmayan veriler tahmin edilmez."
)

season = st.selectbox(
    "Sezon seç",
    ["2627", "2526", "2425", "2324"],
    index=0,
    format_func=lambda s: f"20{s[:2]}-{s[2:]}"
)

if st.button("Maç verilerini çek", type="primary"):
    all_matches = []
    statuses = []
    progress = st.progress(0)

    for i, (league, code) in enumerate(LEAGUES.items()):
        url = (
            "https://www.football-data.co.uk/"
            f"mmz4281/{season}/{code}.csv"
        )

        try:
            response = requests.get(
                url,
                timeout=25,
                headers={"User-Agent": "FREKANS-X/1.0"}
            )
            response.raise_for_status()

            df = pd.read_csv(
                io.BytesIO(response.content),
                encoding="latin1",
                on_bad_lines="skip"
            )

            required = {"Date", "HomeTeam", "AwayTeam"}
            if not required.issubset(df.columns):
                raise ValueError("Gerekli sütunlar bulunamadı.")

            df["Date"] = pd.to_datetime(
                df["Date"],
                dayfirst=True,
                errors="coerce"
            )
            df = df.dropna(
                subset=["Date", "HomeTeam", "AwayTeam"]
            ).copy()

            df["Lig"] = league
            df["Sezon"] = season
            all_matches.append(df)

            statuses.append({
                "Lig": league,
                "Durum": "Veri alındı",
                "Maç sayısı": len(df)
            })

        except Exception as exc:
            statuses.append({
                "Lig": league,
                "Durum": f"Veri alınamadı: {str(exc)[:120]}",
                "Maç sayısı": 0
            })

        progress.progress((i + 1) / len(LEAGUES))

    st.session_state["matches"] = (
        pd.concat(all_matches, ignore_index=True)
        if all_matches else pd.DataFrame()
    )
    st.session_state["statuses"] = statuses

if "statuses" in st.session_state:
    st.subheader("Veri kaynağı kontrolü")
    st.dataframe(
        pd.DataFrame(st.session_state["statuses"]),
        use_container_width=True,
        hide_index=True
    )

matches = st.session_state.get("matches", pd.DataFrame())

if not matches.empty:
    st.success(f"Alınan toplam maç kaydı: {len(matches)}")

    st.subheader("Maç listesi")
    st.dataframe(
        matches[["Date", "Lig", "HomeTeam", "AwayTeam"]]
        .sort_values("Date", ascending=False),
        use_container_width=True,
        hide_index=True
    )

    if {"FTHG", "FTAG"}.issubset(matches.columns):
        played = matches.dropna(
            subset=["FTHG", "FTAG"]
        ).copy()

        played["Sonuç"] = "X"
        played.loc[played["FTHG"] > played["FTAG"], "Sonuç"] = "1"
        played.loc[played["FTHG"] < played["FTAG"], "Sonuç"] = "2"

        st.subheader("Geçmiş maç sonuçları")
        st.dataframe(
            played[
                ["Date", "Lig", "HomeTeam", "AwayTeam",
                 "FTHG", "FTAG", "Sonuç"]
            ],
            use_container_width=True,
            hide_index=True
        )

        st.subheader("Geçmiş sonuç istatistikleri")
        st.caption(
            "Bu bölüm yalnızca oynanmış ve skoru bulunan maçları kapsar."
        )

        result_counts = (
            played.groupby(["Lig", "Sonuç"])
            .size()
            .unstack(fill_value=0)
        )

        st.dataframe(result_counts, use_container_width=True)

        st.download_button(
            "Maç verilerini CSV olarak indir",
            data=matches.to_csv(index=False).encode("utf-8-sig"),
            file_name="frekans_x_mac_verileri.csv",
            mime="text/csv"
        )
else:
    st.warning(
        "Henüz maç verisi yok. Sezonu seçip "
        "'Maç verilerini çek' düğmesine bas."
    )

st.divider()
st.caption(
    "FREKANS X ilk sürüm: veri çekme ve sonuç kontrolü. "
    "Henüz tahmin veya kârlılık garantisi vermez."
)
