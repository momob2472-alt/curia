import os, re, json, time
from flask import Flask, request, jsonify
from flask_cors import CORS
import requests
from bs4 import BeautifulSoup
from urllib.parse import quote

app = Flask(__name__)
CORS(app)

ANTHROPIC_KEY = os.environ.get("ANTHROPIC_API_KEY", "")

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "de-DE,de;q=0.9,en;q=0.8",
}

DDG_HEADERS = {
    "User-Agent": "Mozilla/5.0 (X11; Linux x86_64; rv:109.0) Gecko/20100101 Firefox/115.0",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "de,en;q=0.7",
    "Referer": "https://duckduckgo.com/",
}

AZ_RE = re.compile(r"\b([CT][-‑]\d{1,4}/\d{2,4}(?:\s*[A-Z]{1,4})?)\b")


# ── DuckDuckGo HTML-Suche ─────────────────────────────────────────────────────

def search_ddg(query, max_results=10):
    """
    Uses DuckDuckGo's HTML endpoint (no JS, no bot detection).
    Searches curia.europa.eu and eur-lex.europa.eu for case numbers.
    """
    url = f"https://html.duckduckgo.com/html/?q={quote(query)}&kl=de-de"
    try:
        resp = requests.get(url, headers=DDG_HEADERS, timeout=20)
        resp.raise_for_status()
        resp.encoding = "utf-8"
    except Exception as e:
        return [], str(e)

    soup = BeautifulSoup(resp.text, "lxml")
    results = []

    for result in soup.select(".result, .web-result")[:max_results]:
        title_el   = result.select_one(".result__title, .result__a, a.result__a")
        snippet_el = result.select_one(".result__snippet, .result__body")
        url_el     = result.select_one("a.result__url, .result__url, a[href]")

        title   = title_el.get_text(strip=True)   if title_el   else ""
        snippet = snippet_el.get_text(strip=True)  if snippet_el else ""
        href    = ""
        if title_el and title_el.name == "a":
            href = title_el.get("href","")
        elif url_el:
            href = url_el.get("href","")

        # Extract case numbers from title + snippet
        combined = title + " " + snippet
        case_numbers = list(dict.fromkeys(AZ_RE.findall(combined)))

        if case_numbers or any(x in combined for x in ["EuGH","EuG","Court","judgment","Urteil"]):
            for az in (case_numbers or [""]):
                results.append({
                    "aktenzeichen": az.strip(),
                    "parteien":     title,
                    "snippet":      snippet[:200],
                    "source_url":   href,
                    "curia_url":    f"https://curia.europa.eu/juris/liste.jsf?num={quote(az.strip())}" if az else "",
                    "eurlex_url":   f"https://eur-lex.europa.eu/search.html?query={quote(az.strip())}&DB_TYPE_OF_ACT=judgment" if az else "",
                })

    return results, None


def search_multiple(queries):
    """Runs multiple DDG queries, deduplicates by case number."""
    all_results = []
    for q in queries:
        results, err = search_ddg(q)
        all_results.extend(results)
        time.sleep(0.5)  # Be polite to DDG

    seen, unique = set(), []
    for r in all_results:
        az = r["aktenzeichen"].replace(" ","").lower()
        key = az if az else r["parteien"][:50].lower()
        if key and key not in seen:
            seen.add(key)
            unique.append(r)
    return unique


# ── EUR-Lex Volltext (direkter Dokumentenzugriff — funktioniert) ──────────────

def fetch_fulltext(az):
    """
    Fetches full judgment text via EUR-Lex CELLAR direct link.
    Uses known URL patterns for EuGH/EuG judgments.
    """
    if not az:
        return None

    # Try EUR-Lex search to find the document URL
    try:
        # Direct CELEX number construction: e.g. C-615/10 → 62010CJ0615
        celex = az_to_celex(az)
        if celex:
            url = f"https://eur-lex.europa.eu/legal-content/DE/TXT/?uri=CELEX:{celex}"
            doc = requests.get(url, headers=HEADERS, timeout=20)
            if doc.status_code == 200:
                doc.encoding = "utf-8"
                ds = BeautifulSoup(doc.text, "lxml")
                content = ds.select_one("#document1, .eli-main-title, .textdocument")
                if content:
                    return content.get_text(separator="\n", strip=True)[:9000]
    except Exception:
        pass

    # Fallback: DuckDuckGo to find the EUR-Lex URL
    try:
        results, _ = search_ddg(f'"{az}" eur-lex.europa.eu volltext', max_results=3)
        for r in results:
            url = r.get("source_url","")
            if "eur-lex.europa.eu" in url and "TXT" in url:
                doc = requests.get(url, headers=HEADERS, timeout=20)
                if doc.status_code == 200:
                    doc.encoding = "utf-8"
                    ds = BeautifulSoup(doc.text, "lxml")
                    content = ds.select_one("#document1, .eli-main-title, .textdocument")
                    if content:
                        return content.get_text(separator="\n", strip=True)[:9000]
    except Exception:
        pass
    return None


def az_to_celex(az):
    """
    Converts EuGH case number to CELEX identifier.
    C-615/10 → 62010CJ0615
    T-26/01  → 62001TJ0026
    """
    m = re.match(r"([CT])-(\d+)/(\d{2,4})", az.strip())
    if not m:
        return None
    court, num, year = m.group(1), m.group(2), m.group(3)
    year_full = ("20" if len(year)==2 and int(year)<50 else
                 "19" if len(year)==2 else "") + year
    court_code = "CJ" if court == "C" else "TJ"
    return f"6{year_full}{court_code}{int(num):04d}"


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


def parse_json(text):
    text = text.replace("```json","").replace("```","").strip()
    m = re.search(r"[\[\{][\s\S]*[\]\}]", text)
    return json.loads(m.group(0) if m else text)


# ── Endpoints ─────────────────────────────────────────────────────────────────

@app.route("/debug")
def debug():
    text = request.args.get("text","Militärausrüstung EuGH").strip()
    q    = f'site:curia.europa.eu OR site:eur-lex.europa.eu {text} Urteil'
    results, err = search_ddg(q)
    return jsonify({"query": q, "error": err,
                    "parsed_count": len(results),
                    "first_results": results[:5]})


@app.route("/celex-test")
def celex_test():
    """Tests CELEX URL construction and EUR-Lex direct access."""
    cases = ["C-615/10","C-337/05","C-284/05","T-26/01","C-474/12"]
    results = []
    for az in cases:
        celex = az_to_celex(az)
        url   = f"https://eur-lex.europa.eu/legal-content/DE/TXT/?uri=CELEX:{celex}" if celex else ""
        results.append({"az": az, "celex": celex, "url": url})
    return jsonify({"results": results})


@app.route("/research")
def research():
    question  = request.args.get("q",         "").strip()
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
        strat = parse_json(claude(
            system="""Du bist Experte für EU-Rechtsprechungsrecherche.
Antworte NUR mit JSON, keine Backticks:
{
  "zusammenfassung": "Worum geht es (1 Satz)",
  "suchen": [
    {"query": "vollständige Suchanfrage für DuckDuckGo",
     "begruendung": "warum"}
  ]
}
Erstelle 5-7 DuckDuckGo-Suchanfragen die beginnen mit:
'site:curia.europa.eu OR site:eur-lex.europa.eu'
Gefolgt von juristischen Fachbegriffen DE und EN.
Beispiel: 'site:curia.europa.eu OR site:eur-lex.europa.eu "military equipment" directive 2009/81'
Nutze Anführungszeichen für exakte Phrasen.""",
            user=f"Rechtsfrage: {question}", max_tokens=1000,
        ))
        steps[-1]["status"] = "done"
        steps[-1]["data"] = strat
    except Exception as e:
        return jsonify({"error": f"Strategiefehler: {e}", "steps": steps}), 500

    # Schritt 2: DDG-Suchen
    steps.append({"step":2,"label":"Rechtsprechung suchen (DuckDuckGo → Curia/EUR-Lex)","status":"running"})
    queries   = [s["query"] for s in strat.get("suchen",[])[:6]]
    unique    = search_multiple(queries)
    search_info = [{"query": q} for q in queries]

    steps[-1]["status"] = "done"
    steps[-1]["data"] = {"gefunden": len(unique)}

    if not unique:
        return jsonify({"question": question,
                        "zusammenfassung": strat.get("zusammenfassung",""),
                        "steps": steps, "results": [],
                        "search_queries": queries})

    # Schritt 3: Volltexte + Randnummern via direktem EUR-Lex CELEX-Zugriff
    steps.append({"step":3,"label":"Volltexte analysieren & Randnummern extrahieren","status":"running"})
    enriched = []
    for r in unique[:15]:
        az = r.get("aktenzeichen","")
        ft = fetch_fulltext(az) if az else None
        base = dict(r)
        if ft:
            try:
                rn = parse_json(claude(
                    system="""Analysiere ein EU-Gerichtsurteil. NUR JSON, keine Backticks:
{
  "relevant": true/false,
  "relevanz": "hoch/mittel/niedrig",
  "parteien": "Kläger / Beklagter",
  "gericht": "EuGH oder EuG",
  "kammer": "z.B. Dritte Kammer",
  "datum": "TT.MM.JJJJ",
  "sachverhalt": "2-3 Sätze",
  "randnummern": [{"rn":"Rn. XX","inhalt":"Was steht dort (1 Satz)"}],
  "kernaussage": "Wichtigste Aussage (2-3 Sätze)"
}
Nur Rn. nennen die nachweislich im Text stehen.""",
                    user=f"Frage: {question}\n\nAktenzeichen: {az}\n\nText:\n{ft}",
                    max_tokens=1200,
                ))
                enriched.append({**base, **rn, "volltext_verfuegbar": True})
            except Exception:
                enriched.append({**base, "relevant":True, "relevanz":"mittel",
                                  "randnummern":[], "kernaussage":"", "sachverhalt":"",
                                  "volltext_verfuegbar": False})
        else:
            enriched.append({**base, "relevant":True, "relevanz":"mittel",
                             "randnummern":[], "kernaussage":"", "sachverhalt":"",
                             "volltext_verfuegbar": False})
        time.sleep(0.2)
    steps[-1]["status"] = "done"

    # Schritt 4: Sortieren
    steps.append({"step":4,"label":"Relevanzbewertung","status":"running"})
    relevant = [r for r in enriched if r.get("relevant", True)]
    relevant.sort(key=lambda x: {"hoch":0,"mittel":1,"niedrig":2}
                  .get(x.get("relevanz","mittel"),1))
    steps[-1]["status"] = "done"
    steps[-1]["data"] = {
        "gesamt": len(relevant),
        "hoch":   sum(1 for r in relevant if r.get("relevanz")=="hoch"),
        "mittel": sum(1 for r in relevant if r.get("relevanz")=="mittel"),
    }

    return jsonify({
        "question":        question,
        "zusammenfassung": strat.get("zusammenfassung",""),
        "steps":           steps,
        "results":         relevant,
        "search_queries":  queries,
    })


@app.route("/search")
def search():
    text = request.args.get("text","").strip()
    if not text:
        return jsonify({"error": "text fehlt"}), 400
    q = f'site:curia.europa.eu OR site:eur-lex.europa.eu {text} Urteil EuGH'
    results, err = search_ddg(q)
    return jsonify({"count": len(results), "query": q,
                    "error": err, "results": results})


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
