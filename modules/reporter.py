import os
import json
from config import REPORTS_DIR, logger

class ReportGenerator:
    @staticmethod
    def generate_reports(matches, findings):
        logger.info("Raporlar üretiliyor (JSON & Markdown)...")
        
        # JSON Raporu
        json_path = os.path.join(REPORTS_DIR, "sentinel_report.json")
        report_data = {
            "matches": matches,
            "findings": findings
        }
        with open(json_path, "w", encoding="utf-8") as f:
            json.dump(report_data, f, ensure_ascii=False, indent=4)
            
        # Markdown Raporu
        md_path = os.path.join(REPORTS_DIR, "sentinel_report.md")
        with open(md_path, "w", encoding="utf-8") as f:
            f.write("# 🛡️ NESİNE SENTİNEL X - Denetim ve İstihbarat Raporu\n\n")
            f.write("## ⚽ Taranan Maçlar ve Oranlar\n")
            for m in matches:
                f.write(f"- **{m.get('home_team')} vs {m.get('away_team')}** ({m.get('league')})\n")
                f.write(f"  - Oranlar: 1: {m.get('odds_1')} | X: {m.get('odds_x')} | 2: {m.get('odds_2')}\n")
            
            f.write("\n## 🔍 Denetim Bulguları\n")
            if not findings:
                f.write("Kritik bulguya rastlanmadı.\n")
            for find in findings:
                f.write(f"- **[{find.get('severity')}] {find.get('title')}**\n")
                f.write(f"  - Açıklama: {find.get('description')}\n")
                f.write(f"  - Kanıt/Detay: {find.get('evidence')}\n")
                
        logger.info(f"Raporlar kaydedildi: {json_path} ve {md_path}")
