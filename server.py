import os, re, json, time
from flask import Flask, request, jsonify
from flask_cors import CORS
import requests
from bs4 import BeautifulSoup
from urllib.parse import urlencode, quote

app = Flask(__name__)
CORS(app)

CURIA_BASE = "https://juris.curia.europa.eu/juris/recherche.jsf"
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")

DIRECTIVE_CITATIONS = {
    "2009/81":  "L%2CC%2CCJ%2CR%2C2009E%2C%2C2009%2C81%2C%2C%2C%2C%2C%2C%2Ctrue%2Cfalse%2Cfalse",
    "2014/24":  "L%2CC%2CCJ%2CR%2C2014E%2C%2C2014%2C24%2C%2C%2C%2C%2C%2C%2Ctrue%2Cfalse%2Cfalse",
    "2014/25":  "L%2CC%2CCJ%2CR%2C2014E%2C%2C2014%2C25%2C%2C%2C%2C%2C%2C%2Ctrue%2Cfalse%2Cfalse",
    "2014/23":  "L%2CC%2CCJ%2CR%2C2014E%2C%2C2014%2C23%2C%2C%2C%2C%2C%2C%2Ctrue%2Cfalse%2Cfalse",
    "2004/18":  "L%2CC%2CCJ%2CR%2C2004E%2C%2C2004%2C18%2C%2C%2C%2C%2C%2C%2Ctrue%2Cfalse%2Cfalse",
    "2004/17":  "L%2CC%2CCJ%2CR%2C2004E%2C%2C2004%2C17%2C%2C%2C%2C%2C%2C%2Ctrue%2Cfalse%2Cfalse",
    "2016/680": "L%2CC%2CCJ%2CR%2C2016E%2C%2C2016%2C680%2C%2C%2C%2C%2C%2C%2Ctrue%2Cfalse%2Cfalse",
}

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "de,en;q=0.9",
}


# ── Claude API ────────────────────────────────────────────────────────────────

def claude(system, user, max_tokens=2000):
    if not ANTHROPIC_API_KEY:
        raise ValueError("ANTHROPIC_API_KEY nicht gesetzt")
    r = requests.post(
        "https://api.anthropic.com/v1/messages",
        headers={
            "x-api-key": ANTHROPIC_API_KEY,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        },
        json={
            "model": "claude-opus-4-5",
            "max_tokens": max_tokens,
            "system": system,
            "messages": [{"role": "user", "content": user}],
        },
        timeout=60,
    )
    r.raise_for_status()
    return r.json()["content"][0]["text"]


def parse_json_response(text):
    text = text.replace("```json", "").replace("```", "").strip()
    m = re.search(r"[\[\{][\s\S]*[\]\}]", text)
    return json.loads(m.group(0) if m else text)


# ── Curia Suche ───────────────────────────────────────────────────────────────

def build_curia_url(text=None, directive=None, court="C,T,F", language="de",
                    date_from=None, date_to=None):
    dates_param = ""
    if date_from or date_to:
        dates_param = f"{(date_from or '').replace('-','.')}|{(date_to or '').replace('-','.')}"
    params = {
        "nat": "or", "mat": "or", "pcs": "Oor", "jur": court,
        "language": language, "etat": "clot", "lgrec": language,
        "td": ";;&;PUB1,PUB2,PUB7;NPUB1;;;ORDALL",
        "oqp": "", "avg": "", "for": "", "jge": "", "pro": "", "lg": "",
        "dates": dates_param,
    }
    if text:
        params["text"] = text
    if directive and directive in DIRECTIVE_CITATIONS:
        params["cit"] = DIRECTIVE_CITATIONS[directive]
    return CURIA_BASE + "?" + urlencode(params, quote_via=quote)


def search_curia(text=None, directive=None, court="C,T,F", language="de"):
    url = build_curia_url(text=text, directive=directive, court=court, language=language)
    try:
        resp = requests.get(url, headers=HEADERS, timeout=20)
        resp.raise_for_status()
        resp.encoding = "utf-8"
    except Exception:
        return [], url

    soup = BeautifulSoup(resp.text, "html.parser")
    results = []
    rows = soup.select("table.detail tr.normal, table.detail tr.odd")
    if not rows:
        rows = soup.select("tr[class*='normal'], tr[class*='odd']")

    for row in rows:
        cells = row.find_all("td")
        if len(cells) < 3:
            continue
        try:
            datum = cells[0].get_text(strip=True)
            az, doc_url = "", ""
            if len(cells) > 1:
                link = cells[1].find("a")
                if link:
                    az = link.get_text(strip=True)
                    href = link.get("href", "")
                    doc_url = ("https://curia.europa.eu" + href) if href.startswith("/") else href
                else:
                    az = cells[1].get_text(strip=True)
            name = cells[2].get_text(strip=True) if len(cells) > 2 else ""
            typ  = cells[3].get_text(strip=True) if len(cells) > 3 else ""
            if not az and not name:
                continue
            results.append({
                "aktenzeichen": az,
                "datum": datum,
                "parteien": name,
                "typ": typ,
                "curia_url": f"https://curia.europa.eu/juris/liste.jsf?num={quote(az)}" if az else "",
                "doc_url": doc_url,
            })
        except Exception:
            continue
    return results, url


def fetch_eurlex_text(az):
    """Fetches judgment text from EUR-Lex for Rn. extraction."""
    try:
        search_url = f"https://eur-lex.europa.eu/search.html?query={quote(az)}&DB_TYPE_OF_ACT=judgment&lang=de"
        r = requests.get(search_url, headers=HEADERS, timeout=15)
        r.encoding = "utf-8"
        soup = BeautifulSoup(r.text, "html.parser")
        link = soup.select_one("a.title")
        if not link:
            return None
        href = link.get("href", "")
        if not href.startswith("http"):
            href = "https://eur-lex.europa.eu" + href
        doc = requests.get(href, headers=HEADERS, timeout=15)
        doc.encoding = "utf-8"
        doc_soup = BeautifulSoup(doc.text, "html.parser")
        content = doc_soup.select_one("#document1, .eli-main-title, .textdocument")
        if content:
            return content.get_text(separator="\n", strip=True)[:8000]
        return doc_soup.get_text(separator="\n", strip=True)[:6000]
    except Exception:
        return None


# ── Research Endpunkt ─────────────────────────────────────────────────────────

@app.route("/research")
def research():
    question  = request.args.get("q",        "").strip()
    court     = request.args.get("court",    "C,T,F")
    date_from = request.args.get("date_from","")
    date_to   = request.args.get("date_to",  "")

    if not question:
        return jsonify({"error": "Parameter q fehlt"}), 400
    if not ANTHROPIC_API_KEY:
        return jsonify({"error": "ANTHROPIC_API_KEY nicht konfiguriert auf dem Server"}), 500

    steps = []

    # Schritt 1: Suchstrategie
    steps.append({"step": 1, "label": "Suchstrategie entwickeln", "status": "running"})
    try:
        strategy_raw = claude(
            system="""Du bist ein Experte für EU-Rechtsprechung und Rechtsrecherche.
Analysiere die Rechtsfrage und generiere optimale Suchbegriffe für curia.europa.eu.
Antworte NUR mit JSON, keine Backticks:
{
  "zusammenfassung": "Worum geht es (1 Satz)",
  "suchen": [
    {
      "text": "konkreter Suchbegriff",
      "directive": "Richtliniennummer z.B. 2009/81 oder null",
      "language": "de oder en oder fr",
      "begruendung": "warum diese Variante sinnvoll ist"
    }
  ]
}
Regeln:
- 6-8 Varianten insgesamt
- Deutsche Rechtsbegriffe exakt wie im EU-Recht verwendet
- Englische und französische Entsprechungen
- Artikelnummern als eigene Suche (z.B. "Art. 346 AEUV")
- Kombination aus Begriff + Richtlinie wo sinnvoll
- Nur kurze, präzise Begriffe — keine langen Phrasen""",
            user=f"Rechtsfrage: {question}",
            max_tokens=1200,
        )
        strategy = parse_json_response(strategy_raw)
        steps[-1]["status"] = "done"
        steps[-1]["data"] = strategy
    except Exception as e:
        return jsonify({"error": f"Fehler bei Strategieentwicklung: {e}", "steps": steps}), 500

    # Schritt 2: Curia durchsuchen
    steps.append({"step": 2, "label": "Curia Datenbank durchsuchen", "status": "running"})
    all_results = []
    curia_urls = []

    for s in strategy.get("suchen", [])[:7]:
        directive = s.get("directive")
        if directive in ("null", "none", "", None):
            directive = None
        results, used_url = search_curia(
            text=s.get("text"),
            directive=directive,
            court=court,
            language=s.get("language", "de"),
        )
        all_results.extend(results)
        curia_urls.append({"label": s.get("text", ""), "url": used_url})
        time.sleep(0.3)

    # Deduplizieren
    seen = set()
    unique = []
    for r in all_results:
        key = r["aktenzeichen"].replace(" ", "").lower()
        if key and key not in seen:
            seen.add(key)
            unique.append(r)

    steps[-1]["status"] = "done"
    steps[-1]["data"] = {"gefunden": len(all_results), "nach_dedup": len(unique)}

    if not unique:
        return jsonify({
            "question": question,
            "zusammenfassung": strategy.get("zusammenfassung", ""),
            "steps": steps,
            "results": [],
            "curia_urls": curia_urls,
        })

    # Schritt 3: Volltexte + Randnummern
    steps.append({"step": 3, "label": "Volltexte analysieren & Randnummern extrahieren", "status": "running"})
    enriched = []

    for r in unique[:15]:
        az = r["aktenzeichen"]
        fulltext = fetch_eurlex_text(az) if az else None

        if fulltext:
            try:
                rn_raw = claude(
                    system="""Du analysierst ein EU-Gerichtsurteil auf Relevanz für eine Rechtsfrage.
Antworte NUR mit JSON, keine Backticks:
{
  "relevant": true/false,
  "relevanz": "hoch/mittel/niedrig",
  "parteien": "Parteien wie im Text (Kläger / Beklagter)",
  "gericht": "EuGH oder EuG",
  "kammer": "z.B. Dritte Kammer",
  "sachverhalt": "2-3 Sätze: Worum geht es im Fall",
  "randnummern": [
    {
      "rn": "Rn. XX",
      "inhalt": "Was sagt das Gericht dort konkret zur Rechtsfrage (1 Satz, eigene Worte)"
    }
  ],
  "kernaussage": "Die wichtigste Aussage des Urteils zur Frage (2-3 Sätze)"
}
Wichtig:
- Nur Randnummern nennen, die im Volltext nachweislich vorkommen
- Bei nicht relevantem Urteil: relevant=false, leere Arrays
- Parteien und Kammer direkt aus dem Text entnehmen""",
                    user=f"Rechtsfrage: {question}\n\nAktenzeichen: {az}\n\nUrteilstext (Auszug):\n{fulltext}",
                    max_tokens=1200,
                )
                rn_data = parse_json_response(rn_raw)
                enriched.append({
                    **r,
                    **rn_data,
                    "volltext_verfuegbar": True,
                    "eurlex_url": f"https://eur-lex.europa.eu/search.html?query={quote(az)}&DB_TYPE_OF_ACT=judgment",
                })
            except Exception:
                enriched.append({
                    **r,
                    "relevant": True, "relevanz": "mittel",
                    "randnummern": [], "kernaussage": "",
                    "sachverhalt": "", "volltext_verfuegbar": False,
                    "eurlex_url": f"https://eur-lex.europa.eu/search.html?query={quote(az)}&DB_TYPE_OF_ACT=judgment",
                })
        else:
            enriched.append({
                **r,
                "relevant": True, "relevanz": "mittel",
                "randnummern": [], "kernaussage": "",
                "sachverhalt": "", "volltext_verfuegbar": False,
                "eurlex_url": f"https://eur-lex.europa.eu/search.html?query={quote(az)}&DB_TYPE_OF_ACT=judgment",
            })
        time.sleep(0.2)

    steps[-1]["status"] = "done"

    # Schritt 4: Sortieren
    steps.append({"step": 4, "label": "Relevanzbewertung abschließen", "status": "running"})
    relevant = [r for r in enriched if r.get("relevant", True)]
    order = {"hoch": 0, "mittel": 1, "niedrig": 2}
    relevant.sort(key=lambda x: order.get(x.get("relevanz", "mittel"), 1))
    steps[-1]["status"] = "done"
    steps[-1]["data"] = {
        "gesamt": len(relevant),
        "hoch":   len([r for r in relevant if r.get("relevanz") == "hoch"]),
        "mittel": len([r for r in relevant if r.get("relevanz") == "mittel"]),
        "niedrig":len([r for r in relevant if r.get("relevanz") == "niedrig"]),
    }

    return jsonify({
        "question":        question,
        "zusammenfassung": strategy.get("zusammenfassung", ""),
        "steps":           steps,
        "results":         relevant,
        "curia_urls":      curia_urls[:4],
    })


# ── Einfache Suche ────────────────────────────────────────────────────────────

@app.route("/search")
def search():
    text      = request.args.get("text",      "").strip() or None
    directive = request.args.get("directive", "").strip() or None
    court     = request.args.get("court",     "C,T,F")
    language  = request.args.get("language",  "de")
    if not text and not directive:
        return jsonify({"error": "Mindestens text oder directive angeben"}), 400
    results, url = search_curia(text=text, directive=directive, court=court, language=language)
    return jsonify({"count": len(results), "curia_url": url, "results": results})


@app.route("/health")
def health():
    return jsonify({"status": "ok", "anthropic_api": bool(ANTHROPIC_API_KEY)})

@app.route("/")
def index():
    return jsonify({"name": "Curia Proxy v2", "status": "ok",
                    "anthropic_api": bool(ANTHROPIC_API_KEY)})


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False)
