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


# ── Form vollständig auslesen ─────────────────────────────────────────────────

def scrape_form(session):
    """
    Loads the Curia search form and extracts ALL form fields
    exactly as a browser would submit them — including submit buttons,
    checkboxes (only if checked), selects, radio buttons.
    Returns (action_url, field_dict, raw_html).
    """
    resp = session.get(CURIA_SEARCH, timeout=20)
    resp.encoding = "utf-8"
    html = resp.text
    soup = BeautifulSoup(html, "lxml")

    form = soup.find("form", id="mainForm") or soup.find("form")
    if not form:
        return CURIA_SEARCH, {}, html

    action = form.get("action", CURIA_SEARCH)
    if action.startswith("/"):
        action = "https://juris.curia.europa.eu" + action

    fields = {}

    for el in form.find_all(["input", "select", "textarea", "button"]):
        name  = el.get("name", "")
        itype = el.get("type", "text").lower()
        if not name:
            continue

        if itype == "submit":
            # Don't include submit buttons yet — we'll add the right one later
            continue
        elif itype == "checkbox":
            # Only include if checked
            if el.has_attr("checked"):
                fields[name] = el.get("value", "on")
        elif itype == "radio":
            if el.has_attr("checked"):
                fields[name] = el.get("value", "")
        elif el.name == "select":
            # Use selected option, or first option as default
            selected = el.find("option", selected=True)
            if not selected:
                selected = el.find("option")
            fields[name] = selected.get("value", "") if selected else ""
        elif itype == "hidden":
            fields[name] = el.get("value", "")
        else:
            # text, textarea, etc. — use default value
            fields[name] = el.get("value", "")

    # Find submit buttons (to pick the right one later)
    submit_buttons = []
    for btn in form.find_all("input", type="submit"):
        submit_buttons.append((btn.get("name",""), btn.get("value","")))
    for btn in form.find_all("button", type="submit"):
        submit_buttons.append((btn.get("name",""), btn.get("value","") or btn.get_text(strip=True)))
    # Also look for image inputs (sometimes used as submit)
    for btn in form.find_all("input", type="image"):
        submit_buttons.append((btn.get("name",""), btn.get("value","")))

    return action, fields, html, submit_buttons


# ── Formular-Inspektion (für Debug) ──────────────────────────────────────────

@app.route("/inspect")
def inspect_form():
    """Shows the raw form structure so we can see all fields and buttons."""
    session = requests.Session()
    session.headers.update(HEADERS)
    try:
        action, fields, html, buttons = scrape_form(session)
    except Exception as e:
        return jsonify({"error": str(e)})

    soup = BeautifulSoup(html, "lxml")
    # Find all inputs with their types and default values
    form = soup.find("form", id="mainForm") or soup.find("form")
    all_inputs = []
    if form:
        for el in form.find_all(["input", "select", "button"]):
            all_inputs.append({
                "tag":   el.name,
                "type":  el.get("type",""),
                "name":  el.get("name",""),
                "value": el.get("value","")[:50] if el.get("value") else "",
                "id":    el.get("id",""),
            })

    return jsonify({
        "action":         action,
        "submit_buttons": buttons,
        "field_count":    len(fields),
        "all_inputs":     all_inputs,
        "field_names":    list(fields.keys()),
    })


# ── Curia POST-Suche ──────────────────────────────────────────────────────────

def search_curia_post(text=None, directive=None, court="C",
                      language="de", date_from=None, date_to=None):
    session = requests.Session()
    session.headers.update(HEADERS)

    try:
        action, fields, html, buttons = scrape_form(session)
    except Exception as e:
        return [], CURIA_SEARCH, f"Form scrape error: {e}", {}

    if not fields.get("javax.faces.ViewState"):
        return [], CURIA_SEARCH, "No ViewState", {"fields": list(fields.keys())}

    # ── Populate search fields using correct Curia field names ───────────────
    post_data = dict(fields)  # start with all default form values

    # Free text
    if text:
        post_data["mainForm:critereRechText"] = text

    # Court checkboxes — set only the ones we want
    for f in ["mainForm:jur_all","mainForm:jur_cour","mainForm:jur_tpi","mainForm:jur_tfp"]:
        post_data.pop(f, None)  # remove defaults first

    if court == "C,T,F":
        post_data["mainForm:jur_all"]  = "on"
        post_data["mainForm:jur_cour"] = "C"
        post_data["mainForm:jur_tpi"]  = "T"
        post_data["mainForm:jur_tfp"]  = "F"
    elif court == "C":
        post_data["mainForm:jur_cour"] = "C"
    elif court == "T":
        post_data["mainForm:jur_tpi"]  = "T"

    # Directive citation
    if directive and directive in DIRECTIVE_CITATIONS:
        post_data["mainForm:cit_motifs"] = DIRECTIVE_CITATIONS[directive]

    # Date range
    if date_from or date_to:
        post_data["mainForm:dateFromToRB"] = "on"
        if date_from:
            post_data["mainForm:dateFromInput"] = date_from.replace("-", ".")
        if date_to:
            post_data["mainForm:dateToInput"] = date_to.replace("-", ".")

    # ── Add the submit button ────────────────────────────────────────────────
    # Try each button until we find one that looks like "search"
    search_btn = None
    for name, value in buttons:
        v = value.lower()
        if any(w in v for w in ["suchen","search","recherche","find","submit"]):
            search_btn = (name, value)
            break
    # Fallback: use the last submit button (usually the search button)
    if not search_btn and buttons:
        search_btn = buttons[-1]
    if search_btn and search_btn[0]:
        post_data[search_btn[0]] = search_btn[1]

    session.headers.update({
        "Content-Type": "application/x-www-form-urlencoded",
        "Referer": CURIA_SEARCH,
        "Origin": "https://juris.curia.europa.eu",
    })

    try:
        resp = session.post(action, data=post_data, timeout=30, allow_redirects=True)
        resp.raise_for_status()
        resp.encoding = "utf-8"
        result_html = resp.text
    except Exception as e:
        return [], action, f"POST error: {e}", {}

    results = parse_curia_html(result_html)

    soup2 = BeautifulSoup(result_html[:2000], "lxml")
    page_title = soup2.title.string.strip() if soup2.title else ""

    debug = {
        "page_title":      page_title,
        "html_len":        len(result_html),
        "viewstate_len":   len(fields.get("javax.faces.ViewState", "")),
        "submit_buttons":  buttons,
        "used_button":     search_btn,
        "action":          action,
        "text_field_set":  post_data.get("mainForm:critereRechText", "NOT SET"),
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
            if len(cells) >= 3 and re.match(r"\d{2}\.\d{2}\.\d{4}",
                                             cells[0].get_text(strip=True)):
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


# ── Debug ─────────────────────────────────────────────────────────────────────

@app.route("/debug")
def debug():
    text      = request.args.get("text",      "").strip() or None
    directive = request.args.get("directive", "").strip() or None
    court     = request.args.get("court",     "C")
    results, url, error, dbg = search_curia_post(text=text, directive=directive, court=court)
    return jsonify({"error": error, "parsed_count": len(results),
                    "debug": dbg, "first_results": results[:5]})


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
    text = text.replace("```json", "").replace("```", "").strip()
    m = re.search(r"[\[\{][\s\S]*[\]\}]", text)
    return json.loads(m.group(0) if m else text)


def fetch_eurlex_text(az):
    try:
        url = f"https://eur-lex.europa.eu/search.html?query={quote(az)}&DB_TYPE_OF_ACT=judgment&lang=de"
        r = requests.get(url, headers=HEADERS, timeout=15)
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
        return (content or doc_soup).get_text(separator="\n", strip=True)[:8000]
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

    steps.append({"step": 1, "label": "Suchstrategie entwickeln", "status": "running"})
    try:
        strategy_raw = claude(
            system="""Du bist Experte für EU-Rechtsprechungsrecherche.
Antworte NUR mit JSON, keine Backticks:
{
  "zusammenfassung": "Worum geht es (1 Satz)",
  "suchen": [
    {"text": "Suchbegriff (max 4 Wörter)", "directive": "z.B. 2009/81 oder null",
     "language": "de/en/fr", "begruendung": "warum"}
  ]
}
6-8 Varianten: kurze präzise juristische Begriffe, Artikel-Nummern als eigene Suche.""",
            user=f"Rechtsfrage: {question}", max_tokens=1200,
        )
        strategy = parse_json_response(strategy_raw)
        steps[-1]["status"] = "done"
        steps[-1]["data"] = strategy
    except Exception as e:
        return jsonify({"error": f"Strategiefehler: {e}", "steps": steps}), 500

    steps.append({"step": 2, "label": "Curia Datenbank durchsuchen", "status": "running"})
    all_results, curia_urls, debug_log = [], [], []

    for s in strategy.get("suchen", [])[:7]:
        directive = s.get("directive")
        if str(directive).lower() in ("null", "none", ""):
            directive = None
        results, used_url, err, dbg = search_curia_post(
            text=s.get("text"), directive=directive, court=court,
            language=s.get("language", "de"),
            date_from=date_from or None, date_to=date_to or None,
        )
        all_results.extend(results)
        curia_urls.append({"label": s.get("text",""), "url": used_url})
        debug_log.append({"query": s.get("text"), "found": len(results),
                          "error": err, "page_title": dbg.get("page_title","")})
        time.sleep(0.5)

    seen, unique = set(), []
    for r in all_results:
        key = r["aktenzeichen"].replace(" ", "").lower()
        if key and key not in seen:
            seen.add(key); unique.append(r)

    steps[-1]["status"] = "done"
    steps[-1]["data"] = {"gefunden": len(all_results), "nach_dedup": len(unique),
                          "debug": debug_log}

    if not unique:
        return jsonify({"question": question,
                        "zusammenfassung": strategy.get("zusammenfassung",""),
                        "steps": steps, "results": [],
                        "curia_urls": curia_urls, "debug": debug_log})

    steps.append({"step": 3, "label": "Volltexte & Randnummern", "status": "running"})
    enriched = []
    for r in unique[:15]:
        az = r["aktenzeichen"]
        fulltext = fetch_eurlex_text(az) if az else None
        base = {**r, "eurlex_url": f"https://eur-lex.europa.eu/search.html?query={quote(az)}&DB_TYPE_OF_ACT=judgment"}
        if fulltext:
            try:
                rn_data = parse_json_response(claude(
                    system="""Analysiere ein EU-Urteil. Antworte NUR mit JSON:
{"relevant":true/false,"relevanz":"hoch/mittel/niedrig","parteien":"...",
"gericht":"EuGH oder EuG","kammer":"...","sachverhalt":"2-3 Sätze",
"randnummern":[{"rn":"Rn. XX","inhalt":"Was steht dort (1 Satz)"}],
"kernaussage":"Wichtigste Aussage (2-3 Sätze)"}""",
                    user=f"Frage: {question}\n\nAktenzeichen: {az}\n\nText:\n{fulltext}",
                    max_tokens=1200,
                ))
                enriched.append({**base, **rn_data, "volltext_verfuegbar": True})
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

    steps.append({"step": 4, "label": "Relevanzbewertung", "status": "running"})
    relevant = [r for r in enriched if r.get("relevant", True)]
    relevant.sort(key=lambda x: {"hoch":0,"mittel":1,"niedrig":2}.get(x.get("relevanz","mittel"),1))
    steps[-1]["status"] = "done"
    steps[-1]["data"] = {"gesamt": len(relevant),
                          "hoch":   sum(1 for r in relevant if r.get("relevanz")=="hoch"),
                          "mittel": sum(1 for r in relevant if r.get("relevanz")=="mittel")}

    return jsonify({"question": question,
                    "zusammenfassung": strategy.get("zusammenfassung",""),
                    "steps": steps, "results": relevant, "curia_urls": curia_urls[:4]})


@app.route("/search")
def search():
    text      = request.args.get("text","").strip() or None
    directive = request.args.get("directive","").strip() or None
    court     = request.args.get("court","C")
    language  = request.args.get("language","de")
    if not text and not directive:
        return jsonify({"error": "Mindestens text oder directive angeben"}), 400
    results, url, err, dbg = search_curia_post(
        text=text, directive=directive, court=court, language=language)
    return jsonify({"count": len(results), "error": err, "debug": dbg, "results": results})


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
