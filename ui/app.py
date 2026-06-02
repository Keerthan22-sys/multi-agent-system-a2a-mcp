# Task 10 + 12: Streamlit Interface — now with memory sidebar (Day 3).
from dotenv import load_dotenv
from openai import OpenAI
import json
import os
import asyncio

import streamlit as st
from fastmcp import Client

load_dotenv()

# MCP agent URLs
SCOUT_URL = "http://0.0.0.0:8004/mcp"
PUBLISHER_URL = "http://0.0.0.0:8005/mcp"
MEMORY_URL = "http://0.0.0.0:8006/mcp"  # NEW

api_key = os.getenv("OPENAI_API_KEY")


# Async helper to call MCP tools
async def call_tool(url, tool, params):
    async with Client(url) as client:
        res = await client.call_tool(tool, params)
        return res.data


client = OpenAI(api_key=api_key)


def get_location_context(news_text: str) -> dict:
    """
    Extracts country and capital from a text string using an LLM.
    """
    prompt = f"""
    Given the news text below, identify the primary country it is about.
    Return only a JSON object with the keys 'country' and 'capital'.
    If no country is mentioned, return US and its capital for both.

    Text: "{news_text}"
    """
    response = client.chat.completions.create(
        model="gpt-5-nano",
        messages=[{"role": "user", "content": prompt}],
        response_format={"type": "json_object"},
    )
    return json.loads(response.choices[0].message.content)


# Agent triggers
def run_scout(topic, city):
    return asyncio.run(call_tool(SCOUT_URL, "scout", {"topic": topic, "city": city}))

def run_publisher(payload):
    return asyncio.run(call_tool(PUBLISHER_URL, "publish_brief", {"payload": payload}))

# NEW: memory triggers
def list_recent_briefs(limit=15):
    return asyncio.run(call_tool(MEMORY_URL, "list_recent_briefs", {"limit": limit}))

def get_brief(brief_id):
    return asyncio.run(call_tool(MEMORY_URL, "get_brief", {"brief_id": brief_id}))


# Normalize payload to ensure image src is a valid JSON object
def normalize_payload(payload):
    try:
        img = payload["media"]["images"][0]
        src = img.get("src")
        if isinstance(src, str):
            img["src"] = {"url": src, "type": "image"}
        if src is None:
            img["src"] = {"url": "", "type": "image"}
    except Exception:
        pass
    return payload


# -------------------- UI --------------------
st.set_page_config(page_title="SYNAPSE", layout="wide")
st.title("SYNAPSE — Context-Aware Reports")

# ---------- Sidebar: Past Briefs (NEW) ----------
with st.sidebar:
    st.header("📚 Past Briefs")
    st.caption("Semantic memory across all your runs")

    try:
        recent = list_recent_briefs(limit=15)
        total = recent.get("total", 0)

        if total == 0:
            st.info("No briefs yet. Generate one to start your archive.")
        else:
            st.success(f"{total} brief(s) in memory")
            for b in recent.get("briefs", []):
                label_topic = b["topic"][:42] + ("..." if len(b["topic"]) > 42 else "")
                if st.button(
                    f"📄 {label_topic}",
                    key=b["brief_id"],
                    use_container_width=True,
                    help=f"Created: {b.get('created_at', '?')}\nCity: {b.get('city', '?')}",
                ):
                    st.session_state["viewing_brief"] = b["brief_id"]
    except Exception as e:
        st.warning("Memory server unavailable.")
        st.caption(f"Details: {e}")


# ---------- Main: Generate new brief ----------
topic = st.text_input("Topic", "Semiconductor factory opening in Japan")
city = get_location_context(topic)["capital"]
st.caption(f"Auto-detected city: **{city}**")

if st.button("Generate Report", type="primary"):
    with st.status("Running pipeline...", expanded=True) as status:
        st.write("🔍 Scout: gathering context, media, and memory...")
        scout_data = run_scout(topic, city)
        scout_data = normalize_payload(scout_data)

        mem_count = (scout_data.get("memory_context") or {}).get("count", 0)
        if mem_count > 0:
            st.write(f"🧠 Memory: found {mem_count} related past brief(s)")
        else:
            st.write("🧠 Memory: no related past briefs (this is a fresh topic)")

        st.write("✍️ Publisher: generating article + saving to memory...")
        publisher_data = run_publisher(scout_data)
        status.update(label="Done", state="complete", expanded=False)

    # NEW: memory-used badge
    if publisher_data.get("memory_used", 0) > 0:
        st.success(
            f"✨ This brief drew on {publisher_data['memory_used']} "
            f"related past brief(s) from memory."
        )

    # Article
    st.subheader("Final Article")
    with st.expander("Read full article", expanded=True):
        st.markdown(publisher_data.get("article", "No output"), unsafe_allow_html=True)

    # Image
    try:
        image_url = scout_data["media"]["images"][0]["src"]["url"]
        if image_url:
            st.image(image_url, caption="Related Image", use_container_width=True)
    except Exception:
        pass

    # Payload (collapsed by default)
    with st.expander("Payload (debug)"):
        st.json(publisher_data.get("payload"))


# ---------- Past brief viewer (NEW) ----------
if st.session_state.get("viewing_brief"):
    st.divider()
    st.subheader("📖 Viewing Past Brief")
    try:
        brief = get_brief(st.session_state["viewing_brief"])
        if brief.get("error"):
            st.error(brief["error"])
        else:
            col1, col2, col3 = st.columns(3)
            col1.metric("Topic", brief.get("topic", "?")[:30])
            col2.metric("City", brief.get("city", "?"))
            col3.metric("Created", (brief.get("created_at") or "?")[:10])
            st.markdown(brief.get("article", ""), unsafe_allow_html=True)
    except Exception as e:
        st.error(f"Could not load brief: {e}")

    if st.button("Close past brief"):
        st.session_state.pop("viewing_brief", None)
        st.rerun()
