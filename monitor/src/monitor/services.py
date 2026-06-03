from __future__ import annotations

import time
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlparse

from fastapi import HTTPException, status

from monitor.state import QueueSample, cfg, db_conn, redis_client, state


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
            max(
                mapper_done / recent_minutes,
                queue_drain_rate_per_min("mapper_in", lengths["mapper_in"]),
            ),
        ),
        stage_progress(
            "Parser",
            lengths["mapper_out"],
            0,
            parser_done,
            max(
                parser_done / recent_minutes,
                queue_drain_rate_per_min("mapper_out", lengths["mapper_out"]),
            ),
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


def list_domains(limit: int, offset: int) -> list[dict[str, Any]]:
    if not table_exists("domains"):
        return []
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
        return [row_to_json(row) for row in cur.fetchall()]


def list_runs(limit: int, domain: str | None = None) -> list[dict[str, Any]]:
    if not table_exists("crawl_runs") or not table_exists("domains"):
        return []
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
        return [row_to_json(row) for row in cur.fetchall()]


def enqueue_urls(raw_values: list[str]) -> dict[str, Any]:
    values = [simple_url(value) for value in raw_values]
    values = list(dict.fromkeys(values))
    if not values:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Inserisci almeno un URL")
    queue = cfg().mapper_input_queue
    length = redis_client().lpush(queue, *values)
    return {"queued": values, "queue": queue, "length": length}


def replay_domains(targets: list[str], queue: str, limit_per_domain: int | None) -> dict[str, Any]:
    targets = list(dict.fromkeys([target for target in targets if target]))
    if not targets:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Seleziona almeno un dominio")
    results = []
    total = 0
    for target in targets:
        resolved = resolve_domain(target)
        if not resolved:
            results.append({"requested": target, "found": False, "urls": 0})
            continue
        domain_id, domain = resolved
        pushed = replay_urls(domain_id, queue, limit_per_domain)
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
