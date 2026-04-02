"""
Curia Proxy Server
==================
Umgeht CORS, indem Curia-Anfragen serverseitig ausgeführt werden.
Start: python server.py
API läuft dann auf http://localhost:5000
"""

from flask import Flask, request, jsonify
from flask_cors import CORS
import requests
from bs4 import BeautifulSoup
from urllib.parse import urlencode, quote
import re
import json

app = Flask(__name__)
CORS(app)  # Erlaubt Browser-Zugriff von jeder Origin (für lokale Nutzung)

# ─── Curia-URL-Konstruktion ────────────────────────────────────────────────────

CURIA_BASE = "https://juris.curia.europa.eu/juris/recherche.jsf"

# Vorcodierte Zitationsparameter für häufig genutzte Richtlinien
# Format: cit=L,C,CJ,R,{OJYEAR},,{YEAR},{NUMBER},,,,,,,,true,false,false
DIRECTIVE_CITATIONS = {
    "2009/81":  "L%2CC%2CCJ%2CR%2C2009E%2C%2C2009%2C81%2C%2C%2C%2C%2C%2C%2Ctrue%2Cfalse%2Cfalse",
    "2014/24":  "L%2CC%2CCJ%2CR%2C2014E%2C%2C2014%2C24%2C%2C%2C%2C%2C%2C%2Ctrue%2Cfalse%2Cfalse",
    "2014/25":  "L%2CC%2CCJ%2CR%2C2014E%2C%2C2014%2C25%2C%2C%2C%2C%2C%2C%2Ctrue%2Cfalse%2Cfalse",
    "2014/23":  "L%2CC%2CCJ%2CR%2C2014E%2C%2C2014%2C23%2C%2C%2C%2C%2C%2C%2Ctrue%2Cfalse%2Cfalse",
    "2004/18":  "L%2CC%2CCJ%2CR%2C2004E%2C%2C2004%2C18%2C%2C%2C%2C%2C%2C%2Ctrue%2Cfalse%2Cfalse",
    "2016/680": "L%2CC%2CCJ%2CR%2C2016E%2C%2C2016%2C680%2C%2C%2C%2C%2C%2C%2Ctrue%2Cfalse%2Cfalse",
    "2018/1972":"L%2CC%2CCJ%2CR%2C2018E%2C%2C2018%2C1972%2C%2C%2C%2C%2C%2C%2Ctrue%2Cfalse%2Cfalse",
}

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "de,en;q=0.9",
    "Accept-Encoding": "gzip, deflate, br",
    "Connection": "keep-alive",
    "Referer": "https://curia.europa.eu/",
}


def build_curia_url(text=None, directive=None, article=None,
                    court="C,T,F", language="de", date_from=None, date_to=None):
    """Baut eine Curia-Such-URL aus strukturierten Parametern."""

    dates_param = ""
    if date_from or date_to:
        d_from = date_from.replace("-", ".") if date_from else ""
        d_to   = date_to.replace("-", ".")   if date_to   else ""
        dates_param = f"{d_from}|{d_to}"

    # Textparameter: text + ggf. Artikel kombinieren
    text_parts = []
    if text:
        text_parts.append(text)
    if article:
        text_parts.append(article)
    combined_text = " ".join(text_parts) if text_parts else None

    params = {
        "nat":      "or",
        "mat":      "or",
        "pcs":      "Oor",
        "jur":      court,
        "language": language,
        "etat":     "clot",
        "lgrec":    language,
        "td":       ";;&;PUB1,PUB2,PUB7;NPUB1;;;ORDALL",
        "oqp":      "",
        "avg":      "",
        "for":      "",
        "jge":      "",
        "pro":      "",
        "lg":       "",
        "dates":    dates_param,
    }

    if combined_text:
        params["text"] = combined_text

    if directive and directive in DIRECTIVE_CITATIONS:
        params["cit"] = DIRECTIVE_CITATIONS[directive]

    return CURIA_BASE + "?" + urlencode(params, quote_via=quote)


# ─── HTML-Parser für Curia-Suchergebnisse ─────────────────────────────────────

def parse_curia_results(html: str) -> list[dict]:
    """
    Parst die Curia-Suchergebnisseite und extrahiert strukturierte Urteilsdaten.
    Curia rendert Ergebnisse in einer Tabelle mit class 'detail'.
    """
    soup = BeautifulSoup(html, "html.parser")
    results = []

    # Curia zeigt Ergebnisse in Zeilen mit class 'normal' oder 'odd'
    rows = soup.select("table.detail tr.normal, table.detail tr.odd")

    if not rows:
        # Fallback: Suche nach alternativen Strukturen
        rows = soup.select("tr[class*='normal'], tr[class*='odd']")

    for row in rows:
        cells = row.find_all("td")
        if len(cells) < 3:
            continue

        try:
            # Zelle 0: Datum
            datum_raw = cells[0].get_text(strip=True) if len(cells) > 0 else ""

            # Zelle 1: Aktenzeichen + Link zum Dokument
            az_cell = cells[1] if len(cells) > 1 else None
            az = ""
            doc_url = ""
            if az_cell:
                az_link = az_cell.find("a")
                if az_link:
                    az = az_link.get_text(strip=True)
                    href = az_link.get("href", "")
                    if href.startswith("/"):
                        doc_url = "https://curia.europa.eu" + href
                    elif href.startswith("http"):
                        doc_url = href
                else:
                    az = az_cell.get_text(strip=True)

            # Zelle 2: Name/Parteien
            name_cell = cells[2] if len(cells) > 2 else None
            name = name_cell.get_text(strip=True) if name_cell else ""

            # Zelle 3 (optional): Gericht / Verfahrensart
            typ_cell = cells[3] if len(cells) > 3 else None
            typ = typ_cell.get_text(strip=True) if typ_cell else ""

            # Zelle 4 (optional): Sprachen / Publikation
            pub_cell = cells[4] if len(cells) > 4 else None
            pub = pub_cell.get_text(strip=True) if pub_cell else ""

            if not az and not name:
                continue

            # Curia-Liste-URL für Aktenzeichen
            curia_list_url = (
                f"https://curia.europa.eu/juris/liste.jsf?num={quote(az)}"
                if az else doc_url
            )

            results.append({
                "aktenzeichen": az,
                "datum":        datum_raw,
                "parteien":     name,
                "typ":          typ,
                "publikation":  pub,
                "curia_url":    curia_list_url,
                "doc_url":      doc_url,
            })

        except Exception:
            continue

    return results


def parse_total_count(html: str) -> int:
    """Extrahiert die Gesamtanzahl der Treffer aus der Curia-Seite."""
    soup = BeautifulSoup(html, "html.parser")

    # Curia zeigt z.B. "Ergebnisse 1 - 25 von 47"
    for tag in soup.find_all(string=re.compile(r"\d+\s*(?:Ergebnis|result|résultat)", re.I)):
        m = re.search(r"(\d+)", tag)
        if m:
            return int(m.group(1))

    # Fallback: Anzahl der geparsten Zeilen
    return -1


# ─── API-Endpunkte ─────────────────────────────────────────────────────────────

@app.route("/search", methods=["GET"])
def search():
    """
    Hauptsuche-Endpoint.

    Parameter:
        text       - Freitextsuche (z.B. "Militärausrüstung")
        directive  - Richtlinie (z.B. "2009/81")
        article    - Artikel (z.B. "Art. 346")
        court      - Gerichte: "C" (EuGH), "T" (EuG), "C,T,F" (alle)
        language   - Sprache: "de", "en", "fr"
        date_from  - Datum von (YYYY-MM-DD)
        date_to    - Datum bis (YYYY-MM-DD)
        page       - Seite (noch nicht implementiert)
    """
    text      = request.args.get("text",      "").strip() or None
    directive = request.args.get("directive", "").strip() or None
    article   = request.args.get("article",   "").strip() or None
    court     = request.args.get("court",     "C,T,F")
    language  = request.args.get("language",  "de")
    date_from = request.args.get("date_from", "").strip() or None
    date_to   = request.args.get("date_to",   "").strip() or None

    if not text and not directive and not article:
        return jsonify({"error": "Mindestens ein Suchparameter erforderlich (text, directive oder article)"}), 400

    url = build_curia_url(
        text=text, directive=directive, article=article,
        court=court, language=language,
        date_from=date_from, date_to=date_to
    )

    try:
        session = requests.Session()
        resp = session.get(url, headers=HEADERS, timeout=20)
        resp.raise_for_status()
        resp.encoding = "utf-8"
    except requests.Timeout:
        return jsonify({"error": "Curia antwortet nicht (Timeout nach 20s)"}), 504
    except requests.RequestException as e:
        return jsonify({"error": f"Netzwerkfehler: {str(e)}"}), 502

    results   = parse_curia_results(resp.text)
    total     = parse_total_count(resp.text)
    curia_url = url

    return jsonify({
        "total":     total,
        "count":     len(results),
        "curia_url": curia_url,
        "results":   results,
    })


@app.route("/directives", methods=["GET"])
def list_directives():
    """Gibt alle unterstützten Richtlinien zurück."""
    return jsonify({"directives": list(DIRECTIVE_CITATIONS.keys())})


@app.route("/health", methods=["GET"])
def health():
    return jsonify({"status": "ok", "version": "1.0"})


@app.route("/", methods=["GET"])
def index():
    return jsonify({
        "name":      "Curia Proxy",
        "endpoints": {
            "GET /search":     "Curia durchsuchen",
            "GET /directives": "Unterstützte Richtlinien",
            "GET /health":     "Serverstatus",
        },
        "example": (
            "/search?text=Milit%C3%A4rausr%C3%BCstung"
            "&directive=2009/81&court=C&language=de"
        ),
    })


if __name__ == "__main__":
    print("Curia Proxy läuft auf http://localhost:5000")
    app.run(host="0.0.0.0", port=5000, debug=False)
