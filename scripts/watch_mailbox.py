"""
watch_mailbox.py — Live-tail the SYNAPSE message broker (Day 10).

Subscribes to every synapse:mailbox:* channel via Redis PSUBSCRIBE and pretty-prints
each message as it arrives. Run in a side terminal while generating briefs in the
Streamlit UI to watch the A2A protocol flow in real time.

Usage:
    python scripts/watch_mailbox.py

Requires Redis to be running at REDIS_URL (default redis://localhost:6379).
"""
import json
import os
import sys
from datetime import datetime

REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379")
PATTERN = "synapse:mailbox:*"


def _ts() -> str:
    return datetime.now().strftime("%H:%M:%S.%f")[:-3]


def _color(s: str, code: int) -> str:
    """ANSI color for terminal output. Disabled if not a TTY."""
    if not sys.stdout.isatty():
        return s
    return f"\033[{code}m{s}\033[0m"


def main():
    try:
        import redis
    except ImportError:
        print("ERROR: redis package not installed. Run: pip install redis", file=sys.stderr)
        sys.exit(1)

    try:
        client = redis.from_url(REDIS_URL, decode_responses=True, socket_connect_timeout=2)
        client.ping()
    except Exception as e:
        print(f"ERROR: Cannot connect to Redis at {REDIS_URL}: {e}", file=sys.stderr)
        sys.exit(1)

    pubsub = client.pubsub(ignore_subscribe_messages=True)
    pubsub.psubscribe(PATTERN)

    print(_color(f"📬 SYNAPSE mailbox watcher", 36))
    print(_color(f"   Pattern: {PATTERN}", 90))
    print(_color(f"   Redis:   {REDIS_URL}", 90))
    print(_color(f"   Press Ctrl-C to stop.\n", 90))

    try:
        for msg in pubsub.listen():
            if msg.get("type") != "pmessage":
                continue
            channel = msg.get("channel", "?")
            raw = msg.get("data", "")
            try:
                payload = json.loads(raw)
            except (json.JSONDecodeError, TypeError):
                print(f"[{_ts()}] {channel}  (unparseable: {raw[:80]})")
                continue

            sender = payload.get("sender", "?")
            recipient = payload.get("recipient", "?")
            status = payload.get("status", "?")
            task = payload.get("task_id", "?")

            header = (
                f"[{_ts()}] "
                f"{_color(sender, 33)} → {_color(recipient, 32)} "
                f"({_color(status, 36)}, task={task})"
            )
            print(header)
            print(_color(f"  channel: {channel}", 90))

            body = payload.get("payload")
            if body is not None:
                preview = json.dumps(body, indent=2, default=str)
                # Limit preview length so the terminal stays readable
                if len(preview) > 800:
                    preview = preview[:800] + "\n  ... (truncated)"
                indented = "\n".join("  " + line for line in preview.splitlines())
                print(indented)
            print()
    except KeyboardInterrupt:
        print(_color("\n📬 watcher stopped.", 36))


if __name__ == "__main__":
    main()