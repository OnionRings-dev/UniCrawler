from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Query, status
from fastapi.responses import HTMLResponse
from redis.exceptions import RedisError

from monitor.schemas import EnqueueRequest, ReplayRequest
from monitor.services import (
    dashboard_payload,
    enqueue_urls,
    list_domains,
    list_runs,
    replay_domains,
    resolve_replay_queue,
)
from monitor.state import db_conn, redis_client
from monitor.ui import HTML

router = APIRouter()


@router.get("/", response_class=HTMLResponse)
def index() -> str:
    return HTML


@router.get("/api/health")
def health() -> dict[str, Any]:
    try:
        redis_ok = bool(redis_client().ping())
    except RedisError:
        redis_ok = False
    with db_conn().cursor() as cur:
        cur.execute("SELECT 1 AS ok")
        postgres_ok = cur.fetchone()["ok"] == 1
    return {"ok": redis_ok and postgres_ok, "redis": redis_ok, "postgres": postgres_ok}


@router.get("/api/dashboard")
def dashboard() -> dict[str, Any]:
    return dashboard_payload()


@router.get("/api/status")
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


@router.get("/api/domains")
def domains(limit: int = Query(50, ge=1, le=500), offset: int = Query(0, ge=0)):
    return {"domains": list_domains(limit, offset)}


@router.get("/api/runs")
def runs(limit: int = Query(30, ge=1, le=500), domain: str | None = None):
    return {"runs": list_runs(limit, domain)}


@router.post("/api/enqueue", status_code=status.HTTP_202_ACCEPTED)
def enqueue(request: EnqueueRequest) -> dict[str, Any]:
    raw_values = ([request.url] if request.url else []) + request.urls
    return enqueue_urls(raw_values)


@router.post("/api/replay", status_code=status.HTTP_202_ACCEPTED)
def replay(request: ReplayRequest) -> dict[str, Any]:
    targets = ([request.domain] if request.domain else []) + request.domains
    queue = resolve_replay_queue(request.queue)
    return replay_domains(targets, queue, request.limit_per_domain)
