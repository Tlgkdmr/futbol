import streamlit as st
from config import logger
from modules.scraper import NesineScraper
from modules.odds_intelligence import OddsIntelligenceEngine
from modules.consistency_audit import ConsistencyAuditor
from modules.security_audit import EthicalSecurityAuditor
from modules.reporter import ReportGenerator

st.set_page_config(page_title="Nesine Sentinel X", page_icon="🛡️", layout="wide")

st.title("🛡️ NESİNE SENTİNEL X")
st.subheader("Profesyonel Futbol Oran İstihbarat ve Güvenlik Platformu")

if st.button("🚀 Sistemi Çalıştır ve Denetimi Başlat", type="primary"):
    with st.spinner("Maç bültenleri taranıyor ve analiz yapılıyor..."):
        # 1. Veri Toplama
        scraper = NesineScraper(use_mock=True)
        matches = scraper.fetch_live_odds()
        
        # 2. Oran İstihbaratı
        for match in matches:
            prob = OddsIntelligenceEngine.calculate_implied_probability(
                match["odds_1"], match["odds_x"], match["odds_2"]
            )
            match["probabilities"] = prob
            
        # 3. Denetimler
        findings = []
        findings.extend(ConsistencyAuditor.check_market_consistency(matches))
        findings.extend(EthicalSecurityAuditor.passive_security_check())
        
        # 4. Raporlama
        ReportGenerator.generate_reports(matches, findings)
        
    st.success("✅ Denetim başarıyla tamamlandı ve raporlar oluşturuldu!")
    
    col1, col2 = st.columns(2)
    with col1:
        st.markdown("### ⚽ Taranan Maçlar")
        st.json(matches)
    with col2:
        st.markdown("### 🔍 Güvenlik ve Tutarsızlık Bulguları")
        st.json(findings)
else:
    st.info("Sistemi başlatmak için yukarıdaki mavi butona tıklayın.")

