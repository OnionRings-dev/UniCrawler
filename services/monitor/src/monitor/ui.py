from __future__ import annotations

HTML = """
<!doctype html>
<html lang="it">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>UniCrawler Monitor</title>
  <style>
    :root {
      color-scheme: light;
      --bg: #f5f7f9;
      --panel: #ffffff;
      --ink: #182230;
      --muted: #667085;
      --line: #d6dde6;
      --line-soft: #edf1f5;
      --accent: #0d766e;
      --accent-dark: #075e59;
      --blue: #2563eb;
      --red: #b42318;
      --amber: #b54708;
      --bar-bg: #e8edf3;
    }
    * { box-sizing: border-box; }
    body {
      margin: 0;
      color: var(--ink);
      background: var(--bg);
      font: 14px/1.45 system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
    }
    header, main { max-width: 1320px; margin: 0 auto; padding: 18px; }
    header {
      display: flex;
      align-items: end;
      justify-content: space-between;
      gap: 18px;
      border-bottom: 1px solid var(--line);
    }
    h1, h2, h3, p { margin: 0; }
    h1 { font-size: 24px; letter-spacing: 0; }
    h2 { font-size: 15px; letter-spacing: 0; }
    h3 { font-size: 13px; letter-spacing: 0; }
    main { display: grid; gap: 14px; }
    .top {
      display: grid;
      grid-template-columns: 1.2fr 1fr;
      gap: 14px;
      align-items: start;
    }
    .grid {
      display: grid;
      grid-template-columns: repeat(3, minmax(0, 1fr));
      gap: 14px;
    }
    .panel {
      min-width: 0;
      background: var(--panel);
      border: 1px solid var(--line);
      border-radius: 8px;
      padding: 15px;
    }
    .panel-head {
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 12px;
      margin-bottom: 12px;
    }
    .muted { color: var(--muted); }
    .small { font-size: 12px; }
    .metric {
      display: grid;
      grid-template-columns: minmax(0, 1fr) auto;
      gap: 10px;
      padding: 8px 0;
      border-bottom: 1px solid var(--line-soft);
    }
    .metric:last-child { border-bottom: 0; }
    .value { font-weight: 750; font-variant-numeric: tabular-nums; }
    .progress-list { display: grid; gap: 12px; }
    .progress-title {
      display: grid;
      grid-template-columns: minmax(0, 1fr) auto;
      gap: 10px;
      align-items: baseline;
      margin-bottom: 6px;
    }
    .bar {
      width: 100%;
      height: 10px;
      overflow: hidden;
      background: var(--bar-bg);
      border-radius: 999px;
    }
    .fill {
      height: 100%;
      width: 0%;
      background: var(--accent);
      transition: width .25s ease;
    }
    .stage-meta {
      display: grid;
      grid-template-columns: repeat(4, minmax(0, 1fr));
      gap: 8px;
      margin-top: 7px;
      color: var(--muted);
      font-size: 12px;
    }
    form { display: grid; gap: 10px; }
    label { display: grid; gap: 5px; font-weight: 650; }
    input, textarea, select, button {
      width: 100%;
      min-height: 38px;
      border-radius: 6px;
      border: 1px solid var(--line);
      padding: 8px 10px;
      background: #fff;
      color: var(--ink);
      font: inherit;
    }
    textarea { min-height: 84px; resize: vertical; }
    button {
      display: inline-flex;
      align-items: center;
      justify-content: center;
      gap: 8px;
      border-color: var(--accent);
      background: var(--accent);
      color: #fff;
      cursor: pointer;
      font-weight: 750;
    }
    button:hover { background: var(--accent-dark); }
    button.secondary {
      color: var(--ink);
      border-color: var(--line);
      background: #fff;
    }
    button.secondary:hover { background: #f8fafc; }
    button:disabled {
      cursor: wait;
      opacity: .68;
    }
    .actions { display: grid; grid-template-columns: 1fr 1fr; gap: 14px; }
    .status {
      min-height: 20px;
      color: var(--muted);
      overflow-wrap: anywhere;
    }
    .status.error { color: var(--red); }
    .status.ok { color: var(--accent-dark); }
    table { width: 100%; border-collapse: collapse; }
    th, td {
      padding: 9px 8px;
      text-align: left;
      vertical-align: middle;
      border-bottom: 1px solid var(--line-soft);
      overflow-wrap: anywhere;
    }
    th {
      color: var(--muted);
      font-size: 12px;
      font-weight: 750;
      text-transform: uppercase;
      letter-spacing: .02em;
    }
    td.numeric { text-align: right; font-variant-numeric: tabular-nums; }
    .tables { display: grid; grid-template-columns: 1.35fr .9fr; gap: 14px; align-items: start; }
    .domain-cell { display: flex; gap: 9px; align-items: center; }
    .domain-cell input { width: 16px; min-height: 16px; padding: 0; }
    .table-actions {
      display: flex;
      gap: 8px;
      align-items: center;
      justify-content: flex-end;
      flex-wrap: wrap;
    }
    .pill {
      display: inline-flex;
      align-items: center;
      gap: 7px;
      min-height: 22px;
      padding: 2px 8px;
      border: 1px solid var(--line);
      border-radius: 999px;
      font-size: 12px;
      color: var(--muted);
      background: #fff;
    }
    .pill.updating {
      border-color: #99d1cb;
      color: var(--accent-dark);
      background: #eefaf8;
    }
    .pill.error {
      border-color: #f4b8b2;
      color: var(--red);
      background: #fff5f4;
    }
    .spinner {
      width: 12px;
      height: 12px;
      border: 2px solid currentColor;
      border-right-color: transparent;
      border-radius: 999px;
      animation: spin .8s linear infinite;
    }
    .sync-dot {
      width: 7px;
      height: 7px;
      border-radius: 999px;
      background: var(--accent);
    }
    .updating .sync-dot {
      animation: pulse .9s ease-in-out infinite;
    }
    .error .sync-dot {
      background: var(--red);
    }
    @keyframes spin {
      to { transform: rotate(360deg); }
    }
    @keyframes pulse {
      0%, 100% { transform: scale(.75); opacity: .45; }
      50% { transform: scale(1.12); opacity: 1; }
    }
    @media (max-width: 980px) {
      .top, .actions, .tables { grid-template-columns: 1fr; }
      .grid { grid-template-columns: 1fr; }
      header { align-items: start; flex-direction: column; }
      .stage-meta { grid-template-columns: 1fr 1fr; }
    }
  </style>
</head>
<body>
  <header>
    <div>
      <h1>UniCrawler Monitor</h1>
      <p class="muted">Dati live, inserimento seed, replay domini e stima dei lavori in corso.</p>
    </div>
    <div class="table-actions">
      <span id="last-update" class="pill"><span class="sync-dot"></span><span id="sync-text">in attesa</span></span>
      <button id="refresh" type="button" class="secondary">Aggiorna</button>
    </div>
  </header>
  <main>
    <section class="top">
      <div class="panel">
        <div class="panel-head">
          <h2>Lavori in corso</h2>
          <span class="small muted">stima sugli ultimi 10 minuti</span>
        </div>
        <div id="progress" class="progress-list"></div>
      </div>
      <div class="grid">
        <div class="panel">
          <div class="panel-head"><h2>Code Redis</h2></div>
          <div id="queues"></div>
        </div>
        <div class="panel">
          <div class="panel-head"><h2>Storage</h2></div>
          <div id="counts"></div>
        </div>
        <div class="panel">
          <div class="panel-head"><h2>Salute</h2></div>
          <div id="health"></div>
        </div>
      </div>
    </section>

    <section class="panel">
      <div class="panel-head"><h2>Azioni pipeline</h2></div>
      <div class="actions">
        <form id="enqueue-form">
          <label>Nuovi link da mappare
            <textarea id="seed-urls" placeholder="https://example.com/&#10;https://docs.example.com/"></textarea>
          </label>
          <button type="submit">Inserisci nella mapper queue</button>
        </form>
        <form id="replay-form">
          <label>Domini o endpoint selezionati
            <textarea id="replay-domains" placeholder="example.com&#10;docs.example.com"></textarea>
          </label>
          <label>Limite per dominio
            <input id="replay-limit" type="number" min="1" placeholder="tutti gli URL">
          </label>
          <button type="submit">Replay verso parser</button>
        </form>
      </div>
      <p id="action-status" class="status"></p>
    </section>

    <section class="tables">
      <div class="panel">
        <div class="panel-head">
          <h2>Domini mappati</h2>
          <div class="table-actions">
            <button id="copy-selection" type="button" class="secondary">Usa selezionati</button>
            <button id="replay-selection" type="button">Replay selezionati</button>
          </div>
        </div>
        <table>
          <thead>
            <tr><th>Dominio</th><th>URL</th><th>Documenti</th><th>Ultimo crawl</th><th>Ultimo parse</th></tr>
          </thead>
          <tbody id="domains"></tbody>
        </table>
      </div>
      <div class="panel">
        <div class="panel-head"><h2>Run recenti</h2></div>
        <table>
          <thead>
            <tr><th>Dominio</th><th>Stato</th><th>Pag.</th><th>Err.</th></tr>
          </thead>
          <tbody id="runs"></tbody>
        </table>
      </div>
    </section>
  </main>
  <script>
    const $ = (id) => document.getElementById(id);
    const selectedDomains = new Set();
    let refreshInFlight = false;
    let queuedRefresh = false;
    let autoRefreshTimer = null;

    function escapeHtml(value) {
      return String(value ?? "").replace(/[&<>"']/g, (char) => ({
        "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"
      }[char]));
    }

    function formatDate(value) {
      if (!value) return "";
      return new Date(value).toLocaleString();
    }

    function metricRows(items) {
      return items.map((item) => `
        <div class="metric">
          <span>${escapeHtml(item.label)}</span>
          <span class="value">${escapeHtml(item.value)}</span>
        </div>
      `).join("");
    }

    function splitLines(value) {
      return value.split("\\n").map((item) => item.trim()).filter(Boolean);
    }

    async function postJSON(url, payload) {
      return fetchJSON(url, {
        method: "POST",
        headers: {"content-type": "application/json"},
        body: JSON.stringify(payload)
      });
    }

    function renderProgress(stages) {
      $("progress").innerHTML = stages.map((stage) => `
        <div>
          <div class="progress-title">
            <h3>${escapeHtml(stage.label)}</h3>
            <span class="value">${stage.percent}%</span>
          </div>
          <div class="bar"><div class="fill" style="width:${stage.percent}%"></div></div>
          <div class="stage-meta">
            <span>in coda: <b>${stage.pending}</b></span>
            <span>attivi: <b>${stage.active}</b></span>
            <span>rate: <b>${stage.rate_per_min}/min</b></span>
            <span>attesa: <b>${escapeHtml(stage.eta.label)}</b></span>
          </div>
        </div>
      `).join("");
    }

    function renderDomains(domains) {
      $("domains").innerHTML = domains.map((domain) => {
        const checked = selectedDomains.has(domain.domain) ? "checked" : "";
        return `
          <tr>
            <td>
              <label class="domain-cell">
                <input type="checkbox" data-domain="${escapeHtml(domain.domain)}" ${checked}>
                <span>${escapeHtml(domain.domain)}</span>
              </label>
            </td>
            <td class="numeric">${domain.url_count}</td>
            <td class="numeric">${domain.document_count}</td>
            <td>${formatDate(domain.last_crawl_at)}</td>
            <td>${formatDate(domain.last_parsed_at)}</td>
          </tr>
        `;
      }).join("");
      document.querySelectorAll("input[data-domain]").forEach((input) => {
        input.addEventListener("change", () => {
          if (input.checked) selectedDomains.add(input.dataset.domain);
          else selectedDomains.delete(input.dataset.domain);
        });
      });
    }

    function renderRuns(runs) {
      $("runs").innerHTML = runs.map((run) => `
        <tr>
          <td>${escapeHtml(run.domain)}</td>
          <td>${escapeHtml(run.status)}</td>
          <td class="numeric">${run.page_count}</td>
          <td class="numeric">${run.error_count}</td>
        </tr>
      `).join("");
    }

    function setSyncState(state, message) {
      const badge = $("last-update");
      const text = $("sync-text");
      badge.className = `pill ${state}`;
      text.textContent = message;
      $("refresh").disabled = state === "updating";
      $("refresh").innerHTML = state === "updating"
        ? '<span class="spinner"></span><span>Aggiorno</span>'
        : "Aggiorna";
    }

    async function fetchJSON(url, options = {}, timeoutMs = 8000) {
      const controller = new AbortController();
      const timeout = setTimeout(() => controller.abort(), timeoutMs);
      try {
        const response = await fetch(url, {...options, signal: controller.signal});
        if (!response.ok) {
          let detail = response.statusText;
          try {
            const payload = await response.json();
            detail = payload.detail || detail;
          } catch (_) {}
          throw new Error(detail);
        }
        return response.json();
      } catch (error) {
        if (error.name === "AbortError") {
          throw new Error(`${url} timeout dopo ${Math.round(timeoutMs / 1000)}s`);
        }
        throw error;
      } finally {
        clearTimeout(timeout);
      }
    }

    async function refresh() {
      if (refreshInFlight) {
        queuedRefresh = true;
        return;
      }
      refreshInFlight = true;
      queuedRefresh = false;
      setSyncState("updating", "aggiornamento...");
      try {
        const [dashboard, domains, runs, health] = await Promise.all([
          fetchJSON("/api/v1/dashboard"),
          fetchJSON("/api/v1/domains?limit=60"),
          fetchJSON("/api/v1/runs?limit=25"),
          fetchJSON("/api/health")
        ]);
        renderProgress(dashboard.progress);
        $("queues").innerHTML = metricRows(dashboard.queues.map((queue) => ({
          label: queue.name,
          value: queue.length
        })));
        $("counts").innerHTML = metricRows(Object.entries(dashboard.counts).map(([key, value]) => ({
          label: key.replaceAll("_", " "),
          value
        })));
        $("health").innerHTML = metricRows([
          {label: "Redis", value: health.redis ? "ok" : "errore"},
          {label: "Postgres", value: health.postgres ? "ok" : "errore"},
          {label: "Qdrant", value: dashboard.qdrant.ok ? `${dashboard.qdrant.collections} collections` : "errore"},
          {label: "Nodi", value: `${(dashboard.nodes || []).length} heartbeat`}
        ]);
        renderDomains(domains.domains);
        renderRuns(runs.runs);
        setSyncState("", `aggiornato ${new Date(dashboard.generated_at).toLocaleTimeString()}`);
      } catch (error) {
        setSyncState("error", `errore aggiornamento: ${error.message}`);
      } finally {
        refreshInFlight = false;
        if (queuedRefresh) {
          refresh();
        }
      }
    }

    async function replayDomains(domains) {
      const limit = $("replay-limit").value ? Number($("replay-limit").value) : null;
      return postJSON("/api/v1/replay", {domains, limit_per_domain: limit});
    }

    $("refresh").addEventListener("click", refresh);
    $("enqueue-form").addEventListener("submit", async (event) => {
      event.preventDefault();
      const status = $("action-status");
      status.className = "status";
      status.textContent = "Inserimento in corso...";
      try {
        const data = await postJSON("/api/v1/enqueue", {urls: splitLines($("seed-urls").value)});
        status.className = "status ok";
        status.textContent = `${data.queued.length} link inseriti in ${data.queue}.`;
        $("seed-urls").value = "";
        await refresh();
      } catch (error) {
        status.className = "status error";
        status.textContent = error.message;
      }
    });
    $("replay-form").addEventListener("submit", async (event) => {
      event.preventDefault();
      const status = $("action-status");
      status.className = "status";
      status.textContent = "Replay in corso...";
      try {
        const data = await replayDomains(splitLines($("replay-domains").value));
        status.className = "status ok";
        status.textContent = `${data.total_urls} URL inviati a ${data.queue}.`;
        await refresh();
      } catch (error) {
        status.className = "status error";
        status.textContent = error.message;
      }
    });
    $("copy-selection").addEventListener("click", () => {
      $("replay-domains").value = Array.from(selectedDomains).join("\\n");
    });
    $("replay-selection").addEventListener("click", async () => {
      const status = $("action-status");
      status.className = "status";
      status.textContent = "Replay in corso...";
      try {
        const data = await replayDomains(Array.from(selectedDomains));
        status.className = "status ok";
        status.textContent = `${data.total_urls} URL inviati a ${data.queue}.`;
        await refresh();
      } catch (error) {
        status.className = "status error";
        status.textContent = error.message;
      }
    });
    refresh();
    autoRefreshTimer = setInterval(refresh, 2500);
  </script>
</body>
</html>
"""
