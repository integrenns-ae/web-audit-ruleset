#!/usr/bin/env python3
"""
audit.py - manuell getriggerter Audit-Durchlauf für eine einzelne URL.

Nutzung:
    python audit.py https://integrenns.de

Läuft NICHT in einer netzwerk-eingeschränkten Sandbox - braucht echten
Zugriff auf die Zielseite (Playwright startet einen echten Chromium-Browser).
Gedacht für Ausführung auf dem Hetzner-VPS oder lokal.

Speichert alles unter runs/<domain>/<timestamp>/ - jeder Lauf bleibt erhalten,
damit compare.py spätere Läufe gegen frühere vergleichen kann (Vorher/Nachher).

Setup einmalig:
    pip install -r requirements.txt
    playwright install chromium --with-deps
"""

import asyncio
import sys
import time
from pathlib import Path
from urllib.parse import urlparse

from pipeline.crawl import crawl
from pipeline.probe_infra import probe_infra
from pipeline.probe_perf import probe_perf
from pipeline.probe_network import probe_network
from pipeline.score import run_score
from pipeline.report import run_report


async def run_audit(url: str) -> Path:
    if not url.startswith("http"):
        url = "https://" + url

    domain = urlparse(url).netloc.lower().removeprefix("www.")
    timestamp = time.strftime("%Y%m%d-%H%M%S")
    run_dir = Path("runs") / domain / timestamp
    run_dir.mkdir(parents=True, exist_ok=True)

    print(f"[1/5] Crawle {url} ...")
    crawl_result = await crawl(url, run_dir)
    print(f"      {len(crawl_result['requests'])} Requests, "
          f"FCP={crawl_result['timing'].get('first_contentful_paint_ms')}ms")

    print("[2/5] Prüfe Infrastruktur (TLS, DNS, CMS, Rechtsseiten, robots/sitemap) ...")
    probe_infra(crawl_result, run_dir)

    print("[3/5] Prüfe Performance-Kennzahlen ...")
    probe_perf(crawl_result, run_dir)

    print("[4/5] Prüfe Netzwerk/Inhalt (Tracker, Viewport, Platzhalter, Copyright) ...")
    probe_network(crawl_result, run_dir)

    print("[5/5] Berechne Score und erzeuge Bericht ...")
    score_result = run_score(run_dir)
    report_path = run_report(run_dir)

    print()
    print(f"Gesamtscore: {score_result['overall']} / 100")
    print(f"Bericht: {report_path}")
    print(f"Alle Rohdaten: {run_dir}/")
    return run_dir


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Nutzung: python audit.py <url>")
        sys.exit(1)
    asyncio.run(run_audit(sys.argv[1]))
