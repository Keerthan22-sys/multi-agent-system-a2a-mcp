# Task 12 + 15: Memory MCP Server — now traced (Day 6).
from synapse.tracing import setup_tracing, tracer
setup_tracing("memory-server")

import os
import json
import uuid
from datetime import datetime, timezone
from pathlib import Path

import chromadb
from chromadb.config import Settings
from fastmcp import FastMCP

STORE_PATH = Path(__file__).resolve().parents[2] / "synapse" / "memory_store"
STORE_PATH.mkdir(parents=True, exist_ok=True)

chroma_client = chromadb.PersistentClient(
    path=str(STORE_PATH),
    settings=Settings(anonymized_telemetry=False),
)
collection = chroma_client.get_or_create_collection(
    name="synapse_briefs",
    metadata={"description": "Stored daily briefs for semantic recall across runs."},
)

# l2 distance threshold (tunable). See Day 3 calibration guidance.
MEMORY_DISTANCE_THRESHOLD = 1.0

mcp = FastMCP("Memory Server")


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()

def _safe_truncate(text: str, limit: int) -> str:
    if not isinstance(text, str): return ""
    return text if len(text) <= limit else text[:limit]


@mcp.tool
def store_brief(topic: str, article: str, payload: dict, city: str = "") -> dict:
    with tracer.start_as_current_span("memory.store_brief") as span:
        span.set_attribute("topic", topic)
        span.set_attribute("city", city)
        span.set_attribute("article_length", len(article))

        brief_id = f"brief-{uuid.uuid4().hex[:8]}"
        created_at = _now_iso()
        document = f"Topic: {topic}\n\nArticle: {article}"
        metadata = {
            "topic": topic,
            "article": _safe_truncate(article, 8000),
            "payload_json": _safe_truncate(json.dumps(payload, default=str), 8000),
            "city": city or "",
            "created_at": created_at,
        }
        with tracer.start_as_current_span("memory.chroma_add"):
            collection.add(ids=[brief_id], documents=[document], metadatas=[metadata])

        span.set_attribute("brief_id", brief_id)
        span.set_attribute("total_in_memory", collection.count())
        return {
            "brief_id": brief_id, "topic": topic, "created_at": created_at,
            "stored": True, "total_in_memory": collection.count(),
        }


@mcp.tool
def search_briefs(query: str, k: int = 3) -> dict:
    with tracer.start_as_current_span("memory.search_briefs") as span:
        span.set_attribute("query", query)
        span.set_attribute("k", k)
        span.set_attribute("threshold", MEMORY_DISTANCE_THRESHOLD)

        total = collection.count()
        span.set_attribute("collection_size", total)
        if total == 0:
            return {"query": query, "briefs": [], "count": 0, "message": "Memory is empty."}

        with tracer.start_as_current_span("memory.chroma_query"):
            results = collection.query(
                query_texts=[query], n_results=min(k, total)
            )
        ids = results.get("ids", [[]])[0]
        metas = results.get("metadatas", [[]])[0]
        dists = results.get("distances", [[]])[0]

        briefs = []
        filtered_out = 0
        for i, brief_id in enumerate(ids):
            distance = float(dists[i]) if i < len(dists) else 999.0
            if distance > MEMORY_DISTANCE_THRESHOLD:
                filtered_out += 1
                continue
            meta = metas[i] if i < len(metas) else {}
            briefs.append({
                "brief_id": brief_id,
                "topic": meta.get("topic"),
                "city": meta.get("city"),
                "created_at": meta.get("created_at"),
                "article_snippet": _safe_truncate(meta.get("article", ""), 400),
                "distance": distance,
            })
        span.set_attribute("hits", len(briefs))
        span.set_attribute("filtered_out", filtered_out)
        if briefs:
            span.set_attribute("min_distance", min(b["distance"] for b in briefs))
        return {"query": query, "briefs": briefs, "count": len(briefs)}


@mcp.tool
def list_recent_briefs(limit: int = 10) -> dict:
    with tracer.start_as_current_span("memory.list_recent_briefs") as span:
        span.set_attribute("limit", limit)
        total = collection.count()
        span.set_attribute("collection_size", total)
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
    with tracer.start_as_current_span("memory.get_brief") as span:
        span.set_attribute("brief_id", brief_id)
        result = collection.get(ids=[brief_id], include=["metadatas", "documents"])
        if not result.get("ids"):
            span.set_attribute("found", False)
            return {"error": f"Brief {brief_id} not found"}
        meta = result["metadatas"][0]
        try:
            payload = json.loads(meta.get("payload_json") or "{}")
        except Exception:
            payload = {}
        span.set_attribute("found", True)
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
    with tracer.start_as_current_span("memory.delete_brief") as span:
        span.set_attribute("brief_id", brief_id)
        try:
            collection.delete(ids=[brief_id])
            return {"brief_id": brief_id, "deleted": True}
        except Exception as e:
            span.record_exception(e)
            return {"brief_id": brief_id, "deleted": False, "error": str(e)}


if __name__ == "__main__":
    mcp.run(transport="http", host="0.0.0.0", port=8006)