from __future__ import annotations

import os
import time
from collections import deque
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlparse

import psycopg
from fastapi import FastAPI, HTTPException, Query, status
from fastapi.responses import HTMLResponse
from psycopg.rows import dict_row
from pydantic import BaseModel, Field, field_validator
from redis import Redis
from redis.exceptions import RedisError


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


@dataclass
class QueueSample:
    ts: float
    lengths: dict[str, int]


@dataclass
class AppState:
    cfg: Config | None = None
    redis: Redis | None = None
    db: psycopg.Connection | None = None
    queue_history: deque[QueueSample] = field(default_factory=lambda: deque(maxlen=120))


state = AppState()


class EnqueueRequest(BaseModel):
    url: str | None = None
    urls: list[str] = Field(default_factory=list)

    @field_validator("url")
    @classmethod
    def clean_url(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = value.strip()
        return value or None

    @field_validator("urls")
    @classmethod
    def clean_urls(cls, values: list[str]) -> list[str]:
        return [value.strip() for value in values if value.strip()]


class ReplayRequest(BaseModel):
    domain: str | None = None
    domains: list[str] = Field(default_factory=list)
    queue: str | None = None
    limit_per_domain: int | None = Field(default=None, ge=1)

    @field_validator("domain")
    @classmethod
    def clean_domain(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = value.strip()
        return value or None

    @field_validator("domains")
    @classmethod
    def clean_domains(cls, values: list[str]) -> list[str]:
        return [value.strip() for value in values if value.strip()]


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
    state.redis = Redis(
        host=cfg.redis_addr.split(":", 1)[0],
        port=int(cfg.redis_addr.split(":", 1)[1]) if ":" in cfg.redis_addr else 6379,
        password=cfg.redis_password or None,
        db=cfg.redis_db,
        decode_responses=True,
        health_check_interval=30,
    )
    state.db = psycopg.connect(cfg.postgres_dsn, row_factory=dict_row)
    try:
        yield
    finally:
        if state.db is not None:
            state.db.close()
        if state.redis is not None:
            state.redis.close()


app = FastAPI(title="UniCrawler Monitor", version="1.0.0", lifespan=lifespan)


def cfg() -> Config:
    if state.cfg is None:
        raise RuntimeError("application is not initialized")
    return state.cfg


def redis_client() -> Redis:
    if state.redis is None:
        raise RuntimeError("redis is not initialized")
    return state.redis


def db_conn() -> psycopg.Connection:
    if state.db is None:
        raise RuntimeError("postgres is not initialized")
    return state.db


def row_to_json(row: dict[str, Any]) -> dict[str, Any]:
    return {
        key: value.isoformat() if isinstance(value, datetime) else value
        for key, value in row.items()
    }


def queue_names() -> dict[str, str]:
    current = cfg()
    return {
        "mapper_in": current.mapper_input_queue,
        "mapper_out": current.mapper_output_queue,
        "parser_out": current.parser_output_queue,
        "vectorizer_processing": current.vectorizer_processing_queue,
        "vectorizer_failed": current.vectorizer_failed_queue,
        "vectorizer_oversized": current.vectorizer_oversized_queue,
    }


def replay_queue_aliases() -> dict[str, str]:
    current = cfg()
    return {
        "parser": current.mapper_output_queue,
        "mapper_out": current.mapper_output_queue,
        current.mapper_output_queue: current.mapper_output_queue,
    }


def resolve_replay_queue(raw: str | None) -> str:
    if raw is None or raw.strip() == "":
        return cfg().mapper_output_queue
    key = raw.strip()
    aliases = replay_queue_aliases()
    if key not in aliases:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            f"Replay queue non valida: {key}",
        )
    return aliases[key]


def table_exists(name: str) -> bool:
    with db_conn().cursor() as cur:
        cur.execute("SELECT to_regclass(%s) IS NOT NULL AS exists", (name,))
        return bool(cur.fetchone()["exists"])


def table_count(name: str, where: str = "") -> int:
    if not table_exists(name):
        return 0
    query = f"SELECT count(*) AS count FROM {name}"
    if where:
        query += f" WHERE {where}"
    with db_conn().cursor() as cur:
        cur.execute(query)
        return int(cur.fetchone()["count"])


def recent_count(name: str, column: str, minutes: int, where: str = "") -> int:
    if not table_exists(name):
        return 0
    query = f"SELECT count(*) AS count FROM {name} WHERE {column} >= now() - (%s * interval '1 minute')"
    if where:
        query += f" AND ({where})"
    with db_conn().cursor() as cur:
        cur.execute(query, (minutes,))
        return int(cur.fetchone()["count"])


def current_queue_lengths() -> dict[str, int]:
    rdb = redis_client()
    names = queue_names()
    pipe = rdb.pipeline()
    for name in names.values():
        pipe.llen(name)
    lengths = pipe.execute()
    return {key: int(lengths[index]) for index, key in enumerate(names)}


def remember_queue_lengths(lengths: dict[str, int]) -> None:
    now = time.time()
    previous = state.queue_history[-1] if state.queue_history else None
    if previous is None or now - previous.ts >= 1:
        state.queue_history.append(QueueSample(now, dict(lengths)))


def queue_drain_rate_per_min(queue_key: str, current_length: int) -> float:
    now = time.time()
    samples = [sample for sample in state.queue_history if now - sample.ts <= 300]
    if len(samples) < 2:
        return 0.0
    best = 0.0
    latest_ts = samples[-1].ts
    for sample in samples[:-1]:
        previous_length = sample.lengths.get(queue_key, current_length)
        delta = previous_length - current_length
        elapsed = latest_ts - sample.ts
        if delta > 0 and elapsed > 0:
            best = max(best, delta * 60 / elapsed)
    return best


def normalize_domain_key(raw: str) -> str:
    value = raw.strip()
    if not value:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Dominio mancante")
    if "://" in value:
        parsed = urlparse(value)
        if parsed.hostname:
            return parsed.hostname.lower().strip(".")
    return value.strip("/").lower().strip(".")


def domain_candidates(raw: str) -> list[str]:
    requested = normalize_domain_key(raw)
    parts = [part for part in requested.split(".") if part]
    candidates = [requested]
    for index in range(1, max(len(parts) - 1, 1)):
        candidate = ".".join(parts[index:])
        if candidate not in candidates:
            candidates.append(candidate)
    return candidates


def resolve_domain(raw: str) -> tuple[int, str] | None:
    if not table_exists("domains"):
        return None
    with db_conn().cursor() as cur:
        for candidate in domain_candidates(raw):
            cur.execute("SELECT id, domain FROM domains WHERE domain = %s", (candidate,))
            row = cur.fetchone()
            if row:
                return int(row["id"]), str(row["domain"])
    return None


def simple_url(raw: str) -> str:
    value = raw.strip()
    if not value:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "URL mancante")
    if "://" not in value:
        value = f"https://{value}"
    parsed = urlparse(value)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, f"URL non valido: {raw}")
    return value


def format_eta(total: int, rate_per_min: float) -> dict[str, Any]:
    if total <= 0:
        return {"seconds": 0, "label": "nessuna attesa"}
    if rate_per_min <= 0:
        return {"seconds": None, "label": "stima in raccolta"}
    seconds = int((total / rate_per_min) * 60)
    if seconds < 60:
        label = f"{seconds}s"
    elif seconds < 3600:
        label = f"{seconds // 60}m {seconds % 60}s"
    else:
        label = f"{seconds // 3600}h {(seconds % 3600) // 60}m"
    return {"seconds": seconds, "label": label}


def stage_progress(label: str, pending: int, active: int, done_recent: int, rate_per_min: float) -> dict[str, Any]:
    total_open = pending + active
    if total_open == 0:
        percent = 100
    elif done_recent > 0:
        percent = round(done_recent / (done_recent + total_open) * 100)
    elif active > 0:
        percent = 8
    else:
        percent = 0
    return {
        "label": label,
        "pending": pending,
        "active": active,
        "done_recent": done_recent,
        "rate_per_min": round(rate_per_min, 2),
        "percent": max(0, min(percent, 100)),
        "eta": format_eta(total_open, rate_per_min),
    }


def dashboard_payload() -> dict[str, Any]:
    lengths = current_queue_lengths()
    remember_queue_lengths(lengths)

    queues = [
        {
            "key": key,
            "name": name,
            "length": lengths[key],
        }
        for key, name in queue_names().items()
    ]
    counts = {
        "domains": table_count("domains"),
        "urls": table_count("urls"),
        "crawl_runs": table_count("crawl_runs"),
        "running_runs": table_count("crawl_runs", "status = 'running'"),
        "documents": table_count("page_documents"),
        "document_versions": table_count("page_document_versions"),
        "parse_errors": table_count("page_parse_errors"),
    }
    recent_minutes = 10
    mapper_done = recent_count(
        "crawl_runs",
        "finished_at",
        recent_minutes,
        "status IN ('completed', 'interrupted')",
    )
    parser_done = recent_count("page_documents", "last_parsed_at", recent_minutes)
    vectorizer_done_proxy = max(
        0,
        int(queue_drain_rate_per_min("parser_out", lengths["parser_out"]) * recent_minutes),
    )
    progress = [
        stage_progress(
            "Mapper",
            lengths["mapper_in"],
            counts["running_runs"],
            mapper_done,
            max(mapper_done / recent_minutes, queue_drain_rate_per_min("mapper_in", lengths["mapper_in"])),
        ),
        stage_progress(
            "Parser",
            lengths["mapper_out"],
            0,
            parser_done,
            max(parser_done / recent_minutes, queue_drain_rate_per_min("mapper_out", lengths["mapper_out"])),
        ),
        stage_progress(
            "Vectorizer",
            lengths["parser_out"],
            lengths["vectorizer_processing"],
            vectorizer_done_proxy,
            queue_drain_rate_per_min("parser_out", lengths["parser_out"]),
        ),
    ]
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "recent_minutes": recent_minutes,
        "queues": queues,
        "counts": counts,
        "progress": progress,
    }


@app.get("/", response_class=HTMLResponse)
def index() -> str:
    return HTML


@app.get("/api/health")
def health() -> dict[str, Any]:
    try:
        redis_ok = bool(redis_client().ping())
    except RedisError:
        redis_ok = False
    with db_conn().cursor() as cur:
        cur.execute("SELECT 1 AS ok")
        postgres_ok = cur.fetchone()["ok"] == 1
    return {"ok": redis_ok and postgres_ok, "redis": redis_ok, "postgres": postgres_ok}


@app.get("/api/dashboard")
def dashboard() -> dict[str, Any]:
    return dashboard_payload()


@app.get("/api/status")
def status_compat() -> dict[str, Any]:
    payload = dashboard_payload()
    return {
        "queues": {
            queue["key"]: {"name": queue["name"], "length": queue["length"]}
            for queue in payload["queues"]
        },
        "counts": payload["counts"],
        "progress": payload["progress"],
    }


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
    with db_conn().cursor() as cur:
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
def runs(limit: int = Query(30, ge=1, le=500), domain: str | None = None):
    if not table_exists("crawl_runs") or not table_exists("domains"):
        return {"runs": []}
    params: list[Any] = []
    where = ""
    if domain:
        where = "WHERE d.domain = ANY(%s)"
        params.append(domain_candidates(domain))
    params.append(limit)
    with db_conn().cursor() as cur:
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
    values = [simple_url(value) for value in ([request.url] if request.url else []) + request.urls]
    values = list(dict.fromkeys(values))
    if not values:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Inserisci almeno un URL")
    queue = cfg().mapper_input_queue
    length = redis_client().lpush(queue, *values)
    return {"queued": values, "queue": queue, "length": length}


@app.post("/api/replay", status_code=status.HTTP_202_ACCEPTED)
def replay(request: ReplayRequest) -> dict[str, Any]:
    targets = ([request.domain] if request.domain else []) + request.domains
    targets = list(dict.fromkeys([target for target in targets if target]))
    if not targets:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Seleziona almeno un dominio")
    queue = resolve_replay_queue(request.queue)
    results = []
    total = 0
    for target in targets:
        resolved = resolve_domain(target)
        if not resolved:
            results.append({"requested": target, "found": False, "urls": 0})
            continue
        domain_id, domain = resolved
        pushed = replay_urls(domain_id, queue, request.limit_per_domain)
        total += pushed
        results.append({"requested": target, "domain": domain, "found": True, "urls": pushed})
    if total == 0 and not any(result["found"] for result in results):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Nessun dominio selezionato risulta mappato")
    return {"queue": queue, "total_urls": total, "results": results}


def replay_urls(domain_id: int, queue: str, limit: int | None) -> int:
    if not table_exists("urls"):
        return 0
    batch_size = max(cfg().replay_batch_size, 1)
    pushed = 0
    query = "SELECT url FROM urls WHERE domain_id = %s ORDER BY id"
    params: list[Any] = [domain_id]
    if limit is not None:
        query += " LIMIT %s"
        params.append(limit)
    with db_conn().cursor() as cur:
        cur.execute(query, params)
        batch: list[str] = []
        for row in cur:
            batch.append(str(row["url"]))
            if len(batch) >= batch_size:
                redis_client().rpush(queue, *batch)
                pushed += len(batch)
                batch.clear()
        if batch:
            redis_client().rpush(queue, *batch)
            pushed += len(batch)
    return pushed


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
      const response = await fetch(url, {
        method: "POST",
        headers: {"content-type": "application/json"},
        body: JSON.stringify(payload)
      });
      const data = await response.json();
      if (!response.ok) throw new Error(data.detail || response.statusText);
      return data;
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
          fetch("/api/dashboard").then((r) => {
            if (!r.ok) throw new Error("dashboard non disponibile");
            return r.json();
          }),
          fetch("/api/domains?limit=60").then((r) => {
            if (!r.ok) throw new Error("domini non disponibili");
            return r.json();
          }),
          fetch("/api/runs?limit=25").then((r) => {
            if (!r.ok) throw new Error("run non disponibili");
            return r.json();
          }),
          fetch("/api/health").then((r) => {
            if (!r.ok) throw new Error("health non disponibile");
            return r.json();
          })
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
      return postJSON("/api/replay", {domains, limit_per_domain: limit});
    }

    $("refresh").addEventListener("click", refresh);
    $("enqueue-form").addEventListener("submit", async (event) => {
      event.preventDefault();
      const status = $("action-status");
      status.className = "status";
      status.textContent = "Inserimento in corso...";
      try {
        const data = await postJSON("/api/enqueue", {urls: splitLines($("seed-urls").value)});
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
