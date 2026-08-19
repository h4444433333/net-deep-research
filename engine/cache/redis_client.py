"""
Redis 分层访问。

当前将 Redis 分成两层：
  - cache redis: 只放可淘汰缓存
  - state redis: 放限流、待聚合计数等关键短期状态

缓存策略:
  - key: "{namespace}:cache:source:{domain}" → Hash
  - TTL: 默认 3600 秒
  - 写穿透: 查询时缓存 Miss 则查 DB 并回写缓存

状态策略:
  - 投票计数器: "{namespace}:state:vote:pending:{source_id}:{vote_type}" → 整数
  - 限流计数器: "{namespace}:state:rate:{scope}:{identity}" → 整数

环境变量:
  REDIS_CACHE_HOST / REDIS_CACHE_PORT / REDIS_CACHE_PASSWORD
  REDIS_STATE_HOST / REDIS_STATE_PORT / REDIS_STATE_PASSWORD
  兼容回退:
    REDIS_HOST / REDIS_PORT / REDIS_PASSWORD
  REDIS_NAMESPACE: key 命名空间，避免和同实例其他业务冲突
  REDIS_SOURCE_CACHE_TTL_SECONDS: 信源缓存 TTL
  REDIS_VOTE_PENDING_TTL_SECONDS: 投票 pending key TTL
"""

import os

import redis

_SOURCE_HASH_REQUIRED_FIELDS = frozenset(
    {
        "canonical_url",
        "docs_path",
        "release_path",
        "freshness_url",
        "reputation_score",
        "confidence",
        "authority_base",
        "category",
        "subcategory",
        "status",
        "trust_votes",
        "untrust_votes",
        "security_risk",
        "ssl_valid",
        "xss_flagged",
        "sb_flagged",
    }
)


def _env(key: str, default: str = "") -> str:
    return os.environ.get(key, default).strip()


def _env_int(key: str, default: int) -> int:
    raw = _env(key, str(default))
    try:
        return int(raw)
    except ValueError:
        return default


def _build_redis_config(role: str) -> dict:
    host = _env(f"REDIS_{role}_HOST", _env("REDIS_HOST", "127.0.0.1"))
    port = _env_int(f"REDIS_{role}_PORT", _env_int("REDIS_PORT", 6379))
    password = _env(f"REDIS_{role}_PASSWORD", _env("REDIS_PASSWORD"))
    config = {
        "host": host,
        "port": port,
        "password": password,
        "decode_responses": True,
        "socket_connect_timeout": 2,
        "socket_timeout": 2,
    }
    if not config["password"]:
        config.pop("password")
    return config

_REDIS_NAMESPACE = _env("REDIS_NAMESPACE", "netinfo")
_SOURCE_CACHE_PREFIX = _env("REDIS_SOURCE_CACHE_PREFIX", f"{_REDIS_NAMESPACE}:cache:source")
_VOTE_PENDING_PREFIX = _env("REDIS_VOTE_PENDING_PREFIX", f"{_REDIS_NAMESPACE}:state:vote:pending")
_RATE_LIMIT_PREFIX = _env("REDIS_RATE_LIMIT_PREFIX", f"{_REDIS_NAMESPACE}:state:rate")
_SOURCE_CACHE_TTL_SECONDS = max(60, _env_int("REDIS_SOURCE_CACHE_TTL_SECONDS", 3600))
_VOTE_PENDING_TTL_SECONDS = max(300, _env_int("REDIS_VOTE_PENDING_TTL_SECONDS", 86400))

_cache_client: redis.Redis | None = None
_state_client: redis.Redis | None = None


def get_cache_client() -> redis.Redis:
    global _cache_client
    if _cache_client is None:
        _cache_client = redis.Redis(**_build_redis_config("CACHE"))
    return _cache_client


def get_state_client() -> redis.Redis:
    global _state_client
    if _state_client is None:
        _state_client = redis.Redis(**_build_redis_config("STATE"))
    return _state_client


def _smallint_or_none(value: str | None) -> int | None:
    if value in (None, "", "None"):
        return None
    return int(value)


def _source_cache_key(domain: str) -> str:
    return f"{_SOURCE_CACHE_PREFIX}:{domain}"


def _vote_pending_key(source_id: int, vote_type: str) -> str:
    return f"{_VOTE_PENDING_PREFIX}:{source_id}:{vote_type}"


def build_rate_limit_key(scope: str, identity: str) -> str:
    return f"{_RATE_LIMIT_PREFIX}:{scope}:{identity}"


def _security_payload(data: dict[str, str]) -> dict:
    return {
        "risk": int(data.get("security_risk", 0)),
        "ssl_valid": _smallint_or_none(data.get("ssl_valid")),
        "xss_flagged": int(data.get("xss_flagged", 0)),
        "sb_flagged": int(data.get("sb_flagged", 0)),
    }


def _source_payload(domain: str, data: dict[str, str]) -> dict:
    payload = {
        "domain": domain,
        "canonical_url": data.get("canonical_url") or None,
        "docs_path": data.get("docs_path") or None,
        "release_path": data.get("release_path") or None,
        "freshness_url": data.get("freshness_url") or None,
        "reputation_score": float(data.get("reputation_score", 1.0)),
        "confidence": float(data.get("confidence", 0.0)),
        "authority_base": int(data.get("authority_base", 1)),
        "category": data.get("category", ""),
        "subcategory": data.get("subcategory", ""),
        "status": data.get("status", "active"),
        "trust_votes": int(data.get("trust_votes", 0)),
        "untrust_votes": int(data.get("untrust_votes", 0)),
        "security_risk": int(data.get("security_risk", 0)),
        "ssl_valid": _smallint_or_none(data.get("ssl_valid")),
        "xss_flagged": int(data.get("xss_flagged", 0)),
        "sb_flagged": int(data.get("sb_flagged", 0)),
    }
    payload["security"] = _security_payload(data)
    return payload


def cache_source(domain: str, data: dict, ttl: int | None = None) -> None:
    """将信源信息写入缓存。"""
    try:
        key = _source_cache_key(domain)
        mapping = {}
        for key_name in _SOURCE_HASH_REQUIRED_FIELDS:
            value = data.get(key_name)
            if value is None and key_name == "ssl_valid":
                mapping[key_name] = ""
            elif value is None:
                mapping[key_name] = ""
            else:
                mapping[key_name] = str(value)
        client = get_cache_client()
        client.hset(key, mapping=mapping)
        client.expire(key, ttl or _SOURCE_CACHE_TTL_SECONDS)
    except Exception:
        pass  # 缓存失败不阻塞业务


def get_cached_source(domain: str) -> dict | None:
    """从缓存读取信源信息。返回 dict 或 None。"""
    try:
        data = get_cache_client().hgetall(_source_cache_key(domain))
        if not data:
            return None
        if not _SOURCE_HASH_REQUIRED_FIELDS.issubset(data.keys()):
            return None
        return _source_payload(domain, data)
    except Exception:
        return None


def incr_vote_pending(source_id: int, vote_type: str) -> int:
    """投票计数器 +1。返回当前计数值。

    该计数器属于投票聚合主链路的一部分，Redis 故障时必须抛错，
    由上层显式返回失败，而不是静默降级到“看似成功但未完成聚合”。
    """
    key = _vote_pending_key(source_id, vote_type)
    pipe = get_state_client().pipeline()
    pipe.incr(key)
    # 每次递增都续命，避免低频 pending key 长期滞留。
    pipe.expire(key, _VOTE_PENDING_TTL_SECONDS)
    results = pipe.execute()
    return int(results[0])


def list_pending_vote_source_ids(limit: int | None = None) -> list[int]:
    """扫描 Redis 中的 dirty key，返回待刷回的 source_id 列表。"""
    try:
        source_ids: list[int] = []
        seen: set[int] = set()
        for key in get_state_client().scan_iter(match=f"{_VOTE_PENDING_PREFIX}:*"):
            parts = key.split(":")
            if len(parts) < 5:
                continue
            try:
                source_id = int(parts[-2])
            except ValueError:
                continue
            if source_id in seen:
                continue
            seen.add(source_id)
            source_ids.append(source_id)
            if limit is not None and len(source_ids) >= limit:
                break
        return source_ids
    except Exception:
        return []


def clear_pending_votes(source_id: int) -> None:
    """清理某个 source 的 pending 计数。"""
    try:
        r = get_state_client()
        r.delete(_vote_pending_key(source_id, "trust"), _vote_pending_key(source_id, "untrust"))
    except Exception:
        pass


def get_pending_votes(source_id: int) -> dict:
    """读取某个 source 当前待刷回的投票计数，不做清零。"""
    try:
        r = get_state_client()
        return {
            "trust": int(r.get(_vote_pending_key(source_id, "trust")) or 0),
            "untrust": int(r.get(_vote_pending_key(source_id, "untrust")) or 0),
        }
    except Exception:
        return {"trust": 0, "untrust": 0}


def get_and_reset_pending_votes(source_id: int) -> dict:
    """获取并清零待刷入 DB 的投票计数。返回 {"trust": N, "untrust": M}。"""
    try:
        r = get_state_client()
        pipe = r.pipeline()
        for vt in ("trust", "untrust"):
            key = _vote_pending_key(source_id, vt)
            pipe.get(key)
            pipe.delete(key)
        results = pipe.execute()
        trust = int(results[0] or 0)
        untrust = int(results[2] or 0)
        return {"trust": trust, "untrust": untrust}
    except Exception:
        return {"trust": 0, "untrust": 0}


def check_rate_limit(key: str, max_requests: int, window_seconds: int) -> bool:
    """滑动窗口限流。返回 True 表示允许通过，False 表示被限流。

    key:      限流键，如 "rate:voter:<hash>"
    max_requests: 窗口内最大请求数
    window_seconds: 窗口秒数
    Redis 故障时直接抛错，由调用方决定如何显式失败。
    """
    r = get_state_client()
    current = r.incr(key)
    if current == 1:
        r.expire(key, window_seconds)
    return current <= max_requests
