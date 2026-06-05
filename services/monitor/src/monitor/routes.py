from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Query, Request, Response, status
from fastapi.responses import HTMLResponse, RedirectResponse
from redis.exceptions import RedisError

from monitor.auth import COOKIE_NAME, make_session, require_admin, verify_password
from monitor.schemas import EnqueueRequest, LoginRequest, ReplayRequest
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
api = APIRouter(prefix="/api/v1", dependencies=[Depends(require_admin)])

LOGIN_HTML = """
<!doctype html>
<html lang="en">
<head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><title>UniCrawler Login</title>
<style>body{margin:0;min-height:100vh;display:grid;place-items:center;font:14px system-ui;background:#f5f7f9;color:#182230}form{width:min(360px,calc(100vw - 32px));display:grid;gap:12px;padding:22px;border:1px solid #d6dde6;border-radius:8px;background:#fff}input,button{min-height:40px;border-radius:6px;border:1px solid #d6dde6;padding:8px 10px;font:inherit}button{background:#0d766e;color:#fff;font-weight:700;border-color:#0d766e}h1,p{margin:0}.error{color:#b42318}</style></head>
<body><form method="post" action="/login"><h1>UniCrawler</h1><p>Admin access</p><input name="username" autocomplete="username" placeholder="Username"><input name="password" type="password" autocomplete="current-password" placeholder="Password"><button type="submit">Sign in</button></form></body>
</html>
"""


@router.get("/", response_class=HTMLResponse)
def index(_: str = Depends(require_admin)) -> str:
    return HTML


@router.get("/login", response_class=HTMLResponse)
def login_page() -> str:
    return LOGIN_HTML


@router.post("/login")
async def login(request: Request) -> Response:
    form = await request.form()
    data = LoginRequest(username=str(form.get("username", "")), password=str(form.get("password", "")))
    if data.username != db_cfg_username() or not verify_password(data.password):
        return HTMLResponse(LOGIN_HTML, status_code=status.HTTP_401_UNAUTHORIZED)
    response = RedirectResponse("/", status_code=status.HTTP_303_SEE_OTHER)
    response.set_cookie(
        COOKIE_NAME,
        make_session(data.username),
        httponly=True,
        secure=login_cookie_secure(),
        samesite="lax",
        max_age=12 * 60 * 60,
    )
    return response


def db_cfg_username() -> str:
    from monitor.state import cfg

    return cfg().admin_username


def login_cookie_secure() -> bool:
    from monitor.state import cfg

    return cfg().secure_cookies


@router.post("/logout")
def logout() -> Response:
    response = RedirectResponse("/login", status_code=status.HTTP_303_SEE_OTHER)
    response.delete_cookie(COOKIE_NAME)
    return response


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


@api.get("/dashboard")
def dashboard() -> dict[str, Any]:
    return dashboard_payload()


@api.get("/status")
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


@api.get("/domains")
def domains(limit: int = Query(50, ge=1, le=500), offset: int = Query(0, ge=0)):
    return {"domains": list_domains(limit, offset)}


@api.get("/runs")
def runs(limit: int = Query(30, ge=1, le=500), domain: str | None = None):
    return {"runs": list_runs(limit, domain)}


@api.post("/enqueue", status_code=status.HTTP_202_ACCEPTED)
def enqueue(request: EnqueueRequest) -> dict[str, Any]:
    raw_values = ([request.url] if request.url else []) + request.urls
    return enqueue_urls(raw_values)


@api.post("/replay", status_code=status.HTTP_202_ACCEPTED)
def replay(request: ReplayRequest) -> dict[str, Any]:
    targets = ([request.domain] if request.domain else []) + request.domains
    queue = resolve_replay_queue(request.queue)
    return replay_domains(targets, queue, request.limit_per_domain)


router.include_router(api)
