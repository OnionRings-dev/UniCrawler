from __future__ import annotations

import os
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import datetime
from typing import Any
from urllib.parse import urlparse

import psycopg
from fastapi import FastAPI, HTTPException, Query, status
from fastapi.responses import HTMLResponse
from psycopg.rows import dict_row
from pydantic import BaseModel, Field
from redis import Redis


@dataclass(frozen=True)
class Config:
    redis_addr: str
    redis_password: str
    redis_db: int
    postgres_dsn: str
    mapper_input_queue: str
    mapper_output_queue: str
    parser_output_queue: str
    vectorizer_processing_queue: str
    vectorizer_failed_queue: str
    vectorizer_oversized_queue: str
    replay_batch_size: int


class AppState:
    cfg: Config
    redis: Redis
    db: psycopg.Connection


state = AppState()


class EnqueueRequest(BaseModel):
    url: str = Field(min_length=1)
    queue: str | None = None


class ReplayRequest(BaseModel):
    domain: str = Field(min_length=1)
    queue: str | None = None
    limit: int | None = Field(default=None, ge=1)


def env_string(key: str, fallback: str) -> str:
    return os.getenv(key) or fallback


def env_int(key: str, fallback: int) -> int:
    raw = os.getenv(key)
    if not raw:
        return fallback
    try:
        return int(raw)
    except ValueError:
        return fallback


def load_config() -> Config:
    return Config(
        redis_addr=env_string("REDIS_ADDR", "redis:6379"),
        redis_password=env_string("REDIS_PASSWORD", ""),
        redis_db=env_int("REDIS_DB", 0),
        postgres_dsn=env_string(
            "POSTGRES_DSN",
            "postgres://unicrawler:unicrawler@postgres:5432/unicrawler?sslmode=disable",
        ),
        mapper_input_queue=env_string("MAPPER_INPUT_QUEUE", "mapper:in"),
        mapper_output_queue=env_string("MAPPER_OUTPUT_QUEUE", "mapper:out"),
        parser_output_queue=env_string("PARSER_OUTPUT_QUEUE", "parser:out"),
        vectorizer_processing_queue=env_string(
            "VECTORIZER_PROCESSING_QUEUE", "vectorizer:processing"
        ),
        vectorizer_failed_queue=env_string("VECTORIZER_FAILED_QUEUE", "vectorizer:failed"),
        vectorizer_oversized_queue=env_string(
            "VECTORIZER_OVERSIZED_QUEUE", "vectorizer:oversized"
        ),
        replay_batch_size=env_int("REPLAY_BATCH_SIZE", 1000),
    )


@asynccontextmanager
async def lifespan(app: FastAPI):
    cfg = load_config()
    state.cfg = cfg
    state.redis = Redis.from_url(
        f"redis://{cfg.redis_addr}/{cfg.redis_db}",
        password=cfg.redis_password or None,
        decode_responses=True,
    )
    state.db = psycopg.connect(cfg.postgres_dsn, row_factory=dict_row)
    try:
        yield
    finally:
        state.db.close()
        state.redis.close()


app = FastAPI(title="UniCrawler Monitor", version="0.1.0", lifespan=lifespan)


def row_to_json(row: dict[str, Any]) -> dict[str, Any]:
    return {
        key: value.isoformat() if isinstance(value, datetime) else value
        for key, value in row.items()
    }


def queue_names() -> dict[str, str]:
    cfg = state.cfg
    return {
        "mapper_in": cfg.mapper_input_queue,
        "mapper_out": cfg.mapper_output_queue,
        "parser_out": cfg.parser_output_queue,
        "vectorizer_processing": cfg.vectorizer_processing_queue,
        "vectorizer_failed": cfg.vectorizer_failed_queue,
        "vectorizer_oversized": cfg.vectorizer_oversized_queue,
    }


def table_exists(name: str) -> bool:
    with state.db.cursor() as cur:
        cur.execute("SELECT to_regclass(%s) IS NOT NULL AS exists", (name,))
        return bool(cur.fetchone()["exists"])


def table_count(name: str, where: str = "") -> int:
    if not table_exists(name):
        return 0
    query = f"SELECT count(*) AS count FROM {name}"
    if where:
        query += f" WHERE {where}"
    with state.db.cursor() as cur:
        cur.execute(query)
        return int(cur.fetchone()["count"])


def normalize_replay_key(raw: str) -> str:
    value = raw.strip()
    if not value:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Missing domain")
    if "://" in value:
        parsed = urlparse(value)
        if parsed.hostname:
            return parsed.hostname.lower()
    return value.strip("/").lower()


def resolve_domain_id(raw: str) -> tuple[int, str] | None:
    if not table_exists("domains"):
        return None
    requested = normalize_replay_key(raw)
    with state.db.cursor() as cur:
        cur.execute("SELECT id, domain FROM domains WHERE domain = %s", (requested,))
        row = cur.fetchone()
        if row:
            return int(row["id"]), str(row["domain"])

        parts = requested.split(".")
        candidates = [".".join(parts[i:]) for i in range(1, max(len(parts) - 1, 1))]
        for candidate in candidates:
            cur.execute("SELECT id, domain FROM domains WHERE domain = %s", (candidate,))
            row = cur.fetchone()
            if row:
                return int(row["id"]), str(row["domain"])
    return None


@app.get("/", response_class=HTMLResponse)
def index() -> str:
    return HTML


@app.get("/api/health")
def health() -> dict[str, Any]:
    redis_ok = state.redis.ping()
    with state.db.cursor() as cur:
        cur.execute("SELECT 1 AS ok")
        postgres_ok = cur.fetchone()["ok"] == 1
    return {"ok": bool(redis_ok and postgres_ok), "redis": redis_ok, "postgres": postgres_ok}


@app.get("/api/status")
def pipeline_status() -> dict[str, Any]:
    queues = {
        key: {"name": name, "length": state.redis.llen(name)}
        for key, name in queue_names().items()
    }
    counts = {
        "domains": table_count("domains"),
        "urls": table_count("urls"),
        "crawl_runs": table_count("crawl_runs"),
        "running_runs": table_count("crawl_runs", "status = 'running'"),
        "documents": table_count("page_documents"),
        "document_versions": table_count("page_document_versions"),
        "parse_errors": table_count("page_parse_errors"),
    }
    return {"queues": queues, "counts": counts}


@app.get("/api/domains")
def domains(limit: int = Query(50, ge=1, le=500), offset: int = Query(0, ge=0)):
    if not table_exists("domains"):
        return {"domains": []}
    url_join = ""
    url_count = "0 AS url_count"
    if table_exists("urls"):
        url_join = "LEFT JOIN urls u ON u.domain_id = d.id"
        url_count = "count(DISTINCT u.id) AS url_count"
    document_join = ""
    document_count = "0 AS document_count"
    last_parsed_at = "NULL AS last_parsed_at"
    if table_exists("page_documents"):
        document_join = "LEFT JOIN page_documents pd ON pd.domain_id = d.id"
        document_count = "count(DISTINCT pd.id) AS document_count"
        last_parsed_at = "max(pd.last_parsed_at) AS last_parsed_at"
    crawl_join = ""
    last_crawl_at = "NULL AS last_crawl_at"
    if table_exists("crawl_runs"):
        crawl_join = "LEFT JOIN crawl_runs cr ON cr.domain_id = d.id"
        last_crawl_at = "max(cr.started_at) AS last_crawl_at"
    with state.db.cursor() as cur:
        cur.execute(
            f"""
            SELECT
                d.id,
                d.domain,
                d.created_at,
                d.updated_at,
                {url_count},
                {document_count},
                {last_crawl_at},
                {last_parsed_at}
            FROM domains d
            {url_join}
            {document_join}
            {crawl_join}
            GROUP BY d.id
            ORDER BY d.updated_at DESC, d.id DESC
            LIMIT %s OFFSET %s
            """,
            (limit, offset),
        )
        return {"domains": [row_to_json(row) for row in cur.fetchall()]}


@app.get("/api/runs")
def runs(limit: int = Query(50, ge=1, le=500), domain: str | None = None):
    if not table_exists("crawl_runs") or not table_exists("domains"):
        return {"runs": []}
    params: list[Any] = []
    where = ""
    if domain:
        where = "WHERE d.domain = %s"
        params.append(normalize_replay_key(domain))
    params.append(limit)
    with state.db.cursor() as cur:
        cur.execute(
            f"""
            SELECT
                cr.id,
                d.domain,
                cr.seed_url,
                cr.status,
                cr.started_at,
                cr.finished_at,
                cr.discovered_count,
                cr.page_count,
                cr.error_count
            FROM crawl_runs cr
            JOIN domains d ON d.id = cr.domain_id
            {where}
            ORDER BY cr.started_at DESC
            LIMIT %s
            """,
            params,
        )
        return {"runs": [row_to_json(row) for row in cur.fetchall()]}


@app.post("/api/enqueue", status_code=status.HTTP_202_ACCEPTED)
def enqueue(request: EnqueueRequest) -> dict[str, Any]:
    raw = request.url.strip()
    if not raw:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Missing URL")
    queue = request.queue or state.cfg.mapper_input_queue
    length = state.redis.lpush(queue, raw)
    return {"queued": raw, "queue": queue, "length": length}


@app.post("/api/replay", status_code=status.HTTP_202_ACCEPTED)
def replay(request: ReplayRequest) -> dict[str, Any]:
    resolved = resolve_domain_id(request.domain)
    if not resolved:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Domain has not been mapped yet")
    domain_id, domain = resolved
    queue = request.queue or state.cfg.mapper_output_queue
    pushed = replay_urls(domain_id, queue, request.limit)
    return {"requested": request.domain, "domain": domain, "queue": queue, "urls": pushed}


def replay_urls(domain_id: int, queue: str, limit: int | None) -> int:
    if not table_exists("urls"):
        return 0
    batch_size = max(state.cfg.replay_batch_size, 1)
    pushed = 0
    query = "SELECT url FROM urls WHERE domain_id = %s ORDER BY id"
    params: list[Any] = [domain_id]
    if limit is not None:
        query += " LIMIT %s"
        params.append(limit)

    with state.db.cursor() as cur:
        cur.execute(query, params)
        batch: list[str] = []
        for row in cur:
            batch.append(row["url"])
            if len(batch) >= batch_size:
                state.redis.rpush(queue, *batch)
                pushed += len(batch)
                batch.clear()
        if batch:
            state.redis.rpush(queue, *batch)
            pushed += len(batch)
    return pushed


HTML = """
<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>UniCrawler Monitor</title>
  <style>
    :root {
      color-scheme: light;
      --bg: #f7f8fa;
      --panel: #ffffff;
      --ink: #17202a;
      --muted: #667085;
      --line: #d8dee7;
      --accent: #156f8f;
      --accent-strong: #0b4d63;
      --warn: #9a4f00;
    }
    * { box-sizing: border-box; }
    body {
      margin: 0;
      font: 14px/1.45 system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
      color: var(--ink);
      background: var(--bg);
    }
    header, main { max-width: 1180px; margin: 0 auto; padding: 20px; }
    header {
      display: flex;
      align-items: end;
      justify-content: space-between;
      gap: 16px;
      border-bottom: 1px solid var(--line);
    }
    h1 { margin: 0; font-size: 26px; letter-spacing: 0; }
    h2 { margin: 0 0 12px; font-size: 16px; letter-spacing: 0; }
    .muted { color: var(--muted); }
    .grid { display: grid; grid-template-columns: repeat(3, 1fr); gap: 14px; }
    .panel {
      background: var(--panel);
      border: 1px solid var(--line);
      border-radius: 8px;
      padding: 16px;
      min-width: 0;
    }
    .metric {
      display: grid;
      grid-template-columns: 1fr auto;
      gap: 10px;
      padding: 8px 0;
      border-bottom: 1px solid #eef1f5;
    }
    .metric:last-child { border-bottom: 0; }
    .value { font-weight: 700; font-variant-numeric: tabular-nums; }
    form { display: grid; gap: 10px; }
    label { display: grid; gap: 6px; font-weight: 650; }
    input, select, button {
      width: 100%;
      min-height: 38px;
      border-radius: 6px;
      border: 1px solid var(--line);
      padding: 8px 10px;
      font: inherit;
      background: #fff;
    }
    button {
      border-color: var(--accent);
      background: var(--accent);
      color: #fff;
      font-weight: 700;
      cursor: pointer;
    }
    button:hover { background: var(--accent-strong); }
    table { width: 100%; border-collapse: collapse; }
    th, td {
      padding: 9px 8px;
      border-bottom: 1px solid #eef1f5;
      text-align: left;
      vertical-align: top;
      overflow-wrap: anywhere;
    }
    th { color: var(--muted); font-size: 12px; text-transform: uppercase; letter-spacing: .03em; }
    .span-2 { grid-column: span 2; }
    .status { min-height: 22px; color: var(--warn); }
    @media (max-width: 820px) {
      header { align-items: start; flex-direction: column; }
      .grid { grid-template-columns: 1fr; }
      .span-2 { grid-column: span 1; }
    }
  </style>
</head>
<body>
  <header>
    <div>
      <h1>UniCrawler Monitor</h1>
      <div class="muted">Pipeline queues, crawl state, enqueue, replay.</div>
    </div>
    <button id="refresh" type="button">Refresh</button>
  </header>
  <main class="grid">
    <section class="panel">
      <h2>Queues</h2>
      <div id="queues"></div>
    </section>
    <section class="panel">
      <h2>Storage</h2>
      <div id="counts"></div>
    </section>
    <section class="panel">
      <h2>Actions</h2>
      <form id="enqueue-form">
        <label>Seed URL
          <input id="seed-url" name="url" placeholder="https://example.com/" required>
        </label>
        <button type="submit">Add to mapper queue</button>
      </form>
      <hr>
      <form id="replay-form">
        <label>Mapped domain
          <input id="replay-domain" name="domain" placeholder="example.com" required>
        </label>
        <label>Limit
          <input id="replay-limit" name="limit" type="number" min="1" placeholder="all URLs">
        </label>
        <button type="submit">Replay to parser queue</button>
      </form>
      <p id="action-status" class="status"></p>
    </section>
    <section class="panel span-2">
      <h2>Domains</h2>
      <table>
        <thead>
          <tr><th>Domain</th><th>URLs</th><th>Docs</th><th>Last crawl</th><th>Last parsed</th></tr>
        </thead>
        <tbody id="domains"></tbody>
      </table>
    </section>
    <section class="panel">
      <h2>Recent Runs</h2>
      <table>
        <thead>
          <tr><th>Domain</th><th>Status</th><th>Pages</th><th>Errors</th></tr>
        </thead>
        <tbody id="runs"></tbody>
      </table>
    </section>
  </main>
  <script>
    const $ = (id) => document.getElementById(id);
    const formatDate = (value) => value ? new Date(value).toLocaleString() : "";

    function metricRows(data) {
      return Object.entries(data).map(([key, value]) => {
        const label = key.replaceAll("_", " ");
        const shown = typeof value === "object" ? value.length : value;
        return `<div class="metric"><span>${label}</span><span class="value">${shown}</span></div>`;
      }).join("");
    }

    async function refresh() {
      const [status, domains, runs] = await Promise.all([
        fetch("/api/status").then((r) => r.json()),
        fetch("/api/domains?limit=30").then((r) => r.json()),
        fetch("/api/runs?limit=20").then((r) => r.json()),
      ]);
      $("queues").innerHTML = metricRows(status.queues);
      $("counts").innerHTML = metricRows(status.counts);
      $("domains").innerHTML = domains.domains.map((d) => `
        <tr>
          <td>${d.domain}</td>
          <td>${d.url_count}</td>
          <td>${d.document_count}</td>
          <td>${formatDate(d.last_crawl_at)}</td>
          <td>${formatDate(d.last_parsed_at)}</td>
        </tr>
      `).join("");
      $("runs").innerHTML = runs.runs.map((r) => `
        <tr>
          <td>${r.domain}</td>
          <td>${r.status}</td>
          <td>${r.page_count}</td>
          <td>${r.error_count}</td>
        </tr>
      `).join("");
    }

    async function postJSON(path, body) {
      const response = await fetch(path, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });
      const payload = await response.json();
      if (!response.ok) throw new Error(payload.detail || "request failed");
      return payload;
    }

    $("refresh").addEventListener("click", refresh);
    $("enqueue-form").addEventListener("submit", async (event) => {
      event.preventDefault();
      try {
        const payload = await postJSON("/api/enqueue", { url: $("seed-url").value });
        $("action-status").textContent = `Queued ${payload.queued} in ${payload.queue}.`;
        $("seed-url").value = "";
        await refresh();
      } catch (error) {
        $("action-status").textContent = error.message;
      }
    });
    $("replay-form").addEventListener("submit", async (event) => {
      event.preventDefault();
      const limit = $("replay-limit").value ? Number($("replay-limit").value) : null;
      try {
        const payload = await postJSON("/api/replay", {
          domain: $("replay-domain").value,
          limit,
        });
        $("action-status").textContent = `Replayed ${payload.urls} URLs from ${payload.domain}.`;
        await refresh();
      } catch (error) {
        $("action-status").textContent = error.message;
      }
    });
    refresh();
    setInterval(refresh, 5000);
  </script>
</body>
</html>
"""
