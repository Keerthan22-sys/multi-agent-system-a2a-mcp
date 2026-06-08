#!/usr/bin/env bash
# Starts Phoenix + all MCP tool servers + agents required by the SYNAPSE UI.
# From repo root after: source .venv/bin/activate && pip install -r requirements.txt && pip install -e .

set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

if [[ -f .venv/bin/activate ]]; then
  # shellcheck source=/dev/null
  source .venv/bin/activate
fi

trap 'kill 0' EXIT

# Day 6: launch Phoenix first so the other services have somewhere to send traces.
# Phoenix UI + OTLP HTTP collector on port 6006.
echo "[start] Launching Phoenix on http://localhost:6006 ..."
phoenix serve &
PHOENIX_PID=$!
sleep 3  # give Phoenix a moment to bind ports before everything else starts

# Tool MCP servers
python mcp-servers/world-data/server.py &
python mcp-servers/finance-monitor/server.py &
python mcp-servers/media-engine/server.py &
python mcp-servers/memory/server.py &         # Day 3
python mcp-servers/conversation/server.py &   # Day 4
python mcp-servers/router/server.py &         # Day 5

# Agents
python agents/contextualist_agent/main.py &
python agents/scout_agent/main.py &
python agents/publisher_agent/main.py &

echo "[start] All services launched. Phoenix UI at http://localhost:6006"
wait