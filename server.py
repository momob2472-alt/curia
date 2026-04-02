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
    "Accept-Encoding": "gzip, deflate, br",
}


# ── EUR-Lex Suche ─────────────────────────────────────────────────────────────

def search_eurlex(text, language="de", page=1):
    """
    Searches EUR-Lex for judgments. Returns structured results.
    EUR-Lex is a regular web server — no JSF, no AJAX required.
    Results are linked back to Curia via case number.
    """
    url = (
        f"https://eur-lex.europa.eu/search.html"
        f"?query={quote(text)}"
        f"&DB_TYPE_OF_ACT=judgment"
        f"&lang={language}"
        f"&qid=1"
        f"&page={page}"
    )
    try:
        resp = requests.get(url, headers=HEADERS, timeout=20)
        resp.raise_for_status()
        resp.encoding = "utf-8"
    except Exception as e:
        return [], url, str(e)

    soup = BeautifulSoup(resp.text, "lxml")
    results = []

    # EUR-Lex result items — try multiple selectors
    items = (
        soup.select("div.EurlexContent") or
        soup.select("li.EurlexContent") or
        soup.select(".result-item") or
        []
    )

    # Fallback: all title links on the page
    if not items:
        for link in soup.select("a.title")[:25]:
            title = link.get_text(strip=True)
            href  = link.get("href","")
            az    = _extract_case_number(title)
            if az or "judgment" in title.lower() or re.search(r"C-\d|T-\d", title):
                results.append(_make_result(az, title, "", href))
        return results, url, None

    for item in items:
        # Title and link
        title_el = item.select_one("a.title, .docTitle a, h2 a, h3 a")
        if not title_el:
            continue
        title = title_el.get_text(strip=True)
        href  = title_el.get("href","")
        if not href.startswith("http"):
            href = "https://eur-lex.europa.eu" + href

        # Date
        date_el = item.select_one(".docDate, .date, time, .Published")
        datum = date_el.get_text(strip=True) if date_el else ""
        datum = re.search(r"\d{2}/\d{2}/\d{4}|\d{4}-\d{2}-\d{2}|\d{2}\.\d{2}\.\d{4}", datum or "")
        datum = datum.group(0) if datum else ""

        # Case number — extract from title or nearby text
        az = _extract_case_number(title)
        if not az:
            az = _extract_case_number(item.get_text())

        if az or title:
            results.append(_make_result(az, title, datum, href))

    return results, url, None


def _extract_case_number(text):
    """Extracts EuGH/EuG case number from text."""
    m = re.search(r"\b([CT]‑\d+/\d+|[CT]-\d+/\d+|C‐\d+/\d+)", text or "")
    if m:
        return m.group(1).replace("‑","‐").replace("‑","-").replace("‐","-")
    return ""


def _make_result(az, title, datum, eurlex_url):
    return {
        "aktenzeichen": az,
        "parteien":     title,
        "datum":        datum,
        "eurlex_url":   eurlex_url,
        "curia_url":    f"https://curia.europa.eu/juris/liste.jsf?num={quote(az)}" if az else "",
    }


# ── EUR-Lex Volltext für Randnummern ─────────────────────────────────────────

def fetch_fulltext(az_or_url):
    """Fetches judgment full text from EUR-Lex."""
    try:
        if az_or_url.startswith("http"):
            url = az_or_url
        else:
            search = (f"https://eur-lex.europa.eu/search.html"
                      f"?query={quote(az_or_url)}&DB_TYPE_OF_ACT=judgment&lang=de")
            r = requests.get(search, headers=HEADERS, timeout=15)
            r.encoding = "utf-8"
            soup = BeautifulSoup(r.text, "lxml")
            link = soup.select_one("a.title")
            if not link:
                return None
            href = link.get("href","")
            url = href if href.startswith("http") else "https://eur-lex.europa.eu"+href

        doc = requests.get(url, headers=HEADERS, timeout=20)
        doc.encoding = "utf-8"
        ds = BeautifulSoup(doc.text, "lxml")
        content = ds.select_one("#document1, .eli-main-title, .textdocument, .doc-ti")
        return (content or ds).get_text(separator="\n", strip=True)[:9000]
    except Exception:
        return None


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


# ── Debug EUR-Lex ─────────────────────────────────────────────────────────────

@app.route("/debug")
def debug():
    text = request.args.get("text","").strip()
    lang = request.args.get("lang","de")
    results, url, err = search_eurlex(text, language=lang)
    return jsonify({
        "search_url":    url,
        "error":         err,
        "parsed_count":  len(results),
        "first_results": results[:5],
    })


# ── Research ──────────────────────────────────────────────────────────────────

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
    {"text":"Suchbegriff (max 5 Wörter, exakter juristischer Begriff)",
     "language":"de/en/fr","begruendung":"warum"}
  ]
}
6-8 Varianten:
- Deutsche juristische Fachbegriffe
- Englische Entsprechungen (wichtig: EUR-Lex hat englische Volltexte)
- Französische Entsprechungen
- Artikelnummern als eigene Suche (z.B. "Article 346 TFEU")
- Richtliniennummern als Suchbegriff (z.B. "directive 2009/81")
Kurze, präzise Begriffe wie sie in Urteilstexten stehen.""",
            user=f"Rechtsfrage: {question}", max_tokens=1200,
        ))
        steps[-1]["status"] = "done"
        steps[-1]["data"] = strat
    except Exception as e:
        return jsonify({"error": f"Strategiefehler: {e}", "steps": steps}), 500

    # Schritt 2: EUR-Lex durchsuchen
    steps.append({"step":2,"label":"EUR-Lex Rechtsprechungsdatenbank durchsuchen","status":"running"})
    all_results, search_urls, dbg_log = [], [], []

    for s in strat.get("suchen",[])[:7]:
        text_q = s.get("text","")
        lang   = s.get("language","de")
        results, url, err = search_eurlex(text_q, language=lang)
        all_results.extend(results)
        search_urls.append({"label": text_q, "url": url})
        dbg_log.append({"query": text_q, "lang": lang,
                        "found": len(results), "error": err})
        time.sleep(0.4)

    # Deduplizieren nach Aktenzeichen
    seen, unique = set(), []
    for r in all_results:
        key = r["aktenzeichen"].replace(" ","").lower()
        if key and key not in seen:
            seen.add(key); unique.append(r)
        elif not key:
            # Keep results without case number too (for text-only entries)
            title_key = r["parteien"][:40].lower()
            if title_key not in seen:
                seen.add(title_key); unique.append(r)

    steps[-1]["status"] = "done"
    steps[-1]["data"] = {"gefunden": len(all_results),
                          "nach_dedup": len(unique), "debug": dbg_log}

    if not unique:
        return jsonify({"question": question,
                        "zusammenfassung": strat.get("zusammenfassung",""),
                        "steps": steps, "results": [],
                        "search_urls": search_urls, "debug": dbg_log})

    # Schritt 3: Volltexte + Randnummern
    steps.append({"step":3,"label":"Volltexte analysieren & Randnummern extrahieren","status":"running"})
    enriched = []
    for r in unique[:15]:
        az  = r.get("aktenzeichen","")
        url = r.get("eurlex_url","")
        ft  = fetch_fulltext(url or az) if (url or az) else None

        base = dict(r)
        if ft:
            try:
                rn = parse_json(claude(
                    system="""Analysiere ein EU-Gerichtsurteil auf Relevanz für eine Rechtsfrage.
Antworte NUR mit JSON, keine Backticks:
{
  "relevant": true/false,
  "relevanz": "hoch/mittel/niedrig",
  "parteien": "Kläger / Beklagter (aus dem Text)",
  "gericht": "EuGH oder EuG",
  "kammer": "z.B. Dritte Kammer",
  "datum": "TT.MM.JJJJ",
  "sachverhalt": "2-3 Sätze: Worum geht es",
  "randnummern": [
    {"rn": "Rn. XX", "inhalt": "Was sagt das Gericht dort zur Frage (1 Satz)"}
  ],
  "kernaussage": "Wichtigste Aussage zur Rechtsfrage (2-3 Sätze)"
}
Nur Rn. nennen die im Volltext nachweislich vorkommen.
Wenn nicht relevant: relevant=false.""",
                    user=f"Rechtsfrage: {question}\n\nAktenzeichen: {az}\n\nUrteilstext:\n{ft}",
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

    # Schritt 4: Sortieren + Bewerten
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
        "search_urls":     search_urls[:4],
    })


@app.route("/search")
def search():
    text = request.args.get("text","").strip()
    lang = request.args.get("language","de")
    if not text:
        return jsonify({"error": "text parameter fehlt"}), 400
    results, url, err = search_eurlex(text, language=lang)
    return jsonify({"count": len(results), "search_url": url,
                    "error": err, "results": results})


@app.route("/health")
def health():
    return jsonify({"status":"ok","anthropic_api": bool(ANTHROPIC_KEY)})

@app.route("/")
def index():
    return jsonify({"name":"Curia Proxy v2 (EUR-Lex)","status":"ok",
                    "anthropic_api": bool(ANTHROPIC_KEY)})


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False)


@app.route("/debug-html")
def debug_html():
    """Returns raw EUR-Lex HTML snippet so we can inspect the structure."""
    text = request.args.get("text", "Militärausrüstung").strip()
    lang = request.args.get("lang", "de")
    url = (f"https://eur-lex.europa.eu/search.html"
           f"?query={quote(text)}&DB_TYPE_OF_ACT=judgment&lang={lang}&qid=1")
    try:
        resp = requests.get(url, headers=HEADERS, timeout=20)
        resp.encoding = "utf-8"
        html = resp.text
    except Exception as e:
        return jsonify({"error": str(e)})

    soup = BeautifulSoup(html, "lxml")

    # Collect all classes used in the page to identify result containers
    all_classes = set()
    for el in soup.find_all(True):
        for c in el.get("class", []):
            all_classes.add(c)

    # Find all links that look like judgment links
    judgment_links = []
    for a in soup.find_all("a", href=True)[:60]:
        href = a.get("href","")
        text_content = a.get_text(strip=True)[:80]
        if any(x in href for x in ["CELEX","celex","TXT","judgment","arrêt","Urteil"]):
            judgment_links.append({"href": href[:100], "text": text_content})

    # First 3000 chars of body for structure inspection
    body = soup.find("body")
    body_snippet = body.get_text(separator="|", strip=True)[:1000] if body else ""

    # All divs with class containing "result" or "search"
    result_divs = []
    for el in soup.find_all(class_=re.compile(r"result|search|content|item", re.I))[:20]:
        result_divs.append({
            "tag": el.name,
            "classes": el.get("class",[]),
            "text": el.get_text(strip=True)[:60]
        })

    return jsonify({
        "status_code":     resp.status_code,
        "html_len":        len(html),
        "all_classes":     sorted(list(all_classes))[:50],
        "judgment_links":  judgment_links[:10],
        "result_divs":     result_divs[:15],
        "body_snippet":    body_snippet,
    })
