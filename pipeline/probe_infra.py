"""
pipeline/probe_infra.py

Layer 1 - Code. Prueft TLS-Zertifikat, DNS-Aufloesung, CMS/Framework-Fingerprint
und ob robots.txt/sitemap.xml erreichbar sind. Braucht ebenfalls echten
Netzwerkzugriff (ssl-Handshake, HTTP-Requests) - nicht in dieser Sandbox lauffaehig.
"""

import json
import socket
import ssl
from datetime import datetime, timezone
from pathlib import Path

import requests

# bekannte Fingerprints: (Suchstring im HTML/Headern) -> Label
CMS_FINGERPRINTS = {
    "wp-content": "WordPress",
    "wp-includes": "WordPress",
    "Joomla!": "Joomla",
    "/media/jui/": "Joomla",
    "Drupal.settings": "Drupal",
    "lovable-tagger": "Lovable (React/Vite SPA)",
    "lovable.dev": "Lovable (React/Vite SPA)",
    "wix.com": "Wix",
    "jimdo": "Jimdo",
    "squarespace": "Squarespace",
}

# Versionen mit bekannten kritischen CVEs - Liste ist bewusst klein gehalten,
# soll regelmaessig gepflegt werden (siehe Kommentar am Ende der Datei)
KNOWN_VULNERABLE_PATTERNS = [
    "wp-content/themes/twentyseventeen",  # Platzhalter-Beispiel, echte Liste ergaenzen
]


def check_tls(domain: str) -> dict:
    ctx = ssl.create_default_context()
    try:
        with socket.create_connection((domain, 443), timeout=10) as sock:
            with ctx.wrap_socket(sock, server_hostname=domain) as ssock:
                cert = ssock.getpeercert()
                not_after = datetime.strptime(cert["notAfter"], "%b %d %H:%M:%S %Y %Z")
                not_after = not_after.replace(tzinfo=timezone.utc)
                return {
                    "valid": True,
                    "expires_at": not_after.isoformat(),
                    "expired": not_after < datetime.now(timezone.utc),
                }
    except Exception as e:
        return {"valid": False, "error": str(e), "expired": None}


def check_dns(domain: str) -> dict:
    try:
        ip = socket.gethostbyname(domain)
        return {"resolves": True, "ip": ip}
    except Exception as e:
        return {"resolves": False, "error": str(e)}


def check_robots_sitemap(base_url: str) -> dict:
    result = {"robots_txt": False, "sitemap_xml": False}
    for path, key in (("/robots.txt", "robots_txt"), ("/sitemap.xml", "sitemap_xml")):
        try:
            resp = requests.get(base_url.rstrip("/") + path, timeout=8)
            result[key] = resp.status_code == 200 and len(resp.text.strip()) > 0
        except Exception:
            result[key] = False
    return result


# Häufige Pfad-Varianten für die beiden Pflichtseiten. Eine reine "ist verlinkt"-
# Prüfung reicht nicht (kaputte/relative Links, JS-Routing) - hier wird aktiv
# nachgeschaut, ob unter einem der üblichen Pfade tatsächlich Inhalt liegt.
LEGAL_PAGE_PATH_CANDIDATES = {
    "impressum": ["/impressum", "/impressum.html", "/impressum.php", "/de/impressum", "/legal-notice"],
    "datenschutz": ["/datenschutz", "/datenschutz.html", "/datenschutz.php", "/de/datenschutz", "/privacy-policy", "/datenschutzerklaerung"],
}


def check_legal_pages_reachable(base_url: str) -> dict:
    result = {}
    for key, paths in LEGAL_PAGE_PATH_CANDIDATES.items():
        reachable = False
        found_path = None
        for path in paths:
            try:
                resp = requests.get(base_url.rstrip("/") + path, timeout=8, allow_redirects=True)
                if resp.status_code == 200 and len(resp.text.strip()) > 200:
                    reachable = True
                    found_path = path
                    break
            except Exception:
                continue
        result[key] = {"reachable": reachable, "path": found_path}
    return result


def detect_cms(html: str, headers: dict) -> dict:
    haystack = html + " " + " ".join(f"{k}:{v}" for k, v in headers.items())
    matches = [label for needle, label in CMS_FINGERPRINTS.items() if needle.lower() in haystack.lower()]
    vulnerable = any(pat.lower() in haystack.lower() for pat in KNOWN_VULNERABLE_PATTERNS)
    return {
        "detected": sorted(set(matches)) or ["unbekannt / vermutlich Individualentwicklung"],
        "known_vulnerable_version": vulnerable,
    }


def check_external_links(html: str, own_domain: str, max_links: int = 20) -> list:
    """Prüft bis zu max_links externe Links per HEAD-Request auf Fehlerstatus.
    Absichtlich gedeckelt, um den Audit nicht durch hunderte Links zu verlangsamen -
    das ist ein Stichprobenverfahren, keine vollständige Linkprüfung."""
    import re
    hrefs = re.findall(r'href=["\']([^"\']+)["\']', html, re.I)
    external = [h for h in hrefs if h.startswith("http") and own_domain not in h]
    external = list(dict.fromkeys(external))[:max_links]  # dedupe, Reihenfolge erhalten

    dead = []
    for link in external:
        try:
            resp = requests.head(link, timeout=6, allow_redirects=True)
            if resp.status_code >= 400:
                dead.append({"url": link, "status": resp.status_code})
        except Exception as e:
            dead.append({"url": link, "status": None, "error": str(e)})
    return dead


def probe_infra(crawl_result: dict, out_dir: Path) -> dict:
    domain = crawl_result["domain"]
    base_url = crawl_result["url"]

    result = {
        "domain": domain,
        "tls": check_tls(domain),
        "dns": check_dns(domain),
        "robots_sitemap": check_robots_sitemap(base_url),
        "legal_pages": check_legal_pages_reachable(base_url),
        "cms": detect_cms(crawl_result["html"], crawl_result.get("main_headers", {})),
        "dead_external_links": check_external_links(crawl_result["html"], domain),
    }

    out_path = out_dir / f"{domain}_probe_infra.json"
    out_path.write_text(json.dumps(result, indent=2, ensure_ascii=False))
    return result


if __name__ == "__main__":
    import sys
    crawl_path = Path(sys.argv[1])
    crawl_result = json.loads(crawl_path.read_text())
    out = probe_infra(crawl_result, crawl_path.parent)
    print(json.dumps(out, indent=2, ensure_ascii=False))

# Pflegehinweis (Nachhaltigkeit): CMS_FINGERPRINTS und KNOWN_VULNERABLE_PATTERNS
# sind Wörterbücher, die über die Zeit wachsen sollen - jedes Mal, wenn ein Audit
# eine neue Software/Version erkennt, hier ergänzen statt im Code zu verstreuen.
