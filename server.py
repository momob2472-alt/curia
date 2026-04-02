import os, re, json, time
from flask import Flask, request, jsonify
from flask_cors import CORS
import requests
from bs4 import BeautifulSoup
from urllib.parse import quote

app = Flask(__name__)
CORS(app)

CURIA_SEARCH  = "https://juris.curia.europa.eu/juris/recherche.jsf"
ANTHROPIC_KEY = os.environ.get("ANTHROPIC_API_KEY", "")

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "de-DE,de;q=0.9,en;q=0.8",
    "Accept-Encoding": "gzip, deflate, br",
    "Connection": "keep-alive",
}


# ── Curia POST-Suche ──────────────────────────────────────────────────────────

def search_curia(text=None, court="C", language="de",
                 date_from=None, date_to=None):
    """
    Submits the Curia search form via POST.
    Field names and structure confirmed from /inspect endpoint.
    """
    session = requests.Session()
    session.headers.update(HEADERS)

    # Step 1: Load form to get session cookie + ViewState
    try:
        resp = session.get(CURIA_SEARCH, timeout=20)
        resp.encoding = "utf-8"
        soup = BeautifulSoup(resp.text, "lxml")
    except Exception as e:
        return [], CURIA_SEARCH, f"Form load error: {e}", {}

    form = soup.find("form", id="mainForm") or soup.find("form")
    if not form:
        return [], CURIA_SEARCH, "Form not found", {}

    action = form.get("action", CURIA_SEARCH)
    if action.startswith("/"):
        action = "https://juris.curia.europa.eu" + action

    viewstate = ""
    vs_input = form.find("input", {"name": "javax.faces.ViewState"})
    if vs_input:
        viewstate = vs_input.get("value", "")

    # Step 2: Build POST data with exact field names from /inspect
    post_data = {
        # Required hidden field
        "mainForm":                  "mainForm",
        "javax.faces.ViewState":     viewstate,

        # Sort preferences (defaults)
        "mainForm:triPrefAff":       "def",
        "mainForm:triPrefTri":       "def",

        # Case status: closed cases only
        "mainForm:critereAffaire":   "clot",

        # ECLI prefix (default value in form)
        "mainForm:critereEcli":      "ECLI:EU:",

        # Free text search ← our main search field
        "mainForm:critereRechText":  text or "",

        # Submit button ← confirmed name from /inspect
        "mainForm:j_id108":          "Suchen",
    }

    # Court selection — checkboxes: include field = checked, exclude = unchecked
    # Confirmed from /inspect: value="" for all court checkboxes
    if court in ("C", "C,T,F"):
        post_data["mainForm:jur_cour"] = ""   # EuGH checked
    if court in ("T", "C,T,F"):
        post_data["mainForm:jur_tpi"]  = ""   # EuG checked
    if court == "C,T,F":
        post_data["mainForm:jur_all"]  = ""   # All checked
        post_data["mainForm:jur_tfp"]  = ""   # EuGöD checked

    # Date range
    if date_from or date_to:
        post_data["mainForm:dateFromToRB"] = "fromTo"
        if date_from:
            post_data["mainForm:dateFromInput"] = date_from.replace("-", ".")
        if date_to:
            post_data["mainForm:dateToInput"] = date_to.replace("-", ".")

    session.headers.update({
        "Content-Type": "application/x-www-form-urlencoded",
        "Referer": CURIA_SEARCH,
        "Origin": "https://juris.curia.europa.eu",
    })

    try:
        resp2 = session.post(action, data=post_data, timeout=30, allow_redirects=True)
        resp2.raise_for_status()
        resp2.encoding = "utf-8"
        result_html = resp2.text
    except Exception as e:
        return [], action, f"POST error: {e}", {}

    results = parse_curia_html(result_html)

    s2 = BeautifulSoup(result_html[:1500], "lxml")
    page_title = s2.title.string.strip() if s2.title else ""

    debug = {
        "page_title":    page_title,
        "html_len":      len(result_html),
        "viewstate":     viewstate,
        "action":        action,
        "text":          text,
        "parsed_count":  len(results),
    }
    return results, action, None, debug


def parse_curia_html(html):
    soup = BeautifulSoup(html, "lxml")
    results = []

    rows = (
        soup.select("table.detail tr.normal, table.detail tr.odd") or
        soup.select("tr.normal, tr.odd") or []
    )
    if not rows:
        for row in soup.find_all("tr"):
            cells = row.find_all("td")
            if (len(cells) >= 3 and
                    re.match(r"\d{2}\.\d{2}\.\d{4}",
                             cells[0].get_text(strip=True))):
                rows.append(row)

    for row in rows:
        cells = row.find_all("td")
        if len(cells) < 3:
            continue
        try:
            datum = cells[0].get_text(strip=True)
            if not re.match(r"\d{2}\.\d{2}\.\d{4}", datum):
                continue
            az, doc_url = "", ""
            link = cells[1].find("a")
            if link:
                az = link.get_text(strip=True)
                href = link.get("href", "")
                doc_url = ("https://curia.europa.eu" + href
                           if href.startswith("/") else href)
            else:
                az = cells[1].get_text(strip=True)
            name = cells[2].get_text(strip=True) if len(cells) > 2 else ""
            typ  = cells[3].get_text(strip=True) if len(cells) > 3 else ""
            results.append({
                "aktenzeichen": az.strip(),
                "datum":        datum.strip(),
                "parteien":     name.strip(),
                "typ":          typ.strip(),
                "curia_url":    f"https://curia.europa.eu/juris/liste.jsf?num={quote(az.strip())}" if az else "",
                "doc_url":      doc_url,
            })
        except Exception:
            continue
    return results


# ── Endpoints ─────────────────────────────────────────────────────────────────

@app.route("/debug")
def debug():
    text  = request.args.get("text",  "").strip() or None
    court = request.args.get("court", "C")
    results, url, error, dbg = search_curia(text=text, court=court)
    return jsonify({
        "error":         error,
        "parsed_count":  len(results),
        "debug":         dbg,
        "first_results": results[:5],
    })


@app.route("/inspect")
def inspect():
    session = requests.Session()
    session.headers.update(HEADERS)
    resp = session.get(CURIA_SEARCH, timeout=20)
    resp.encoding = "utf-8"
    soup = BeautifulSoup(resp.text, "lxml")
    form = soup.find("form", id="mainForm") or soup.find("form")
    inputs = []
    if form:
        for el in form.find_all(["input","select","button"]):
            inputs.append({
                "tag": el.name, "type": el.get("type",""),
                "name": el.get("name",""), "value": el.get("value","")[:40],
                "id": el.get("id",""),
            })
    return jsonify({"inputs": inputs,
                    "action": form.get("action","") if form else ""})


# ── Claude API ────────────────────────────────────────────────────────────────

def claude(system, user, max_tokens=2000):
    if not ANTHROPIC_KEY:
        raise ValueError("ANTHROPIC_API_KEY nicht gesetzt")
    r = requests.post(
        "https://api.anthropic.com/v1/messages",
        headers={"x-api-key": ANTHROPIC_KEY,
                 "anthropic-version": "2023-06-01",
                 "content-type": "application/json"},
        json={"model": "claude-opus-4-5", "max_tokens": max_tokens,
              "system": system,
              "messages": [{"role": "user", "content": user}]},
        timeout=60,
    )
    r.raise_for_status()
    return r.json()["content"][0]["text"]


def parse_json_response(text):
    text = text.replace("```json","").replace("```","").strip()
    m = re.search(r"[\[\{][\s\S]*[\]\}]", text)
    return json.loads(m.group(0) if m else text)


def fetch_eurlex_text(az):
    try:
        url = (f"https://eur-lex.europa.eu/search.html"
               f"?query={quote(az)}&DB_TYPE_OF_ACT=judgment&lang=de")
        r = requests.get(url, headers=HEADERS, timeout=15)
        r.encoding = "utf-8"
        soup = BeautifulSoup(r.text, "lxml")
        link = soup.select_one("a.title")
        if not link:
            return None
        href = link.get("href","")
        if not href.startswith("http"):
            href = "https://eur-lex.europa.eu" + href
        doc = requests.get(href, headers=HEADERS, timeout=15)
        doc.encoding = "utf-8"
        ds = BeautifulSoup(doc.text, "lxml")
        c = ds.select_one("#document1, .eli-main-title, .textdocument")
        return (c or ds).get_text(separator="\n", strip=True)[:8000]
    except Exception:
        return None


# ── Research ──────────────────────────────────────────────────────────────────

@app.route("/research")
def research():
    question  = request.args.get("q",         "").strip()
    court     = request.args.get("court",     "C")
    date_from = request.args.get("date_from", "")
    date_to   = request.args.get("date_to",   "")

    if not question:
        return jsonify({"error": "Parameter q fehlt"}), 400
    if not ANTHROPIC_KEY:
        return jsonify({"error": "ANTHROPIC_API_KEY nicht konfiguriert"}), 500

    steps = []

    # Schritt 1: Suchstrategie
    steps.append({"step":1,"label":"Suchstrategie entwickeln","status":"running"})
    try:
        strat = parse_json_response(claude(
            system="""EU-Rechtsprechungsrecherche-Experte.
Antworte NUR mit JSON, keine Backticks:
{
  "zusammenfassung": "Worum geht es (1 Satz)",
  "suchen": [
    {"text":"Suchbegriff (max 4 Wörter, exakter juristischer Begriff)",
     "language":"de/en/fr","begruendung":"warum"}
  ]
}
6-8 Varianten. Nur Freitext-Suchbegriffe (keine Richtlinien-Filter).
Kurze präzise Begriffe wie sie im Urteilstext stehen.
Artikel-Nummern als eigene Suche: z.B. "Art. 346 AEUV".""",
            user=f"Rechtsfrage: {question}", max_tokens=1200,
        ))
        steps[-1]["status"] = "done"
        steps[-1]["data"] = strat
    except Exception as e:
        return jsonify({"error": f"Strategiefehler: {e}", "steps": steps}), 500

    # Schritt 2: Curia POST-Suchen
    steps.append({"step":2,"label":"Curia Datenbank durchsuchen","status":"running"})
    all_results, curia_urls, dbg_log = [], [], []

    for s in strat.get("suchen",[])[:7]:
        results, url, err, dbg = search_curia(
            text=s.get("text"), court=court,
            language=s.get("language","de"),
            date_from=date_from or None,
            date_to=date_to or None,
        )
        all_results.extend(results)
        curia_urls.append({"label": s.get("text",""), "url": url})
        dbg_log.append({"query": s.get("text"), "found": len(results),
                        "error": err, "page_title": dbg.get("page_title","")})
        time.sleep(0.5)

    seen, unique = set(), []
    for r in all_results:
        key = r["aktenzeichen"].replace(" ","").lower()
        if key and key not in seen:
            seen.add(key); unique.append(r)

    steps[-1]["status"] = "done"
    steps[-1]["data"] = {"gefunden": len(all_results),
                          "nach_dedup": len(unique), "debug": dbg_log}

    if not unique:
        return jsonify({"question": question,
                        "zusammenfassung": strat.get("zusammenfassung",""),
                        "steps": steps, "results": [],
                        "curia_urls": curia_urls, "debug": dbg_log})

    # Schritt 3: Volltexte + Rn.
    steps.append({"step":3,"label":"Volltexte & Randnummern","status":"running"})
    enriched = []
    for r in unique[:15]:
        az = r["aktenzeichen"]
        ft = fetch_eurlex_text(az) if az else None
        base = {**r, "eurlex_url":
                f"https://eur-lex.europa.eu/search.html?query={quote(az)}&DB_TYPE_OF_ACT=judgment"}
        if ft:
            try:
                rn = parse_json_response(claude(
                    system="""EU-Urteil analysieren. NUR JSON, keine Backticks:
{"relevant":true/false,"relevanz":"hoch/mittel/niedrig",
"parteien":"Kläger / Beklagter","gericht":"EuGH oder EuG","kammer":"...",
"sachverhalt":"2-3 Sätze",
"randnummern":[{"rn":"Rn. XX","inhalt":"Konkrete Aussage (1 Satz)"}],
"kernaussage":"Wichtigste Aussage zur Frage (2-3 Sätze)"}""",
                    user=f"Frage: {question}\n\nAktenzeichen: {az}\n\nText:\n{ft}",
                    max_tokens=1200,
                ))
                enriched.append({**base, **rn, "volltext_verfuegbar": True})
            except Exception:
                enriched.append({**base, "relevant": True, "relevanz": "mittel",
                                  "randnummern":[], "kernaussage":"", "sachverhalt":"",
                                  "volltext_verfuegbar": False})
        else:
            enriched.append({**base, "relevant": True, "relevanz": "mittel",
                             "randnummern":[], "kernaussage":"", "sachverhalt":"",
                             "volltext_verfuegbar": False})
        time.sleep(0.2)
    steps[-1]["status"] = "done"

    # Schritt 4: Sortieren
    steps.append({"step":4,"label":"Relevanzbewertung","status":"running"})
    relevant = [r for r in enriched if r.get("relevant", True)]
    relevant.sort(key=lambda x: {"hoch":0,"mittel":1,"niedrig":2}
                  .get(x.get("relevanz","mittel"), 1))
    steps[-1]["status"] = "done"
    steps[-1]["data"] = {
        "gesamt": len(relevant),
        "hoch":   sum(1 for r in relevant if r.get("relevanz")=="hoch"),
        "mittel": sum(1 for r in relevant if r.get("relevanz")=="mittel"),
    }

    return jsonify({"question": question,
                    "zusammenfassung": strat.get("zusammenfassung",""),
                    "steps": steps, "results": relevant,
                    "curia_urls": curia_urls[:4]})


@app.route("/search")
def search():
    text  = request.args.get("text","").strip() or None
    court = request.args.get("court","C")
    lang  = request.args.get("language","de")
    if not text:
        return jsonify({"error": "text parameter fehlt"}), 400
    results, url, err, dbg = search_curia(text=text, court=court, language=lang)
    return jsonify({"count": len(results), "error": err,
                    "debug": dbg, "results": results})


@app.route("/health")
def health():
    return jsonify({"status":"ok","anthropic_api": bool(ANTHROPIC_KEY)})

@app.route("/")
def index():
    return jsonify({"name":"Curia Proxy v2","status":"ok",
                    "anthropic_api": bool(ANTHROPIC_KEY)})


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False)
