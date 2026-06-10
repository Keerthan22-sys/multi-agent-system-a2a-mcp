"""
SYNAPSE Evaluation Runner (Day 7)

Runs every topic in evals/dataset.json through the full Synapse pipeline
(Scout → Publisher) and scores the produced brief via the eval server's LLM-as-judge.
Stores results to evals/results/runs.json for the UI to display.

Usage:
    # Full run (~3-5 minutes for 20 topics)
    python evals/run_eval.py

    # Smoke test on 3 topics
    python evals/run_eval.py --limit 3

    # Custom dataset
    python evals/run_eval.py --dataset path/to/other.json

Note: each topic generates a real brief that gets stored in the memory MCP server.
If you want a clean baseline across runs, clear synapse/memory_store/ between runs.
"""
import argparse
import asyncio
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from fastmcp import Client

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DATASET = REPO_ROOT / "evals" / "dataset.json"

SCOUT_URL = "http://0.0.0.0:8004/mcp"
PUBLISHER_URL = "http://0.0.0.0:8005/mcp"
EVAL_URL = "http://0.0.0.0:8009/mcp"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


async def _run_pipeline(topic: str, city: str) -> tuple[dict, dict]:
    """Run Scout → Publisher for a single topic. Returns (scout_data, publisher_data)."""
    async with Client(SCOUT_URL) as scout:
        scout_res = await scout.call_tool("scout", {"topic": topic, "city": city})
        scout_data = scout_res.data
    async with Client(PUBLISHER_URL) as pub:
        pub_res = await pub.call_tool("publish_brief", {"payload": scout_data})
        pub_data = pub_res.data
    return scout_data, pub_data


async def _judge(topic: str, article: str, source_payload: dict, rubric_hints: str) -> dict:
    """Send a brief to the eval server's judge tool."""
    async with Client(EVAL_URL) as ev:
        res = await ev.call_tool("judge_brief", {
            "topic": topic,
            "article": article,
            "source_payload": source_payload,
            "rubric_hints": rubric_hints,
        })
        return res.data


async def _store(run_id: str, run_data: dict):
    async with Client(EVAL_URL) as ev:
        await ev.call_tool("store_eval_run", {"run_id": run_id, "run_data": run_data})


def _aggregate(results: list) -> dict:
    valid = [r for r in results if r.get("scores") and not r["scores"].get("error")]
    if not valid:
        return {}
    aggs = {}
    for dim in ("faithfulness", "coverage", "specificity", "hallucination", "overall"):
        scores = [r["scores"].get(dim, 0) for r in valid]
        aggs[dim] = round(sum(scores) / len(scores), 3) if scores else 0
    return aggs


async def run_eval(dataset_path: Path, limit: int | None = None):
    dataset = json.loads(dataset_path.read_text())
    if limit:
        dataset = dataset[:limit]

    run_id = f"eval-{datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S')}"
    started_at = _now_iso()
    t0 = time.time()

    print(f"\n🧪 SYNAPSE Eval Run: {run_id}")
    print(f"   Dataset: {dataset_path.name}")
    print(f"   Topics:  {len(dataset)}")
    print(f"   Started: {started_at}\n")

    results = []

    for i, entry in enumerate(dataset, start=1):
        topic = entry["topic"]
        city = entry.get("expected_city", "")
        rubric = entry.get("rubric_hints", "")

        print(f"[{i:>2}/{len(dataset)}] {topic[:60]}")
        t_topic = time.time()

        result = {
            "topic_id": entry.get("id", f"topic-{i}"),
            "topic": topic,
            "city": city,
            "tags": entry.get("tags", []),
            "rubric_hints": rubric,
            "started_at": _now_iso(),
        }

        try:
            scout_data, pub_data = await _run_pipeline(topic, city)
            article = pub_data.get("article", "")
            payload = {
                k: v for k, v in pub_data.get("payload", {}).items()
                if k != "memory_context"
            }
            judgement = await _judge(topic, article, payload, rubric)

            result.update({
                "article": article,
                "article_excerpt": article[:300] + ("..." if len(article) > 300 else ""),
                "scores": judgement,
                "brief_id": pub_data.get("brief_id", ""),
                "conversation_id": pub_data.get("conversation_id", ""),
                "elapsed_seconds": round(time.time() - t_topic, 2),
            })

            overall = judgement.get("overall", 0)
            print(f"        overall={overall:.2f}  "
                  f"faith={judgement.get('faithfulness', 0):.2f}  "
                  f"cov={judgement.get('coverage', 0):.2f}  "
                  f"spec={judgement.get('specificity', 0):.2f}  "
                  f"halluc={judgement.get('hallucination', 0):.2f}")
        except Exception as e:
            result["error"] = str(e)
            result["scores"] = None
            print(f"        ❌ ERROR: {e}")

        results.append(result)

    aggregates = _aggregate(results)
    successful = sum(1 for r in results if r.get("scores") and not r["scores"].get("error"))
    elapsed = round(time.time() - t0, 1)

    run_data = {
        "run_id": run_id,
        "started_at": started_at,
        "finished_at": _now_iso(),
        "elapsed_seconds": elapsed,
        "dataset_path": str(dataset_path.relative_to(REPO_ROOT)),
        "dataset_size": len(dataset),
        "successful": successful,
        "failed": len(results) - successful,
        "aggregates": aggregates,
        "results": results,
    }

    try:
        await _store(run_id, run_data)
        print(f"\n✅ Stored run {run_id}")
    except Exception as e:
        # Fallback: write directly to disk so results aren't lost
        fallback = REPO_ROOT / "evals" / "results" / f"{run_id}.json"
        fallback.parent.mkdir(parents=True, exist_ok=True)
        fallback.write_text(json.dumps(run_data, indent=2, default=str))
        print(f"\n⚠️  Eval server unavailable; saved locally to {fallback}")
        print(f"    ({e})")

    print(f"\n📊 Run Summary ({elapsed}s)")
    print(f"   Successful: {successful}/{len(dataset)}")
    if aggregates:
        for dim in ("faithfulness", "coverage", "specificity", "hallucination", "overall"):
            bar = "█" * int(aggregates.get(dim, 0) * 20)
            print(f"   {dim:>15}: {aggregates.get(dim, 0):.3f}  {bar}")

    return run_data


def _parse_args():
    parser = argparse.ArgumentParser(description="SYNAPSE eval runner")
    parser.add_argument("--limit", type=int, default=None,
                        help="Run only the first N topics (smoke test)")
    parser.add_argument("--dataset", type=str, default=str(DEFAULT_DATASET),
                        help="Path to dataset JSON")
    return parser.parse_args()


if __name__ == "__main__":
    args = _parse_args()
    dataset_path = Path(args.dataset).resolve()
    if not dataset_path.exists():
        print(f"❌ Dataset not found: {dataset_path}", file=sys.stderr)
        sys.exit(1)
    asyncio.run(run_eval(dataset_path, limit=args.limit))