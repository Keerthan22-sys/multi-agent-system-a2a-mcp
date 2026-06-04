# Task 9 + 12: Publisher Agent — now memory-aware (Day 3).
import os
import json
import asyncio
from fastmcp import Client, FastMCP
from dotenv import load_dotenv

load_dotenv()

mcp = FastMCP("Publisher Agent")

MEMORY_URL = "http://0.0.0.0:8006/mcp"  # NEW: memory MCP server


def _build_memory_section(memory_context: dict) -> str:
    """
    Convert the memory_context attached by the Scout into a prompt section
    that the LLM can use to avoid repetition and build on prior coverage.
    """
    briefs = (memory_context or {}).get("briefs", [])
    if not briefs:
        return ""

    lines = ["", "## Past briefs you've already published on related topics:"]
    for i, b in enumerate(briefs, start=1):
        snippet = (b.get("article_snippet") or "")[:300]
        lines.append(
            f"{i}. [{b.get('created_at', 'unknown')}] '{b.get('topic')}' — {snippet}..."
        )
    lines.append(
        "\nUse these only if directly relevant. Do not repeat prior coverage; "
        "where the new news connects, briefly reference the earlier story and "
        "focus the new brief on what has changed.\n"
    )
    return "\n".join(lines)


@mcp.tool
async def publish_brief(payload: dict) -> dict:
    """
    Generate a daily brief article using an LLM, informed by past briefs in memory,
    then persist this brief back to memory.
    """
    # Pull and remove memory_context so it doesn't bloat the structured data block.
    memory_context = payload.get("memory_context", {})
    payload_for_prompt = {k: v for k, v in payload.items() if k != "memory_context"}
    memory_section = _build_memory_section(memory_context)

    prompt = f"""
You are a news writer.

Using the news below, write a short daily brief article.
Use a neutral journalistic tone. At the end of the article,
add info about city, its conversion rate and current weather.

Data:
{json.dumps(payload_for_prompt, indent=2)}
{memory_section}

Rules:
- Do not invent facts.
- If data is missing, say "Not available."
- Include:
  - headline
  - 2-3 paragraphs
  - a short "Why it matters" section.
  - A section "About the place of news" section mentioning the weather, conversion rate
"""

    from openai import OpenAI
    client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

    response = client.responses.create(
        model="gpt-5-nano",
        input=prompt,
        max_output_tokens=1500,
        reasoning={"effort": "low"},
    )
    article = response.output_text.strip()

    # NEW: persist this brief to memory (non-fatal if memory is down).
    try:
        async with Client(MEMORY_URL) as memory_client:
            await memory_client.call_tool(
                "store_brief",
                {
                    "topic": payload.get("topic", "Unknown"),
                    "article": article,
                    "payload": payload_for_prompt,
                    "city": payload.get("location", ""),
                },
            )
    except Exception as e:
        print(f"[publisher] Memory store failed (non-fatal): {e}")

    return {
        "article": article,
        "payload": payload,
        "memory_used": len(memory_context.get("briefs", [])),
    }


if __name__ == "__main__":
    mcp.run(transport="http", host="0.0.0.0", port=8005)
