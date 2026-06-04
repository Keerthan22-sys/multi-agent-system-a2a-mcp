# Task 14 (Day 5): Router MCP Server — dynamic decisions about tool use and intent.
# Same FastMCP pattern as the rest of your servers.
# Two tools, both LLM-powered with structured JSON output:
#   - route_tools(topic)               → which tool servers to invoke
#   - route_intent(message, context)   → follow-up vs pivot in a conversation
import os
import json

from dotenv import load_dotenv
from fastmcp import FastMCP

load_dotenv()

mcp = FastMCP("Router Server")


# ---------- Helpers ----------

def _llm_decide(prompt: str) -> dict:
    """
    Call the OpenAI chat-completions API in JSON mode.
    Caller is responsible for handling JSON parse failures.
    """
    from openai import OpenAI
    client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
    response = client.chat.completions.create(
        model="gpt-5-nano",
        messages=[{"role": "user", "content": prompt}],
        response_format={"type": "json_object"},
    )
    return json.loads(response.choices[0].message.content)


def _coerce_bool(value, default: bool = True) -> bool:
    """Be tolerant of LLMs returning 'yes'/'true'/1 etc."""
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        return value.strip().lower() in ("true", "yes", "1", "y")
    return default


# ---------- Tools ----------

@mcp.tool
def route_tools(topic: str) -> dict:
    """
    Decide which tool servers are relevant for the given topic.
    Returns boolean flags + a one-sentence rationale.

    Default behavior on any failure: enable everything (fail safe).
    """
    prompt = f"""You decide which data sources a news-brief system should query for a topic.

Topic: "{topic}"

Available sources:
- news: recent news headlines about the topic (almost always relevant)
- weather: current weather at the location (relevant only if topic involves location, climate, outdoor events, travel, sports played outdoors, agriculture)
- fx: currency exchange rate (relevant only if topic involves money, business, trade, finance, markets, economy, prices, GDP, valuation)
- media: a stock image to illustrate the brief (usually true for visual richness; skip only for very abstract topics like math proofs)

Respond with JSON only, exactly this shape:
{{
  "use_news": true|false,
  "use_weather": true|false,
  "use_fx": true|false,
  "use_media": true|false,
  "reasoning": "<one short sentence explaining the choice>"
}}

Lean toward inclusion when unsure. Skip only when the source is clearly irrelevant.
"""
    try:
        decision = _llm_decide(prompt)
        return {
            "use_news": _coerce_bool(decision.get("use_news"), True),
            "use_weather": _coerce_bool(decision.get("use_weather"), True),
            "use_fx": _coerce_bool(decision.get("use_fx"), True),
            "use_media": _coerce_bool(decision.get("use_media"), True),
            "reasoning": str(decision.get("reasoning", "")).strip(),
        }
    except Exception as e:
        # Fail safe: enable everything if routing fails
        return {
            "use_news": True,
            "use_weather": True,
            "use_fx": True,
            "use_media": True,
            "reasoning": f"Routing failed, defaulting to all tools enabled. ({e})",
        }


@mcp.tool
def route_intent(
    message: str,
    conversation_topic: str = "",
    recent_turns: list = None,
) -> dict:
    """
    Classify a chat message as either a follow-up about the current conversation
    or a pivot to a new topic that would warrant a fresh brief.

    Default behavior on any failure: treat as follow-up (fail safe — never blocks).
    """
    recent_turns = recent_turns or []

    # Show only the last 4 turns to the model — keeps the prompt small.
    turns_text = ""
    for t in recent_turns[-4:]:
        role = t.get("role", "?")
        content = (t.get("content") or "")[:240]
        turns_text += f"{role}: {content}\n\n"
    if not turns_text:
        turns_text = "(no prior turns)"

    prompt = f"""You are an intent classifier for a chat assistant that publishes news briefs.

Current conversation topic: "{conversation_topic or 'unknown'}"

Recent conversation:
{turns_text}

New user message: "{message}"

Decide which of these the new message is:
- "follow_up": the user is elaborating on, asking about, or referring to the current topic — even if loosely
- "pivot": the user is moving to a clearly different topic that would require a fresh news brief

Respond with JSON only, exactly this shape:
{{
  "intent": "follow_up" | "pivot",
  "suggested_topic": "<if pivot, a clean short topic for the new brief; empty string otherwise>",
  "confidence": 0.0,
  "reasoning": "<one short sentence>"
}}

Lean strongly toward "follow_up". Only return "pivot" when the new topic has no meaningful overlap with the current one.
"""
    try:
        decision = _llm_decide(prompt)
        intent = str(decision.get("intent", "follow_up")).strip().lower()
        if intent not in ("follow_up", "pivot"):
            intent = "follow_up"
        try:
            confidence = float(decision.get("confidence", 0.5))
        except (TypeError, ValueError):
            confidence = 0.5
        return {
            "intent": intent,
            "suggested_topic": str(decision.get("suggested_topic", "")).strip(),
            "confidence": max(0.0, min(1.0, confidence)),
            "reasoning": str(decision.get("reasoning", "")).strip(),
        }
    except Exception as e:
        return {
            "intent": "follow_up",
            "suggested_topic": "",
            "confidence": 0.0,
            "reasoning": f"Intent routing failed, defaulting to follow_up. ({e})",
        }


if __name__ == "__main__":
    mcp.run(transport="http", host="0.0.0.0", port=8008)