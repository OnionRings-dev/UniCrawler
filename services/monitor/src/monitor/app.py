from __future__ import annotations

from contextlib import asynccontextmanager

import psycopg
from fastapi import FastAPI
from psycopg.rows import dict_row
from redis import Redis

from monitor.config import load_config, redis_connection_settings
from monitor.routes import router
from monitor.state import state


@asynccontextmanager
async def lifespan(app: FastAPI):
    config = load_config()
    state.cfg = config
    state.redis = Redis(
        **redis_connection_settings(config),
        decode_responses=True,
        health_check_interval=30,
        socket_connect_timeout=3,
        socket_timeout=5,
    )
    state.db = psycopg.connect(config.postgres_dsn, row_factory=dict_row, connect_timeout=5)
    try:
        yield
    finally:
        if state.db is not None:
            state.db.close()
        if state.redis is not None:
            state.redis.close()


def create_app() -> FastAPI:
    app = FastAPI(title="UniCrawler Monitor", version="1.0.0", lifespan=lifespan)
    app.include_router(router)
    return app


app = create_app()
