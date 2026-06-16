# Task 17 + 18: Critic MCP Server — now returns LLM usage (Day 9).
from synapse.tracing import setup_tracing, tracer
setup_tracing("critic-server")

import os
import json

from dotenv import load_dotenv
from fastmcp import FastMCP

from synapse.costs import extract_usage, empty_usage

load_dotenv()
mcp = FastMCP("Critic Server")


def _llm_review(prompt: str) -> tuple[dict, dict]:
    """Returns (parsed_response, usage_dict)."""
    from openai import OpenAI
    client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
    response = client.chat.completions.create(
        model="gpt-5-nano",
        messages=[{"role": "user", "content": prompt}],
        response_format={"type": "json_object"},
    )
    usage = extract_usage(response, model="gpt-5-nano")
    parsed = json.loads(response.choices[0].message.content)
    return parsed, usage


@mcp.tool
def review_brief(topic: str, article: str, source_payload: dict) -> dict:
    """Review a draft and decide approve / revise. Returns usage for cost tracking."""
    with tracer.start_as_current_span("critic.review_brief") as span:
        span.set_attribute("topic", topic)
        span.set_attribute("article_length", len(article))

        prompt = f"""You are a strict but fair editor reviewing a news brief before it's published.

Topic: "{topic}"

Source data the brief should be built from:
{json.dumps(source_payload, indent=2, default=str)}

Brief to review:
---
{article}
---

Your job: decide whether this brief ships as-is, or needs a targeted revision.

Required structure:
- A headline
- 2-3 paragraphs of news content
- A "Why it matters" section
- An "About the place of news" section with weather and conversion rate

Concrete things to flag (these warrant revision):
- Invented facts not present in the source data (hallucination)
- Missing one of the four required sections above
- Generic language where the source provides specifics (e.g. brief says "a major investment" when source has "$500M")
- Excessive hedging ("may", "could", "potentially", "experts believe") that obscures the actual story
- Internal contradictions between sections

Things to IGNORE (do not request revisions for these):
- Minor stylistic preferences
- Word choice that doesn't change meaning
- Section ordering
- Length within reason

Respond with JSON only:
{{
  "decision": "approve" | "revise",
  "issues": ["<concrete, actionable issue>", ...],
  "reasoning": "<one short sentence>"
}}

Rules:
- If decision is "approve", issues MUST be an empty list.
- If decision is "revise", issues MUST contain at least one specific, actionable item.
- Lean toward "approve" — only request revisions when issues are concrete and material.
"""
        try:
            review, usage = _llm_review(prompt)
            decision = str(review.get("decision", "approve")).strip().lower()
            if decision not in ("approve", "revise"):
                decision = "approve"

            issues = review.get("issues", [])
            if not isinstance(issues, list):
                issues = []
            issues = [str(i).strip() for i in issues if str(i).strip()]

            if decision == "approve":
                issues = []
            if decision == "revise" and not issues:
                decision = "approve"

            result = {
                "decision": decision,
                "issues": issues,
                "reasoning": str(review.get("reasoning", "")).strip(),
                "_usage": usage,  # NEW (Day 9): expose token usage
            }
            span.set_attribute("decision", decision)
            span.set_attribute("issue_count", len(issues))
            span.set_attribute("usage.total_tokens", usage["total_tokens"])
            return result
        except Exception as e:
            span.set_attribute("status", "failed_safe_approve")
            span.record_exception(e)
            return {
                "decision": "approve", "issues": [],
                "reasoning": f"Critic failed, defaulting to approve. ({e})",
                "_usage": empty_usage(),
            }


if __name__ == "__main__":
    mcp.run(transport="http", host="0.0.0.0", port=8010)