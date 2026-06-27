# Dockerfile — single image for all SYNAPSE Python services (Day 11).
# Each docker-compose service runs the same image with a different command.
FROM python:3.11-slim

# Minimal system deps. curl is handy for container healthchecks if you add them.
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install Python deps first so they get cached separately from source changes
COPY requirements.txt pyproject.toml ./
RUN pip install --no-cache-dir -r requirements.txt

# Now copy source — putting this AFTER deps means changing code doesn't bust the deps layer
COPY synapse ./synapse
COPY agents ./agents
COPY mcp-servers ./mcp-servers
COPY evals ./evals
COPY ui ./ui
COPY scripts ./scripts

# Install our package in editable mode so `from synapse.X import ...` resolves
RUN pip install --no-cache-dir -e .

# No CMD — docker-compose sets `command:` per service