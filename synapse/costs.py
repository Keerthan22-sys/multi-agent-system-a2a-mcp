# synapse/costs.py — Token accounting + cost estimation (Day 9).
#
# Two APIs to handle: Responses (Publisher) uses input_tokens/output_tokens.
# Chat Completions (Router, Critic, UI city detection) uses prompt_tokens/completion_tokens.
# extract_usage() normalizes both into a single shape.
import os
from typing import Any


# Approximate pricing per 1M tokens (USD). Update as actual pricing changes.
# These are model-family approximations — gpt-5-nano is light enough that even
# 2x error here barely changes the qualitative read.
PRICING_USD_PER_1M = {
    "gpt-5-nano": {"input": 0.05, "output": 0.40},
    # Fallback for unknown models
    "default":   {"input": 0.05, "output": 0.40},
}

# Display rate for cost in INR. Update as needed.
USD_TO_INR = float(os.getenv("SYNAPSE_USD_TO_INR", "84.0"))


def _pricing(model: str) -> dict:
    return PRICING_USD_PER_1M.get(model, PRICING_USD_PER_1M["default"])


def estimate_cost_usd(model: str, input_tokens: int, output_tokens: int) -> float:
    p = _pricing(model)
    return (
        input_tokens * p["input"] / 1_000_000
        + output_tokens * p["output"] / 1_000_000
    )


def extract_usage(response: Any, model: str = "gpt-5-nano") -> dict:
    """
    Normalize usage info from either Responses or Chat Completions API responses.
    Returns: {model, input_tokens, output_tokens, total_tokens, cost_usd}
    """
    if not hasattr(response, "usage") or response.usage is None:
        return {
            "model": model,
            "input_tokens": 0, "output_tokens": 0, "total_tokens": 0,
            "cost_usd": 0.0,
        }
    u = response.usage
    # Responses API: input_tokens / output_tokens
    # Chat Completions: prompt_tokens / completion_tokens
    input_tokens = (
        getattr(u, "input_tokens", None)
        or getattr(u, "prompt_tokens", None)
        or 0
    )
    output_tokens = (
        getattr(u, "output_tokens", None)
        or getattr(u, "completion_tokens", None)
        or 0
    )
    total = getattr(u, "total_tokens", None) or (input_tokens + output_tokens)
    return {
        "model": model,
        "input_tokens": int(input_tokens),
        "output_tokens": int(output_tokens),
        "total_tokens": int(total),
        "cost_usd": estimate_cost_usd(model, input_tokens, output_tokens),
    }


def empty_usage() -> dict:
    return {
        "model": "",
        "input_tokens": 0, "output_tokens": 0, "total_tokens": 0,
        "cost_usd": 0.0,
    }


def accumulate(acc: dict, addition: dict) -> dict:
    """
    Sum two usage dicts. Returns the accumulator (also mutated in place).
    Use shape: {input_tokens, output_tokens, total_tokens, cost_usd, calls, by_source}
    """
    acc.setdefault("input_tokens", 0)
    acc.setdefault("output_tokens", 0)
    acc.setdefault("total_tokens", 0)
    acc.setdefault("cost_usd", 0.0)
    acc.setdefault("calls", 0)

    if not addition:
        return acc

    acc["input_tokens"] += int(addition.get("input_tokens", 0))
    acc["output_tokens"] += int(addition.get("output_tokens", 0))
    acc["total_tokens"] += int(addition.get("total_tokens", 0))
    acc["cost_usd"] += float(addition.get("cost_usd", 0.0))
    acc["calls"] += 1
    return acc


def format_cost_inr(cost_usd: float) -> str:
    """Format a USD cost as INR for UI display."""
    inr = cost_usd * USD_TO_INR
    if inr < 0.01:
        return f"<₹0.01"
    if inr < 1:
        return f"₹{inr:.3f}"
    return f"₹{inr:.2f}"