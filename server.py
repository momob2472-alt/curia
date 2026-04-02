import os
from flask import Flask, request, jsonify
from flask_cors import CORS
import requests
from bs4 import BeautifulSoup
from urllib.parse import urlencode, quote
import re

app = Flask(__name__)
CORS(app)

CURIA_BASE = "https://juris.curia.europa.eu/juris/recherche.jsf"

DIRECTIVE_CITATIONS = {
    "2009/81":  "L%2CC%2CCJ%2CR%2C2009E%2C%2C2009%2C81%2C%2C%2C%2C%2C%2C%2Ctrue%2Cfalse%2Cfalse",
    "2014/24":  "L%2CC%2CCJ%2CR%2C2014E%2C%2C2014%2C24%2C%2C%2C%2C%2C%2C%2Ctrue%2Cfalse%2Cfalse",
    "2014/25":  "L%2CC%2CCJ%2CR%2C2014E%2C%2C2014%2C25%2C%2C%2C%2C%2C%2C%2Ctrue%2Cfalse%2Cfalse",
    "2014/23":  "L%2CC%2CCJ%2CR%2C2014E%2C%2C2014%2C23%2C%2C%2C%2C%2C%2C%2Ctrue%2Cfalse%2Cfalse",
    "2004/18":  "L%2CC%2CCJ%2CR%2C2004E%2C%2C2004%2C18%2C%2C%2C%2C%2C%2C%2Ctrue%2Cfalse%2Cfalse",
}

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "de,en;q=0.9",
}

def build_curia_url(text=None, directive=None, court="C,T,F", language="de", date_from=None, date_to=None):
    dates_param = ""
    if date_from or date_to:
        d_from = date_from.replace("-", ".") if date_from else ""
        d_to   = date_to.replace("-", ".")   if date_to   else ""
        dates_param = f"{d_from}|{d_to}"

    params = {
        "nat": "or", "mat": "or", "pcs": "Oor",
        "jur": court, "language": language, "etat": "clot",
        "lgrec": language, "td": ";;&;PUB1,PUB2,PUB7;NPUB1;;;ORDALL",
        "oqp": "", "avg": "", "for": "", "jge": "", "pro": "", "lg": "",
        "dates": dates_param,
    }
    if text:
        params["text"] = text
    if directive and directive in DIRECTIVE_CITATIONS:
        params["cit"] = DIRECTIVE_CITATIONS[directive]

    return CURIA_BASE + "?" + urlencode(params, quote_via=quote)

def parse_curia_results(html):
    soup = BeautifulSoup(html, "html.parser")
    results = []
    rows = soup.select("table.detail tr.normal, table.detail tr.odd")
    if not rows:
        rows = soup.select("tr[class*='normal'], tr[class*='odd']")
    for row in rows:
        cells = row.find_all("td")
        if len(cells) < 3:
            continue
        try:
            datum = cells[0].get_text(strip=True) if cells else ""
            az_cell = cells[1] if len(cells) > 1 else None
            az, doc_url = "", ""
            if az_cell:
                link = az_cell.find("a")
                if link:
                    az = link.get_text(strip=True)
                    href = link.get("href", "")
                    doc_url = ("https://curia.europa.eu" + href) if href.startswith("/") else href
                else:
                    az = az_cell.get_text(strip=True)
            name = cells[2].get_text(strip=True) if len(cells) > 2 else ""
            typ  = cells[3].get_text(strip=True) if len(cells) > 3 else ""
            if not az and not name:
                continue
            results.append({
                "aktenzeichen": az,
                "datum": datum,
                "parteien": name,
                "typ": typ,
                "curia_url": f"https://curia.europa.eu/juris/liste.jsf?num={quote(az)}" if az else doc_url,
                "doc_url": doc_url,
            })
        except Exception:
            continue
    return results

def parse_total_count(html):
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup.find_all(string=re.compile(r"\d+\s*(?:Ergebnis|result|résultat)", re.I)):
        m = re.search(r"(\d+)", tag)
        if m:
            return int(m.group(1))
    return -1

@app.route("/search")
def search():
    text      = request.args.get("text",      "").strip() or None
    directive = request.args.get("directive", "").strip() or None
    court     = request.args.get("court",     "C,T,F")
    language  = request.args.get("language",  "de")
    date_from = request.args.get("date_from", "").strip() or None
    date_to   = request.args.get("date_to",   "").strip() or None

    if not text and not directive:
        return jsonify({"error": "Mindestens text oder directive angeben"}), 400

    url = build_curia_url(text=text, directive=directive, court=court,
                          language=language, date_from=date_from, date_to=date_to)
    try:
        resp = requests.get(url, headers=HEADERS, timeout=20)
        resp.raise_for_status()
        resp.encoding = "utf-8"
    except requests.Timeout:
        return jsonify({"error": "Timeout"}), 504
    except requests.RequestException as e:
        return jsonify({"error": str(e)}), 502

    return jsonify({
        "total":     parse_total_count(resp.text),
        "count":     len(parse_curia_results(resp.text)),
        "curia_url": url,
        "results":   parse_curia_results(resp.text),
    })

@app.route("/directives")
def list_directives():
    return jsonify({"directives": list(DIRECTIVE_CITATIONS.keys())})

@app.route("/health")
def health():
    return jsonify({"status": "ok"})

@app.route("/")
def index():
    return jsonify({"name": "Curia Proxy", "status": "ok"})

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False)
