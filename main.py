from __future__ import annotations

import io
import math
import sqlite3
from pathlib import Path
from datetime import datetime, timezone
from typing import Dict, List, Optional, Tuple

import pandas as pd
import requests

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
