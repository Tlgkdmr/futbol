from __future__ import annotations

import io
import math
from datetime import datetime
from typing import Dict, List, Optional, Tuple

import pandas as pd
import requests
import streamlit as st

# Sayfa Yapılandırması
st.set_page_config(
    page_title="FREKANS X - Profesyonel Oran Analizi",
    page_icon="⚽",
    layout="wide",
    initial_sidebar_state="expanded",
)

# Profesyonel Koyu Lacivert Tema & Turkuaz / Mavi Detaylar
st.markdown(
    """
    <style>
    .main {
        background-color: #0b131d;
        color: #e2e8f0;
    }
    .sidebar .sidebar-content {
        background-color: #0f172a;
    }
    .metric-card {
        background-color: #1e293b;
        border: 1px solid #334155;
        padding: 15px;
        border-radius: 10px;
        text-align: center;
    }
    .header-banner {
        background: linear-gradient(90deg, #0f172a 0%, #1e293b 100%);
        padding: 20px;
        border-radius: 12px;
        border-left: 5px solid #06b6d4;
        margin-bottom: 20px;
    }
    </style>
""",
    unsafe_allow_html=True,
)

APP_VERSION = "2.0.1"
BASE_URL = "https://www.football-data.co.uk/mmz4281"
FIXTURES_URL = "https://www.football-data.co.uk/fixtures.csv"
REQUEST_TIMEOUT = 25
USER_AGENT = "FREKANS-X/2.0 (Professional Football Analytics)"

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

DIV_TO_LEAGUE = {code: name for name, code in LEAGUES.items()}


def parse_csv_response(content: bytes) -> pd.DataFrame:
  """Esnek kodlama ile CSV verisini okur."""
  last_error: Optional[Exception] = None
  for encoding in ("latin1", "utf-8-sig", "cp1252"):
    try:
      frame = pd.read_csv(
          io.BytesIO(content),
          encoding=encoding,
          on_bad_lines="skip",
          low_memory=False,
      )
      if len(frame.columns) > 1:
        return frame
    except Exception as exc:
      last_error = exc
  raise ValueError(f"CSV okunamadı: {last_error or 'Biçim tanınmadı'}")


@st.cache_data(ttl=3600, show_spinner=False)
def fetch_season_data(season: str) -> Tuple[pd.DataFrame, List[dict]]:
  """Seçilen sezon için geçmiş maç verilerini Football-Data.co.uk adresinden indirir."""
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
        raise ValueError("Boş dosya")
      frame = parse_csv_response(response.content)

      required = {"Date", "HomeTeam", "AwayTeam"}
      if not required.issubset(frame.columns):
        raise ValueError("Eksik temel sütunlar")

      frame["Date"] = pd.to_datetime(
          frame["Date"], dayfirst=True, errors="coerce"
      )
      frame["HomeTeam"] = frame["HomeTeam"].astype("string").str.strip()
      frame["AwayTeam"] = frame["AwayTeam"].astype("string").str.strip()
      frame = frame.dropna(subset=["Date", "HomeTeam", "AwayTeam"]).copy()
      frame["Lig"] = league
      frame["Sezon"] = season

      frames.append(frame)
      statuses.append({
          "Lig": league,
          "Durum": "Başarılı",
          "Maç": len(frame),
          "Not": "",
      })
    except Exception as exc:
      statuses.append({
          "Lig": league,
          "Durum": "Hata / Veri Yok",
          "Maç": 0,
          "Not": str(exc)[:100],
      })

  session.close()
  data = pd.concat(frames, ignore_index=True, sort=False) if frames else pd.DataFrame()
  return data, statuses


@st.cache_data(ttl=1800, show_spinner=False)
def fetch_upcoming_fixtures() -> Tuple[pd.DataFrame, str]:
  """Gelecek maç fikstürünü çeker."""
  response = requests.get(
      FIXTURES_URL,
      timeout=REQUEST_TIMEOUT,
      headers={"User-Agent": USER_AGENT, "Accept": "text/csv,*/*;q=0.8"},
  )
  response.raise_for_status()
  if b"<html" in response.content.lstrip()[:200].lower():
    raise ValueError("Fikstür kaynağı HTML sayfasına yönlendirdi.")

  frame = parse_csv_response(response.content)
  canonical = {
      "div": "Div",
      "league": "Div",
      "date": "Date",
      "time": "Time",
      "hometeam": "HomeTeam",
      "home": "HomeTeam",
      "awayteam": "AwayTeam",
      "away": "AwayTeam",
      "b365h": "B365H",
      "b365d": "B365D",
      "b365a": "B365A",
      "avgh": "AvgH",
      "avgd": "AvgD",
      "avga": "AvgA",
  }
  rename = {}
  for col in frame.columns:
    clean = str(col).strip().replace("\ufeff", "").casefold()
    if clean in canonical:
      rename[col] = canonical[clean]
  frame = frame.rename(columns=rename)

  if "Date" in frame.columns:
    frame["Date"] = pd.to_datetime(frame["Date"], dayfirst=True, errors="coerce")
  frame = frame.dropna(subset=["Date", "HomeTeam", "AwayTeam"]).copy()

  if "Div" in frame.columns:
    div = frame["Div"].astype("string").str.strip().str.upper()
    frame["Lig"] = div.map(DIV_TO_LEAGUE).fillna(div)
  else:
    frame["Lig"] = "Diğer"

  return frame, f"Fikstür başarıyla çekildi ({len(frame)} maç)."


def process_matches(data: pd.DataFrame) -> pd.DataFrame:
  """Geçmiş maç verilerini skor, İY/MS sonuçları ve gol istatistikleri ile zenginleştirir."""
  df = data.copy()
  num_cols = ["FTHG", "FTAG", "HTHG", "HTAG", "B365H", "B365D", "B365A"]
  for col in num_cols:
    if col in df.columns:
      df[col] = pd.to_numeric(df[col], errors="coerce")

  full_valid = df.get("FTHG").notna() & df.get("FTAG").notna() if "FTHG" in df and "FTAG" in df else pd.Series(False, index=df.index)
  
  df["MS Sonuç"] = pd.Series(pd.NA, index=df.index, dtype="string")
  df.loc[full_valid & (df["FTHG"] > df["FTAG"]), "MS Sonuç"] = "1"
  df.loc[full_valid & (df["FTHG"] == df["FTAG"]), "MS Sonuç"] = "X"
  df.loc[full_valid & (df["FTHG"] < df["FTAG"]), "MS Sonuç"] = "2"

  df["Toplam Gol"] = df["FTHG"] + df["FTAG"] if full_valid.any() else pd.NA

  half_valid = df.get("HTHG").notna() & df.get("HTAG").notna() if "HTHG" in df and "HTAG" in df else pd.Series(False, index=df.index)
  df["İY Sonuç"] = pd.Series(pd.NA, index=df.index, dtype="string")
  df.loc[half_valid & (df["HTHG"] > df["HTAG"]), "İY Sonuç"] = "1"
  df.loc[half_valid & (df["HTHG"] == df["HTAG"]), "İY Sonuç"] = "X"
  df.loc[half_valid & (df["HTHG"] < df["HTAG"]), "İY Sonuç"] = "2"

  df["İY/MS"] = df["İY Sonuç"].astype(str) + "/" + df["MS Sonuç"].astype(str)
  df.loc[df["İY Sonuç"].isna() | df["MS Sonuç"].isna(), "İY/MS"] = pd.NA

  if "Date" in df.columns:
    df["Tarih"] = df["Date"].dt.strftime("%d.%m.%Y")
    df["Gün"] = df["Date"].dt.day_name()
    df["Saat"] = df["Date"].dt.strftime("%H:%M") if "Time" in df.columns else "00:00"

  df["MS Skor"] = df["FTHG"].astype(str).str.replace(".0", "", regex=False) + "-" + df["FTAG"].astype(str).str.replace(".0", "", regex=False)
  df["İY Skor"] = df["HTHG"].astype(str).str.replace(".0", "", regex=False) + "-" + df["HTAG"].astype(str).str.replace(".0", "", regex=False)
  
  return df


# --- ARAYÜZ BAŞLANGICI ---
st.title("⚽ FREKANS X - Profesyonel Oran Analiz Sistemi")
st.caption(f"Sürüm {APP_VERSION} | Gerçek Veri Tabanı & Toleranslı Oran Eşleştirme Motoru")

# Sidebar - Veri ve Sezon Ayarları
with st.sidebar:
  st.header("⚙️ Veri ve Sezon Ayarları")
  current_year = datetime.now().year
  default_seasons = [f"{str(y)[-2:]}{str(y+1)[-2:]}" for y in range(current_year, current_year - 4, -1)]
  selected_season = st.selectbox(
      "Arşiv Sezonu Seçin", default_seasons, index=0, format_func=lambda s: f"20{s[:2]}–20{s[2:]}"
  )
  selected_leagues = st.multiselect("Lig Filtresi", list(LEAGUES.keys()), default=list(LEAGUES.keys()))
  
  refresh_btn = st.button("Verileri Güncelle / Yeniden Çek", type="primary", use_container_width=True)

if "raw_matches" not in st.session_state or refresh_btn:
  with st.spinner("Geçmiş maç arşivi Football-Data.co.uk adresinden indiriliyor..."):
    data, statuses = fetch_season_data(selected_season)
    st.session_state["raw_matches"] = data
    st.session_state["statuses"] = statuses

raw_data = st.session_state.get("raw_matches", pd.DataFrame())
if not raw_data.empty and selected_leagues:
  raw_data = raw_data[raw_data["Lig"].isin(selected_leagues)].copy()

matches = process_matches(raw_data) if not raw_data.empty else pd.DataFrame()

# Sekmeler
tabs = st.tabs([
    "🔍 Benzer Oran Analizi",
    "📅 Günün Fikstür & Tahmin",
    "📊 Oran Aralık Başarısı",
    "📁 Kaynak Raporu",
])

# SEKME 1: BENZER ORAN EŞLEŞTİRME SİSTEMİ
with tabs[0]:
  st.subheader("🎯 Üçlü Oran Toleranslı Benzer Maç Arama Motoru")
  st.markdown("Gelecek maçınızın veya incelemek istediğiniz oranların **MS 1, MS X, MS 2** değerlerini girerek geçmişteki benzer oranlı maçları tespit edin.")

  col_o1, col_o2, col_o3, col_tol = st.columns(4)
  with col_o1:
    target_h = st.number_input("MS 1 Oranı", min_value=1.01, max_value=50.0, value=2.05, step=0.01)
  with col_o2:
    target_d = st.number_input("MS X Oranı", min_value=1.01, max_value=50.0, value=3.40, step=0.01)
  with col_o3:
    target_a = st.number_input("MS 2 Oranı", min_value=1.01, max_value=50.0, value=3.70, step=0.01)
  with col_tol:
    tolerance = st.slider("Oran Toleransı (%)", min_value=1.0, max_value=15.0, value=4.0, step=0.5) / 100.0

  h_col = "B365H" if "B365H" in matches.columns else ("AvgH" if "AvgH" in matches.columns else None)
  d_col = "B365D" if "B365D" in matches.columns else ("AvgD" if "AvgD" in matches.columns else None)
  a_col = "B365A" if "B365A" in matches.columns else ("AvgA" if "AvgA" in matches.columns else None)

  if not matches.empty and h_col and d_col and a_col:
    m_work = matches.dropna(subset=[h_col, d_col, a_col, "MS Sonuç"]).copy()
    m_work[h_col] = pd.to_numeric(m_work[h_col], errors="coerce")
    m_work[d_col] = pd.to_numeric(m_work[d_col], errors="coerce")
    m_work[a_col] = pd.to_numeric(m_work[a_col], errors="coerce")
    m_work = m_work.dropna(subset=[h_col, d_col, a_col])

    low_h, high_h = target_h * (1 - tolerance), target_h * (1 + tolerance)
    low_d, high_d = target_d * (1 - tolerance), target_d * (1 + tolerance)
    low_a, high_a = target_a * (1 - tolerance), target_a * (1 + tolerance)

    matched = m_work[
        m_work[h_col].between(low_h, high_h)
        & m_work[d_col].between(low_d, high_d)
        & m_work[a_col].between(low_a, high_a)
    ].copy()

    matched["Uzaklık %"] = (
        (abs(matched[h_col] - target_h) / target_h)
        + (abs(matched[d_col] - target_d) / target_d)
        + (abs(matched[a_col] - target_a) / target_a)
    ) / 3.0 * 100.0
    matched = matched.sort_values("Uzaklık %", ascending=True)

    st.markdown("---")
    st.info(f"Girilen oranlara ve %{tolerance*100:.1f} toleransa sahip **{len(matched)}** geçmiş maç bulundu.")

    if not matched.empty:
      if len(matched) < 15:
        st.warning("⚠️ Örneklem sayısı düşük (< 15 maç). İstatistiksel çıkarımlar yanıltıcı olabilir.")
      else:
        st.success("✅ Yeterli örneklem hacmine ulaşıldı.")

      st.subheader("📈 İstatistik ve Analiz Ekranı")
      tot_matches = len(matched)
      
      col_m1, col_m2, col_m3, col_m4 = st.columns(4)
      col_m1.metric("Eşleşen Maç Sayısı", tot_matches)
      col_m2.metric("Maç Başı Ortalama Gol", f"{matched['Toplam Gol'].mean():.2f}" if "Toplam Gol" in matched else "N/A")
      col_m3.metric("İlk Yarı Gol Ortalaması", f"{(matched['HTHG'] + matched['HTAG']).mean():.2f}" if "HTHG" in matched else "N/A")
      col_m4.metric("Karşılıklı Gol Var Oranı", f"{((matched['FTHG'] > 0) & (matched['FTAG'] > 0)).sum() / tot_matches * 100:.1f}%")

      c_d1, c_d2 = st.columns(2)
      with c_d1:
        st.markdown("**Maç Sonu (1/X/2) Dağılımı**")
        ms_counts = matched["MS Sonuç"].value_counts(normalize=True) * 100
        st.dataframe(ms_counts.round(1).astype(str) + "%", use_container_width=True)

      with c_d2:
        st.markdown("**İlk Yarı / Maç Sonu (İY/MS) Dağılımı**")
        if "İY/MS" in matched.columns:
          iyms_counts = matched["İY/MS"].value_counts(normalize=True) * 100
          st.dataframe(iyms_counts.head(6).round(1).astype(str) + "%", use_container_width=True)

      st.subheader("🏆 Benzer Oranlı Geçmiş Maçlar Listesi")
      display_cols = [
          "Tarih", "Lig", "HomeTeam", "AwayTeam", h_col, d_col, a_col,
          "İY Skor", "MS Skor", "İY Sonuç", "MS Sonuç", "İY/MS", "Uzaklık %"
      ]
      available_display = [c for c in display_cols if c in matched.columns]
      st.dataframe(matched[available_display].rename(columns={h_col: "MS 1", d_col: "MS X", a_col: "MS 2", "HomeTeam": "Ev Sahibi", "AwayTeam": "Deplasman"}).head(100), use_container_width=True, hide_index=True)

      csv_data = matched[available_display].to_csv(index=False).encode("utf-8-sig")
      st.download_button("📥 Eşleşen Maçları CSV İndir", csv_data, "frekans_x_benzer_maclar.csv", "text/csv")
    else:
      st.warning("Bu oran aralığında geçmiş maç bulunamadı. Tolerans oranını artırmayı deneyin.")
  else:
    st.error("Seçilen sezonda oran sütunları (Bet365 / Avg) eksik veya yüklenemedi.")

# SEKME 2: GÜNÜN FİKSTÜR & TAHMİN
with tabs[1]:
  st.subheader("📅 Günün Fikstürleri ve Gerçek Oran Takibi")
  st.markdown("Football-Data.co.uk canlı fikstür kaynağı üzerinden gelecek maçları ve oranları inceleyin.")

  try:
    fixtures, fix_note = fetch_upcoming_fixtures()
    st.info(fix_note)
    if not fixtures.empty:
      today = pd.Timestamp.now().normalize()
      future_fix = fixtures[fixtures["Date"] >= today].copy()
      
      st.markdown(f"**Gelecek Toplam Fikstür Maçı:** {len(future_fix)}")
      fix_display_cols = [c for c in ["Date", "Lig", "HomeTeam", "AwayTeam", "B365H", "B365D", "B365A"] if c in future_fix.columns]
      st.dataframe(future_fix[fix_display_cols].head(100), use_container_width=True, hide_index=True)
    else:
      st.warning("Güncel fikstür bulunamadı.")
  except Exception as e:
    st.error(f"Fikstür yüklenirken hata oluştu: {e}")

# SEKME 3: ORAN ARALIK BAŞARI ANALİZİ
with tabs[2]:
  st.subheader("📊 Oran Aralıklarına Göre Tarihsel Başarı Analizi")
  st.markdown("Geçmiş maçlardaki oranların aralıklara göre başarı oranlarını inceleyin.")

  if not matches.empty and h_col:
    df_bins = matches.dropna(subset=[h_col, "MS Sonuç"]).copy()
    df_bins[h_col] = pd.to_numeric(df_bins[h_col], errors="coerce")
    
    bins = [1.0, 1.50, 1.75, 2.00, 2.25, 2.50, 3.00, 4.00, 100.0]
    labels = ["1.00-1.50", "1.50-1.75", "1.75-2.00", "2.00-2.25", "2.25-2.50", "2.50-3.00", "3.00-4.00", "4.00+"]
    df_bins["Oran Aralığı"] = pd.cut(df_bins[h_col], bins=bins, labels=labels, right=False)

    summary = df_bins.groupby("Oran Aralığı", observed=True).agg(
        Toplam_Maç=("MS Sonuç", "size"),
        MS1_Kazanan=("MS Sonuç", lambda x: (x == "1").sum())
    ).reset_index()
    summary["MS1 Başarı %"] = (summary["MS1_Kazanan"] / summary["Toplam_Maç"] * 100).round(1)
    st.dataframe(summary, use_container_width=True, hide_index=True)
  else:
    st.info("Analiz için yeterli oran verisi bulunamadı.")

# SEKME 4: KAYNAK RAPORU
with tabs[3]:
  st.subheader("📁 Veri Kaynağı ve Durum Raporu")
  statuses = st.session_state.get("statuses", [])
  if statuses:
    st.dataframe(pd.DataFrame(statuses), use_container_width=True, hide_index=True)
  else:
    st.warning("Henüz kaynak sorgusu yapılmadı.")

# Alt Bilgi
st.markdown("---")
st.markdown("<p style='text-align: center; color: #64748b; font-size: 12px;'>FREKANS X • Profesyonel Futbol İstatistik ve Oran Analiz Platformu</p>", unsafe_allow_html=True)

