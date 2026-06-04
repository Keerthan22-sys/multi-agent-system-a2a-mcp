# Task 7 + 14: Contextualist Agent — now accepts routing flags (Day 5).
import os
import asyncio
import json
from fastmcp import FastMCP, Client
from contextlib import AsyncExitStack

from synapse.protocol.post_office import send_message, read_messages

mcp = FastMCP("Contextualist Agent")

WORLD_DATA_URL = "http://0.0.0.0:8001/mcp"
FINANCE_URL = "http://0.0.0.0:8002/mcp"


@mcp.tool
async def contextualize(
    topic: str,
    city: str,
    task_id: str = "task-1",
    use_news: bool = True,
    use_weather: bool = True,
    use_fx: bool = True,
):
    """
    Fetches contextual data for a topic and city, conditional on routing flags.
    Skipped tools leave their corresponding field as None on the signal.
    """
    news, weather, fx = None, None, None
    tools_used = []
    tools_skipped = []

    async with AsyncExitStack() as stack:
        # Only open clients we actually need
        world_client = None
        if use_news or use_weather:
            world_client = await stack.enter_async_context(Client(WORLD_DATA_URL))

        finance_client = None
        if use_fx:
            finance_client = await stack.enter_async_context(Client(FINANCE_URL))

        # Build the parallel call list
        calls = []
        call_keys = []
        if use_news and world_client:
            calls.append(world_client.call_tool("search_news", {"query": topic}))
            call_keys.append("news")
        else:
            tools_skipped.append("news")

        if use_weather and world_client:
            calls.append(world_client.call_tool("get_weather", {"city": city}))
            call_keys.append("weather")
        else:
            tools_skipped.append("weather")

        if use_fx and finance_client:
            calls.append(finance_client.call_tool("get_fx_rate", {"location": city}))
            call_keys.append("fx")
        else:
            tools_skipped.append("fx")

        if calls:
            results = await asyncio.gather(*calls)
            data_by_key = {key: results[i].data for i, key in enumerate(call_keys)}
        else:
            data_by_key = {}

        news = data_by_key.get("news")
        weather = data_by_key.get("weather")
        fx = data_by_key.get("fx")
        tools_used = list(data_by_key.keys())

    # Build the signal — None for skipped fields so Publisher can detect them
    signal = {
        "topic": topic,
        "news_headline": (news.get("headline") if news else None),
        "location": {
            "city": city,
            "weather": (
                f"{weather.get('temperature')}°C, {weather.get('description')}"
                if weather else None
            ),
        },
        "financial_context": fx,
        "tools_used": tools_used,
        "tools_skipped": tools_skipped,
    }

    send_message({
        "sender": "contextualist",
        "recipient": "scout",
        "task_id": task_id,
        "status": "done",
        "payload": signal,
    })

    return signal


if __name__ == "__main__":
    mcp.run(transport="http", host="0.0.0.0", port=8000)