# Task 17 (Day 8): Critic MCP Server — reviews briefs, returns approve/revise decisions.
from synapse.tracing import setup_tracing, tracer
setup_tracing("critic-server")

import os
import json

from dotenv import load_dotenv
from fastmcp import FastMCP

load_dotenv()
mcp = FastMCP("Critic Server")


def _llm_review(prompt: str) -> dict:
    from openai import OpenAI
    client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
    response = client.chat.completions.create(
        model="gpt-5-nano",
        messages=[{"role": "user", "content": prompt}],
        response_format={"type": "json_object"},
    )
    return json.loads(response.choices[0].message.content)


@mcp.tool
def review_brief(
    topic: str,
    article: str,
    source_payload: dict,
) -> dict:
    """
    Review a draft brief and decide whether it ships or needs revision.
    Returns: {decision, issues, reasoning}

    Designed to be CONSERVATIVE about requesting revisions — only flags
    concrete, fixable issues. Style nitpicks don't trigger revisions.
    """
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

Respond with JSON only, exactly this shape:
{{
  "decision": "approve" | "revise",
  "issues": ["<concrete, actionable issue>", "<another concrete issue>", ...],
  "reasoning": "<one short sentence summarizing your decision>"
}}

Rules:
- If decision is "approve", issues MUST be an empty list.
- If decision is "revise", issues MUST contain at least one specific, actionable item the writer can fix.
- Each issue should reference what's wrong AND point to the fix when possible.
- Lean toward "approve" — only request revisions when issues are concrete and material.
"""
        try:
            review = _llm_review(prompt)
            decision = str(review.get("decision", "approve")).strip().lower()
            if decision not in ("approve", "revise"):
                decision = "approve"

            issues = review.get("issues", [])
            if not isinstance(issues, list):
                issues = []
            issues = [str(i).strip() for i in issues if str(i).strip()]

            # Sanity check: if approve, issues must be empty
            if decision == "approve":
                issues = []

            # If revise but no issues, downgrade to approve (model contradiction)
            if decision == "revise" and not issues:
                decision = "approve"

            result = {
                "decision": decision,
                "issues": issues,
                "reasoning": str(review.get("reasoning", "")).strip(),
            }
            span.set_attribute("decision", decision)
            span.set_attribute("issue_count", len(issues))
            span.set_attribute("reasoning", result["reasoning"])
            return result
        except Exception as e:
            # Fail-safe: if the critic can't do its job, ship the brief
            span.set_attribute("status", "failed_safe_approve")
            span.record_exception(e)
            return {
                "decision": "approve",
                "issues": [],
                "reasoning": f"Critic failed, defaulting to approve. ({e})",
            }


if __name__ == "__main__":
    mcp.run(transport="http", host="0.0.0.0", port=8010)