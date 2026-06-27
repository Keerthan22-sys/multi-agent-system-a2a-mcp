# Task 9 + 12 + 13 + 15 + 17 + 18: Publisher Agent — now tracks total LLM cost (Day 9).
from synapse.tracing import setup_tracing, tracer
setup_tracing("publisher-agent")

import os
import json
import asyncio
from fastmcp import Client, FastMCP
from dotenv import load_dotenv

from synapse.costs import extract_usage, empty_usage, accumulate
from synapse.config import MEMORY_URL, CONVERSATION_URL, CRITIC_URL

load_dotenv()

mcp = FastMCP("Publisher Agent")

MAX_TURNS_IN_PROMPT = 10

CRITIC_ENABLED = os.getenv("SYNAPSE_ENABLE_CRITIC", "true").lower() == "true"
MAX_REVISIONS = int(os.getenv("SYNAPSE_MAX_REVISIONS", "2"))


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
    recent = turns[-MAX_TURNS_IN_PROMPT:]
    lines = []
    for t in recent:
        speaker = "User" if t.get("role") == "user" else "Assistant"
        lines.append(f"{speaker}: {t.get('content', '')}")
    return "\n\n".join(lines)


def _openai_call_with_usage(prompt: str, max_tokens: int = 1500) -> tuple[str, dict]:
    """LLM call that also returns extracted usage info."""
    from openai import OpenAI
    client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
    response = client.responses.create(
        model="gpt-5-nano",
        input=prompt,
        max_output_tokens=max_tokens,
        reasoning={"effort": "low"},
    )
    usage = extract_usage(response, model="gpt-5-nano")
    return response.output_text.strip(), usage


def _build_initial_prompt(payload_for_prompt: dict, memory_section: str) -> str:
    return f"""
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


def _build_revision_prompt(payload_for_prompt: dict, current_draft: str, issues: list) -> str:
    issues_text = "\n".join(f"  - {issue}" for issue in issues)
    return f"""
You are revising a news brief based on specific editor feedback.

Original source data:
{json.dumps(payload_for_prompt, indent=2, default=str)}

Current draft:
---
{current_draft}
---

Editor's issues to fix:
{issues_text}

Produce a revised brief that addresses ALL the issues above. Maintain the required
structure (headline; 2-3 paragraphs; "Why it matters" section; "About the place
of news" section with weather and conversion rate).

Critical rules:
- Stay grounded in the source data. Do NOT invent facts to fix specificity issues.
- Keep what was already working from the current draft.
- If an issue contradicts the source data, prefer the source data.
"""


async def _critic_review(topic: str, draft: str, payload_for_prompt: dict) -> dict:
    try:
        async with Client(CRITIC_URL) as critic_client:
            res = await critic_client.call_tool(
                "review_brief",
                {
                    "topic": topic,
                    "article": draft,
                    "source_payload": payload_for_prompt,
                },
            )
            return res.data
    except Exception as e:
        print(f"[publisher] Critic unavailable: {e}")
        return {
            "decision": "approve", "issues": [],
            "reasoning": f"Critic unreachable: {e}",
            "_usage": empty_usage(),
        }


@mcp.tool
async def publish_brief(payload: dict) -> dict:
    """Generate the INITIAL brief with internal critique loop + cost tracking."""
    with tracer.start_as_current_span("publisher.publish_brief") as root:
        topic = payload.get("topic", "Unknown")
        root.set_attribute("topic", topic)
        root.set_attribute("city", payload.get("location", ""))
        root.set_attribute("critic_enabled", CRITIC_ENABLED)

        memory_context = payload.get("memory_context", {})
        payload_for_prompt = {k: v for k, v in payload.items() if k != "memory_context"}
        memory_section = _build_memory_section(memory_context)
        memory_hits = len(memory_context.get("briefs", []))
        root.set_attribute("memory_hits", memory_hits)

        # NEW (Day 9): consolidated usage accumulator
        usage_total = {
            "input_tokens": 0, "output_tokens": 0, "total_tokens": 0,
            "cost_usd": 0.0, "calls": 0,
            "by_source": {"publisher": empty_usage(), "critic": empty_usage(),
                          "router": empty_usage()},
        }
        # Track usage from Router (came in with the payload via Scout)
        router_usage = (
            (payload.get("routing_decision") or {}).get("_usage") or empty_usage()
        )
        accumulate(usage_total, router_usage)
        usage_total["by_source"]["router"] = router_usage

        # ---------- Stage 1: initial draft ----------
        initial_prompt = _build_initial_prompt(payload_for_prompt, memory_section)
        with tracer.start_as_current_span("publisher.llm_initial_draft"):
            draft, draft_usage = _openai_call_with_usage(initial_prompt, max_tokens=1500)
        accumulate(usage_total, draft_usage)
        accumulate(usage_total["by_source"]["publisher"], draft_usage)

        # ---------- Stage 2: critique loop ----------
        critique_history = []
        revision_count = 0
        approved_on_attempt = 1
        final_decision = "approve"

        if CRITIC_ENABLED:
            for attempt in range(MAX_REVISIONS + 1):
                with tracer.start_as_current_span("publisher.critique_round") as round_span:
                    round_span.set_attribute("attempt", attempt + 1)

                    review = await _critic_review(topic, draft, payload_for_prompt)
                    decision = review.get("decision", "approve")
                    issues = review.get("issues", [])

                    # Accumulate critic's usage
                    critic_usage = review.get("_usage") or empty_usage()
                    accumulate(usage_total, critic_usage)
                    accumulate(usage_total["by_source"]["critic"], critic_usage)

                    round_span.set_attribute("decision", decision)
                    round_span.set_attribute("issue_count", len(issues))

                    critique_history.append({
                        "attempt": attempt + 1,
                        "draft_excerpt": (draft[:280] + "...") if len(draft) > 280 else draft,
                        "decision": decision,
                        "issues": issues,
                        "reasoning": review.get("reasoning", ""),
                    })

                    final_decision = decision
                    if decision == "approve":
                        approved_on_attempt = attempt + 1
                        break

                    if attempt >= MAX_REVISIONS:
                        approved_on_attempt = attempt + 1
                        break

                    revision_count += 1
                    revision_prompt = _build_revision_prompt(
                        payload_for_prompt, draft, issues
                    )
                    with tracer.start_as_current_span("publisher.llm_revise"):
                        draft, revise_usage = _openai_call_with_usage(
                            revision_prompt, max_tokens=1500
                        )
                    accumulate(usage_total, revise_usage)
                    accumulate(usage_total["by_source"]["publisher"], revise_usage)

        final_article = draft
        root.set_attribute("revision_count", revision_count)
        root.set_attribute("approved_on_attempt", approved_on_attempt)
        root.set_attribute("article_length", len(final_article))
        # NEW (Day 9): cost attributes
        root.set_attribute("usage.total_tokens", usage_total["total_tokens"])
        root.set_attribute("usage.calls", usage_total["calls"])
        root.set_attribute("usage.cost_usd", round(usage_total["cost_usd"], 6))

        # ---------- Stage 3: persist (unchanged) ----------
        brief_id = ""
        with tracer.start_as_current_span("publisher.memory_store") as mem_span:
            try:
                async with Client(MEMORY_URL) as memory_client:
                    mem_res = await memory_client.call_tool(
                        "store_brief",
                        {
                            "topic": topic,
                            "article": final_article,
                            "payload": payload_for_prompt,
                            "city": payload.get("location", ""),
                        },
                    )
                    brief_id = (mem_res.data or {}).get("brief_id", "")
                    mem_span.set_attribute("brief_id", brief_id)
            except Exception as e:
                mem_span.record_exception(e)
                print(f"[publisher] Memory store failed: {e}")

        conversation_id = ""
        with tracer.start_as_current_span("publisher.conversation_seed") as conv_span:
            try:
                async with Client(CONVERSATION_URL) as conv_client:
                    conv_res = await conv_client.call_tool(
                        "start_conversation",
                        {
                            "topic": topic,
                            "city": payload.get("location", ""),
                            "initial_payload": payload_for_prompt,
                            "initial_article": final_article,
                            "brief_id": brief_id,
                        },
                    )
                    conversation_id = (conv_res.data or {}).get("conversation_id", "")
                    conv_span.set_attribute("conversation_id", conversation_id)
            except Exception as e:
                conv_span.record_exception(e)

        # NEW (Day 9): extract cache hit summary from the payload's context
        cache_hits = (
            payload.get("context", {}).get("cache_hits", {})
            if isinstance(payload.get("context"), dict) else {}
        )

        return {
            "article": final_article,
            "payload": payload,
            "memory_used": memory_hits,
            "brief_id": brief_id,
            "conversation_id": conversation_id,
            "critic_enabled": CRITIC_ENABLED,
            "revision_count": revision_count,
            "approved_on_attempt": approved_on_attempt,
            "critique_history": critique_history,
            # NEW (Day 9)
            "usage": usage_total,
            "cache_hits": cache_hits,
        }


@mcp.tool
async def follow_up(conversation_id: str, user_question: str) -> dict:
    """Answer a follow-up. Tracks single-call usage."""
    with tracer.start_as_current_span("publisher.follow_up") as root:
        root.set_attribute("conversation_id", conversation_id)
        root.set_attribute("question_length", len(user_question))

        with tracer.start_as_current_span("publisher.fetch_conversation"):
            async with Client(CONVERSATION_URL) as conv_client:
                conv_res = await conv_client.call_tool(
                    "get_conversation", {"conversation_id": conversation_id},
                )
                conversation = conv_res.data or {}
        if conversation.get("error"):
            return {"error": conversation["error"]}

        initial_payload = conversation.get("initial_payload", {})
        turns = conversation.get("turns", [])

        async with Client(CONVERSATION_URL) as conv_client:
            await conv_client.call_tool(
                "add_turn",
                {"conversation_id": conversation_id, "role": "user", "content": user_question},
            )

        transcript = _render_turns_for_prompt(
            turns + [{"role": "user", "content": user_question}]
        )
        prompt = f"""
You are continuing a conversation about a news brief you previously published.

Stay grounded in the original data. Do not invent facts. If the user asks
something the data does not cover, say "The brief doesn't cover that."

Original gathered data:
{json.dumps(initial_payload, indent=2, default=str)}

Conversation so far:
{transcript}

Now write your next reply as Assistant. Be concise — usually 2-4 short
paragraphs. Markdown formatting is fine.
"""
        with tracer.start_as_current_span("publisher.llm_followup"):
            response_text, usage = _openai_call_with_usage(prompt, max_tokens=900)
        root.set_attribute("response_length", len(response_text))
        root.set_attribute("usage.total_tokens", usage["total_tokens"])

        async with Client(CONVERSATION_URL) as conv_client:
            await conv_client.call_tool(
                "add_turn",
                {"conversation_id": conversation_id, "role": "assistant", "content": response_text},
            )

        return {
            "conversation_id": conversation_id,
            "response": response_text,
            "turn_count": len(turns) + 2,
            "usage": {  # NEW (Day 9)
                **usage, "calls": 1,
                "by_source": {"publisher": usage},
            },
        }


if __name__ == "__main__":
    mcp.run(transport="http", host="0.0.0.0", port=8005)