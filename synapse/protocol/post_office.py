# synapse/protocol/post_office.py — Day 10
#
# The mailbox graduates from a JSON file to Redis pub/sub.
#
# Same three-function API the rest of the codebase has used since day one:
#     send_message(message: dict)   — publish to message['recipient']'s channel
#     read_messages() -> list[dict]  — drain my buffer (messages addressed to me)
#     clear_messages()               — empty my buffer
#
# Plus one new initializer:
#     init_mailbox(agent_name)       — subscribe me to channel synapse:mailbox:<name>
#                                      Call once at process startup; idempotent.
#
# The Scout (the only agent that reads) calls init_mailbox at startup.
# The Contextualist (publish-only) does not need to call it.
#
# If Redis is unreachable, the module transparently falls back to the
# file-based behavior of Days 1-9 so the system keeps working.

import json
import os
import threading
from pathlib import Path
from typing import Optional

REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379")
CHANNEL_PREFIX = "synapse:mailbox"

# State (per-process)
_mode: Optional[str] = None         # "redis" or "file"
_client = None
_pubsub = None
_subscriber_thread: Optional[threading.Thread] = None
_message_buffer: list = []
_buffer_lock = threading.Lock()
_agent_name: Optional[str] = None
_init_lock = threading.Lock()

# File-fallback path (unchanged from Day 1-9 location)
_FALLBACK_PATH = Path(__file__).parent / "post_office.json"


def init_mailbox(agent_name: str) -> bool:
    """
    Subscribe this process to the mailbox channel for `agent_name`.
    Call once at process startup. Returns True if Redis is the backend,
    False if the system fell back to file mode.

    Idempotent — second and later calls are no-ops.
    """
    global _mode, _client, _pubsub, _subscriber_thread, _agent_name

    with _init_lock:
        if _mode is not None:
            return _mode == "redis"

        _agent_name = agent_name

        # Try Redis first
        try:
            import redis
            _client = redis.from_url(
                REDIS_URL, decode_responses=True, socket_connect_timeout=2
            )
            _client.ping()

            _pubsub = _client.pubsub(ignore_subscribe_messages=True)
            channel = f"{CHANNEL_PREFIX}:{agent_name}"
            _pubsub.subscribe(channel)

            def _listen():
                """Background thread: drain Redis pubsub into the local buffer."""
                for msg in _pubsub.listen():
                    if msg.get("type") != "message":
                        continue
                    try:
                        payload = json.loads(msg["data"])
                    except (json.JSONDecodeError, TypeError):
                        continue
                    with _buffer_lock:
                        _message_buffer.append(payload)

            _subscriber_thread = threading.Thread(target=_listen, daemon=True)
            _subscriber_thread.start()
            _mode = "redis"
            print(f"[post_office] {agent_name} subscribed to '{channel}' on {REDIS_URL}")
            return True
        except Exception as e:
            _mode = "file"
            _ensure_fallback_file()
            print(
                f"[post_office] Redis unavailable, falling back to file mode "
                f"for {agent_name}: {e}"
            )
            return False


def _ensure_fallback_file():
    if not _FALLBACK_PATH.exists():
        _FALLBACK_PATH.write_text("[]")


def _trace_attrs(span, message: dict):
    """Set common span attributes for a mailbox operation."""
    span.set_attribute("recipient", message.get("recipient", ""))
    span.set_attribute("sender", message.get("sender", ""))
    span.set_attribute("status", message.get("status", ""))
    span.set_attribute("task_id", message.get("task_id", ""))
    span.set_attribute("backend", _mode or "uninitialized")


def send_message(message: dict) -> None:
    """
    Publish `message` to its recipient's channel.
    Falls back to file mode if Redis is unavailable.
    """
    # Lazy import to avoid circular deps; tracing module is independent
    from synapse.tracing import tracer

    recipient = message.get("recipient", "broadcast")

    with tracer.start_as_current_span("post_office.send") as span:
        _trace_attrs(span, message)

        if _mode == "redis":
            try:
                channel = f"{CHANNEL_PREFIX}:{recipient}"
                _client.publish(channel, json.dumps(message, default=str))
                span.set_attribute("channel", channel)
                return
            except Exception as e:
                span.record_exception(e)
                print(f"[post_office] Publish failed: {e}; using file fallback")
                # Fall through to file mode for this call

        # File mode (either by config or by fallback)
        _ensure_fallback_file()
        try:
            existing = json.loads(_FALLBACK_PATH.read_text() or "[]")
        except json.JSONDecodeError:
            existing = []
        existing.append(message)
        _FALLBACK_PATH.write_text(json.dumps(existing, indent=2, default=str))
        span.set_attribute("channel", f"file:{_FALLBACK_PATH.name}")


def read_messages() -> list:
    """
    Return messages addressed to this agent (since `init_mailbox` was called).
    """
    from synapse.tracing import tracer

    with tracer.start_as_current_span("post_office.read") as span:
        span.set_attribute("backend", _mode or "uninitialized")
        span.set_attribute("agent", _agent_name or "")

        if _mode == "redis":
            with _buffer_lock:
                snapshot = list(_message_buffer)
            span.set_attribute("message_count", len(snapshot))
            return snapshot

        # File fallback — filter by recipient when we have an agent name
        if not _FALLBACK_PATH.exists():
            return []
        try:
            all_msgs = json.loads(_FALLBACK_PATH.read_text() or "[]")
        except json.JSONDecodeError:
            return []

        result = (
            [m for m in all_msgs if m.get("recipient") == _agent_name]
            if _agent_name else all_msgs
        )
        span.set_attribute("message_count", len(result))
        return result


def clear_messages() -> None:
    """Clear this agent's message buffer."""
    from synapse.tracing import tracer

    with tracer.start_as_current_span("post_office.clear") as span:
        span.set_attribute("backend", _mode or "uninitialized")
        span.set_attribute("agent", _agent_name or "")

        if _mode == "redis":
            with _buffer_lock:
                _message_buffer.clear()
            return

        # File fallback — clear only this agent's messages, keep others
        if not _FALLBACK_PATH.exists():
            return
        try:
            all_msgs = json.loads(_FALLBACK_PATH.read_text() or "[]")
        except json.JSONDecodeError:
            all_msgs = []
        kept = (
            [m for m in all_msgs if m.get("recipient") != _agent_name]
            if _agent_name else []
        )
        _FALLBACK_PATH.write_text(json.dumps(kept, indent=2, default=str))


def mode() -> str:
    """Return the current backend mode: 'redis', 'file', or 'uninitialized'."""
    return _mode or "uninitialized"


def stats() -> dict:
    """Lightweight introspection for the UI / debugging."""
    return {
        "mode": _mode or "uninitialized",
        "agent_name": _agent_name,
        "buffered_messages": len(_message_buffer),
        "redis_url": REDIS_URL if _mode == "redis" else None,
        "channel": (
            f"{CHANNEL_PREFIX}:{_agent_name}"
            if _mode == "redis" and _agent_name else None
        ),
    }