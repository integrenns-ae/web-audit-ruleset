"""Tests für pipeline/rechtsseiten.py — welchem Link folgt der Crawler?

Die vier Linkmuster unten stammen aus echten Seiten, die alle ein
vollständiges Impressum haben und bis v.19 trotzdem keines lieferten, weil
nur drei feste Pfade geraten wurden.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from pipeline.rechtsseiten import (  # noqa: E402
    MAX_VERSUCHE, als_pfad, ist_brauchbar, waehle_rechtslinks, ziele_fuer,
)

START = "https://www.beispiel.de"


def L(href, text, abs_=None):
    return {"href": href, "abs": abs_ if abs_ is not None else
            (href if href.startswith("http") else
             ("" if href.startswith("#") else START + href)), "text": text}


# --- Die echten Faelle ------------------------------------------------------

def test_joomla_modal_anker():
    """zahnarzt-argus.de"""
    links = [L("#js-c641", "Impressum"), L("/leistungen", "Leistungen")]
    assert waehle_rechtslinks(links, "impressum", START) == ["#js-c641"]


def test_jimdo_about_pfad():
    """tierarzt-hahn.de und gaststaette-lapp.de"""
    links = [L("/about/", "Impressum")]
    assert waehle_rechtslinks(links, "impressum", START) == [START + "/about/"]


def test_anker_auf_derselben_seite():
    """recht-giessen.de"""
    links = [L("#imprint", "Impressum")]
    assert waehle_rechtslinks(links, "impressum", START) == ["#imprint"]


def test_klassischer_pfad_schlaegt_beschriftung():
    """Rangfolge: Wer die Adresse richtig benennt, wird zuerst versucht."""
    links = [L("/about/", "Impressum"), L("/impressum", "Rechtliches")]
    assert waehle_rechtslinks(links, "impressum", START)[0] == START + "/impressum"


# --- Trennung der beiden Seiten --------------------------------------------

def test_datenschutz_und_impressum_werden_getrennt():
    links = [L("/a", "Impressum"), L("/b", "Datenschutz")]
    assert waehle_rechtslinks(links, "impressum", START) == [START + "/a"]
    assert waehle_rechtslinks(links, "datenschutz", START) == [START + "/b"]


def test_gemeinsamer_link_zaehlt_fuer_beide():
    links = [L("/recht", "Impressum & Datenschutz")]
    assert waehle_rechtslinks(links, "impressum", START) == [START + "/recht"]
    assert waehle_rechtslinks(links, "datenschutz", START) == [START + "/recht"]


# --- Was draussen bleibt ----------------------------------------------------

def test_fremde_hosts_werden_nicht_verfolgt():
    """Wohin ein Link auf eine fremde Domain fuehrt, ist nicht vorhersehbar."""
    links = [L("https://fremd.de/impressum", "Impressum",
               abs_="https://fremd.de/impressum")]
    assert waehle_rechtslinks(links, "impressum", START) == []


def test_www_gilt_als_derselbe_host():
    links = [L("https://beispiel.de/impressum", "Impressum",
               abs_="https://beispiel.de/impressum")]
    assert waehle_rechtslinks(links, "impressum", START) == [
        "https://beispiel.de/impressum"]


def test_mailto_und_javascript_bleiben_draussen():
    links = [L("mailto:info@beispiel.de", "Impressum"),
             L("javascript:void(0)", "Impressum")]
    assert waehle_rechtslinks(links, "impressum", START) == []


def test_langer_fliesstext_ist_kein_navigationslink():
    links = [L("/blog/1", "Was alles in ein Impressum gehört und warum das "
                          "für jede Website wichtig ist")]
    assert waehle_rechtslinks(links, "impressum", START) == []


def test_ohne_treffer_leere_liste():
    assert waehle_rechtslinks([L("/kontakt", "Kontakt")], "impressum", START) == []
    assert waehle_rechtslinks([], "impressum", START) == []
    assert waehle_rechtslinks(None, "impressum", START) == []


def test_dubletten_fallen_weg():
    links = [L("/impressum", "Impressum"), L("/impressum", "Impressum"),
             L("/impressum", "Rechtliches")]
    assert waehle_rechtslinks(links, "impressum", START) == [START + "/impressum"]


# --- Versuchsreihe ----------------------------------------------------------

def test_geratene_pfade_kommen_nach_den_links():
    ziele = ziele_fuer([L("/about/", "Impressum")], "impressum", START)
    assert ziele[0] == START + "/about/"
    assert START + "/impressum" in ziele


def test_htm_variante_wird_geraten():
    """tierarztpraxis-dörr.de nutzt impressum.htm - fehlte bis v.19."""
    ziele = ziele_fuer([], "impressum", "https://doerr.de")
    assert any(z.endswith("/impressum.htm") for z in ziele)


def test_versuche_sind_gedeckelt():
    """Jeder Versuch kostet bis zu 15s, der Worker bricht nach 120s ab."""
    viele = [L(f"/x{i}", "Impressum") for i in range(20)]
    assert len(ziele_fuer(viele, "impressum", START)) == MAX_VERSUCHE


def test_kein_doppelter_eintrag_zwischen_link_und_pfad():
    ziele = ziele_fuer([L("/impressum", "Impressum")], "impressum", START)
    assert ziele.count(START + "/impressum") == 1


# --- Kleinkram --------------------------------------------------------------

def test_brauchbarkeit_nach_wortzahl():
    assert ist_brauchbar("Wort " * 40) is True
    assert ist_brauchbar("Wort " * 39) is False
    assert ist_brauchbar("") is False


def test_pfad_kurzform():
    assert als_pfad("#imprint", START) == "#imprint"
    assert als_pfad(START + "/about/", START) == "/about/"
    assert als_pfad(START + "/x#y", START) == "/x#y"
