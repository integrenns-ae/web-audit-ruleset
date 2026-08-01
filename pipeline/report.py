"""
pipeline/report.py

Layer 1 - Code. Rendert score_result.json in einen lesbaren Markdown-Bericht.
Bewusst noch OHNE die spaeter geplante LLM-Umformulierung (phrase-findings) -
das hier ist der technische Rohbericht für dich selbst, nicht die kundenfertige
Fassung. Sprachliche Politur kommt als eigener, spaeterer Schritt.
"""

import json
from datetime import datetime
from pathlib import Path

CATEGORY_LABELS = {
    "technik_performance": "Technik & Performance",
    "mobil_zugaenglichkeit": "Mobil & Zugänglichkeit",
    "recht": "Recht",
    "lokale_sichtbarkeit": "Lokale Sichtbarkeit",
    "inhalt_aktualitaet": "Inhalt & Aktualität",
}


def render_report(score_result: dict, crawl_meta: dict) -> str:
    lines = []
    lines.append(f"# Audit-Bericht: {score_result['domain']}")
    lines.append("")
    lines.append(f"Erstellt: {datetime.now().strftime('%Y-%m-%d %H:%M')} · "
                  f"Regelwerk-Version: {score_result['ruleset_version']}")
    lines.append("")
    lines.append(f"## Gesamtscore: {score_result['overall']} / 100")
    lines.append("")
    lines.append("| Kategorie | Punkte |")
    lines.append("|---|---|")
    for cat, val in score_result["category_scores"].items():
        lines.append(f"| {CATEGORY_LABELS.get(cat, cat)} | {val} |")
    lines.append("")

    if score_result["findings"]:
        lines.append("## Befunde (automatisiert erkannt)")
        lines.append("")
        by_cat = {}
        for f in score_result["findings"]:
            by_cat.setdefault(f["category"], []).append(f)
        for cat, items in by_cat.items():
            lines.append(f"### {CATEGORY_LABELS.get(cat, cat)}")
            for f in sorted(items, key=lambda x: x["score_impact"]):
                count_str = f" (×{f['count']})" if "count" in f else ""
                lines.append(f"- **{f['score_impact']:+d}** — {f['message_internal']}{count_str}")
            lines.append("")

    if score_result["manual_review_needed"]:
        lines.append("## Noch manuell zu prüfen (v1 automatisiert diese Regeln nicht)")
        lines.append("")
        for r in score_result["manual_review_needed"]:
            lines.append(f"- [{CATEGORY_LABELS.get(r['category'], r['category'])}] {r['message_internal']}")
        lines.append("")

    lines.append("## Rohdaten")
    lines.append(f"- Gecrawlt am: {crawl_meta.get('crawled_at')}")
    lines.append(f"- Anzahl Requests beim Laden: {len(crawl_meta.get('requests', []))}")
    lines.append(f"- Screenshot: `{crawl_meta.get('screenshot_path')}`")

    return "\n".join(lines)


def run_report(run_dir: Path) -> Path:
    domain_files = list(run_dir.glob("*_score_result.json"))
    if not domain_files:
        raise FileNotFoundError(f"Keine *_score_result.json in {run_dir} gefunden")
    domain = domain_files[0].stem.replace("_score_result", "")

    score_result = json.loads((run_dir / f"{domain}_score_result.json").read_text())
    crawl_meta = json.loads((run_dir / f"{domain}_crawl.json").read_text())

    markdown = render_report(score_result, crawl_meta)
    out_path = run_dir / f"{domain}_report.md"
    out_path.write_text(markdown)
    return out_path


if __name__ == "__main__":
    import sys
    out_path = run_report(Path(sys.argv[1]))
    print(f"Bericht geschrieben nach: {out_path}")
