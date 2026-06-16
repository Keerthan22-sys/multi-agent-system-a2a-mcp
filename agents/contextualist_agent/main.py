# Task 7 + 14 + 15 + 18: Contextualist — now reports cache hits upstream (Day 9).
from synapse.tracing import setup_tracing, tracer
setup_tracing("contextualist-agent")

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
    with tracer.start_as_current_span("contextualist.contextualize") as span:
        span.set_attribute("topic", topic)
        span.set_attribute("city", city)
        span.set_attribute("task_id", task_id)

        news, weather, fx = None, None, None
        tools_used, tools_skipped = [], []

        async with AsyncExitStack() as stack:
            world_client = None
            if use_news or use_weather:
                world_client = await stack.enter_async_context(Client(WORLD_DATA_URL))
            finance_client = None
            if use_fx:
                finance_client = await stack.enter_async_context(Client(FINANCE_URL))

            calls, call_keys = [], []
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

            with tracer.start_as_current_span("contextualist.fetch_parallel") as fetch_span:
                fetch_span.set_attribute("call_count", len(calls))
                if calls:
                    results = await asyncio.gather(*calls)
                    data_by_key = {k: results[i].data for i, k in enumerate(call_keys)}
                else:
                    data_by_key = {}

            news = data_by_key.get("news")
            weather = data_by_key.get("weather")
            fx = data_by_key.get("fx")
            tools_used = list(data_by_key.keys())

        # NEW (Day 9): extract cache hits from each tool response if present
        cache_hits = {
            "news": bool(news.get("_cache_hit")) if isinstance(news, dict) else False,
            "weather": bool(weather.get("_cache_hit")) if isinstance(weather, dict) else False,
            "fx": bool(fx.get("_cache_hit")) if isinstance(fx, dict) else False,
        }
        span.set_attribute("cache_hits.news", cache_hits["news"])
        span.set_attribute("cache_hits.weather", cache_hits["weather"])
        span.set_attribute("cache_hits.fx", cache_hits["fx"])

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
            "cache_hits": cache_hits,  # NEW (Day 9)
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