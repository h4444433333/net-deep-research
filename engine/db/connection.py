from __future__ import annotations

"""
PostgreSQL 连接管理。

当前模块既要兼容历史单库写法，也要为后续真实读写分离与逻辑分库提供稳定入口：
- `get_write_connection()`：显式写路径
- `get_read_connection()`：显式读路径
- `get_connection()`：历史别名，保持写库语义

默认行为：
- 写路径使用主写库配置 `DB_*`
- 读路径优先使用 `DB_READ_*`
- 若角色级或读库级配置缺失，则安全回退到可用的上级配置
"""

import os
from contextlib import contextmanager
from typing import Literal

from psycopg2 import pool as _pool_mod

def _env(key: str, default: str = "") -> str:
    return os.environ.get(key, default).strip()


_min = int(_env("DB_POOL_MIN", "1"))
_max = int(_env("DB_POOL_MAX", "5"))

DbIntent = Literal["read", "write"]
DbConsistency = Literal["eventual", "strong"]
DbRole = Literal["primary", "process", "content", "analytics"]

_POOLS: dict[tuple[str, str, str], _pool_mod.ThreadedConnectionPool] = {}
_DB_KEYS = ("host", "port", "user", "password", "dbname")
_ROLE_PREFIX = {
    "primary": "",
    "process": "DB_PROCESS_",
    "content": "DB_CONTENT_",
    "analytics": "DB_ANALYTICS_",
}


def _keepalive_config() -> dict[str, int]:
    # 让空闲连接通过 TCP keepalive 保活，避免代理层（如 PolarDB RWLB）掐掉空闲连接
    return {
        "keepalives": 1,
        "keepalives_idle": 30,
        "keepalives_interval": 10,
        "keepalives_count": 5,
    }


def _primary_write_config() -> dict[str, str | int]:
    return {
        "host": _env("DB_HOST", "127.0.0.1"),
        "port": _env("DB_PORT", "5432"),
        "user": _env("DB_USER", "postgres"),
        "password": _env("DB_PASSWORD", ""),
        "dbname": _env("DB_NAME", "db_reputation"),
        "connect_timeout": 5,
        **_keepalive_config(),
    }


def _prefixed_db_config(prefix: str) -> dict[str, str | int] | None:
    if not prefix:
        return _primary_write_config()

    host = _env(f"{prefix}HOST")
    if not host:
        return None

    return {
        "host": host,
        "port": _env(f"{prefix}PORT", "5432"),
        "user": _env(f"{prefix}USER", "postgres"),
        "password": _env(f"{prefix}PASSWORD", ""),
        "dbname": _env(f"{prefix}NAME", "db_reputation"),
        "connect_timeout": 5,
        **_keepalive_config(),
    }


def _read_prefix_for_role(role: DbRole) -> str:
    base = _ROLE_PREFIX[role]
    return "DB_READ_" if role == "primary" else f"{base}READ_"


def _pool_snapshot(pool: _pool_mod.ThreadedConnectionPool | None) -> dict[str, int | None]:
    if pool is None:
        return {"minconn": _min, "maxconn": _max, "used": None, "idle": None}
    used = getattr(pool, "_used", None)
    idle = getattr(pool, "_pool", None)
    return {
        "minconn": getattr(pool, "minconn", _min),
        "maxconn": getattr(pool, "maxconn", _max),
        "used": len(used) if used is not None else None,
        "idle": len(idle) if idle is not None else None,
    }


def _log_pool_issue(node: str, message: str, exc: Exception, *, data: dict | None = None) -> None:
    try:
        from utils.request_trace import log_trace_exception
    except Exception:
        return
    log_trace_exception(node, exc, message=message, data=data)


def _log_route(route: dict[str, object]) -> None:
    try:
        from utils.request_trace import log_trace_node
    except Exception:
        return
    log_trace_node(
        "db.route",
        "database route resolved",
        data=route,
    )


def _log_pool_acquired(route: dict[str, object], pool: _pool_mod.ThreadedConnectionPool, conn) -> None:
    try:
        from utils.request_trace import log_trace_node
    except Exception:
        return
    log_trace_node(
        "db.pool.acquire.ok",
        "database connection acquired",
        data={
            **route,
            "pool": _pool_snapshot(pool),
            "connection_closed": bool(getattr(conn, "closed", 0)),
        },
    )


def _log_pool_initialized(route: dict[str, object], pool: _pool_mod.ThreadedConnectionPool) -> None:
    try:
        from utils.request_trace import log_trace_node
    except Exception:
        return
    log_trace_node(
        "db.pool.init.ok",
        "database connection pool initialized",
        data={
            **route,
            "pool": _pool_snapshot(pool),
        },
    )


def _route_chain(role: DbRole, intent: DbIntent, consistency: DbConsistency) -> list[tuple[DbRole, DbIntent, str]]:
    if intent == "write" or consistency == "strong":
        fallback_reason = "strong_consistency" if intent == "read" and consistency == "strong" else "write_path"
        return [
            (role, "write", fallback_reason),
            ("primary", "write", "role_fallback_to_primary"),
        ]

    return [
        (role, "read", "requested_read_path"),
        (role, "write", "read_pool_missing_fallback_to_role_write"),
        ("primary", "read", "role_fallback_to_primary_read"),
        ("primary", "write", "read_pool_missing_fallback_to_primary_write"),
    ]


def _config_for_route(role: DbRole, intent: DbIntent) -> dict[str, str | int] | None:
    if intent == "write":
        return _prefixed_db_config(_ROLE_PREFIX[role])
    return _prefixed_db_config(_read_prefix_for_role(role))


def _resolve_route(
    *,
    role: DbRole,
    intent: DbIntent,
    consistency: DbConsistency,
    reason: str | None = None,
) -> tuple[tuple[str, str, str], dict[str, object]]:
    chain = _route_chain(role, intent, consistency)
    for candidate_role, candidate_intent, fallback_reason in chain:
        config = _config_for_route(candidate_role, candidate_intent)
        if config is None:
            continue
        route = {
            "requested_role": role,
            "requested_intent": intent,
            "consistency": consistency,
            "resolved_role": candidate_role,
            "resolved_intent": candidate_intent,
            "fallback": candidate_role != role or candidate_intent != intent,
            "fallback_reason": fallback_reason if (candidate_role != role or candidate_intent != intent) else None,
            "reason": reason,
            "target_host": config["host"],
            "target_db": config["dbname"],
        }
        return (candidate_role, candidate_intent, fallback_reason), route
    raise RuntimeError(f"no database config available for role={role} intent={intent}")


def _get_pool(route_key: tuple[str, str, str], route: dict[str, object]):
    pool = _POOLS.get(route_key)
    if pool is None:
        role, intent, _ = route_key
        config = _config_for_route(role, intent)
        if config is None:
            raise RuntimeError(f"database config missing for role={role} intent={intent}")
        try:
            pool = _pool_mod.ThreadedConnectionPool(_min, _max, **config)
        except Exception as exc:
            _log_pool_issue(
                "db.pool.init",
                "database connection pool initialization failed",
                exc,
                data={
                    **route,
                    "pool": _pool_snapshot(None),
                },
            )
            raise
        _POOLS[route_key] = pool
        _log_pool_initialized(route, pool)
    return pool


def _probe_connection(conn) -> None:
    """复用连接前用 SELECT 1 探活，确认未被服务端/代理层断开。"""
    with conn.cursor() as cur:
        cur.execute("SELECT 1")
        cur.fetchone()


def _discard_connection(pool: _pool_mod.ThreadedConnectionPool, conn) -> None:
    """关闭并丢弃一个已失效的连接，不再归还连接池。"""
    try:
        pool.putconn(conn, close=True)
    except Exception:
        try:
            conn.close()
        except Exception:
            pass


def _acquire_healthy_conn(pool: _pool_mod.ThreadedConnectionPool, route: dict[str, object]):
    """从池中取出经探活的可用连接，自动剔除 stale connection。"""
    last_exc: Exception | None = None
    for _ in range(max(1, _max)):
        conn = pool.getconn()
        conn.autocommit = True
        try:
            _probe_connection(conn)
            return conn
        except Exception as exc:
            last_exc = exc
            _log_pool_issue(
                "db.pool.stale",
                "stale database connection detected; discarding and re-acquiring",
                exc,
                data={**route, "pool": _pool_snapshot(pool)},
            )
            _discard_connection(pool, conn)
    raise last_exc if last_exc is not None else RuntimeError(
        "failed to acquire a healthy database connection"
    )


@contextmanager
def get_db_connection(
    *,
    role: DbRole = "primary",
    intent: DbIntent = "write",
    consistency: DbConsistency = "eventual",
    reason: str | None = None,
):
    """按读写意图、逻辑库角色与一致性策略获取连接。"""
    route_key, route = _resolve_route(
        role=role,
        intent=intent,
        consistency=consistency,
        reason=reason,
    )
    _log_route(route)
    pool = _get_pool(route_key, route)
    try:
        conn = _acquire_healthy_conn(pool, route)
    except Exception as exc:
        _log_pool_issue(
            "db.pool.acquire",
            "database connection acquire failed",
            exc,
            data={**route, "pool": _pool_snapshot(pool)},
        )
        raise
    conn.autocommit = True
    _log_pool_acquired(route, pool, conn)
    try:
        yield conn
    finally:
        try:
            pool.putconn(conn)
        except Exception as exc:
            _log_pool_issue(
                "db.pool.release",
                "database connection release failed; connection closed instead",
                exc,
                data={
                    **route,
                    "pool": _pool_snapshot(pool),
                    "connection_closed": bool(getattr(conn, "closed", 0)),
                },
            )
            try:
                conn.close()
            except Exception as close_exc:
                _log_pool_issue(
                    "db.pool.release.close",
                    "database connection close after release failure failed",
                    close_exc,
                    data={**route, "connection_closed": bool(getattr(conn, "closed", 0))},
                )


def get_write_connection(*, role: DbRole = "primary", reason: str | None = None):
    return get_db_connection(role=role, intent="write", consistency="strong", reason=reason)


def get_read_connection(
    *,
    role: DbRole = "primary",
    consistency: DbConsistency = "eventual",
    reason: str | None = None,
):
    return get_db_connection(role=role, intent="read", consistency=consistency, reason=reason)


def get_connection():
    """历史兼容入口：默认仍指向主写库。"""
    return get_write_connection(role="primary", reason="legacy_default_connection")


@contextmanager
def get_write_transaction(*, role: DbRole = "primary", reason: str | None = None):
    """跨表原子落库用的事务写连接。

    与 `get_write_connection` 的区别：连接以 `autocommit=False` 打开，
    正常 yield 结束后统一 `commit`；若 yield 块内抛出异常则 `rollback`
    并继续向上抛出，保证「要么全部落库、要么全部不落库」。
    """
    route_key, route = _resolve_route(
        role=role,
        intent="write",
        consistency="strong",
        reason=reason,
    )
    _log_route(route)
    pool = _get_pool(route_key, route)
    try:
        conn = _acquire_healthy_conn(pool, route)
    except Exception as exc:
        _log_pool_issue(
            "db.pool.acquire",
            "database connection acquire failed",
            exc,
            data={**route, "pool": _pool_snapshot(pool)},
        )
        raise

    conn.autocommit = False
    _log_pool_acquired(route, pool, conn)
    try:
        yield conn
        conn.commit()
    except Exception:
        try:
            conn.rollback()
        except Exception as rollback_exc:
            _log_pool_issue(
                "db.transaction.rollback",
                "transaction rollback failed",
                rollback_exc,
                data={**route, "pool": _pool_snapshot(pool)},
            )
        raise
    finally:
        try:
            conn.autocommit = True
        except Exception:
            pass
        try:
            pool.putconn(conn)
        except Exception as exc:
            _log_pool_issue(
                "db.pool.release",
                "database connection release failed; connection closed instead",
                exc,
                data={
                    **route,
                    "pool": _pool_snapshot(pool),
                    "connection_closed": bool(getattr(conn, "closed", 0)),
                },
            )
            try:
                conn.close()
            except Exception as close_exc:
                _log_pool_issue(
                    "db.pool.release.close",
                    "database connection close after release failure failed",
                    close_exc,
                    data={**route, "connection_closed": bool(getattr(conn, "closed", 0))},
                )
