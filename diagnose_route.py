import asyncio
from fastmcp import Client

async def main():
    async with Client("http://0.0.0.0:8008/mcp") as c:
        # Test tool routing
        for topic in [
            "Bitcoin price prediction 2026",
            "Tokyo cherry blossom forecast",
            "Latest math proof in number theory",
            "Indian semiconductor manufacturing",
        ]:
            r = await c.call_tool("route_tools", {"topic": topic})
            print(f"\n'{topic}'")
            print(f"  {r.data}")

        # Test intent routing
        r = await c.call_tool("route_intent", {
            "message": "What about the GDP impact?",
            "conversation_topic": "Indian semiconductor manufacturing",
            "recent_turns": [],
        })
        print(f"\nFollow-up test: {r.data}")

        r = await c.call_tool("route_intent", {
            "message": "Tell me about K-pop tours instead",
            "conversation_topic": "Indian semiconductor manufacturing",
            "recent_turns": [],
        })
        print(f"\nPivot test: {r.data}")

asyncio.run(main())