#!/usr/bin/env bash
# Starts all MCP tool servers and agent servers required by the SYNAPSE UI.
# From repo root after: source .venv/bin/activate && pip install -r requirements.txt && pip install -e .

set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

if [[ -f .venv/bin/activate ]]; then
  # shellcheck source=/dev/null
  source .venv/bin/activate
fi

trap 'kill 0' EXIT

python mcp-servers/world-data/server.py &
python mcp-servers/finance-monitor/server.py &
python mcp-servers/media-engine/server.py &
python agents/contextualist_agent/main.py &
python agents/scout_agent/main.py &
python agents/publisher_agent/main.py &

wait
