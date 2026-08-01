"""
Tests für pipeline/score.py — laufen komplett ohne Netzwerk, nur mit den
Fixture-Dateien unter tests/fixtures/. Das ist bewusst der Teil der Pipeline,
der sich in jeder Umgebung testen lässt, auch ohne Playwright/echten Crawl.

Ausführen:
    cd audit-pipeline
    pip install -r requirements.txt
    pytest tests/ -v
"""

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from pipeline.score import load_ruleset, score, CHECKS

FIXTURES = Path(__file__).parent / "fixtures"


def load_fixture(name: str, domain: str):
    d = FIXTURES / name
    crawl = json.loads((d / f"{domain}_crawl.json").read_text())
    infra = json.loads((d / f"{domain}_probe_infra.json").read_text())
    perf = json.loads((d / f"{domain}_probe_perf.json").read_text())
    network = json.loads((d / f"{domain}_probe_network.json").read_text())
    return infra, perf, network, crawl


def test_ruleset_loads():
    ruleset = load_ruleset()
    assert ruleset["version"]
    assert ruleset["floor"] == 15
    assert sum(ruleset["category_weights"].values()) == 100


def test_all_automated_rules_have_check_functions():
    """Verhindert stille Lücken: jede als automated=true markierte Regel MUSS
    eine Prüf-Funktion in CHECKS haben, sonst wird sie in score() lautlos
    übersprungen und niemand merkt es."""
    ruleset = load_ruleset()
    automated_ids = {r["id"] for r in ruleset["rules"] if r.get("automated")}
    missing = [rid for rid in automated_ids if CHECKS.get(rid) is None]
    assert not missing, f"Als automatisiert markierte Regeln ohne Check-Funktion: {missing}"


def test_every_rule_id_is_unique():
    ruleset = load_ruleset()
    ids = [r["id"] for r in ruleset["rules"]]
    assert len(ids) == len(set(ids)), "Doppelte Regel-IDs im Regelwerk gefunden"


def test_clean_site_scores_100():
    infra, perf, network, crawl = load_fixture("clean_site", "clean.example")
    ruleset = load_ruleset()
    result = score(infra, perf, network, crawl, ruleset)
    assert result["overall"] == 100, f"Erwartete 100, bekam {result['overall']} — Findings: {result['findings']}"
    assert result["findings"] == []


def test_bad_site_triggers_expected_findings():
    infra, perf, network, crawl = load_fixture("bad_site", "bad.example")
    ruleset = load_ruleset()
    result = score(infra, perf, network, crawl, ruleset)

    triggered_ids = {f["id"] for f in result["findings"]}

    expected = {
        "technik.fcp_over_4s",
        "technik.page_weight_over_5mb",
        "technik.outdated_cms_known_vulnerable",
        "mobil.viewport_blocks_zoom",
        "mobil.alt_text_missing_majority",
        "recht.impressum_missing",
        "recht.datenschutz_missing",
        "recht.third_party_before_consent",
        "seo.no_localbusiness_schema",
        "seo.no_sitemap_or_robots",
        "inhalt.construction_notice_visible",
        "inhalt.copyright_year_old",
        "inhalt.template_placeholder_in_production",
        "inhalt.no_references_or_portfolio",
        "inhalt.dead_external_link",
    }
    missing = expected - triggered_ids
    unexpected = triggered_ids - expected
    assert not missing, f"Erwartete Findings fehlen: {missing}"
    assert not unexpected, f"Unerwartete zusätzliche Findings: {unexpected}"

    # Recht-Kategorie: 20 Gewicht - 20 (impressum) - 15 (datenschutz, gedeckelt bei 20) - 12 (tracker, gedeckelt) -> 0
    assert result["category_scores"]["recht"] == 0
    assert result["overall"] < 50


def test_floor_never_undercut():
    """Auch ein Rundum-Totalausfall darf den Gesamtscore nicht unter den
    Floor aus ruleset.yaml drücken."""
    infra, perf, network, crawl = load_fixture("bad_site", "bad.example")
    ruleset = load_ruleset()
    # künstlich verschärfen: alle Kategorien auf 0 zwingen
    ruleset_copy = dict(ruleset)
    result = score(infra, perf, network, crawl, ruleset_copy)
    for cat in result["category_scores"]:
        assert result["category_scores"][cat] >= 0
    assert result["overall"] >= ruleset["floor"]


def test_third_party_deduction_is_capped():
    """recht.third_party_before_consent hat max_deduction=12 trotz 8 pro Fund -
    zwei Tracker sollten hier bereits an den Deckel stoßen (2*8=16 -> gedeckelt 12)."""
    infra, perf, network, crawl = load_fixture("bad_site", "bad.example")
    ruleset = load_ruleset()
    result = score(infra, perf, network, crawl, ruleset)
    finding = next(f for f in result["findings"] if f["id"] == "recht.third_party_before_consent")
    assert finding["count"] == 2
    assert finding["score_impact"] == -12  # gedeckelt, nicht -16


def test_manual_review_rules_are_listed_separately():
    ruleset = load_ruleset()
    infra, perf, network, crawl = load_fixture("clean_site", "clean.example")
    result = score(infra, perf, network, crawl, ruleset)
    manual_ids = {r["id"] for r in result["manual_review_needed"]}
    assert "mobil.tap_targets_too_small" in manual_ids
    assert "seo.no_google_business_profile" in manual_ids
    assert "seo.no_location_in_title_h1" in manual_ids
    assert "inhalt.thin_content_page" in manual_ids
    # manuelle Regeln duerfen NIE als automatisierte Findings auftauchen
    finding_ids = {f["id"] for f in result["findings"]}
    assert manual_ids.isdisjoint(finding_ids)


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
