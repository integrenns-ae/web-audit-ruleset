"""
pipeline/rechtsseiten.py

Findet Impressum und Datenschutzerklärung einer Seite.

Warum ein eigenes Modul: Bis v.19 wurden drei feste Pfade geraten
(/impressum, /impressum.html, /legal-notice). Kleine deutsche Firmenseiten
legen ihre Rechtsseiten aber selten dort ab, wo ein Schema sie erwartet —
nachgewiesen an vier echten Seiten:

    <a href="#js-c641">Impressum</a>   Joomla-Modal, Inhalt per iframe
    <a href="/about/">Impressum</a>    Jimdo-Standardpfad
    <a href="#imprint">Impressum</a>   Anker auf derselben Seite

Deshalb wird jetzt zuerst den LINKS der Seite gefolgt und erst danach geraten.

Die Auswahl steckt bewusst hier in Python und nicht im Browser-Skript: So
lässt sie sich gegen echte Linklisten testen, ohne Playwright zu starten.
"""

from __future__ import annotations

from urllib.parse import urljoin, urlparse

# Ein Rechtslink wird an der ADRESSE oder an der BESCHRIFTUNG erkannt — dieselbe
# Logik wie in probe_network.check_legal_pages_linked (v.17).
RECHTS_WOERTER = {
    "impressum": ("impressum", "imprint", "anbieterkennzeichnung",
                  "legal notice", "legal-notice"),
    "datenschutz": ("datenschutz", "privacy", "privacy-policy"),
}

# Beschriftungen sind Navigations- oder Fußzeilenlinks, also kurz. Die Grenze
# verhindert, dass ein Fließtext-Absatz mit dem Wort "Impressum" darin als
# Link zum Impressum gilt.
MAX_BESCHRIFTUNG = 60

# Geraten wird weiterhin — aber erst, wenn kein Link weiterhalf.
# `.htm` ist dazugekommen: tierarztpraxis-dörr.de nutzt impressum.htm.
FESTE_PFADE = {
    "impressum": ["/impressum", "/impressum.html", "/impressum.htm", "/legal-notice"],
    "datenschutz": ["/datenschutz", "/datenschutzerklaerung", "/datenschutz.html",
                    "/datenschutz.htm", "/privacy-policy"],
}

# Zeitbudget: Der Worker bricht einen Lauf nach 120 Sekunden ab. Seit die
# Links der Seite gefolgt werden, sitzt der erste Versuch meist - der
# Normalfall ist also SCHNELLER als das frühere Raten. Nur der schlechteste
# Fall wächst, deshalb drei statt vier Versuche je Rechtsseite und ein
# knapperes Zeitlimit je Navigation (siehe crawl.py).
MAX_VERSUCHE = 3

# Unter so wenig Wörtern ist es keine Rechtsseite, sondern eine Fehlerseite
# oder eine leere Hülle.
MIN_WOERTER = 40


def _passt(wort_liste, adresse: str, beschriftung: str) -> int | None:
    """Rang des Treffers, kleiner ist besser. None = kein Treffer.

    0 = die Adresse selbst nennt es (…/impressum)
    1 = die Beschriftung ist genau das Wort ("Impressum")
    2 = die Beschriftung enthält es ("Impressum & Datenschutz")
    """
    a = adresse.lower()
    b = " ".join(beschriftung.split()).lower()
    if any(w in a for w in wort_liste):
        return 0
    if len(b) <= MAX_BESCHRIFTUNG:
        if b in wort_liste:
            return 1
        if any(w in b for w in wort_liste):
            return 2
    return None


def waehle_rechtslinks(links: list, schluessel: str, start_url: str) -> list:
    """Aus allen Links der Seite die heraussuchen, die zur Rechtsseite führen.

    `links` ist, was das Browser-Skript liefert: Listen-Einträge mit `href`
    (wie im Quelltext), `abs` (vom Browser aufgelöst) und `text`.

    Zurück kommen Ziele, beste zuerst, ohne Dubletten: entweder ein Anker
    ("#imprint") oder eine absolute URL. Fremde Hosts bleiben draußen — wohin
    ein Link auf eine fremde Domain führt, ist nicht vorhersehbar, und der
    Text dort gehört nicht zwingend zu dieser Seite.
    """
    woerter = RECHTS_WOERTER[schluessel]
    eigener_host = (urlparse(start_url).netloc or "").lower().removeprefix("www.")

    bewertet = []
    for i, link in enumerate(links or []):
        if not isinstance(link, dict):
            continue
        href = (link.get("href") or "").strip()
        absolut = (link.get("abs") or "").strip()
        text = link.get("text") or ""
        if not href:
            continue

        rang = _passt(woerter, href + " " + absolut, text)
        if rang is None:
            continue

        if href.startswith("#"):
            ziel = href
        else:
            ziel = absolut or urljoin(start_url, href)
            host = (urlparse(ziel).netloc or "").lower().removeprefix("www.")
            if host and eigener_host and host != eigener_host:
                continue
            if urlparse(ziel).scheme not in ("http", "https"):
                continue
        # i hält die Reihenfolge der Seite stabil, wenn der Rang gleich ist.
        bewertet.append((rang, i, ziel))

    bewertet.sort()
    gesehen = set()
    raus = []
    for _, _, ziel in bewertet:
        if ziel in gesehen:
            continue
        gesehen.add(ziel)
        raus.append(ziel)
    return raus


def ziele_fuer(links: list, schluessel: str, start_url: str) -> list:
    """Vollständige Versuchsreihe: erst die Links der Seite, dann geratene Pfade."""
    basis = start_url.rstrip("/")
    ziele = waehle_rechtslinks(links, schluessel, start_url)
    gesehen = set(ziele)
    for pfad in FESTE_PFADE[schluessel]:
        kandidat = basis + pfad
        if kandidat not in gesehen:
            gesehen.add(kandidat)
            ziele.append(kandidat)
    return ziele[:MAX_VERSUCHE]


def ist_brauchbar(text: str) -> bool:
    return bool(text) and len(text.split()) >= MIN_WOERTER


def als_pfad(ziel: str, start_url: str) -> str:
    """Kurzform fürs Protokoll: Pfad statt voller URL, Anker bleibt Anker."""
    if ziel.startswith("#"):
        return ziel
    teile = urlparse(ziel)
    if not teile.netloc:
        return ziel
    return (teile.path or "/") + (f"#{teile.fragment}" if teile.fragment else "")
