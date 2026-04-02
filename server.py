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


# ── EUR-Lex Direktzugriff via CELEX ──────────────────────────────────────────

def az_to_celex(az):
    """C-615/10 → 62010CJ0615 | T-26/01 → 62001TJ0026"""
    m = re.match(r"([CT])[-‑](\d+)/(\d{2,4})", az.strip())
    if not m:
        return None
    court, num, year = m.group(1), m.group(2), m.group(3)
    if len(year) == 2:
        year = ("20" if int(year) < 50 else "19") + year
    court_code = "CJ" if court == "C" else "TJ"
    return f"6{year}{court_code}{int(num):04d}"


def fetch_fulltext(az):
    """
    Fetches real judgment text from EUR-Lex via CELEX identifier.
    This endpoint works without JavaScript or bot detection.
    """
    celex = az_to_celex(az)
    if not celex:
        return None, None
    url = f"https://eur-lex.europa.eu/legal-content/DE/TXT/?uri=CELEX:{celex}"
    try:
        resp = requests.get(url, headers=HEADERS, timeout=20)
        if resp.status_code != 200:
            return None, url
        resp.encoding = "utf-8"
        soup = BeautifulSoup(resp.text, "lxml")
        content = soup.select_one("#document1, .eli-main-title, .textdocument")
        if content:
            return content.get_text(separator="\n", strip=True)[:10000], url
        # Fallback: full page text
        text = soup.get_text(separator="\n", strip=True)
        if len(text) > 500:
            return text[:10000], url
        return None, url
    except Exception:
        return None, url


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
    text = text.replace("```json", "").replace("```", "").strip()
    m = re.search(r"[\[\{][\s\S]*[\]\}]", text)
    return json.loads(m.group(0) if m else text)


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

    # ── Schritt 1: Aktenzeichen identifizieren ────────────────────────────────
    steps.append({"step": 1, "label": "Relevante Urteile identifizieren", "status": "running"})
    try:
        raw = claude(
            system="""Du bist ein Experte für EU-Rechtsprechung des EuGH und EuG.
Deine Aufgabe: Identifiziere alle EuGH- und EuG-Urteile die zur Rechtsfrage relevant sind.

Antworte NUR mit JSON, keine Backticks:
{
  "zusammenfassung": "Worum geht es (1 Satz)",
  "urteile": [
    {
      "aktenzeichen": "C-XXX/XX oder T-XXX/XX",
      "titel": "Kurzbezeichnung / Parteien",
      "datum": "TT.MM.JJJJ",
      "relevanz": "hoch/mittel/niedrig",
      "relevanzgrund": "Warum ist dieses Urteil relevant (1 Satz)"
    }
  ]
}

Regeln:
- Nur real existierende Aktenzeichen nennen (keine erfundenen)
- Lieber weniger als falsche Aktenzeichen
- Auch indirekt relevante Urteile aufnehmen (z.B. Vorgänger-Rechtsprechung)
- Sortierung: hoch relevante zuerst
- Aktenzeichten-Format: genau C-XXX/XX oder T-XXX/XX""",
            user=f"Rechtsfrage: {question}"
            + (f"\nZeitraum: {date_from} bis {date_to}" if date_from or date_to else ""),
            max_tokens=2000,
        )
        identified = parse_json(raw)
        steps[-1]["status"] = "done"
        steps[-1]["data"] = {
            "urteile_identifiziert": len(identified.get("urteile", [])),
        }
    except Exception as e:
        return jsonify({"error": f"Fehler bei Identifizierung: {e}", "steps": steps}), 500

    urteile = identified.get("urteile", [])
    if not urteile:
        return jsonify({
            "question": question,
            "zusammenfassung": identified.get("zusammenfassung", ""),
            "steps": steps, "results": [],
        })

    # ── Schritt 2: Volltexte über EUR-Lex CELEX abrufen ──────────────────────
    steps.append({"step": 2, "label": "Volltexte von EUR-Lex abrufen (CELEX)", "status": "running"})
    fetched, skipped = 0, 0

    enriched_with_text = []
    for u in urteile[:15]:
        az = u.get("aktenzeichen", "")
        fulltext, eurlex_url = fetch_fulltext(az) if az else (None, None)
        celex = az_to_celex(az) if az else None
        enriched_with_text.append({
            **u,
            "fulltext":    fulltext,
            "eurlex_url":  eurlex_url or "",
            "celex":       celex or "",
            "curia_url":   f"https://curia.europa.eu/juris/liste.jsf?num={quote(az)}" if az else "",
        })
        if fulltext:
            fetched += 1
        else:
            skipped += 1
        time.sleep(0.15)

    steps[-1]["status"] = "done"
    steps[-1]["data"] = {"volltext_geladen": fetched, "nicht_gefunden": skipped}

    # ── Schritt 3: Randnummern extrahieren ────────────────────────────────────
    steps.append({"step": 3, "label": "Randnummern aus Volltexten extrahieren", "status": "running"})
    results = []

    for item in enriched_with_text:
        az       = item.get("aktenzeichen", "")
        fulltext = item.get("fulltext")

        if fulltext:
            # Extract Rn. from real document text
            try:
                rn_data = parse_json(claude(
                    system="""Analysiere diesen echten Urteilstext und extrahiere
die relevanten Randnummern für die Rechtsfrage.
Antworte NUR mit JSON, keine Backticks:
{
  "parteien": "Kläger / Beklagter (aus dem Text)",
  "gericht": "EuGH oder EuG",
  "kammer": "z.B. Dritte Kammer (aus dem Text)",
  "datum": "TT.MM.JJJJ (aus dem Text)",
  "sachverhalt": "2-3 Sätze: Worum geht es im Fall",
  "randnummern": [
    {
      "rn": "Rn. XX",
      "inhalt": "Was sagt das Gericht dort konkret zur Rechtsfrage (1 Satz, eigene Worte)"
    }
  ],
  "kernaussage": "Die wichtigste Aussage des Urteils zur Rechtsfrage (2-3 Sätze)",
  "volltext_bestaetigt": true
}
Nur Randnummern die im Text nachweislich vorkommen (z.B. '35.' oder 'Randnummer 35').
Wenn keine passenden Rn. gefunden: leere Liste.""",
                    user=f"Rechtsfrage: {question}\n\nAktenzeichen: {az}\n\nUrteilstext:\n{fulltext}",
                    max_tokens=1500,
                ))
                results.append({
                    "aktenzeichen":      az,
                    "relevanz":          item.get("relevanz", "mittel"),
                    "relevanzgrund":     item.get("relevanzgrund", ""),
                    "eurlex_url":        item.get("eurlex_url", ""),
                    "curia_url":         item.get("curia_url", ""),
                    "celex":             item.get("celex", ""),
                    "volltext_verfuegbar": True,
                    **rn_data,
                })
            except Exception:
                results.append({
                    "aktenzeichen":  az,
                    "parteien":      item.get("titel", ""),
                    "datum":         item.get("datum", ""),
                    "relevanz":      item.get("relevanz", "mittel"),
                    "relevanzgrund": item.get("relevanzgrund", ""),
                    "eurlex_url":    item.get("eurlex_url", ""),
                    "curia_url":     item.get("curia_url", ""),
                    "celex":         item.get("celex", ""),
                    "randnummern":   [],
                    "kernaussage":   "",
                    "sachverhalt":   "",
                    "volltext_verfuegbar": True,
                })
        else:
            # No full text — use Claude's knowledge but mark clearly
            results.append({
                "aktenzeichen":  az,
                "parteien":      item.get("titel", ""),
                "datum":         item.get("datum", ""),
                "relevanz":      item.get("relevanz", "mittel"),
                "relevanzgrund": item.get("relevanzgrund", ""),
                "eurlex_url":    item.get("eurlex_url", ""),
                "curia_url":     item.get("curia_url", ""),
                "celex":         item.get("celex", ""),
                "randnummern":   [],
                "kernaussage":   "",
                "sachverhalt":   "",
                "volltext_verfuegbar": False,
            })
        time.sleep(0.1)

    steps[-1]["status"] = "done"
    steps[-1]["data"] = {
        "mit_volltext": sum(1 for r in results if r.get("volltext_verfuegbar")),
        "ohne_volltext": sum(1 for r in results if not r.get("volltext_verfuegbar")),
    }

    # ── Schritt 4: Sortieren ──────────────────────────────────────────────────
    steps.append({"step": 4, "label": "Relevanzbewertung", "status": "running"})
    order = {"hoch": 0, "mittel": 1, "niedrig": 2}
    results.sort(key=lambda x: (
        order.get(x.get("relevanz", "mittel"), 1),
        0 if x.get("volltext_verfuegbar") else 1,
    ))
    steps[-1]["status"] = "done"
    steps[-1]["data"] = {
        "gesamt": len(results),
        "hoch":   sum(1 for r in results if r.get("relevanz") == "hoch"),
        "mittel": sum(1 for r in results if r.get("relevanz") == "mittel"),
    }

    return jsonify({
        "question":        question,
        "zusammenfassung": identified.get("zusammenfassung", ""),
        "steps":           steps,
        "results":         results,
    })


# ── Test-Endpunkte ────────────────────────────────────────────────────────────

@app.route("/celex-test")
def celex_test():
    """Tests CELEX access — confirms EUR-Lex direct links work."""
    cases = ["C-615/10", "C-337/05", "C-284/05", "T-26/01", "C-474/12"]
    out = []
    for az in cases:
        celex = az_to_celex(az)
        url   = f"https://eur-lex.europa.eu/legal-content/DE/TXT/?uri=CELEX:{celex}" if celex else ""
        try:
            r = requests.get(url, headers=HEADERS, timeout=10)
            text_len = len(r.text) if r.status_code == 200 else 0
            ok = r.status_code == 200 and text_len > 1000
        except Exception:
            ok, text_len = False, 0
        out.append({"az": az, "celex": celex, "status": "✓" if ok else "✗",
                    "text_len": text_len, "url": url})
    return jsonify({"results": out})


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
