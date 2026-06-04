import asyncio
from fastmcp import Client

async def main():
    async with Client("http://0.0.0.0:8007/mcp") as c:
        # Start a conversation
        r = await c.call_tool("start_conversation", {
            "topic": "Test brief",
            "city": "Bengaluru",
            "initial_payload": {"foo": "bar"},
            "initial_article": "This is the initial article body.",
            "brief_id": "brief-test123",
        })
        conv_id = r.data["conversation_id"]
        print("Started:", conv_id)

        # Add a follow-up turn
        await c.call_tool("add_turn", {
            "conversation_id": conv_id,
            "role": "user",
            "content": "Can you elaborate on point 2?",
        })

        # Fetch full conversation
        r = await c.call_tool("get_conversation", {"conversation_id": conv_id})
        print("Turns:", len(r.data["turns"]))

        # List
        r = await c.call_tool("list_conversations", {"limit": 5})
        print("Total:", r.data["total"])

asyncio.run(main())