# synapse/config.py — Centralized service URL resolution (Day 11).
#
# Every service URL lives here. Each host is read from an env var with
# "0.0.0.0" as the default — so the same code runs in:
#   - Local dev (no env vars set → 0.0.0.0 used → matches the file-based start script)
#   - Docker Compose (env vars set to service names → containers find each other)
#   - Kubernetes (env vars set via ConfigMap, future Day 12)
#
# Usage in any service file:
#     from synapse.config import SCOUT_URL, PUBLISHER_URL, MEMORY_URL
import os


def _url(host_env: str, default_host: str, port: int) -> str:
    """Build an MCP URL from an env var + port."""
    host = os.getenv(host_env, default_host).strip()
    return f"http://{host}:{port}/mcp"


# ---------- Tool MCP servers ----------
WORLD_DATA_URL   = _url("WORLD_DATA_HOST",   "0.0.0.0", 8001)
FINANCE_URL      = _url("FINANCE_HOST",      "0.0.0.0", 8002)
MEDIA_URL        = _url("MEDIA_HOST",        "0.0.0.0", 8003)
MEMORY_URL       = _url("MEMORY_HOST",       "0.0.0.0", 8006)
CONVERSATION_URL = _url("CONVERSATION_HOST", "0.0.0.0", 8007)
ROUTER_URL       = _url("ROUTER_HOST",       "0.0.0.0", 8008)
EVAL_URL         = _url("EVAL_HOST",         "0.0.0.0", 8009)
CRITIC_URL       = _url("CRITIC_HOST",       "0.0.0.0", 8010)

# ---------- Agent MCP servers ----------
CONTEXTUALIST_URL = _url("CONTEXTUALIST_HOST", "0.0.0.0", 8000)
SCOUT_URL         = _url("SCOUT_HOST",         "0.0.0.0", 8004)
PUBLISHER_URL     = _url("PUBLISHER_HOST",     "0.0.0.0", 8005)

# ---------- Infrastructure (already env-var-aware in their respective modules) ----------
PHOENIX_ENDPOINT = os.getenv("PHOENIX_COLLECTOR_ENDPOINT", "http://localhost:6006")
REDIS_URL        = os.getenv("REDIS_URL", "redis://localhost:6379")