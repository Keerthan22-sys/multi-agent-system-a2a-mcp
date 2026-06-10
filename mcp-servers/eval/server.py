# Task 16 (Day 7): Evaluation MCP Server — LLM-as-judge + result storage.
from synapse.tracing import setup_tracing, tracer
setup_tracing("eval-server")

import os
import json
from datetime import datetime, timezone
from pathlib import Path
from threading import Lock

from dotenv import load_dotenv
from fastmcp import FastMCP

load_dotenv()

# Results live alongside the eval dataset, outside the synapse runtime package.
RESULTS_DIR = Path(__file__).resolve().parents[2] / "evals" / "results"
RESULTS_DIR.mkdir(parents=True, exist_ok=True)
RESULTS_PATH = RESULTS_DIR / "runs.json"
_lock = Lock()


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _read_all() -> dict:
    if not RESULTS_PATH.exists():
        RESULTS_PATH.write_text("{}")
        return {}
    try:
        return json.loads(RESULTS_PATH.read_text() or "{}")
    except json.JSONDecodeError:
        RESULTS_PATH.write_text("{}")
        return {}


def _write_all(data: dict) -> None:
    RESULTS_PATH.write_text(json.dumps(data, indent=2, default=str))


def _llm_judge(prompt: str) -> dict:
    from openai import OpenAI
    client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
    response = client.chat.completions.create(
        model="gpt-5-nano",
        messages=[{"role": "user", "content": prompt}],
        response_format={"type": "json_object"},
    )
    return json.loads(response.choices[0].message.content)


def _clamp_score(value, default: float = 0.0) -> float:
    try:
        v = float(value)
        return max(0.0, min(1.0, v))
    except (TypeError, ValueError):
        return default


mcp = FastMCP("Eval Server")


# ---------- Tools ----------

@mcp.tool
def judge_brief(
    topic: str,
    article: str,
    source_payload: dict,
    rubric_hints: str = "",
) -> dict:
    """
    LLM-as-judge: score a brief against five rubric dimensions.
    Returns dict with: faithfulness, coverage, specificity, hallucination, overall, reasoning.
    Each numeric score is in [0.0, 1.0].
    """
    with tracer.start_as_current_span("eval.judge_brief") as span:
        span.set_attribute("topic", topic)
        span.set_attribute("article_length", len(article))
        span.set_attribute("has_rubric_hints", bool(rubric_hints))

        hints_section = (
            f"\nDomain-specific scoring hints for this topic:\n{rubric_hints}\n"
            if rubric_hints else ""
        )

        prompt = f"""You are evaluating a news brief produced by an automated multi-agent system.

Topic the brief was generated for: "{topic}"

Source data the brief was supposed to be built from:
{json.dumps(source_payload, indent=2, default=str)}

The actual brief produced:
---
{article}
---
{hints_section}
Score the brief on 5 dimensions, each from 0.0 to 1.0:

1. faithfulness: Are factual claims in the brief actually present in the source data?
   1.0 = every claim is grounded in the source
   0.5 = some claims are reasonable extrapolations
   0.0 = many claims appear invented

2. coverage: Does the brief follow the required structure?
   Required: (a) headline, (b) 2-3 paragraphs of content, (c) a "Why it matters" section, (d) an "About the place of news" section with weather/conversion-rate info
   1.0 = all four present and substantial
   0.5 = some present but thin
   0.0 = missing or empty sections

3. specificity: Does the brief cite specific entities, names, numbers from the source?
   1.0 = highly specific (proper nouns, figures, dates)
   0.5 = mix of specific and generic
   0.0 = mostly generic statements

4. hallucination: Is the brief free of fabricated content? (Higher is better.)
   1.0 = no hallucinated facts whatsoever
   0.5 = minor embellishments
   0.0 = significant fabrication

5. overall: Holistic quality, weighted toward faithfulness and specificity.

Respond with JSON only, exactly this shape:
{{
  "faithfulness": 0.0,
  "coverage": 0.0,
  "specificity": 0.0,
  "hallucination": 0.0,
  "overall": 0.0,
  "reasoning": "<2-3 sentence justification highlighting strengths and weaknesses>"
}}
"""
        try:
            decision = _llm_judge(prompt)
            scores = {
                "faithfulness": _clamp_score(decision.get("faithfulness")),
                "coverage": _clamp_score(decision.get("coverage")),
                "specificity": _clamp_score(decision.get("specificity")),
                "hallucination": _clamp_score(decision.get("hallucination")),
                "overall": _clamp_score(decision.get("overall")),
                "reasoning": str(decision.get("reasoning", "")).strip(),
            }
            for k, v in scores.items():
                if k != "reasoning":
                    span.set_attribute(f"score.{k}", v)
            return scores
        except Exception as e:
            span.record_exception(e)
            return {
                "faithfulness": 0.0, "coverage": 0.0, "specificity": 0.0,
                "hallucination": 0.0, "overall": 0.0,
                "reasoning": f"Judge failed: {e}",
                "error": str(e),
            }


@mcp.tool
def store_eval_run(run_id: str, run_data: dict) -> dict:
    """Persist a completed eval run."""
    with tracer.start_as_current_span("eval.store_run") as span:
        span.set_attribute("run_id", run_id)
        with _lock:
            data = _read_all()
            data[run_id] = run_data
            _write_all(data)
        span.set_attribute("total_runs", len(data))
        return {"run_id": run_id, "stored": True, "total_runs": len(data)}


@mcp.tool
def list_eval_runs(limit: int = 20) -> dict:
    """Return all eval runs, newest first. Lightweight summary only."""
    with tracer.start_as_current_span("eval.list_runs") as span:
        with _lock:
            data = _read_all()
        runs = []
        for run_id, run_data in data.items():
            runs.append({
                "run_id": run_id,
                "started_at": run_data.get("started_at"),
                "dataset_size": run_data.get("dataset_size", 0),
                "successful": run_data.get("successful", 0),
                "failed": run_data.get("failed", 0),
                "aggregates": run_data.get("aggregates", {}),
            })
        runs.sort(key=lambda r: r.get("started_at") or "", reverse=True)
        span.set_attribute("total", len(runs))
        return {"runs": runs[:limit], "total": len(runs)}


@mcp.tool
def get_eval_run(run_id: str) -> dict:
    """Return the full data for a single eval run, including per-topic results."""
    with tracer.start_as_current_span("eval.get_run") as span:
        span.set_attribute("run_id", run_id)
        with _lock:
            data = _read_all()
        if run_id not in data:
            return {"error": f"Run {run_id} not found"}
        return data[run_id]


@mcp.tool
def delete_eval_run(run_id: str) -> dict:
    """Remove a single run (useful when discarding failed/exploratory runs)."""
    with tracer.start_as_current_span("eval.delete_run") as span:
        span.set_attribute("run_id", run_id)
        with _lock:
            data = _read_all()
            if run_id not in data:
                return {"run_id": run_id, "deleted": False, "error": "Not found"}
            del data[run_id]
            _write_all(data)
        return {"run_id": run_id, "deleted": True}


if __name__ == "__main__":
    mcp.run(transport="http", host="0.0.0.0", port=8009)