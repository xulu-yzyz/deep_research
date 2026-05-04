from __future__ import annotations

import os
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import redis as redis_t

_redis: redis_t.Redis | None | bool = False  # False = not tried yet


def get_redis_client() -> redis_t.Redis | None:
    """返回可 ping 通的 Redis 客户端；未配置或失败时返回 None。"""
    global _redis
    if _redis is not False:
        return _redis  # type: ignore[return-value]

    url = os.getenv("REDIS_URL", "").strip()
    enabled = os.getenv("REDIS_ENABLED", "1").strip().lower() not in ("0", "false", "no")
    if not url or not enabled:
        _redis = None
        return None

    try:
        import redis

        r = redis.from_url(url, decode_responses=True, socket_connect_timeout=2, socket_timeout=5)
        r.ping()
        _redis = r
        return r
    except Exception:
        _redis = None
        return None