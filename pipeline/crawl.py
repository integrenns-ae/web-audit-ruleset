"""
pipeline/crawl.py

Layer 1 - Code (deterministisch). Läuft mit echtem Netzwerkzugriff, also NICHT
in dieser Sandbox lauffähig - gedacht für den Hetzner-VPS oder lokale Ausführung.

Nutzt Playwright statt requests/BeautifulSoup, weil client-seitig gerenderte
Seiten (React/Vue-SPAs, z.B. integrenns.de) sonst als leere Huelle ankommen.

Output: crawl/<domain>.json mit:
  - html: das gerenderte HTML nach Netzwerk-Ruhe
  - requests: Liste aller Netzwerk-Requests (url, resource_type, size, status)
  - response_headers: Header der Hauptantwort
  - screenshot_path
  - timing: FCP und weitere Performance-Timings (siehe probe_perf.py)
"""

import asyncio
import os
import json
import time
from pathlib import Path
from urllib.parse import urlparse

from playwright.async_api import async_playwright

from pipeline.rechtsseiten import (
    FESTE_PFADE, als_pfad, ist_brauchbar, ziele_fuer,
)

# Zeitlimit je Rechtsseiten-Navigation. Knapper als die 15s der Startseite,
# weil im schlechtesten Fall mehrere Versuche hintereinander laufen und der
# Worker den ganzen Lauf nach 120s abbricht.
RECHTSSEITE_TIMEOUT_MS = 10_000


async def _sichtbarer_text(page) -> str:
    """innerText des Hauptdokuments PLUS aller Unterrahmen.

    Ohne die Unterrahmen bleibt ein Impressum unsichtbar, das — wie bei
    zahnarzt-argus.de — in einem <iframe> steckt: document.body.innerText
    reicht nicht ueber die Rahmengrenze.
    """
    teile = []
    for rahmen in page.frames:
        try:
            text = await rahmen.evaluate(
                "() => document.body ? document.body.innerText : ''")
        except Exception:
            continue
        if text and text.strip():
            teile.append(text)
    return "\n".join(teile)


async def _anker_inhalt(page, anker_id: str):
    """Text eines Seitenankers und, falls vorhanden, die Quelle seines iframes.

    Modale Dialoge (uk-modal u.a.) tragen ihren Inhalt entweder direkt im
    Element oder laden ihn per iframe nach. Der iframe wird NICHT geklickt,
    sondern seine Quelle zurueckgegeben und wie eine normale Seite geladen —
    das ist deterministisch und haengt nicht am Verhalten des Dialogs.
    """
    try:
        return await page.evaluate(
            """(id) => {
                const el = document.getElementById(id);
                if (!el) return {text: '', iframe: ''};
                const f = el.querySelector('iframe[src]');
                return {text: el.innerText || '', iframe: f ? f.src : ''};
            }""",
            anker_id,
        )
    except Exception:
        return {"text": "", "iframe": ""}


async def _rechtsseite_holen(page, start_url: str, schluessel: str, alle_links: list) -> dict:
    """Eine Rechtsseite finden und ihren Text einlesen."""
    leer = {"reachable": False, "path": None, "word_count": 0, "text_content": ""}

    def gefunden(ziel, text):
        return {"reachable": True, "path": als_pfad(ziel, start_url),
                "word_count": len(text.split()), "text_content": text}

    nachzuladen = []
    for ziel in ziele_fuer(alle_links, schluessel, start_url):
        try:
            if ziel.startswith("#"):
                # Anker nur auswerten, solange wir auf der Startseite stehen.
                if page.url.rstrip("/") != start_url.rstrip("/"):
                    await page.goto(start_url, wait_until="domcontentloaded", timeout=15_000)
                inhalt = await _anker_inhalt(page, ziel[1:])
                if ist_brauchbar(inhalt.get("text", "")):
                    return gefunden(ziel, inhalt["text"])
                if inhalt.get("iframe"):
                    nachzuladen.append((ziel, inhalt["iframe"]))
                continue

            antwort = await page.goto(ziel, wait_until="networkidle", timeout=RECHTSSEITE_TIMEOUT_MS)
            await page.wait_for_timeout(800)
            text = await _sichtbarer_text(page)
            if antwort and antwort.status < 400 and ist_brauchbar(text):
                return gefunden(ziel, text)
        except Exception:
            continue

    # Zuletzt die iframe-Quellen aus Modalen - sie kosten eine eigene Navigation.
    for anker, quelle in nachzuladen:
        try:
            antwort = await page.goto(quelle, wait_until="networkidle", timeout=RECHTSSEITE_TIMEOUT_MS)
            await page.wait_for_timeout(500)
            text = await _sichtbarer_text(page)
            if antwort and antwort.status < 400 and ist_brauchbar(text):
                return gefunden(anker, text)
        except Exception:
            continue

    return leer


async def crawl(url: str, out_dir: Path) -> dict:
    out_dir.mkdir(parents=True, exist_ok=True)
    domain = urlparse(url).netloc.lower().removeprefix("www.")

    requests_log = []

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        # AUDIT_INSECURE_TLS=1 nur setzen, wenn die Umgebung einen TLS-terminierenden
        # Egress-Proxy mit eigener CA benutzt (z.B. die Claude-Sandbox). Auf dem
        # Hetzner-VPS/lokal NICHT setzen - dort gilt normale Zertifikatsprüfung.
        _insecure_tls = os.environ.get("AUDIT_INSECURE_TLS") == "1"
        context = await browser.new_context(
            ignore_https_errors=_insecure_tls,
            viewport={"width": 390, "height": 844},  # mobile-first, wie die Zielgruppe surft
            user_agent=(
                "Mozilla/5.0 (Linux; Android 13; Mobile) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/124.0 Mobile Safari/537.36 "
                "IntegrennsAuditBot/1.0 (+https://integrenns.de/bot)"
            ),
        )
        page = await context.new_page()

        def on_request_finished(request):
            requests_log.append({
                "url": request.url,
                "resource_type": request.resource_type,
                "method": request.method,
                "timestamp": time.time(),
            })

        page.on("requestfinished", on_request_finished)

        response = await page.goto(url, wait_until="networkidle", timeout=30_000)
        main_headers = dict(response.headers) if response else {}
        main_status = response.status if response else None

        # kurze Wartezeit für spät nachladende Skripte (CSR-Apps)
        await page.wait_for_timeout(1500)

        html = await page.content()

        # Performance-Timings direkt aus der Navigation/Paint Timing API
        timing = await page.evaluate("""
            () => {
                const nav = performance.getEntriesByType('navigation')[0] || {};
                const paint = performance.getEntriesByType('paint');
                const fcp = paint.find(p => p.name === 'first-contentful-paint');
                return {
                    dom_content_loaded_ms: nav.domContentLoadedEventEnd || null,
                    load_event_ms: nav.loadEventEnd || null,
                    first_contentful_paint_ms: fcp ? fcp.startTime : null,
                    transfer_size_bytes: nav.transferSize || null,
                };
            }
        """)

        screenshot_path = out_dir / f"{domain}_screenshot.png"
        await page.screenshot(path=str(screenshot_path), full_page=True)

        # Wichtig für client-seitig geroutete SPAs (z.B. integrenns.de auf Lovable/
        # React): ein rohes requests.get(/impressum) sieht bei solchen Seiten oft nur
        # die leere App-Huelle. Playwright navigiert wie ein echter Browser und lässt
        # das Client-Routing tatsächlich greifen - deshalb hier zusätzlich per
        # Browser-Navigation prüfen, nicht nur per HTTP-Request in probe_infra.
        # Erst den LINKS der Seite folgen, erst danach Pfade raten. Begruendung
        # und Auswahl-Logik in pipeline/rechtsseiten.py.
        alle_links = await page.evaluate("""
            () => [...document.querySelectorAll('a[href]')].map(a => ({
                href: a.getAttribute('href') || '',
                abs:  a.href || '',
                text: (a.textContent || '').replace(/\\s+/g, ' ').trim().slice(0, 200),
            }))
        """)

        legal_subpages = {}
        for key in FESTE_PFADE:
            legal_subpages[key] = await _rechtsseite_holen(
                page, url, key, alle_links)

        # zurueck zur Startseite navigieren, falls weitere Schritte im selben Context folgen
        await page.goto(url, wait_until="domcontentloaded", timeout=15_000)

        await browser.close()

    result = {
        "url": url,
        "domain": domain,
        "crawled_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "html": html,
        "main_status": main_status,
        "main_headers": main_headers,
        "requests": requests_log,
        "timing": timing,
        "screenshot_path": str(screenshot_path),
        "legal_subpages": legal_subpages,
    }

    out_path = out_dir / f"{domain}_crawl.json"
    out_path.write_text(json.dumps(result, indent=2, ensure_ascii=False))
    return result


if __name__ == "__main__":
    import sys
    target = sys.argv[1] if len(sys.argv) > 1 else "https://integrenns.de"
    out = asyncio.run(crawl(target, Path("runs/_manual_crawl_test")))
    print(f"Crawled {out['domain']}: {len(out['requests'])} requests, "
          f"FCP={out['timing'].get('first_contentful_paint_ms')}ms")
