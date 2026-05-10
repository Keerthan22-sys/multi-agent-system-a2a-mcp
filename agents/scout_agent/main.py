# Task 8: Build Scout Agent to Aggregate Signals
import asyncio
import json
import time
from fastmcp import FastMCP, Client
from contextlib import AsyncExitStack
from synapse.protocol.post_office import send_message, read_messages, clear_messages

# Initialize the MCP server for the Scout agent
mcp = FastMCP("Scout Agent")

# MCP endpoints for downstream agents
CONTEXTUALIST_URL = "http://0.0.0.0:8000/mcp"
MEDIA_URL = "http://0.0.0.0:8003/mcp"


def wait_for_response(task_id: str, timeout: int = 10):
    """
    Poll the post office for a response matching the given task ID.
    """
    start = time.time()
    while time.time() - start < timeout:
        messages = read_messages()
        for msg in messages:
            if msg.get("task_id") == task_id and msg.get("recipient") == "scout":
                return msg
        time.sleep(0.5)
    return None


@mcp.tool
async def scout(topic: str, city: str, task_id: str = "task-1"):
    """
    Aggregate contextual and media signals for a given topic and city.
    """
    # Clear old messages to avoid mixing responses
    clear_messages()

    # Manage multiple MCP clients safely
    async with AsyncExitStack() as stack:
        # Connect to the Contextualist MCP server
        contextualist_client = await stack.enter_async_context(
            Client(CONTEXTUALIST_URL)
        )

        # Connect to the Media Engine MCP server
        media_client = await stack.enter_async_context(
            Client(MEDIA_URL)
        )

        # Send a contextualization request
        await contextualist_client.call_tool(
            "contextualize",
            {
                "topic": topic,
                "city": city,
                "task_id": task_id
            }
        )

        # Wait for the contextualist response via the post office
        response = wait_for_response(task_id)
        context = response["payload"]

        # Fetch related media assets
        media_res = await media_client.call_tool(
            "search_images",
            {
                "query": topic,
                "per_page": 1
            }
        )
        media = media_res.data

    # Combine all signals into one final object
    final_signal = {
        "topic": topic,
        "location": city,
        "context": context,
        "media": media
    }

    # Send the aggregated signal to the Publisher agent
    send_message({
        "sender": "scout",
        "recipient": "publisher",
        "task_id": task_id,
        "status": "done",
        "payload": final_signal
    })

    # Log the final signal for debugging and visibility
    print(final_signal)

    return final_signal


if __name__ == "__main__":
    # Start the Scout MCP server
    mcp.run(
        transport="http",
        host="0.0.0.0",
        port=8004
    )