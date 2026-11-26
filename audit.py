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
from pipeline.robots import is_crawl_allowed
from pipeline.probe_infra import probe_infra
from pipeline.probe_perf import probe_perf
from pipeline.probe_network import probe_network
from pipeline.score import run_score
from pipeline.report import run_report


def anderes_schema(url: str) -> str:
    """https <-> http. Alles andere bleibt unveraendert."""
    if url.startswith("https://"):
        return url.replace("https://", "http://", 1)
    if url.startswith("http://"):
        return url.replace("http://", "https://", 1)
    return url


async def crawl_ohne_werfen(url: str, run_dir: Path):
    """Wie crawl(), liefert aber None statt zu werfen.

    Der Grund ist der Unterschied zwischen "antwortet schlecht" und
    "antwortet gar nicht": Ein TLS-Fehler (net::ERR_CERT_*, ERR_SSL_*) laesst
    Playwright WERFEN, er liefert keinen schlechten Status. Der Schema-
    Rueckfall weiter unten sah aber nur auf den Status - eine Seite ohne
    HTTPS, die ueber http einwandfrei antwortet, galt deshalb als "nicht
    erreichbar" und wurde verworfen.

    Das traf systematisch die falschen: Eine Firmenseite ohne HTTPS verliert
    im Score 25 Punkte und waere der deutlichste aller Befunde - sie kam aber
    nie bis zur Bewertung. Nachgewiesen an tierarztpraxis-doerr.de und
    recht-giessen.de, beide ueber http einwandfrei erreichbar.
    """
    try:
        return await crawl(url, run_dir)
    except Exception as e:  # noqa: BLE001 - jeder Crawl-Fehler soll den Rueckfall erlauben
        kurz = str(e).splitlines()[0][:120] if str(e) else ""
        print(f"      Crawl ueber {urlparse(url).scheme} fehlgeschlagen: "
              f"{type(e).__name__}: {kurz}")
        return None


async def run_audit(url: str) -> Path:
    if not url.startswith("http"):
        url = "https://" + url

    # Hoeflichkeits-Gate: sperrt die robots.txt unseren Bot komplett aus, wird
    # KEINE Seite abgerufen. Steht bewusst ganz vorn - vor dem Crawl UND vor dem
    # Anlegen des Lauf-Ordners, damit eine abgelehnte Domain keinerlei Spur
    # hinterlaesst (wir versprechen "kein einziger Request").
    allowed, robots_reason = is_crawl_allowed(url)
    if not allowed:
        print()
        print(f"ROBOTS DISALLOW: {robots_reason} - es wird keine Seite abgerufen "
              f"und bewusst KEIN Score erzeugt.")
        return None

    domain = urlparse(url).netloc.lower().removeprefix("www.")
    timestamp = time.strftime("%Y%m%d-%H%M%S")
    run_dir = Path("runs") / domain / timestamp
    run_dir.mkdir(parents=True, exist_ok=True)

    print(f"[1/5] Crawle {url} ...")
    crawl_result = await crawl_ohne_werfen(url, run_dir)

    # Schema-Fallback + Fehlerseiten-Schutz: Kommt über das erste Schema keine
    # brauchbare Antwort, das andere versuchen. "Keine brauchbare Antwort"
    # heißt dreierlei - der Crawl ist geworfen (crawl_result None, typisch bei
    # TLS-Fehlern), er lieferte keinen Status, oder einen ab 400. Alle drei
    # Fälle gehören in den Rückfall; vorher griff nur der dritte.
    status = crawl_result.get("main_status") if crawl_result else None
    if status is None or status >= 400:
        scheme = urlparse(url).scheme
        alt = anderes_schema(url)
        print(f"      Startseite liefert über {scheme} keine brauchbare Antwort "
              f"(Status {status}) - versuche {urlparse(alt).scheme} ...")
        alt_result = await crawl_ohne_werfen(alt, run_dir)
        alt_status = alt_result.get("main_status") if alt_result else None
        if alt_status is not None and alt_status < 400:
            url, crawl_result, status = alt, alt_result, alt_status
            print(f"      OK über {urlparse(alt).scheme} (Status {alt_status}).")

    # Beide Schemata unbrauchbar -> NICHT scoren (sonst Müll-Score aus Fehlerseite).
    if crawl_result is None or status is None or status >= 400:
        print()
        print(f"NICHT AUDITIERBAR: Startseite liefert Status {status} (auch über das "
              f"alternative Schema). Server blockiert automatisierte Zugriffe oder ist "
              f"offline - es wird bewusst KEIN Score erzeugt.")
        return None

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
    result = asyncio.run(run_audit(sys.argv[1]))
    sys.exit(0 if result else 2)
