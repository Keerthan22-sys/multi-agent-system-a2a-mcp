# Task 13 (Day 4): Conversation MCP Server — stores multi-turn conversation state.
# Same FastMCP pattern as the rest of your servers.
# Storage: a single JSON file (synapse/conversations/conversations.json),
# mirroring the post-office aesthetic.
import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
from threading import Lock

from fastmcp import FastMCP


# Persist conversations to disk inside the synapse package.
# Resolves to <repo_root>/synapse/conversations/conversations.json
STORE_DIR = Path(__file__).resolve().parents[2] / "synapse" / "conversations"
STORE_DIR.mkdir(parents=True, exist_ok=True)
STORE_PATH = STORE_DIR / "conversations.json"

# Simple in-process lock so the read-modify-write cycle doesn't tear
# under concurrent tool calls. Good enough at portfolio scale.
_lock = Lock()


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _read_all() -> dict:
    if not STORE_PATH.exists():
        STORE_PATH.write_text("{}")
        return {}
    try:
        return json.loads(STORE_PATH.read_text() or "{}")
    except json.JSONDecodeError:
        # If the file is somehow corrupted, reset to empty rather than crash.
        STORE_PATH.write_text("{}")
        return {}


def _write_all(data: dict) -> None:
    STORE_PATH.write_text(json.dumps(data, indent=2, default=str))


mcp = FastMCP("Conversation Server")


# ---------- Tools ----------

@mcp.tool
def start_conversation(
    topic: str,
    city: str,
    initial_payload: dict,
    initial_article: str,
    brief_id: str = "",
) -> dict:
    """
    Begin a new conversation seeded with the initial brief.
    Returns the conversation_id used for all follow-up turns.
    """
    conversation_id = f"conv-{uuid.uuid4().hex[:8]}"
    now = _now_iso()

    record = {
        "conversation_id": conversation_id,
        "topic": topic,
        "city": city,
        "started_at": now,
        "updated_at": now,
        "initial_payload": initial_payload,
        "brief_id": brief_id,
        "turns": [
            {"role": "user", "content": topic, "timestamp": now},
            {"role": "assistant", "content": initial_article, "timestamp": now},
        ],
    }

    with _lock:
        data = _read_all()
        data[conversation_id] = record
        _write_all(data)

    return {
        "conversation_id": conversation_id,
        "topic": topic,
        "started_at": now,
        "turn_count": 2,
    }


@mcp.tool
def add_turn(conversation_id: str, role: str, content: str) -> dict:
    """
    Append a single turn (role=user|assistant) to an existing conversation.
    """
    if role not in ("user", "assistant"):
        return {"error": f"Invalid role '{role}'. Must be 'user' or 'assistant'."}

    now = _now_iso()
    with _lock:
        data = _read_all()
        if conversation_id not in data:
            return {"error": f"Conversation {conversation_id} not found."}
        data[conversation_id]["turns"].append({
            "role": role,
            "content": content,
            "timestamp": now,
        })
        data[conversation_id]["updated_at"] = now
        _write_all(data)
        turn_count = len(data[conversation_id]["turns"])

    return {
        "conversation_id": conversation_id,
        "turn_count": turn_count,
        "appended": True,
    }


@mcp.tool
def get_conversation(conversation_id: str) -> dict:
    """
    Return the full conversation: metadata, initial payload, and all turns.
    Used by the Publisher to build follow-up prompts, and by the UI to render.
    """
    with _lock:
        data = _read_all()
    if conversation_id not in data:
        return {"error": f"Conversation {conversation_id} not found."}
    return data[conversation_id]


@mcp.tool
def list_conversations(limit: int = 10) -> dict:
    """
    Return the most recent conversations in reverse chronological order.
    Lightweight: returns metadata only (no turns, no payload).
    """
    with _lock:
        data = _read_all()

    items = []
    for conv_id, record in data.items():
        items.append({
            "conversation_id": conv_id,
            "topic": record.get("topic"),
            "city": record.get("city"),
            "started_at": record.get("started_at"),
            "updated_at": record.get("updated_at"),
            "turn_count": len(record.get("turns", [])),
            "brief_id": record.get("brief_id", ""),
        })
    items.sort(key=lambda x: x.get("updated_at") or "", reverse=True)

    return {"conversations": items[:limit], "total": len(items)}


@mcp.tool
def delete_conversation(conversation_id: str) -> dict:
    """
    Remove a single conversation (useful for cleanup in dev).
    """
    with _lock:
        data = _read_all()
        if conversation_id not in data:
            return {"conversation_id": conversation_id, "deleted": False, "error": "Not found"}
        del data[conversation_id]
        _write_all(data)
    return {"conversation_id": conversation_id, "deleted": True}


if __name__ == "__main__":
    mcp.run(transport="http", host="0.0.0.0", port=8007)