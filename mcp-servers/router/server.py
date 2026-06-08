# Task 14 + 15: Router MCP Server — now traced (Day 6).
from synapse.tracing import setup_tracing, tracer
setup_tracing("router-server")

import os
import json
from dotenv import load_dotenv
from fastmcp import FastMCP

load_dotenv()
mcp = FastMCP("Router Server")


def _llm_decide(prompt: str) -> dict:
    from openai import OpenAI
    client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
    response = client.chat.completions.create(
        model="gpt-5-nano",
        messages=[{"role": "user", "content": prompt}],
        response_format={"type": "json_object"},
    )
    return json.loads(response.choices[0].message.content)


def _coerce_bool(value, default: bool = True) -> bool:
    if isinstance(value, bool): return value
    if isinstance(value, (int, float)): return bool(value)
    if isinstance(value, str):
        return value.strip().lower() in ("true", "yes", "1", "y")
    return default


@mcp.tool
def route_tools(topic: str) -> dict:
    """Decide which tool servers are relevant for the given topic."""
    with tracer.start_as_current_span("router.route_tools") as span:
        span.set_attribute("topic", topic)

        prompt = f"""You decide which data sources a news-brief system should query for a topic.

Topic: "{topic}"

Available sources:
- news: recent news headlines about the topic (almost always relevant)
- weather: current weather at the location (relevant only if topic involves location, climate, outdoor events, travel, sports played outdoors, agriculture)
- fx: currency exchange rate (relevant only if topic involves money, business, trade, finance, markets, economy, prices, GDP, valuation)
- media: a stock image to illustrate the brief (usually true for visual richness; skip only for very abstract topics like math proofs)

Respond with JSON only:
{{
  "use_news": true|false, "use_weather": true|false,
  "use_fx": true|false, "use_media": true|false,
  "reasoning": "<one short sentence>"
}}

Lean toward inclusion when unsure.
"""
        try:
            decision = _llm_decide(prompt)
            result = {
                "use_news": _coerce_bool(decision.get("use_news"), True),
                "use_weather": _coerce_bool(decision.get("use_weather"), True),
                "use_fx": _coerce_bool(decision.get("use_fx"), True),
                "use_media": _coerce_bool(decision.get("use_media"), True),
                "reasoning": str(decision.get("reasoning", "")).strip(),
            }
            for k in ("use_news", "use_weather", "use_fx", "use_media"):
                span.set_attribute(k, result[k])
            span.set_attribute("reasoning", result["reasoning"])
            span.set_attribute("status", "ok")
            return result
        except Exception as e:
            span.set_attribute("status", "failed_safe_default")
            span.record_exception(e)
            return {
                "use_news": True, "use_weather": True,
                "use_fx": True, "use_media": True,
                "reasoning": f"Routing failed, defaulting to all tools enabled. ({e})",
            }


@mcp.tool
def route_intent(message: str, conversation_topic: str = "", recent_turns: list = None) -> dict:
    """Classify a message as follow-up or pivot."""
    with tracer.start_as_current_span("router.route_intent") as span:
        span.set_attribute("message_length", len(message))
        span.set_attribute("conversation_topic", conversation_topic or "")

        recent_turns = recent_turns or []
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

Decide:
- "follow_up": user is elaborating on or referring to the current topic
- "pivot": user is moving to a clearly different topic

Respond with JSON only:
{{
  "intent": "follow_up" | "pivot",
  "suggested_topic": "<if pivot, a short topic; else empty>",
  "confidence": 0.0,
  "reasoning": "<one sentence>"
}}

Lean strongly toward "follow_up".
"""
        try:
            decision = _llm_decide(prompt)
            intent = str(decision.get("intent", "follow_up")).strip().lower()
            if intent not in ("follow_up", "pivot"): intent = "follow_up"
            try:
                confidence = float(decision.get("confidence", 0.5))
            except (TypeError, ValueError):
                confidence = 0.5
            result = {
                "intent": intent,
                "suggested_topic": str(decision.get("suggested_topic", "")).strip(),
                "confidence": max(0.0, min(1.0, confidence)),
                "reasoning": str(decision.get("reasoning", "")).strip(),
            }
            span.set_attribute("intent", result["intent"])
            span.set_attribute("confidence", result["confidence"])
            span.set_attribute("suggested_topic", result["suggested_topic"])
            return result
        except Exception as e:
            span.set_attribute("status", "failed_safe_default")
            span.record_exception(e)
            return {
                "intent": "follow_up", "suggested_topic": "",
                "confidence": 0.0,
                "reasoning": f"Intent routing failed, defaulting to follow_up. ({e})",
            }


if __name__ == "__main__":
    mcp.run(transport="http", host="0.0.0.0", port=8008)