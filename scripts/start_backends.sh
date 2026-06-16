#!/usr/bin/env bash
# Starts Phoenix + all MCP tool servers + agents for the SYNAPSE UI.

set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

if [[ -f .venv/bin/activate ]]; then
  source .venv/bin/activate
fi

trap 'kill 0' EXIT

# Day 9: warn if Redis isn't reachable (cache will silently no-op)
echo "[start] Checking Redis at localhost:6379 ..."
if command -v redis-cli >/dev/null 2>&1; then
  if redis-cli ping >/dev/null 2>&1; then
    echo "[start] Redis OK — cache enabled"
  else
    echo "[start] ⚠️  Redis not responding. Cache will be disabled."
    echo "         Start it with: brew services start redis"
    echo "                   or:  sudo systemctl start redis-server"
    echo "                   or:  docker run -d -p 6379:6379 redis"
  fi
else
  echo "[start] ⚠️  redis-cli not found. Skipping Redis check."
fi

# Phoenix (Day 6)
echo "[start] Launching Phoenix on http://localhost:6006 ..."
phoenix serve &
sleep 3

# Tool MCP servers
python mcp-servers/world-data/server.py &
python mcp-servers/finance-monitor/server.py &
python mcp-servers/media-engine/server.py &
python mcp-servers/memory/server.py &         # Day 3
python mcp-servers/conversation/server.py &   # Day 4
python mcp-servers/router/server.py &         # Day 5
python mcp-servers/eval/server.py &           # Day 7
python mcp-servers/critic/server.py &         # Day 8

# Agents
python agents/contextualist_agent/main.py &
python agents/scout_agent/main.py &
python agents/publisher_agent/main.py &

echo "[start] All services launched. Phoenix UI at http://localhost:6006"
echo "[start] Streamlit:  streamlit run ui/app.py"
echo "[start] Cache:      redis-cli monitor   (watch cache traffic live)"
wait