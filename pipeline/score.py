"""
pipeline/score.py

Layer 1 - Code, deterministisch. KEIN Sprachmodell beteiligt - das ist hier
bewusst so, damit derselbe Input immer denselben Score ergibt, egal wann
und wie oft er berechnet wird (Voraussetzung fuer den Vorher/Nachher-Vergleich,
den compare.py später macht).

Ablauf:
  1. ruleset.yaml laden (Gewichte, Deckel, Version)
  2. jede Regel-ID gegen eine kleine Pruef-Funktion mappen (siehe CHECKS unten)
  3. pro Kategorie Abzuege aufsummieren, bei 0 deckeln, vom Kategorie-Gewicht abziehen
  4. Gesamtscore = Summe der Kategoriewerte, nach unten hin auf ruleset["floor"] gedeckelt

Braucht KEINEN Netzwerkzugriff - laeuft rein auf den bereits gespeicherten
Probe-JSON-Dateien. Das ist der Teil, der sich in dieser Sandbox testen laesst.
"""

import json
from pathlib import Path

import yaml


def load_ruleset(path: Path = Path(__file__).parent.parent / "ruleset.yaml") -> dict:
    return yaml.safe_load(path.read_text())


# ---------------------------------------------------------------------------
# Pruef-Funktionen: eine pro automatisierter Regel-ID aus ruleset.yaml.
# Jede Funktion bekommt die drei rohen Probe-Dicts und gibt zurueck:
#   - für Einzel-Regeln: True/False (ausgelöst oder nicht)
#   - für "_per_unit"-Regeln: eine Ganzzahl (wie oft ausgelöst)
# ---------------------------------------------------------------------------

def _check_no_https(infra, perf, network, crawl_meta):
    return crawl_meta.get("url", "").startswith("http://")


def _check_cert_expired(infra, perf, network, crawl_meta):
    return bool(infra.get("tls", {}).get("expired"))


def _check_fcp_over_4s(infra, perf, network, crawl_meta):
    fcp = perf.get("first_contentful_paint_ms")
    return fcp is not None and fcp > 4000


def _check_fcp_2_5_to_4s(infra, perf, network, crawl_meta):
    fcp = perf.get("first_contentful_paint_ms")
    return fcp is not None and 2500 <= fcp <= 4000


def _check_page_weight_over_5mb(infra, perf, network, crawl_meta):
    size = perf.get("transfer_size_bytes")
    return size is not None and size > 5 * 1024 * 1024


def _check_large_uncompressed_images(infra, perf, network, crawl_meta):
    return bool(perf.get("large_image_over_1mb_detected"))  # v2, aktuell immer None -> False


def _check_outdated_cms_known_vulnerable(infra, perf, network, crawl_meta):
    return bool(infra.get("cms", {}).get("known_vulnerable_version"))


def _check_no_responsive_viewport(infra, perf, network, crawl_meta):
    return not network.get("viewport", {}).get("present", False)


def _check_viewport_blocks_zoom(infra, perf, network, crawl_meta):
    return bool(network.get("viewport", {}).get("blocks_zoom"))


def _check_alt_text_missing_majority(infra, perf, network, crawl_meta):
    return bool(network.get("alt_texts", {}).get("majority_missing"))


def _check_impressum_missing(infra, perf, network, crawl_meta):
    linked = network.get("legal_pages_linked", {}).get("impressum_linked", False)
    reachable_http = infra.get("legal_pages", {}).get("impressum", {}).get("reachable", False)
    reachable_spa = crawl_meta.get("legal_subpages", {}).get("impressum", {}).get("reachable", False)
    return not (linked or reachable_http or reachable_spa)


def _check_datenschutz_missing(infra, perf, network, crawl_meta):
    linked = network.get("legal_pages_linked", {}).get("datenschutz_linked", False)
    reachable_http = infra.get("legal_pages", {}).get("datenschutz", {}).get("reachable", False)
    reachable_spa = crawl_meta.get("legal_subpages", {}).get("datenschutz", {}).get("reachable", False)
    return not (linked or reachable_http or reachable_spa)


def _check_third_party_before_consent(infra, perf, network, crawl_meta):
    return len(network.get("trackers_before_consent", []))  # per_unit


def _check_no_localbusiness_schema(infra, perf, network, crawl_meta):
    return "application/ld+json" not in crawl_meta.get("html", "") or \
           "localbusiness" not in crawl_meta.get("html", "").lower()


def _check_no_sitemap_or_robots(infra, perf, network, crawl_meta):
    rs = infra.get("robots_sitemap", {})
    return not (rs.get("robots_txt") or rs.get("sitemap_xml"))


def _check_construction_notice_visible(infra, perf, network, crawl_meta):
    return bool(network.get("construction_notice"))


def _check_copyright_year_old(infra, perf, network, crawl_meta):
    cr = network.get("copyright", {})
    return bool(cr.get("found") and cr.get("older_than_2_years"))


def _check_template_placeholder_in_production(infra, perf, network, crawl_meta):
    return len(network.get("placeholders_found", [])) > 0


def _check_no_references_or_portfolio(infra, perf, network, crawl_meta):
    html_lower = crawl_meta.get("html", "").lower()
    keywords = ["referenz", "portfolio", "kundenstimme", "unsere projekte", "case stud"]
    return not any(k in html_lower for k in keywords)


def _check_outdated_legal_reference(infra, perf, network, crawl_meta):
    impressum_text = crawl_meta.get("legal_subpages", {}).get("impressum", {}).get("text_content", "")
    lower = impressum_text.lower()
    return ("rstv" in lower or "§ 55" in lower or "tmg" in lower) and "mstv" not in lower and "ddg" not in lower


def _check_impressum_no_authorized_rep(infra, perf, network, crawl_meta):
    impressum = crawl_meta.get("legal_subpages", {}).get("impressum", {})
    if not impressum.get("reachable"):
        return False  # wird bereits durch impressum_missing abgedeckt, keine Doppelbestrafung
    lower = impressum.get("text_content", "").lower()
    role_keywords = ["geschäftsführer", "inhaber", "vertreten durch", "vertretungsberechtigt"]
    return not any(k in lower for k in role_keywords)


def _check_dead_external_link(infra, perf, network, crawl_meta):
    return len(infra.get("dead_external_links", []))  # per_unit


# nicht implementierte (automated: false) Regeln brauchen keinen Eintrag hier -
# score() ueberspringt sie automatisch und markiert sie im Ergebnis als "manual_review"

CHECKS = {
    "technik.no_https": _check_no_https,
    "technik.cert_expired": _check_cert_expired,
    "technik.fcp_over_4s": _check_fcp_over_4s,
    "technik.fcp_2_5_to_4s": _check_fcp_2_5_to_4s,
    "technik.page_weight_over_5mb": _check_page_weight_over_5mb,
    "technik.large_uncompressed_images": _check_large_uncompressed_images,
    "technik.outdated_cms_known_vulnerable": _check_outdated_cms_known_vulnerable,
    "mobil.no_responsive_viewport": _check_no_responsive_viewport,
    "mobil.viewport_blocks_zoom": _check_viewport_blocks_zoom,
    "mobil.alt_text_missing_majority": _check_alt_text_missing_majority,
    "recht.impressum_missing": _check_impressum_missing,
    "recht.datenschutz_missing": _check_datenschutz_missing,
    "recht.third_party_before_consent": _check_third_party_before_consent,
    "seo.no_localbusiness_schema": _check_no_localbusiness_schema,
    "seo.no_sitemap_or_robots": _check_no_sitemap_or_robots,
    "inhalt.construction_notice_visible": _check_construction_notice_visible,
    "inhalt.copyright_year_old": _check_copyright_year_old,
    "inhalt.template_placeholder_in_production": _check_template_placeholder_in_production,
    "inhalt.no_references_or_portfolio": _check_no_references_or_portfolio,
    "recht.outdated_legal_reference": _check_outdated_legal_reference,
    "recht.impressum_no_authorized_rep": _check_impressum_no_authorized_rep,
    "inhalt.dead_external_link": _check_dead_external_link,
    # Diese zwei bleiben in v1 ehrlich unautomatisiert (siehe ruleset.yaml,
    # dort jetzt automated: false mit Begründung) - sie brauchen mehr als eine
    # Einzelseiten-Prüfung: einen Standort-Parameter pro Kunde bzw. einen
    # Mehrseiten-Crawl der ganzen Domain, nicht nur der Startseite.
}


def score(infra: dict, perf: dict, network: dict, crawl_meta: dict, ruleset: dict) -> dict:
    category_totals = {cat: 0 for cat in ruleset["category_weights"]}
    findings = []

    for rule in ruleset["rules"]:
        rid = rule["id"]
        if not rule.get("automated", False):
            continue  # v2-Regeln werden separat als "manual_review" ausgewiesen, s.u.

        check_fn = CHECKS.get(rid)
        if check_fn is None:
            continue  # sollte durch Tests abgefangen werden, s.o.

        outcome = check_fn(infra, perf, network, crawl_meta)

        if "deduction_per_unit" in rule:
            count = int(outcome)
            if count > 0:
                impact = min(count * rule["deduction_per_unit"], rule["max_deduction"])
                category_totals[rule["category"]] += impact
                findings.append({
                    "id": rid, "category": rule["category"], "source": "rule",
                    "count": count, "score_impact": -impact,
                    "message_internal": rule["message_internal"],
                    "message_customer": rule.get("message_customer"),
                })
        else:
            if outcome:
                impact = rule["deduction"]
                category_totals[rule["category"]] += impact
                findings.append({
                    "id": rid, "category": rule["category"], "source": "rule",
                    "score_impact": -impact,
                    "message_internal": rule["message_internal"],
                    "message_customer": rule.get("message_customer"),
                })

    category_scores = {}
    for cat, weight in ruleset["category_weights"].items():
        category_scores[cat] = max(0, weight - category_totals[cat])

    overall = max(ruleset["floor"], sum(category_scores.values()))

    manual_review_rules = [
        {"id": r["id"], "category": r["category"], "message_internal": r["message_internal"]}
        for r in ruleset["rules"] if not r.get("automated", False)
    ]

    return {
        "ruleset_version": ruleset["version"],
        "category_scores": category_scores,
        "overall": overall,
        "findings": findings,
        "manual_review_needed": manual_review_rules,
    }


def run_score(run_dir: Path) -> dict:
    """Liest crawl/probe-Dateien aus run_dir und schreibt score_result.json."""
    domain_files = list(run_dir.glob("*_crawl.json"))
    if not domain_files:
        raise FileNotFoundError(f"Keine *_crawl.json in {run_dir} gefunden")
    domain = domain_files[0].stem.replace("_crawl", "")

    crawl_meta = json.loads((run_dir / f"{domain}_crawl.json").read_text())
    infra = json.loads((run_dir / f"{domain}_probe_infra.json").read_text())
    perf = json.loads((run_dir / f"{domain}_probe_perf.json").read_text())
    network = json.loads((run_dir / f"{domain}_probe_network.json").read_text())

    ruleset = load_ruleset()
    result = score(infra, perf, network, crawl_meta, ruleset)
    result["domain"] = domain

    out_path = run_dir / f"{domain}_score_result.json"
    out_path.write_text(json.dumps(result, indent=2, ensure_ascii=False))
    return result


if __name__ == "__main__":
    import sys
    result = run_score(Path(sys.argv[1]))
    print(json.dumps(result, indent=2, ensure_ascii=False))
