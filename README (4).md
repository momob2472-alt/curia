<!DOCTYPE html>
<html lang="de">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Curia Recherche</title>
<style>
  *, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }
  :root {
    --bg: #ffffff; --bg2: #f5f5f2; --bg3: #efefec;
    --text: #1a1a18; --text2: #5c5c58; --text3: #888884;
    --border: rgba(0,0,0,0.1); --border2: rgba(0,0,0,0.2);
    --green: #0F6E56; --green-bg: #E1F5EE; --green-bd: #5DCAA5;
    --blue: #185FA5;  --blue-bg: #E6F1FB;  --blue-bd: #85B7EB;
    --amber-bg: #FAEEDA; --amber: #633806; --amber-bd: #EF9F27;
    --red-bg: #FCEBEB; --red: #A32D2D;
    --radius: 8px; --radius-lg: 12px;
    font-family: system-ui, -apple-system, sans-serif;
  }
  body { background: var(--bg); color: var(--text); padding: 2rem; max-width: 900px; margin: 0 auto; }
  h1 { font-size: 20px; font-weight: 600; margin-bottom: 4px; }
  .sub { font-size: 13px; color: var(--text3); margin-bottom: 1.5rem; }

  /* Form */
  .form-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 10px; margin-bottom: 1rem; }
  .form-full { grid-column: 1 / -1; }
  label { display: block; font-size: 11px; font-weight: 500; text-transform: uppercase;
          letter-spacing: .06em; color: var(--text2); margin-bottom: 4px; }
  input, select { width: 100%; padding: 8px 10px; font-size: 14px; border: 1px solid var(--border2);
                  border-radius: var(--radius); background: var(--bg); color: var(--text); outline: none; }
  input:focus, select:focus { border-color: var(--blue); box-shadow: 0 0 0 3px rgba(24,95,165,.12); }
  .date-row { display: grid; grid-template-columns: 1fr 1fr; gap: 8px; }

  /* Buttons */
  .btn-row { display: flex; gap: 8px; margin-bottom: 1.5rem; }
  button { padding: 9px 20px; border-radius: var(--radius); font-size: 14px; font-weight: 500;
           cursor: pointer; border: 1px solid var(--border2); background: var(--bg); color: var(--text); }
  button.primary { background: var(--text); color: var(--bg); border-color: transparent; }
  button.primary:hover { opacity: .85; }
  button:disabled { opacity: .4; cursor: not-allowed; }
  button.secondary:hover { background: var(--bg2); }

  /* Status */
  .status-bar { font-size: 13px; color: var(--text3); margin-bottom: 1rem; display: flex;
                align-items: center; gap: 8px; }
  .spinner { width: 14px; height: 14px; border: 2px solid var(--border2);
             border-top-color: var(--text); border-radius: 50%; animation: spin .8s linear infinite; }
  @keyframes spin { to { transform: rotate(360deg); } }
  .error { background: var(--red-bg); color: var(--red); border: 1px solid #F09595;
           border-radius: var(--radius); padding: 10px 14px; font-size: 13px; margin-bottom: 1rem; }
  .proxy-warn { background: var(--amber-bg); color: var(--amber); border: 1px solid var(--amber-bd);
                border-radius: var(--radius); padding: 10px 14px; font-size: 13px; margin-bottom: 1.5rem; }
  .proxy-warn a { color: var(--amber); }

  /* Cards */
  .card { border: 1px solid var(--border); border-radius: var(--radius-lg);
          padding: 1rem 1.25rem; margin-bottom: 10px; background: var(--bg); }
  .card-top { display: flex; justify-content: space-between; align-items: flex-start;
              gap: 10px; margin-bottom: 6px; }
  .card-title { font-size: 14px; font-weight: 600; color: var(--text); flex: 1; line-height: 1.4; }
  .meta { display: flex; gap: 5px; flex-wrap: wrap; margin-bottom: 8px; }
  .badge { font-size: 11px; padding: 2px 7px; border-radius: 99px; border: 1px solid var(--border); }
  .badge.az  { background: var(--blue-bg);  color: var(--blue);  border-color: var(--blue-bd);  font-weight: 600; font-family: monospace; }
  .badge.dt  { background: var(--amber-bg); color: var(--amber); border-color: var(--amber-bd); }
  .badge.typ { background: var(--bg2); color: var(--text2); }
  .card-links { display: flex; gap: 6px; margin-top: 10px; padding-top: 8px;
                border-top: 1px solid var(--border); }
  .link-btn { font-size: 12px; padding: 3px 10px; border-radius: var(--radius);
              border: 1px solid var(--border2); cursor: pointer; color: var(--text);
              background: var(--bg2); text-decoration: none; }
  .link-btn:hover { border-color: var(--border2); background: var(--bg3); }

  /* Pagination */
  .pag { display: flex; align-items: center; gap: 8px; margin-top: 1.5rem; }
  .pag button { padding: 6px 14px; font-size: 13px; }
  .pag-info { font-size: 13px; color: var(--text3); }

  /* Proxy URL field */
  .proxy-row { display: flex; align-items: center; gap: 8px; margin-bottom: 1.5rem; }
  .proxy-row input { flex: 1; font-size: 12px; font-family: monospace; }
  .proxy-row button { font-size: 12px; padding: 6px 12px; white-space: nowrap; }
  .dot { width: 8px; height: 8px; border-radius: 50%; flex-shrink: 0; }
  .dot.ok  { background: #1D9E75; }
  .dot.err { background: #E24B4A; }
  .dot.unk { background: #EF9F27; }
</style>
</head>
<body>

<h1>Curia Direktsuche</h1>
<p class="sub">Direkte Datenbankabfrage über lokalen Proxy · Ergebnisse 1:1 aus curia.europa.eu</p>

<!-- Proxy-Konfiguration -->
<div style="margin-bottom:1.5rem">
  <label>Proxy-URL (lokal oder Server)</label>
  <div class="proxy-row">
    <span class="dot unk" id="proxyDot"></span>
    <input type="text" id="proxyUrl" value="http://localhost:5000" placeholder="http://localhost:5000">
    <button class="secondary" onclick="checkProxy()">Verbindung testen</button>
  </div>
  <div id="proxyStatus" class="proxy-warn" style="display:none"></div>
</div>

<!-- Suchformular -->
<div class="form-grid">
  <div class="form-full">
    <label>Freitextsuche</label>
    <input type="text" id="text" placeholder='z.B. Militärausrüstung  oder  "military equipment"  oder  Art. 346'>
  </div>
  <div>
    <label>Richtlinie (Zitationsfilter)</label>
    <select id="directive">
      <option value="">— keine Richtlinie —</option>
      <option value="2009/81" selected>2009/81/EG (Verteidigung & Sicherheit)</option>
      <option value="2014/24">2014/24/EU (Vergabe öffentliche Aufträge)</option>
      <option value="2014/25">2014/25/EU (Sektorenvergabe)</option>
      <option value="2014/23">2014/23/EU (Konzessionsvergabe)</option>
      <option value="2004/18">2004/18/EG (Vergabe, alt)</option>
      <option value="2016/680">2016/680/EU (Datenschutz Strafverfolgung)</option>
    </select>
  </div>
  <div>
    <label>Gericht</label>
    <select id="court">
      <option value="C,T,F">Alle (EuGH + EuG + EuGöD)</option>
      <option value="C">EuGH</option>
      <option value="T">EuG</option>
    </select>
  </div>
  <div>
    <label>Sprache der Ergebnisse</label>
    <select id="language">
      <option value="de">Deutsch</option>
      <option value="en">Englisch</option>
      <option value="fr">Französisch</option>
    </select>
  </div>
  <div>
    <label>Zeitraum</label>
    <div class="date-row">
      <input type="text" id="dateFrom" placeholder="Von: JJJJ-MM-TT">
      <input type="text" id="dateTo"   placeholder="Bis: JJJJ-MM-TT">
    </div>
  </div>
</div>

<div class="btn-row">
  <button class="primary" id="searchBtn" onclick="search()">Suchen ↗</button>
  <button class="secondary" onclick="clearForm()">Zurücksetzen</button>
</div>

<div id="statusBar" class="status-bar" style="display:none"></div>
<div id="errorBox" style="display:none"></div>
<div id="results"></div>
<div id="pagination" class="pag" style="display:none"></div>

<script>
let lastParams = null;

// ── Proxy-Check ───────────────────────────────────────────────────────────────
async function checkProxy() {
  const url = document.getElementById('proxyUrl').value.trim();
  const dot = document.getElementById('proxyDot');
  const status = document.getElementById('proxyStatus');
  dot.className = 'dot unk';
  try {
    const r = await fetch(url + '/health', { signal: AbortSignal.timeout(4000) });
    const d = await r.json();
    if (d.status === 'ok') {
      dot.className = 'dot ok';
      status.style.display = 'none';
    } else throw new Error();
  } catch {
    dot.className = 'dot err';
    status.style.display = 'block';
    status.innerHTML = `
      Proxy nicht erreichbar. <strong>server.py starten:</strong><br>
      <code>cd curia-proxy &amp;&amp; python server.py</code><br>
      Dann diese Seite neu laden.`;
  }
}

// ── Suche ─────────────────────────────────────────────────────────────────────
async function search(page = 0) {
  const proxyUrl  = document.getElementById('proxyUrl').value.trim();
  const text      = document.getElementById('text').value.trim();
  const directive = document.getElementById('directive').value;
  const court     = document.getElementById('court').value;
  const language  = document.getElementById('language').value;
  const dateFrom  = document.getElementById('dateFrom').value.trim();
  const dateTo    = document.getElementById('dateTo').value.trim();

  if (!text && !directive) {
    showError('Bitte Suchtext oder Richtlinie angeben.');
    return;
  }

  const params = new URLSearchParams();
  if (text)      params.set('text',      text);
  if (directive) params.set('directive', directive);
  if (court)     params.set('court',     court);
  if (language)  params.set('language',  language);
  if (dateFrom)  params.set('date_from', dateFrom);
  if (dateTo)    params.set('date_to',   dateTo);
  if (page)      params.set('page',      page);

  lastParams = params;

  setLoading(true);
  clearError();
  document.getElementById('results').innerHTML = '';
  document.getElementById('pagination').style.display = 'none';

  try {
    const r = await fetch(`${proxyUrl}/search?${params}`, {
      signal: AbortSignal.timeout(25000)
    });

    if (!r.ok) {
      const err = await r.json().catch(() => ({}));
      throw new Error(err.error || `HTTP ${r.status}`);
    }

    const data = await r.json();
    renderResults(data, proxyUrl);

  } catch (e) {
    if (e.name === 'TimeoutError') {
      showError('Curia hat nicht innerhalb von 25 Sekunden geantwortet. Bitte nochmals versuchen.');
    } else if (e.message.includes('fetch')) {
      showError('Proxy nicht erreichbar. Ist server.py gestartet? (http://localhost:5000)');
    } else {
      showError('Fehler: ' + e.message);
    }
  } finally {
    setLoading(false);
  }
}

// ── Ergebnisse rendern ────────────────────────────────────────────────────────
function renderResults(data, proxyUrl) {
  const container = document.getElementById('results');

  if (!data.results || data.results.length === 0) {
    container.innerHTML = '<p style="color:#888;font-size:14px;padding:1rem 0">Keine Treffer. Suchbegriff anpassen oder Richtlinienfilter entfernen.</p>';
    return;
  }

  const totalInfo = data.total > 0 ? `${data.total} Treffer` : `${data.results.length} Treffer`;
  let html = `<div style="font-size:13px;color:#888;margin-bottom:1rem;display:flex;justify-content:space-between;align-items:center">
    <span>${totalInfo} · <a href="${esc(data.curia_url)}" target="_blank" style="color:#185FA5;font-size:12px">In Curia öffnen ↗</a></span>
  </div>`;

  data.results.forEach(r => {
    const eurlexUrl = r.aktenzeichen
      ? `https://eur-lex.europa.eu/search.html?query=${encodeURIComponent(r.aktenzeichen)}&DB_TYPE_OF_ACT=judgment`
      : '';

    html += `<div class="card">
      <div class="card-top">
        <div class="card-title">${esc(r.parteien || r.aktenzeichen || '—')}</div>
      </div>
      <div class="meta">
        ${r.aktenzeichen ? `<span class="badge az">${esc(r.aktenzeichen)}</span>` : ''}
        ${r.datum        ? `<span class="badge dt">${esc(r.datum)}</span>`        : ''}
        ${r.typ          ? `<span class="badge typ">${esc(r.typ)}</span>`         : ''}
        ${r.publikation  ? `<span class="badge typ" style="font-size:10px">${esc(r.publikation)}</span>` : ''}
      </div>
      <div class="card-links">
        ${r.curia_url ? `<a class="link-btn" href="${esc(r.curia_url)}" target="_blank">⇗ Curia</a>` : ''}
        ${r.doc_url   ? `<a class="link-btn" href="${esc(r.doc_url)}"   target="_blank">⇗ Volltext</a>` : ''}
        ${eurlexUrl   ? `<a class="link-btn" href="${esc(eurlexUrl)}"   target="_blank">⇗ EUR-Lex</a>` : ''}
      </div>
    </div>`;
  });

  container.innerHTML = html;
}

// ── Hilfsfunktionen ───────────────────────────────────────────────────────────
function setLoading(on) {
  const bar = document.getElementById('statusBar');
  const btn = document.getElementById('searchBtn');
  if (on) {
    bar.style.display = 'flex';
    bar.innerHTML = '<div class="spinner"></div><span>Curia wird abgefragt…</span>';
    btn.disabled = true;
    btn.textContent = '… läuft';
  } else {
    bar.style.display = 'none';
    btn.disabled = false;
    btn.textContent = 'Suchen ↗';
  }
}

function showError(msg) {
  const el = document.getElementById('errorBox');
  el.style.display = 'block';
  el.className = 'error';
  el.textContent = msg;
}

function clearError() {
  const el = document.getElementById('errorBox');
  el.style.display = 'none';
}

function clearForm() {
  ['text','dateFrom','dateTo'].forEach(id => document.getElementById(id).value = '');
  document.getElementById('directive').value = '';
  document.getElementById('court').value = 'C,T,F';
  document.getElementById('language').value = 'de';
  document.getElementById('results').innerHTML = '';
  clearError();
}

function esc(s) {
  return String(s||'').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');
}

// ── Enter-Taste ───────────────────────────────────────────────────────────────
document.addEventListener('keydown', e => {
  if (e.key === 'Enter' && document.activeElement.tagName !== 'BUTTON') search();
});

// Beim Laden Proxy prüfen
window.addEventListener('load', checkProxy);
</script>
</body>
</html>
