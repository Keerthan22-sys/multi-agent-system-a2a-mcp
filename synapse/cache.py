# synapse/cache.py — Redis-backed cache with graceful no-op fallback (Day 9).
#
# Usage from any service:
#
#     from synapse.cache import get_cached, set_cached
#
#     cached = get_cached("news", {"query": query})
#     if cached:
#         cached["_cache_hit"] = True
#         return cached
#
#     result = expensive_call(query)
#     set_cached("news", {"query": query}, result, ttl_seconds=300)
#     result["_cache_hit"] = False
#     return result
#
# If Redis is unreachable, get_cached returns None and set_cached returns False,
# so the system gracefully falls back to non-cached behavior.
import hashlib
import json
import os
from typing import Any, Optional

REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379")

# Module-level client; lazily initialized on first use
_client = None
_init_attempted = False


def _get_client():
    global _client, _init_attempted
    if _init_attempted:
        return _client
    _init_attempted = True
    try:
        import redis
        c = redis.from_url(
            REDIS_URL, decode_responses=True, socket_connect_timeout=2
        )
        c.ping()
        _client = c
        print(f"[cache] Redis connected at {REDIS_URL}")
    except ImportError:
        print("[cache] redis package not installed; cache disabled.")
        _client = None
    except Exception as e:
        print(f"[cache] Redis unreachable at {REDIS_URL}; cache disabled. ({e})")
        _client = None
    return _client


def _key(namespace: str, params: dict) -> str:
    """Deterministic cache key from input params."""
    canonical = json.dumps(params, sort_keys=True, default=str)
    digest = hashlib.md5(canonical.encode()).hexdigest()[:16]
    return f"synapse:{namespace}:{digest}"


def get_cached(namespace: str, params: dict) -> Optional[Any]:
    """Return cached value for these params, or None on miss / cache unavailable."""
    client = _get_client()
    if client is None:
        return None
    try:
        raw = client.get(_key(namespace, params))
        return json.loads(raw) if raw else None
    except Exception:
        return None


def set_cached(namespace: str, params: dict, value: Any, ttl_seconds: int) -> bool:
    """Persist value under these params with the given TTL. Returns True on success."""
    client = _get_client()
    if client is None:
        return False
    try:
        client.setex(
            _key(namespace, params),
            ttl_seconds,
            json.dumps(value, default=str),
        )
        return True
    except Exception:
        return False


def invalidate(namespace: str, params: dict) -> bool:
    """Delete a specific cache entry. Returns True if a key was removed."""
    client = _get_client()
    if client is None:
        return False
    try:
        return bool(client.delete(_key(namespace, params)))
    except Exception:
        return False


def stats() -> dict:
    """Best-effort overall cache statistics from Redis INFO."""
    client = _get_client()
    if client is None:
        return {"available": False}
    try:
        info = client.info()
        return {
            "available": True,
            "total_keys": client.dbsize(),
            "memory_human": info.get("used_memory_human", "?"),
            "hits": int(info.get("keyspace_hits", 0)),
            "misses": int(info.get("keyspace_misses", 0)),
        }
    except Exception as e:
        return {"available": False, "error": str(e)}


# Default TTLs by data freshness — services can override these per-call.
TTL = {
    "news": 300,          # 5 min — news churns fast
    "weather": 600,       # 10 min — weather stable enough
    "fx": 600,            # 10 min — FX moves slowly
    "media": 3600,        # 1 hour — stock images don't change
    "router_tools": 3600, # 1 hour — same topic, same routing
    "city": 3600,         # 1 hour — same topic, same capital
}