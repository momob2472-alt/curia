import os, re, json, time
from flask import Flask, request, jsonify
from flask_cors import CORS
import requests
from bs4 import BeautifulSoup
from urllib.parse import urlencode, quote

app = Flask(__name__)
CORS(app)

CURIA_BASE    = "https://juris.curia.europa.eu/juris/recherche.jsf"
CURIA_HOME    = "https://juris.curia.europa.eu/juris/recherche.jsf"
ANTHROPIC_KEY = os.environ.get("ANTHROPIC_API_KEY", "")

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
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
    "Accept-Language": "de-DE,de;q=0.9,en;q=0.8",
    "Accept-Encoding": "gzip, deflate, br",
    "Connection": "keep-alive",
    "Upgrade-Insecure-Requests": "1",
}


# ── Session-basierter Curia-Zugriff ───────────────────────────────────────────

def make_session():
    """Creates a requests session that mimics a browser."""
    s = requests.Session()
    s.headers.update(HEADERS)
    # Warm up: visit homepage to get session cookies
    try:
        s.get("https://curia.europa.eu/", timeout=10)
        s.get(CURIA_HOME, timeout=10)
    except Exception:
        pass
    return s


def build_curia_url(text=None, directive=None, court="C,T,F",
                    language="de", date_from=None, date_to=None):
    dates_param = ""
    if date_from or date_to:
        dates_param = (
            f"{(date_from or '').replace('-','.')}|"
            f"{(date_to   or '').replace('-','.')}"
        )
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


def parse_curia_html(html):
    """
    Parses Curia search result HTML.
    Tries multiple selector strategies since Curia's layout can vary.
    """
    soup = BeautifulSoup(html, "lxml")
    results = []

    # Strategy 1: standard detail table rows
    rows = soup.select("table.detail tr.normal, table.detail tr.odd")

    # Strategy 2: any tr with class containing normal/odd
    if not rows:
        rows = soup.select("tr[class*='normal'], tr[class*='odd']")

    # Strategy 3: all rows inside tables with more than 2 columns
    if not rows:
        for table in soup.find_all("table"):
            for row in table.find_all("tr"):
                cells = row.find_all("td")
                if len(cells) >= 3:
                    rows.append(row)

    for row in rows:
        cells = row.find_all("td")
        if len(cells) < 3:
            continue
        try:
            datum = cells[0].get_text(strip=True)
            az, doc_url = "", ""
            link = cells[1].find("a") if len(cells) > 1 else None
            if link:
                az = link.get_text(strip=True)
                href = link.get("href", "")
                doc_url = ("https://curia.europa.eu" + href
                           if href.startswith("/") else href)
            else:
                az = cells[1].get_text(strip=True) if len(cells) > 1 else ""
            name = cells[2].get_text(strip=True) if len(cells) > 2 else ""
            typ  = cells[3].get_text(strip=True) if len(cells) > 3 else ""
            if not az and not name:
                continue
            results.append({
                "aktenzeichen": az.strip(),
                "datum":        datum.strip(),
                "parteien":     name.strip(),
                "typ":          typ.strip(),
                "curia_url":    (f"https://curia.europa.eu/juris/liste.jsf?num={quote(az.strip())}"
                                 if az else ""),
                "doc_url":      doc_url,
            })
        except Exception:
            continue
    return results


def search_curia_with_session(text=None, directive=None,
                               court="C,T,F", language="de"):
    """Searches Curia using a proper browser session."""
    url = build_curia_url(text=text, directive=directive,
                          court=court, language=language)
    session = make_session()
    try:
        resp = session.get(url, timeout=25)
        resp.raise_for_status()
        resp.encoding = "utf-8"
        html = resp.text
    except Exception as e:
        return [], url, str(e), ""

    results = parse_curia_html(html)
    # Return a snippet of raw HTML for debugging (first 3000 chars)
    html_snippet = html[:3000]
    return results, url, None, html_snippet


# ── Debug Endpunkt ────────────────────────────────────────────────────────────

@app.route("/debug")
def debug():
    """
    Returns raw Curia HTML so we can inspect the actual structure.
    Call: /debug?text=Militärausrüstung&directive=2009/81
    """
    text      = request.args.get("text",      "").strip() or None
    directive = request.args.get("directive", "").strip() or None
    court     = request.args.get("court",     "C,T,F")
    language  = request.args.get("language",  "de")

    results, url, error, html_snippet = search_curia_with_session(
        text=text, directive=directive, court=court, language=language
    )

    soup = BeautifulSoup(html_snippet, "lxml") if html_snippet else None
    all_tables     = len(soup.find_all("table"))         if soup else 0
    all_tr_normal  = len(soup.select("tr.normal"))       if soup else 0
    all_tr_odd     = len(soup.select("tr.odd"))          if soup else 0
    detail_tables  = len(soup.select("table.detail"))    if soup else 0
    page_title     = soup.title.string if soup and soup.title else ""

    return jsonify({
        "curia_url":     url,
        "error":         error,
        "parsed_count":  len(results),
        "page_title":    page_title,
        "html_tables":   all_tables,
        "detail_tables": detail_tables,
        "tr_normal":     all_tr_normal,
        "tr_odd":        all_tr_odd,
        "html_snippet":  html_snippet[:2000] if html_snippet else "",
        "first_results": results[:3],
    })


# ── Claude API ────────────────────────────────────────────────────────────────

def claude(system, user, max_tokens=2000):
    if not ANTHROPIC_KEY:
        raise ValueError("ANTHROPIC_API_KEY nicht gesetzt")
    r = requests.post(
        "https://api.anthropic.com/v1/messages",
        headers={
            "x-api-key": ANTHROPIC_KEY,
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


# ── EUR-Lex Volltext ──────────────────────────────────────────────────────────

def fetch_eurlex_text(az):
    try:
        search_url = (
            f"https://eur-lex.europa.eu/search.html"
            f"?query={quote(az)}&DB_TYPE_OF_ACT=judgment&lang=de"
        )
        r = requests.get(search_url, headers=HEADERS, timeout=15)
        r.encoding = "utf-8"
        soup = BeautifulSoup(r.text, "lxml")
        link = soup.select_one("a.title")
        if not link:
            return None
        href = link.get("href", "")
        if not href.startswith("http"):
            href = "https://eur-lex.europa.eu" + href
        doc = requests.get(href, headers=HEADERS, timeout=15)
        doc.encoding = "utf-8"
        doc_soup = BeautifulSoup(doc.text, "lxml")
        content = doc_soup.select_one("#document1, .eli-main-title, .textdocument")
        if content:
            return content.get_text(separator="\n", strip=True)[:8000]
        return doc_soup.get_text(separator="\n", strip=True)[:6000]
    except Exception:
        return None


# ── Research Endpunkt ─────────────────────────────────────────────────────────

@app.route("/research")
def research():
    question  = request.args.get("q",         "").strip()
    court     = request.args.get("court",     "C,T,F")
    date_from = request.args.get("date_from", "")
    date_to   = request.args.get("date_to",   "")

    if not question:
        return jsonify({"error": "Parameter q fehlt"}), 400
    if not ANTHROPIC_KEY:
        return jsonify({"error": "ANTHROPIC_API_KEY nicht konfiguriert"}), 500

    steps = []

    # Schritt 1: Suchstrategie
    steps.append({"step": 1, "label": "Suchstrategie entwickeln", "status": "running"})
    try:
        strategy_raw = claude(
            system="""Du bist Experte für EU-Rechtsprechungsrecherche.
Generiere optimale Suchbegriffe für curia.europa.eu.
Antworte NUR mit JSON, keine Backticks:
{
  "zusammenfassung": "Worum geht es (1 Satz)",
  "suchen": [
    {
      "text": "konkreter Suchbegriff (max 4 Wörter, exakt wie im EU-Recht)",
      "directive": "z.B. 2009/81 oder null",
      "language": "de/en/fr",
      "begruendung": "warum"
    }
  ]
}
Erstelle 6-8 Varianten mit kurzen, präzisen juristischen Fachbegriffen.
Artikel-Nummern wie 'Art. 346' als eigene Suchanfrage aufnehmen.""",
            user=f"Rechtsfrage: {question}",
            max_tokens=1200,
        )
        strategy = parse_json_response(strategy_raw)
        steps[-1]["status"] = "done"
        steps[-1]["data"] = strategy
    except Exception as e:
        return jsonify({"error": f"Strategiefehler: {e}", "steps": steps}), 500

    # Schritt 2: Curia durchsuchen
    steps.append({"step": 2, "label": "Curia Datenbank durchsuchen", "status": "running"})
    all_results = []
    curia_urls  = []
    debug_info  = []  # sammle HTML-Infos für Fehlerdiagnose

    for s in strategy.get("suchen", [])[:7]:
        directive = s.get("directive")
        if str(directive).lower() in ("null", "none", ""):
            directive = None

        results, used_url, err, html_snippet = search_curia_with_session(
            text=s.get("text"),
            directive=directive,
            court=court,
            language=s.get("language", "de"),
        )
        all_results.extend(results)
        curia_urls.append({"label": s.get("text", ""), "url": used_url})
        debug_info.append({
            "query":   s.get("text"),
            "found":   len(results),
            "error":   err,
            "html_len": len(html_snippet),
        })
        time.sleep(0.4)

    # Deduplizieren
    seen, unique = set(), []
    for r in all_results:
        key = r["aktenzeichen"].replace(" ", "").lower()
        if key and key not in seen:
            seen.add(key)
            unique.append(r)

    steps[-1]["status"] = "done"
    steps[-1]["data"]   = {
        "gefunden":  len(all_results),
        "nach_dedup": len(unique),
        "debug":      debug_info,
    }

    if not unique:
        return jsonify({
            "question":        question,
            "zusammenfassung": strategy.get("zusammenfassung", ""),
            "steps":           steps,
            "results":         [],
            "curia_urls":      curia_urls,
            "debug":           debug_info,
        })

    # Schritt 3: Volltexte + Randnummern
    steps.append({"step": 3, "label": "Volltexte & Randnummern", "status": "running"})
    enriched = []

    for r in unique[:15]:
        az       = r["aktenzeichen"]
        fulltext = fetch_eurlex_text(az) if az else None

        if fulltext:
            try:
                rn_raw = claude(
                    system="""Analysiere ein EU-Urteil auf Relevanz für eine Rechtsfrage.
Antworte NUR mit JSON, keine Backticks:
{
  "relevant": true/false,
  "relevanz": "hoch/mittel/niedrig",
  "parteien": "Parteien (Kläger / Beklagter)",
  "gericht": "EuGH oder EuG",
  "kammer": "z.B. Dritte Kammer",
  "sachverhalt": "2-3 Sätze zum Fall",
  "randnummern": [
    {"rn": "Rn. XX", "inhalt": "Was steht dort konkret (1 Satz)"}
  ],
  "kernaussage": "Wichtigste Aussage zur Rechtsfrage (2-3 Sätze)"
}""",
                    user=f"Frage: {question}\n\nAktenzeichen: {az}\n\nText:\n{fulltext}",
                    max_tokens=1200,
                )
                rn_data = parse_json_response(rn_raw)
                enriched.append({
                    **r, **rn_data,
                    "volltext_verfuegbar": True,
                    "eurlex_url": f"https://eur-lex.europa.eu/search.html?query={quote(az)}&DB_TYPE_OF_ACT=judgment",
                })
            except Exception:
                enriched.append({**r, "relevant": True, "relevanz": "mittel",
                                  "randnummern": [], "kernaussage": "", "sachverhalt": "",
                                  "volltext_verfuegbar": False,
                                  "eurlex_url": f"https://eur-lex.europa.eu/search.html?query={quote(az)}&DB_TYPE_OF_ACT=judgment"})
        else:
            enriched.append({**r, "relevant": True, "relevanz": "mittel",
                             "randnummern": [], "kernaussage": "", "sachverhalt": "",
                             "volltext_verfuegbar": False,
                             "eurlex_url": f"https://eur-lex.europa.eu/search.html?query={quote(az)}&DB_TYPE_OF_ACT=judgment"})
        time.sleep(0.2)

    steps[-1]["status"] = "done"

    # Schritt 4: Sortieren
    steps.append({"step": 4, "label": "Relevanzbewertung", "status": "running"})
    relevant = [r for r in enriched if r.get("relevant", True)]
    order    = {"hoch": 0, "mittel": 1, "niedrig": 2}
    relevant.sort(key=lambda x: order.get(x.get("relevanz", "mittel"), 1))
    steps[-1]["status"] = "done"
    steps[-1]["data"]   = {
        "gesamt":  len(relevant),
        "hoch":    sum(1 for r in relevant if r.get("relevanz") == "hoch"),
        "mittel":  sum(1 for r in relevant if r.get("relevanz") == "mittel"),
        "niedrig": sum(1 for r in relevant if r.get("relevanz") == "niedrig"),
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
    results, url, err, _ = search_curia_with_session(
        text=text, directive=directive, court=court, language=language
    )
    return jsonify({"count": len(results), "curia_url": url,
                    "error": err, "results": results})


@app.route("/health")
def health():
    return jsonify({"status": "ok", "anthropic_api": bool(ANTHROPIC_KEY)})

@app.route("/")
def index():
    return jsonify({"name": "Curia Proxy v2", "status": "ok",
                    "anthropic_api": bool(ANTHROPIC_KEY)})


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False)
