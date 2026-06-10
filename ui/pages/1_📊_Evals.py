# ui/pages/1_📊_Evals.py — Eval results dashboard (Day 7).
from synapse.tracing import setup_tracing, tracer
setup_tracing("ui-evals")

import asyncio
import os
import pandas as pd
import streamlit as st
from fastmcp import Client

EVAL_URL = "http://0.0.0.0:8009/mcp"
PHOENIX_UI_URL = os.getenv("PHOENIX_COLLECTOR_ENDPOINT", "http://localhost:6006")

DIMENSIONS = ["faithfulness", "coverage", "specificity", "hallucination", "overall"]


# ---------- Async helpers ----------
async def _call(tool, params):
    async with Client(EVAL_URL) as c:
        res = await c.call_tool(tool, params)
        return res.data


def list_eval_runs(limit=50):
    return asyncio.run(_call("list_eval_runs", {"limit": limit}))

def get_eval_run(run_id):
    return asyncio.run(_call("get_eval_run", {"run_id": run_id}))


# ---------- UI ----------
st.set_page_config(page_title="SYNAPSE — Evals", layout="wide")
st.title("📊 SYNAPSE — Evaluation Results")

with st.sidebar:
    st.link_button(
        "🔭 View traces in Phoenix",
        PHOENIX_UI_URL,
        use_container_width=True,
    )
    st.divider()
    st.caption(
        "💡 To run a new eval, execute "
        "`python evals/run_eval.py` from your repo root."
    )

try:
    runs_data = list_eval_runs(limit=50)
except Exception as e:
    st.error(f"Eval server unavailable: {e}")
    st.stop()

runs = runs_data.get("runs", [])
total = runs_data.get("total", 0)

if total == 0:
    st.info(
        "No eval runs yet.\n\n"
        "From your repo root:\n"
        "```bash\n"
        "python evals/run_eval.py --limit 3   # smoke test\n"
        "python evals/run_eval.py              # full 20-topic run\n"
        "```"
    )
    st.stop()


# ---------- Top section: summary metrics ----------
latest = runs[0]
latest_agg = latest.get("aggregates", {})

cols = st.columns(4)
cols[0].metric("Total runs", total)
cols[1].metric("Latest topics", latest.get("dataset_size", 0))
cols[2].metric("Latest avg overall", f"{latest_agg.get('overall', 0):.2f}")
cols[3].metric(
    "Success rate",
    f"{latest.get('successful', 0)}/{latest.get('dataset_size', 0)}",
)


# ---------- Trend chart ----------
if len(runs) >= 2:
    st.subheader("📈 Score trend across runs")
    trend_rows = []
    for r in reversed(runs):  # chronological order for the chart
        agg = r.get("aggregates", {})
        trend_rows.append({
            "run_id": r["run_id"][-6:],  # last 6 chars as short label
            "started_at": r.get("started_at", ""),
            **{dim: agg.get(dim, 0) for dim in DIMENSIONS},
        })
    trend_df = pd.DataFrame(trend_rows).set_index("run_id")[DIMENSIONS]
    st.line_chart(trend_df, height=280)


# ---------- Run picker ----------
st.subheader("📋 Run history")

run_options = {
    f"{r['run_id']}  ·  {r.get('started_at', '')[:16]}  ·  "
    f"avg overall {r.get('aggregates', {}).get('overall', 0):.2f}  ·  "
    f"{r.get('successful', 0)}/{r.get('dataset_size', 0)} topics"
    : r["run_id"]
    for r in runs
}
selected_label = st.selectbox("Select a run to inspect", list(run_options.keys()))
selected_run_id = run_options[selected_label]


# ---------- Selected run detail ----------
run = get_eval_run(selected_run_id)
if run.get("error"):
    st.error(run["error"])
    st.stop()

st.divider()
st.subheader(f"Run: `{run['run_id']}`")

meta_cols = st.columns(4)
meta_cols[0].metric("Dataset", run.get("dataset_path", "?").split("/")[-1])
meta_cols[1].metric("Topics", run.get("dataset_size", 0))
meta_cols[2].metric("Elapsed", f"{run.get('elapsed_seconds', 0):.1f}s")
meta_cols[3].metric("Failures", run.get("failed", 0))

st.markdown("**Score averages (this run)**")
agg = run.get("aggregates", {})
for dim in DIMENSIONS:
    score = agg.get(dim, 0)
    bar_cols = st.columns([1, 4, 1])
    bar_cols[0].caption(dim.capitalize())
    bar_cols[1].progress(min(max(score, 0.0), 1.0))
    bar_cols[2].caption(f"{score:.3f}")


# ---------- Per-topic table ----------
st.markdown("**Per-topic results**")

results = run.get("results", [])
table_rows = []
for r in results:
    scores = r.get("scores") or {}
    table_rows.append({
        "Topic ID": r.get("topic_id", ""),
        "Topic": r.get("topic", "")[:60],
        "Tags": ", ".join(r.get("tags", [])),
        "Overall": scores.get("overall"),
        "Faithfulness": scores.get("faithfulness"),
        "Coverage": scores.get("coverage"),
        "Specificity": scores.get("specificity"),
        "Hallucination": scores.get("hallucination"),
        "Elapsed (s)": r.get("elapsed_seconds"),
        "Status": "❌ ERROR" if r.get("error") else "✅",
    })

table_df = pd.DataFrame(table_rows)
st.dataframe(
    table_df,
    use_container_width=True,
    hide_index=True,
    column_config={
        "Overall": st.column_config.ProgressColumn(
            "Overall", format="%.2f", min_value=0.0, max_value=1.0,
        ),
        "Faithfulness": st.column_config.ProgressColumn(
            "Faithfulness", format="%.2f", min_value=0.0, max_value=1.0,
        ),
        "Coverage": st.column_config.ProgressColumn(
            "Coverage", format="%.2f", min_value=0.0, max_value=1.0,
        ),
        "Specificity": st.column_config.ProgressColumn(
            "Specificity", format="%.2f", min_value=0.0, max_value=1.0,
        ),
        "Hallucination": st.column_config.ProgressColumn(
            "Hallucination", format="%.2f", min_value=0.0, max_value=1.0,
        ),
    },
)


# ---------- Per-topic drill-down ----------
st.markdown("**Drill-down**")
topic_picker = {
    f"{r.get('topic_id', '')}  —  {r.get('topic', '')[:60]}": r.get("topic_id")
    for r in results
}
selected_topic_label = st.selectbox(
    "Inspect a specific topic", list(topic_picker.keys()),
)
selected_topic_id = topic_picker[selected_topic_label]

selected_result = next(
    (r for r in results if r.get("topic_id") == selected_topic_id), None
)

if selected_result:
    if selected_result.get("error"):
        st.error(f"This topic failed: {selected_result['error']}")
    else:
        scores = selected_result.get("scores") or {}

        st.markdown(f"**Topic:** {selected_result['topic']}")
        st.caption(
            f"Tags: {', '.join(selected_result.get('tags', []))}  ·  "
            f"City: {selected_result.get('city', '?')}  ·  "
            f"Brief ID: `{selected_result.get('brief_id', '?')}`"
        )

        score_cols = st.columns(5)
        for i, dim in enumerate(DIMENSIONS):
            score_cols[i].metric(dim.capitalize(), f"{scores.get(dim, 0):.2f}")

        with st.expander("Judge's reasoning", expanded=True):
            st.write(scores.get("reasoning", "(no reasoning provided)"))

        with st.expander("Generated article"):
            st.markdown(selected_result.get("article", "(missing)"),
                        unsafe_allow_html=True)

        with st.expander("Rubric hints (sent to judge)"):
            st.code(selected_result.get("rubric_hints", "(none)"))