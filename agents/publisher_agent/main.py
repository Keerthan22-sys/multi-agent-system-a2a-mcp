# Task 9 + 12 + 13: Publisher Agent — memory-aware, conversation-aware (Day 4).
import os
import json
import asyncio
from fastmcp import Client, FastMCP
from dotenv import load_dotenv

load_dotenv()

mcp = FastMCP("Publisher Agent")

MEMORY_URL = "http://0.0.0.0:8006/mcp"           # Day 3
CONVERSATION_URL = "http://0.0.0.0:8007/mcp"     # NEW (Day 4)

# Cap on how many prior turns to include in follow-up prompts.
# Older turns get truncated; recent ones get the room.
MAX_TURNS_IN_PROMPT = 10


# ---------- Helpers ----------

def _build_memory_section(memory_context: dict) -> str:
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


def _render_turns_for_prompt(turns: list) -> str:
    """Format recent turns as a conversation transcript for the LLM."""
    recent = turns[-MAX_TURNS_IN_PROMPT:]
    lines = []
    for t in recent:
        speaker = "User" if t.get("role") == "user" else "Assistant"
        lines.append(f"{speaker}: {t.get('content', '')}")
    return "\n\n".join(lines)


def _openai_call(prompt: str, max_tokens: int = 1500) -> str:
    from openai import OpenAI
    client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
    response = client.responses.create(
        model="gpt-5-nano",
        input=prompt,
        max_output_tokens=max_tokens,
        reasoning={"effort": "low"},
    )
    return response.output_text.strip()


# ---------- Tools ----------

@mcp.tool
async def publish_brief(payload: dict) -> dict:
    """
    Generate the INITIAL brief for a new topic.
    Also seeds a conversation so the user can follow up afterwards.
    """
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
  - A section "About the place of news" mentioning the weather, conversion rate
"""
    article = _openai_call(prompt, max_tokens=1500)

    # Store in memory (Day 3) and capture brief_id
    brief_id = ""
    try:
        async with Client(MEMORY_URL) as memory_client:
            mem_res = await memory_client.call_tool(
                "store_brief",
                {
                    "topic": payload.get("topic", "Unknown"),
                    "article": article,
                    "payload": payload_for_prompt,
                    "city": payload.get("location", ""),
                },
            )
            brief_id = (mem_res.data or {}).get("brief_id", "")
    except Exception as e:
        print(f"[publisher] Memory store failed (non-fatal): {e}")

    # NEW (Day 4): seed a conversation around this brief
    conversation_id = ""
    try:
        async with Client(CONVERSATION_URL) as conv_client:
            conv_res = await conv_client.call_tool(
                "start_conversation",
                {
                    "topic": payload.get("topic", "Unknown"),
                    "city": payload.get("location", ""),
                    "initial_payload": payload_for_prompt,
                    "initial_article": article,
                    "brief_id": brief_id,
                },
            )
            conversation_id = (conv_res.data or {}).get("conversation_id", "")
    except Exception as e:
        print(f"[publisher] Conversation seed failed (non-fatal): {e}")

    return {
        "article": article,
        "payload": payload,
        "memory_used": len(memory_context.get("briefs", [])),
        "brief_id": brief_id,
        "conversation_id": conversation_id,
    }


@mcp.tool
async def follow_up(conversation_id: str, user_question: str) -> dict:
    """
    Answer a follow-up question within an existing conversation.
    Does NOT run the Scout / Contextualist / tool pipeline — operates purely on
    the initial_payload and conversation history already in the conversation server.
    """
    # 1. Fetch the conversation
    async with Client(CONVERSATION_URL) as conv_client:
        conv_res = await conv_client.call_tool(
            "get_conversation",
            {"conversation_id": conversation_id},
        )
        conversation = conv_res.data or {}

    if conversation.get("error"):
        return {"error": conversation["error"]}

    initial_payload = conversation.get("initial_payload", {})
    turns = conversation.get("turns", [])

    # 2. Append the user's new question first (so it's part of the record
    #    even if the LLM call fails afterwards)
    async with Client(CONVERSATION_URL) as conv_client:
        await conv_client.call_tool(
            "add_turn",
            {
                "conversation_id": conversation_id,
                "role": "user",
                "content": user_question,
            },
        )

    # 3. Build a prompt grounded in the original data + conversation transcript
    transcript = _render_turns_for_prompt(turns + [
        {"role": "user", "content": user_question}
    ])

    prompt = f"""
You are continuing a conversation about a news brief you previously published.
The original gathered data is shown first, then the running conversation.

Stay grounded in the original data. Do not invent facts. If the user asks
something the data does not cover, say "The brief doesn't cover that."

Original gathered data:
{json.dumps(initial_payload, indent=2, default=str)}

Conversation so far:
{transcript}

Now write your next reply as Assistant. Be concise — usually 2-4 short
paragraphs. Markdown formatting is fine. Do not repeat the original brief
verbatim unless the user explicitly asks for it.
"""
    response_text = _openai_call(prompt, max_tokens=900)

    # 4. Append the assistant's reply
    async with Client(CONVERSATION_URL) as conv_client:
        await conv_client.call_tool(
            "add_turn",
            {
                "conversation_id": conversation_id,
                "role": "assistant",
                "content": response_text,
            },
        )

    return {
        "conversation_id": conversation_id,
        "response": response_text,
        "turn_count": len(turns) + 2,  # the +2 = user question + this reply
    }


if __name__ == "__main__":
    mcp.run(transport="http", host="0.0.0.0", port=8005)