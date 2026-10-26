
import io
import requests
import pandas as pd
import streamlit as st

st.set_page_config(page_title="FREKANS X", page_icon="⚽", layout="wide")

st.title("⚽ FREKANS X")
st.write("Futbol maç verileri ve oran analiz sistemi")

LEAGUES = {
    "Türkiye Süper Lig": "T1",
    "İngiltere Premier League": "E0",
    "İngiltere Championship": "E1",
    "İspanya La Liga": "SP1",
    "İtalya Serie A": "I1",
    "Almanya Bundesliga": "D1",
    "Fransa Ligue 1": "F1",
    "Hollanda Eredivisie": "N1",
    "Portekiz Primeira Liga": "P1",
    "Belçika Pro League": "B1",
}

season = st.selectbox(
    "Sezon seç",
    ["2526", "2425", "2324"],
    index=0,
    format_func=lambda s: f"20{s[:2]}-{s[2:]}"
)

if st.button("Maç verilerini çek", type="primary):
    st.session_state["matches"] = []
    st.session_state["status"] = []

    progress = st.progress(0)

    for i, (league, code) in enumerate(LEAGUES.items()):
        url = f"https://www.football-data.co.uk/mmz4281/{season}/{code}.csv"

        try:
            response = requests.get(url, timeout=25)
            response.raise_for_status()

            df = pd.read_csv(
                io.BytesIO(response.content),
                encoding="latin1",
                on_bad_lines="skip"
            )

            required = {"HomeTeam", "AwayTeam", "Date"}
            if not required.issubset(df.columns):
                raise ValueError("Gerekli maç sütunları bulunamadı.")

            df["Lig"] = league
            df["Date"] = pd.to_datetime(df["Date"], dayfirst=True, errors="coerce")
            df = df.dropna(subset=["Date", "HomeTeam", "AwayTeam"])

            st.session_state["matches"].append(df)
            st.session_state["status"].append({
                "Lig": league,
                "Durum": "Veri alındı",
                "Maç sayısı": len(df)
            })

        except Exception as e:
            st.session_state["status"].append({
                "Lig": league,
                "Durum": f"Veri alınamadı: {str(e)[:100]}",
                "Maç sayısı": 0
            })

        progress.progress((i + 1) / len(LEAGUES))

if "status" in st.session_state:
    st.subheader("Kaynak kontrolü")
    st.dataframe(pd.DataFrame(st.session_state["status"]), use_container_width=True)

if st.session_state.get("matches"):
    matches = pd.concat(st.session_state["matches"], ignore_index=True)

    st.success(f"Toplam {len(matches)} maç kaydı alındı.")
    st.subheader("Maç verileri")
    st.dataframe(
        matches[["Date", "Lig", "HomeTeam", "AwayTeam"]].sort_values("Date", ascending=False),
        use_container_width=True
    )

    if {"FTHG", "FTAG"}.issubset(matches.columns):
        played = matches.dropna(subset=["FTHG", "FTAG"]).copy()
        played["Sonuç"] = "X"
        played.loc[played["FTHG"] > played["FTAG"], "Sonuç"] = "1"
        played.loc[played["FTHG"] < played["FTAG"], "Sonuç"] = "2"

        st.subheader("Geçmiş maç sonuçları")
        st.dataframe(
            played[["Date", "Lig", "HomeTeam", "AwayTeam", "FTHG", "FTAG", "Sonuç"]],
            use_container_width=True
        )

        st.caption("Geçmiş sonuçlar tahmin garantisi değildir.")
else:
    st.info("Başlamak için 'Maç verilerini çek' düğmesine bas.")

