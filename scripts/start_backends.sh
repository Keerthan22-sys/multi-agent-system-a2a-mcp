#!/usr/bin/env bash
# Starts Phoenix + all MCP tool servers + agents for the SYNAPSE UI.
# From repo root after: source .venv/bin/activate && pip install -r requirements.txt && pip install -e .

set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

if [[ -f .venv/bin/activate ]]; then
  # shellcheck source=/dev/null
  source .venv/bin/activate
fi

trap 'kill 0' EXIT

# Phoenix (Day 6): start first so traces start landing immediately
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
python mcp-servers/eval/server.py &           # NEW: Day 7

# Agents
python agents/contextualist_agent/main.py &
python agents/scout_agent/main.py &
python agents/publisher_agent/main.py &

echo "[start] All services launched. Phoenix UI at http://localhost:6006"
echo "[start] Streamlit:  streamlit run ui/app.py"
echo "[start] Run evals:  python evals/run_eval.py"
wait