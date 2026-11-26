"""Tests für den Schema-Rückfall in audit.py.

Hintergrund (v.19): Ein TLS-Fehler lässt den Crawl WERFEN, er liefert keinen
schlechten Status. Der Rückfall auf das andere Schema sah aber nur auf den
Status — eine Seite ohne HTTPS, die über http einwandfrei antwortet, galt
deshalb als "nicht erreichbar".

Das traf systematisch die falschen Seiten: Wer kein HTTPS hat, verliert im
Score 25 Punkte und wäre der deutlichste Befund überhaupt — kam aber nie bis
zur Bewertung. Nachgewiesen an tierarztpraxis-dörr.de und recht-giessen.de.
"""
from __future__ import annotations

import asyncio
import sys
import types
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

# playwright ist in der Testumgebung und in der CI bewusst NICHT installiert -
# diese Tests brauchen keinen Browser, sie ersetzen crawl() ohnehin. audit.py
# zieht es aber ueber pipeline.crawl herein. Ein Platzhalter genuegt, damit
# der Import gelingt; so bleibt die CI leichtgewichtig.
if "playwright" not in sys.modules:
    _pw = types.ModuleType("playwright")
    _api = types.ModuleType("playwright.async_api")
    _api.async_playwright = None
    _pw.async_api = _api
    sys.modules["playwright"] = _pw
    sys.modules["playwright.async_api"] = _api

import audit  # noqa: E402


# --- anderes_schema ---------------------------------------------------------

def test_schema_wird_getauscht():
    assert audit.anderes_schema("https://a.de/x") == "http://a.de/x"
    assert audit.anderes_schema("http://a.de/x") == "https://a.de/x"


def test_nur_das_erste_vorkommen_wird_getauscht():
    """Sonst zerschiesst eine URL mit einem Link im Query-String sich selbst."""
    u = "https://a.de/weiter?ziel=https://b.de"
    assert audit.anderes_schema(u) == "http://a.de/weiter?ziel=https://b.de"


def test_ohne_schema_unveraendert():
    assert audit.anderes_schema("a.de") == "a.de"


# --- crawl_ohne_werfen ------------------------------------------------------

def test_wirft_nicht_sondern_gibt_none(monkeypatch, tmp_path):
    async def kaputt(url, run_dir):
        raise RuntimeError("net::ERR_CERT_COMMON_NAME_INVALID")

    monkeypatch.setattr(audit, "crawl", kaputt)
    assert asyncio.run(audit.crawl_ohne_werfen("https://a.de", tmp_path)) is None


def test_reicht_das_ergebnis_durch(monkeypatch, tmp_path):
    async def gut(url, run_dir):
        return {"main_status": 200, "url": url}

    monkeypatch.setattr(audit, "crawl", gut)
    r = asyncio.run(audit.crawl_ohne_werfen("http://a.de", tmp_path))
    assert r["main_status"] == 200


# --- Der eigentliche Fehlerfall ---------------------------------------------

def _fake_crawl_nur_http(versuche: list):
    """https wirft (kein Zertifikat), http antwortet sauber."""
    async def crawl(url, run_dir):
        versuche.append(url)
        if url.startswith("https://"):
            raise RuntimeError("net::ERR_SSL_PROTOCOL_ERROR")
        return {"main_status": 200, "url": url, "requests": [],
                "timing": {"first_contentful_paint_ms": 900},
                "html": "<html><body>Praxis</body></html>",
                "legal_subpages": {}, "domain": "a.de"}
    return crawl


def test_seite_ohne_https_faellt_auf_http_zurueck(monkeypatch, tmp_path):
    """Der Kern: Nach einem geworfenen https-Crawl MUSS http versucht werden."""
    versuche: list[str] = []
    monkeypatch.setattr(audit, "crawl", _fake_crawl_nur_http(versuche))
    monkeypatch.setattr(audit, "is_crawl_allowed", lambda u: (True, "erlaubt"))

    # Nur bis zum Rueckfall laufen lassen - danach braucht es die Probes nicht.
    monkeypatch.chdir(tmp_path)
    try:
        asyncio.run(audit.run_audit("a.de"))
    except Exception:
        pass  # spaetere Schritte sind hier nicht Gegenstand

    assert versuche[0].startswith("https://"), versuche
    assert any(u.startswith("http://") for u in versuche), (
        "Nach dem geworfenen https-Crawl wurde http nie versucht - genau der Fehler")


def test_beide_schemata_kaputt_gibt_none(monkeypatch, tmp_path):
    """Gegenprobe: Eine wirklich tote Seite bleibt nicht auditierbar."""
    async def immer_kaputt(url, run_dir):
        raise RuntimeError("net::ERR_NAME_NOT_RESOLVED")

    monkeypatch.setattr(audit, "crawl", immer_kaputt)
    monkeypatch.setattr(audit, "is_crawl_allowed", lambda u: (True, "erlaubt"))
    monkeypatch.chdir(tmp_path)
    assert asyncio.run(audit.run_audit("gibtsnicht.invalid")) is None
