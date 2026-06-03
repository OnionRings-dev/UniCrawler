from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field

import psycopg
from redis import Redis

from monitor.config import Config


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
