import os, re, json, time
from flask import Flask, request, jsonify
from flask_cors import CORS
import requests
from bs4 import BeautifulSoup
from urllib.parse import urlencode, quote, unquote

app = Flask(__name__)
CORS(app)

CURIA_SEARCH  = "https://juris.curia.europa.eu/juris/recherche.jsf"
ANTHROPIC_KEY = os.environ.get("ANTHROPIC_API_KEY", "")

DIRECTIVE_CITATIONS = {
    "2009/81":  "L,C,CJ,R,2009E,,2009,81,,,,,,,,true,false,false",
    "2014/24":  "L,C,CJ,R,2014E,,2014,24,,,,,,,,true,false,false",
    "2014/25":  "L,C,CJ,R,2014E,,2014,25,,,,,,,,true,false,false",
    "2014/23":  "L,C,CJ,R,2014E,,2014,23,,,,,,,,true,false,false",
    "2004/18":  "L,C,CJ,R,2004E,,2004,18,,,,,,,,true,false,false",
    "2004/17":  "L,C,CJ,R,2004E,,2004,17,,,,,,,,true,false,false",
    "2016/680": "L,C,CJ,R,2016E,,2016,680,,,,,,,,true,false,false",
}

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "de-DE,de;q=0.9,en;q=0.8",
    "Accept-Encoding": "gzip, deflate, br",
    "Connection": "keep-alive",
}


# ── JSF-Session aufbauen und Suche als POST ───────────────────────────────────

def get_form_state(session):
    """
    Loads the Curia search form and extracts:
    - JSF ViewState (required for POST)
    - All hidden form fields
    - Form action URL
    - cid parameter
    """
    resp = session.get(CURIA_SEARCH, timeout=20)
    resp.encoding = "utf-8"
    soup = BeautifulSoup(resp.text, "lxml")

    form = soup.find("form", id=re.compile(r"form", re.I)) or soup.find("form")
    if not form:
        return None, {}

    # Extract all hidden inputs
    hidden = {}
    for inp in form.find_all("input"):
        name = inp.get("name", "")
        val  = inp.get("value", "")
        if name:
            hidden[name] = val

    action = form.get("action", CURIA_SEARCH)
    if action.startswith("/"):
        action = "https://juris.curia.europa.eu" + action

    return action, hidden


def search_curia_post(text=None, directive=None, court="C,T,F",
                       language="de", date_from=None, date_to=None):
    """
    Searches Curia using a proper JSF POST request.
    Returns (results, search_url, error, debug_info).
    """
    session = requests.Session()
    session.headers.update(HEADERS)

    # Step 1: Load form and get ViewState
    try:
        action, hidden = get_form_state(session)
        if not hidden.get("javax.faces.ViewState"):
            return [], CURIA_SEARCH, "Kein ViewState gefunden", {"hidden_fields": list(hidden.keys())}
    except Exception as e:
        return [], CURIA_SEARCH, f"Formular-Ladefehler: {e}", {}

    # Step 2: Build POST body from form fields + search params
    dates_param = ""
    if date_from or date_to:
        dates_param = (
            f"{(date_from or '').replace('-','.')}|"
            f"{(date_to   or '').replace('-','.')}"
        )

    post_data = dict(hidden)  # start with all hidden fields (incl. ViewState)

    # Curia form field names (from inspecting the actual form)
    # These are the standard field names in the Curia JSF form
    post_data.update({
        "language":  language,
        "jur":       court,
        "nat":       "or",
        "mat":       "or",
        "pcs":       "Oor",
        "etat":      "clot",
        "lgrec":     language,
        "td":        ";;&;PUB1,PUB2,PUB7;NPUB1;;;ORDALL",
        "oqp":       "",
        "avg":       "",
        "for":       "",
        "jge":       "",
        "pro":       "",
        "lg":        "",
        "dates":     dates_param,
        "text":      text or "",
        "cit":       DIRECTIVE_CITATIONS.get(directive, "") if directive else "",
    })

    # Find the submit button name in hidden fields and add it
    for key in hidden:
        if "bouton" in key.lower() or "submit" in key.lower() or "btn" in key.lower() or "recherche" in key.lower():
            post_data[key] = hidden[key]

    session.headers.update({
        "Content-Type": "application/x-www-form-urlencoded",
        "Referer": CURIA_SEARCH,
        "Origin": "https://juris.curia.europa.eu",
    })

    # Step 3: POST
    try:
        resp = session.post(action or CURIA_SEARCH, data=post_data, timeout=25)
        resp.raise_for_status()
        resp.encoding = "utf-8"
        html = resp.text
    except Exception as e:
        return [], action or CURIA_SEARCH, f"POST-Fehler: {e}", {}

    results = parse_curia_html(html)
    page_title = ""
    try:
        s = BeautifulSoup(html[:500], "lxml")
        page_title = s.title.string.strip() if s.title else ""
    except Exception:
        pass

    debug = {
        "page_title":    page_title,
        "viewstate_len": len(hidden.get("javax.faces.ViewState", "")),
        "hidden_fields": list(hidden.keys()),
        "post_fields":   list(post_data.keys()),
        "html_len":      len(html),
        "action":        action,
    }
    return results, action or CURIA_SEARCH, None, debug


def parse_curia_html(html):
    """
    Parses Curia result HTML.
    After a successful POST, results appear in table rows.
    """
    soup = BeautifulSoup(html, "lxml")
    results = []

    # Try all selector strategies
    rows = (
        soup.select("table.detail tr.normal, table.detail tr.odd") or
        soup.select("tr[class*='normal'], tr[class*='odd']") or
        []
    )

    # Fallback: find rows in any table that look like case entries
    if not rows:
        for table in soup.find_all("table"):
            for row in table.find_all("tr"):
                cells = row.find_all("td")
                if len(cells) >= 3:
                    # Check if first cell looks like a date or case number
                    first = cells[0].get_text(strip=True)
                    if re.match(r"\d{2}\.\d{2}\.\d{4}", first) or re.match(r"[CT]-\d+/\d+", first):
                        rows.append(row)

    for row in rows:
        cells = row.find_all("td")
        if len(cells) < 2:
            continue
        try:
            # Try date in cell 0, AZ in cell 1
            datum = cells[0].get_text(strip=True)
            az, doc_url = "", ""
            link = cells[1].find("a")
            if link:
                az = link.get_text(strip=True)
                href = link.get("href", "")
                doc_url = ("https://curia.europa.eu" + href
                           if href.startswith("/") else href)
            else:
                az = cells[1].get_text(strip=True)

            # Skip navigation noise
            if not re.match(r"[CT]-\d+", az) and not re.match(r"\d{2}\.\d{2}\.\d{4}", datum):
                continue

            name = cells[2].get_text(strip=True) if len(cells) > 2 else ""
            typ  = cells[3].get_text(strip=True) if len(cells) > 3 else ""

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


# ── Debug ─────────────────────────────────────────────────────────────────────

@app.route("/debug")
def debug():
    text      = request.args.get("text",      "").strip() or None
    directive = request.args.get("directive", "").strip() or None

    results, url, error, dbg = search_curia_post(
        text=text, directive=directive
    )
    return jsonify({
        "error":         error,
        "parsed_count":  len(results),
        "debug":         dbg,
        "first_results": results[:5],
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


# ── Research ──────────────────────────────────────────────────────────────────

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
      "text": "konkreter Suchbegriff (max 4 Wörter)",
      "directive": "z.B. 2009/81 oder null",
      "language": "de/en/fr",
      "begruendung": "warum"
    }
  ]
}
Regeln: 6-8 Varianten, kurze präzise juristische Fachbegriffe, 
Artikel-Nummern als eigene Suche (z.B. "Art. 346 AEUV").""",
            user=f"Rechtsfrage: {question}",
            max_tokens=1200,
        )
        strategy = parse_json_response(strategy_raw)
        steps[-1]["status"] = "done"
        steps[-1]["data"] = strategy
    except Exception as e:
        return jsonify({"error": f"Strategiefehler: {e}", "steps": steps}), 500

    # Schritt 2: Curia durchsuchen (POST)
    steps.append({"step": 2, "label": "Curia Datenbank durchsuchen", "status": "running"})
    all_results = []
    curia_urls  = []
    debug_log   = []

    for s in strategy.get("suchen", [])[:7]:
        directive = s.get("directive")
        if str(directive).lower() in ("null", "none", ""):
            directive = None

        results, used_url, err, dbg = search_curia_post(
            text=s.get("text"),
            directive=directive,
            court=court,
            language=s.get("language", "de"),
            date_from=date_from or None,
            date_to=date_to or None,
        )
        all_results.extend(results)
        curia_urls.append({"label": s.get("text", ""), "url": used_url})
        debug_log.append({"query": s.get("text"), "found": len(results),
                          "error": err, "page_title": dbg.get("page_title","")})
        time.sleep(0.5)

    seen, unique = set(), []
    for r in all_results:
        key = r["aktenzeichen"].replace(" ", "").lower()
        if key and key not in seen:
            seen.add(key)
            unique.append(r)

    steps[-1]["status"] = "done"
    steps[-1]["data"]   = {"gefunden": len(all_results), "nach_dedup": len(unique),
                            "debug": debug_log}

    if not unique:
        return jsonify({
            "question": question,
            "zusammenfassung": strategy.get("zusammenfassung", ""),
            "steps": steps, "results": [],
            "curia_urls": curia_urls, "debug": debug_log,
        })

    # Schritt 3: Volltexte + Rn.
    steps.append({"step": 3, "label": "Volltexte & Randnummern", "status": "running"})
    enriched = []
    for r in unique[:15]:
        az       = r["aktenzeichen"]
        fulltext = fetch_eurlex_text(az) if az else None
        if fulltext:
            try:
                rn_raw = claude(
                    system="""Analysiere ein EU-Urteil.
Antworte NUR mit JSON, keine Backticks:
{
  "relevant": true/false,
  "relevanz": "hoch/mittel/niedrig",
  "parteien": "Kläger / Beklagter",
  "gericht": "EuGH oder EuG",
  "kammer": "z.B. Dritte Kammer",
  "sachverhalt": "2-3 Sätze",
  "randnummern": [{"rn": "Rn. XX", "inhalt": "Was steht dort (1 Satz)"}],
  "kernaussage": "Wichtigste Aussage (2-3 Sätze)"
}""",
                    user=f"Frage: {question}\n\nAktenzeichen: {az}\n\nText:\n{fulltext}",
                    max_tokens=1200,
                )
                rn_data = parse_json_response(rn_raw)
                enriched.append({**r, **rn_data, "volltext_verfuegbar": True,
                    "eurlex_url": f"https://eur-lex.europa.eu/search.html?query={quote(az)}&DB_TYPE_OF_ACT=judgment"})
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
    order = {"hoch": 0, "mittel": 1, "niedrig": 2}
    relevant.sort(key=lambda x: order.get(x.get("relevanz", "mittel"), 1))
    steps[-1]["status"] = "done"
    steps[-1]["data"] = {
        "gesamt":  len(relevant),
        "hoch":    sum(1 for r in relevant if r.get("relevanz") == "hoch"),
        "mittel":  sum(1 for r in relevant if r.get("relevanz") == "mittel"),
        "niedrig": sum(1 for r in relevant if r.get("relevanz") == "niedrig"),
    }

    return jsonify({
        "question": question,
        "zusammenfassung": strategy.get("zusammenfassung", ""),
        "steps": steps, "results": relevant, "curia_urls": curia_urls[:4],
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
    results, url, err, dbg = search_curia_post(
        text=text, directive=directive, court=court, language=language
    )
    return jsonify({"count": len(results), "error": err,
                    "debug": dbg, "results": results})


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
