# Task 8 + 12 + 14 + 15 + 19: Scout Agent — subscribes to Redis channel at startup (Day 10).
from synapse.tracing import setup_tracing, tracer
setup_tracing("scout-agent")

import asyncio
import json
import time
from fastmcp import FastMCP, Client
from contextlib import AsyncExitStack
# NEW (Day 10): init_mailbox added to the imports
from synapse.protocol.post_office import (
    send_message, read_messages, clear_messages, init_mailbox
)
from synapse.config import CONTEXTUALIST_URL, MEDIA_URL, MEMORY_URL, ROUTER_URL

mcp = FastMCP("Scout Agent")

def wait_for_response(task_id: str, timeout: int = 10):
    """
    Poll the buffered mailbox for our response.
    With the Day 10 Redis backend, messages arrive in a background subscriber
    thread and accumulate in the local buffer — read_messages drains it.
    The polling interval still applies but is now satisfied much faster.
    """
    start = time.time()
    while time.time() - start < timeout:
        messages = read_messages()
        for msg in messages:
            if msg.get("task_id") == task_id and msg.get("recipient") == "scout":
                return msg
        time.sleep(0.2)  # Day 10: tighter poll since Redis delivery is near-instant
    return None


_ROUTING_DEFAULT = {
    "use_news": True, "use_weather": True, "use_fx": True, "use_media": True,
    "reasoning": "Router unavailable; defaulted to all tools.",
}


@mcp.tool
async def scout(topic: str, city: str, task_id: str = "task-1"):
    with tracer.start_as_current_span("scout.scout") as root:
        root.set_attribute("topic", topic)
        root.set_attribute("city", city)
        root.set_attribute("task_id", task_id)

        clear_messages()

        async with AsyncExitStack() as stack:
            # Step 1: routing
            with tracer.start_as_current_span("scout.router_consult") as r_span:
                routing = dict(_ROUTING_DEFAULT)
                try:
                    router_client = await stack.enter_async_context(Client(ROUTER_URL))
                    r = await router_client.call_tool("route_tools", {"topic": topic})
                    if isinstance(r.data, dict):
                        routing.update(r.data)
                    r_span.set_attribute("router_reachable", True)
                except Exception as e:
                    r_span.set_attribute("router_reachable", False)
                    r_span.record_exception(e)
                for k in ("use_news", "use_weather", "use_fx", "use_media"):
                    r_span.set_attribute(k, routing[k])
                r_span.set_attribute("reasoning", routing.get("reasoning", ""))

            # Step 2: contextualize
            with tracer.start_as_current_span("scout.contextualize_call") as c_span:
                contextualist_client = await stack.enter_async_context(
                    Client(CONTEXTUALIST_URL)
                )
                await contextualist_client.call_tool(
                    "contextualize",
                    {
                        "topic": topic, "city": city, "task_id": task_id,
                        "use_news": routing["use_news"],
                        "use_weather": routing["use_weather"],
                        "use_fx": routing["use_fx"],
                    },
                )
                response = wait_for_response(task_id)
                context = response["payload"] if response else {}
                c_span.set_attribute("context_received", response is not None)

            # Step 3: media
            media = {}
            if routing["use_media"]:
                with tracer.start_as_current_span("scout.media_call") as m_span:
                    try:
                        media_client = await stack.enter_async_context(Client(MEDIA_URL))
                        media_res = await media_client.call_tool(
                            "search_images", {"query": topic, "per_page": 1}
                        )
                        media = media_res.data
                        m_span.set_attribute("media_returned", bool(media))
                    except Exception as e:
                        m_span.record_exception(e)

            # Step 4: memory
            with tracer.start_as_current_span("scout.memory_query") as mem_span:
                memory_context = {"briefs": [], "count": 0}
                try:
                    memory_client = await stack.enter_async_context(Client(MEMORY_URL))
                    mem_res = await memory_client.call_tool(
                        "search_briefs", {"query": topic, "k": 3}
                    )
                    memory_context = mem_res.data
                    mem_span.set_attribute("hits", memory_context.get("count", 0))
                except Exception as e:
                    mem_span.record_exception(e)

        final_signal = {
            "topic": topic,
            "location": city,
            "context": context,
            "media": media,
            "memory_context": memory_context,
            "routing_decision": routing,
        }

        root.set_attribute("memory_hits", memory_context.get("count", 0))
        root.set_attribute("tools_enabled",
                          sum(1 for k in ("use_news", "use_weather", "use_fx", "use_media")
                              if routing[k]))

        send_message({
            "sender": "scout", "recipient": "publisher",
            "task_id": task_id, "status": "done",
            "payload": final_signal,
        })
        return final_signal


if __name__ == "__main__":
    # NEW (Day 10): subscribe to the scout mailbox channel BEFORE serving requests.
    # If Redis is up, this connects the subscriber thread; if not, falls back to file mode.
    init_mailbox("scout")
    mcp.run(transport="http", host="0.0.0.0", port=8004)