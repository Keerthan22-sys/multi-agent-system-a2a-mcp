# Task 12 (Day 3): Memory MCP Server — persistent semantic recall across runs.
# Same FastMCP pattern as world-data / finance-monitor / media-engine.
import os
import json
import uuid
from datetime import datetime, timezone
from pathlib import Path

import chromadb
from chromadb.config import Settings
from fastmcp import FastMCP


# Persist Chroma to disk inside the synapse package so it travels with the repo.
# Resolves to <repo_root>/synapse/memory_store/
STORE_PATH = Path(__file__).resolve().parents[2] / "synapse" / "memory_store"
STORE_PATH.mkdir(parents=True, exist_ok=True)

# Persistent client (file-backed). Default embedding function uses ONNX MiniLM,
# downloaded automatically on first run (~80MB).
chroma_client = chromadb.PersistentClient(
    path=str(STORE_PATH),
    settings=Settings(anonymized_telemetry=False),
)

collection = chroma_client.get_or_create_collection(
    name="synapse_briefs",
    metadata={"description": "Stored daily briefs for semantic recall across runs."},
)


mcp = FastMCP("Memory Server")


# ---------- Helpers ----------

def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _safe_truncate(text: str, limit: int) -> str:
    if not isinstance(text, str):
        return ""
    return text if len(text) <= limit else text[:limit]

MEMORY_DISTANCE_THRESHOLD = 1.0

# ---------- Tools ----------

@mcp.tool
def store_brief(topic: str, article: str, payload: dict, city: str = "") -> dict:
    """
    Persist a finished brief to long-term memory.
    Returns the brief_id assigned to this record.
    """
    brief_id = f"brief-{uuid.uuid4().hex[:8]}"
    created_at = _now_iso()

    # The document is what Chroma embeds for semantic search.
    # We combine topic + article so retrieval works on either dimension.
    document = f"Topic: {topic}\n\nArticle: {article}"

    # Metadata holds the actual readable content we want to recall.
    # Chroma metadata values must be scalar — we JSON-encode the payload.
    metadata = {
        "topic": topic,
        "article": _safe_truncate(article, 8000),
        "payload_json": _safe_truncate(json.dumps(payload, default=str), 8000),
        "city": city or "",
        "created_at": created_at,
    }

    collection.add(
        ids=[brief_id],
        documents=[document],
        metadatas=[metadata],
    )

    return {
        "brief_id": brief_id,
        "topic": topic,
        "created_at": created_at,
        "stored": True,
        "total_in_memory": collection.count(),
    }


@mcp.tool
def search_briefs(query: str, k: int = 3) -> dict:
    """
    Semantic search across all stored briefs.
    Returns up to k briefs most relevant to the query.
    """
    total = collection.count()
    if total == 0:
        return {"query": query, "briefs": [], "count": 0, "message": "Memory is empty."}

    n_results = min(k, total)
    results = collection.query(
        query_texts=[query],
        n_results=n_results,
    )

    briefs = []
    ids = results.get("ids", [[]])[0]
    metas = results.get("metadatas", [[]])[0]
    dists = results.get("distances", [[]])[0]

    for i, brief_id in enumerate(ids):
        meta = metas[i] if i < len(metas) else {}
        briefs.append({
            "brief_id": brief_id,
            "topic": meta.get("topic"),
            "city": meta.get("city"),
            "created_at": meta.get("created_at"),
            "article_snippet": _safe_truncate(meta.get("article", ""), 400),
            "distance": float(dists[i]) if i < len(dists) else None,
        })

    return {"query": query, "briefs": briefs, "count": len(briefs)}


@mcp.tool
def list_recent_briefs(limit: int = 10) -> dict:
    """
    Return the most recent briefs in reverse chronological order.
    Used by the UI 'past briefs' sidebar.
    """
    total = collection.count()
    if total == 0:
        return {"briefs": [], "total": 0}

    all_data = collection.get(include=["metadatas"])
    ids = all_data.get("ids", [])
    metas = all_data.get("metadatas", [])

    items = []
    for i, brief_id in enumerate(ids):
        meta = metas[i] if i < len(metas) else {}
        items.append({
            "brief_id": brief_id,
            "topic": meta.get("topic"),
            "city": meta.get("city"),
            "created_at": meta.get("created_at"),
        })

    items.sort(key=lambda x: x.get("created_at") or "", reverse=True)

    return {"briefs": items[:limit], "total": total}


@mcp.tool
def get_brief(brief_id: str) -> dict:
    """
    Retrieve the full content of a single brief by its ID.
    """
    result = collection.get(ids=[brief_id], include=["metadatas", "documents"])

    if not result.get("ids"):
        return {"error": f"Brief {brief_id} not found"}

    meta = result["metadatas"][0]
    try:
        payload = json.loads(meta.get("payload_json") or "{}")
    except Exception:
        payload = {}

    return {
        "brief_id": brief_id,
        "topic": meta.get("topic"),
        "article": meta.get("article"),
        "payload": payload,
        "city": meta.get("city"),
        "created_at": meta.get("created_at"),
    }


@mcp.tool
def delete_brief(brief_id: str) -> dict:
    """
    Remove a single brief from memory (useful for cleanup in dev).
    """
    try:
        collection.delete(ids=[brief_id])
        return {"brief_id": brief_id, "deleted": True}
    except Exception as e:
        return {"brief_id": brief_id, "deleted": False, "error": str(e)}


if __name__ == "__main__":
    mcp.run(transport="http", host="0.0.0.0", port=8006)
