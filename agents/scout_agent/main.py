# Task 8 + 12 + 14: Scout Agent — now Router-aware (Day 5).
import asyncio
import json
import time
from fastmcp import FastMCP, Client
from contextlib import AsyncExitStack
from synapse.protocol.post_office import send_message, read_messages, clear_messages

mcp = FastMCP("Scout Agent")

CONTEXTUALIST_URL = "http://0.0.0.0:8000/mcp"
MEDIA_URL = "http://0.0.0.0:8003/mcp"
MEMORY_URL = "http://0.0.0.0:8006/mcp"      # Day 3
ROUTER_URL = "http://0.0.0.0:8008/mcp"      # NEW: Day 5


def wait_for_response(task_id: str, timeout: int = 10):
    start = time.time()
    while time.time() - start < timeout:
        messages = read_messages()
        for msg in messages:
            if msg.get("task_id") == task_id and msg.get("recipient") == "scout":
                return msg
        time.sleep(0.5)
    return None


# Fail-safe default if the router is unreachable
_ROUTING_DEFAULT = {
    "use_news": True,
    "use_weather": True,
    "use_fx": True,
    "use_media": True,
    "reasoning": "Router unavailable; defaulted to all tools.",
}


@mcp.tool
async def scout(topic: str, city: str, task_id: str = "task-1"):
    """
    Orchestrate context, media, and memory — now routed dynamically by the Router agent.
    """
    clear_messages()

    async with AsyncExitStack() as stack:
        # 1. Ask the Router which tools to invoke for this topic
        routing = dict(_ROUTING_DEFAULT)
        try:
            router_client = await stack.enter_async_context(Client(ROUTER_URL))
            r = await router_client.call_tool("route_tools", {"topic": topic})
            if isinstance(r.data, dict):
                routing.update(r.data)
        except Exception as e:
            print(f"[scout] Router unavailable, using defaults: {e}")

        # 2. Hand routing flags to the Contextualist
        contextualist_client = await stack.enter_async_context(Client(CONTEXTUALIST_URL))
        await contextualist_client.call_tool(
            "contextualize",
            {
                "topic": topic,
                "city": city,
                "task_id": task_id,
                "use_news": routing["use_news"],
                "use_weather": routing["use_weather"],
                "use_fx": routing["use_fx"],
            },
        )
        response = wait_for_response(task_id)
        context = response["payload"] if response else {}

        # 3. Conditionally fetch media
        media = {}
        if routing["use_media"]:
            try:
                media_client = await stack.enter_async_context(Client(MEDIA_URL))
                media_res = await media_client.call_tool(
                    "search_images",
                    {"query": topic, "per_page": 1},
                )
                media = media_res.data
            except Exception as e:
                print(f"[scout] Media fetch failed: {e}")

        # 4. Memory is always queried — it's local and cheap
        memory_context = {"briefs": [], "count": 0}
        try:
            memory_client = await stack.enter_async_context(Client(MEMORY_URL))
            mem_res = await memory_client.call_tool(
                "search_briefs",
                {"query": topic, "k": 3},
            )
            memory_context = mem_res.data
        except Exception as e:
            print(f"[scout] Memory query failed: {e}")

    final_signal = {
        "topic": topic,
        "location": city,
        "context": context,
        "media": media,
        "memory_context": memory_context,
        "routing_decision": routing,  # NEW: ride-along for UI observability
    }

    send_message({
        "sender": "scout",
        "recipient": "publisher",
        "task_id": task_id,
        "status": "done",
        "payload": final_signal,
    })

    print(final_signal)
    return final_signal


if __name__ == "__main__":
    mcp.run(transport="http", host="0.0.0.0", port=8004)