# Task 10 + 12 + 13: Streamlit Interface — chat-shaped multi-turn UI (Day 4).
from dotenv import load_dotenv
from openai import OpenAI
import json
import os
import asyncio

import streamlit as st
from fastmcp import Client

load_dotenv()

# MCP endpoints
SCOUT_URL = "http://0.0.0.0:8004/mcp"
PUBLISHER_URL = "http://0.0.0.0:8005/mcp"
MEMORY_URL = "http://0.0.0.0:8006/mcp"          # Day 3
CONVERSATION_URL = "http://0.0.0.0:8007/mcp"    # Day 4

api_key = os.getenv("OPENAI_API_KEY")
client = OpenAI(api_key=api_key)


# ---------- Async helpers ----------
async def call_tool(url, tool, params):
    async with Client(url) as c:
        res = await c.call_tool(tool, params)
        return res.data


def run_scout(topic, city):
    return asyncio.run(call_tool(SCOUT_URL, "scout", {"topic": topic, "city": city}))

def run_publisher_initial(payload):
    return asyncio.run(call_tool(PUBLISHER_URL, "publish_brief", {"payload": payload}))

def run_publisher_followup(conversation_id, user_question):
    return asyncio.run(call_tool(
        PUBLISHER_URL,
        "follow_up",
        {"conversation_id": conversation_id, "user_question": user_question},
    ))

# Memory (Day 3)
def list_recent_briefs(limit=10):
    return asyncio.run(call_tool(MEMORY_URL, "list_recent_briefs", {"limit": limit}))

def get_brief(brief_id):
    return asyncio.run(call_tool(MEMORY_URL, "get_brief", {"brief_id": brief_id}))

# Conversations (Day 4)
def list_conversations(limit=10):
    return asyncio.run(call_tool(CONVERSATION_URL, "list_conversations", {"limit": limit}))

def get_conversation(conversation_id):
    return asyncio.run(call_tool(CONVERSATION_URL, "get_conversation",
                                 {"conversation_id": conversation_id}))


def get_location_context(news_text: str) -> dict:
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


# ---------- Session state ----------
# active_conversation_id: the conversation currently rendered in the chat.
#   None = no conversation active, show the "start new brief" input.
# turns: list of {role, content} dicts mirroring the conversation server.
# initial_image_url: the image fetched for the initial brief (shown above chat).
# memory_used: badge for "how many past briefs informed this one"
for key, default in [
    ("active_conversation_id", None),
    ("turns", []),
    ("initial_image_url", None),
    ("memory_used", 0),
    ("topic", ""),
    ("city", ""),
]:
    if key not in st.session_state:
        st.session_state[key] = default


def _hydrate_from_conversation(conversation_id: str):
    """Load a conversation from the server into session state."""
    conv = get_conversation(conversation_id)
    if conv.get("error"):
        st.error(conv["error"])
        return
    st.session_state["active_conversation_id"] = conversation_id
    st.session_state["turns"] = conv.get("turns", [])
    st.session_state["topic"] = conv.get("topic", "")
    st.session_state["city"] = conv.get("city", "")
    # Try to recover the image from initial_payload
    try:
        st.session_state["initial_image_url"] = (
            conv["initial_payload"]["media"]["images"][0]["src"]["url"]
        )
    except Exception:
        st.session_state["initial_image_url"] = None
    st.session_state["memory_used"] = 0  # we don't replay the memory-used badge


def _reset_session():
    st.session_state["active_conversation_id"] = None
    st.session_state["turns"] = []
    st.session_state["initial_image_url"] = None
    st.session_state["memory_used"] = 0
    st.session_state["topic"] = ""
    st.session_state["city"] = ""


# -------------------- UI --------------------
st.set_page_config(page_title="SYNAPSE", layout="wide")
st.title("SYNAPSE — Context-Aware Reports")

# ---------- Sidebar ----------
with st.sidebar:
    st.header("💬 Conversations")
    if st.button("➕ New conversation", use_container_width=True, type="primary"):
        _reset_session()
        st.rerun()

    try:
        convs = list_conversations(limit=15)
        if convs.get("total", 0) == 0:
            st.caption("No conversations yet.")
        else:
            for c in convs.get("conversations", []):
                label = c["topic"][:38] + ("..." if len(c["topic"]) > 38 else "")
                is_active = c["conversation_id"] == st.session_state["active_conversation_id"]
                marker = "▶ " if is_active else "📄 "
                if st.button(
                    f"{marker}{label}",
                    key=f"conv-{c['conversation_id']}",
                    use_container_width=True,
                    help=f"{c['turn_count']} turns · {c.get('updated_at', '')[:16]}",
                ):
                    _hydrate_from_conversation(c["conversation_id"])
                    st.rerun()
    except Exception as e:
        st.caption(f"Conversation server unavailable: {e}")

    st.divider()
    st.header("📚 Past Briefs")
    try:
        recent = list_recent_briefs(limit=10)
        total = recent.get("total", 0)
        if total == 0:
            st.caption("No briefs yet.")
        else:
            st.caption(f"{total} brief(s) in memory")
            for b in recent.get("briefs", []):
                label = b["topic"][:38] + ("..." if len(b["topic"]) > 38 else "")
                if st.button(
                    f"📖 {label}",
                    key=f"brief-{b['brief_id']}",
                    use_container_width=True,
                    help=f"{b.get('created_at', '?')[:16]} · {b.get('city', '?')}",
                ):
                    st.session_state["viewing_brief"] = b["brief_id"]
    except Exception as e:
        st.caption(f"Memory server unavailable: {e}")


# ---------- Main: depends on whether a conversation is active ----------

if st.session_state["active_conversation_id"] is None:
    # --- NEW BRIEF MODE ---
    st.subheader("Start a new brief")
    topic = st.text_input("Topic", "Semiconductor factory opening in Japan")

    try:
        city_guess = get_location_context(topic).get("capital", "Tokyo")
    except Exception:
        city_guess = "Tokyo"
    st.caption(f"Auto-detected city: **{city_guess}**")

    if st.button("Generate Brief", type="primary"):
        with st.status("Running pipeline...", expanded=True) as status:
            st.write("🔍 Scout: gathering context, media, and memory...")
            scout_data = run_scout(topic, city_guess)
            scout_data = normalize_payload(scout_data)

            mem_count = (scout_data.get("memory_context") or {}).get("count", 0)
            if mem_count > 0:
                st.write(f"🧠 Memory: found {mem_count} related past brief(s)")
            else:
                st.write("🧠 Memory: no related past briefs")

            st.write("✍️ Publisher: generating brief + starting conversation...")
            pub_data = run_publisher_initial(scout_data)
            status.update(label="Done", state="complete", expanded=False)

        # Hydrate session from the brand-new conversation
        st.session_state["active_conversation_id"] = pub_data["conversation_id"]
        st.session_state["topic"] = topic
        st.session_state["city"] = city_guess
        st.session_state["memory_used"] = pub_data.get("memory_used", 0)
        try:
            st.session_state["initial_image_url"] = (
                scout_data["media"]["images"][0]["src"]["url"]
            )
        except Exception:
            st.session_state["initial_image_url"] = None
        # Pull the canonical turns from the conversation server
        conv = get_conversation(pub_data["conversation_id"])
        st.session_state["turns"] = conv.get("turns", [])
        st.rerun()

else:
    # --- CONVERSATION MODE ---
    header_cols = st.columns([6, 1])
    with header_cols[0]:
        st.subheader(f"💬 {st.session_state['topic']}")
        st.caption(f"City: {st.session_state['city']} · "
                   f"Conversation: `{st.session_state['active_conversation_id']}`")
    with header_cols[1]:
        if st.button("End", use_container_width=True):
            _reset_session()
            st.rerun()

    if st.session_state["memory_used"] > 0:
        st.success(
            f"✨ Initial brief drew on {st.session_state['memory_used']} "
            f"related past brief(s) from memory."
        )

    # Render the conversation as chat messages
    for turn in st.session_state["turns"]:
        with st.chat_message(turn.get("role", "assistant")):
            st.markdown(turn.get("content", ""), unsafe_allow_html=True)
            # Show the initial image with the assistant's first response
            if (
                turn.get("role") == "assistant"
                and turn is st.session_state["turns"][1]
                and st.session_state["initial_image_url"]
            ):
                st.image(
                    st.session_state["initial_image_url"],
                    caption="Related image",
                    use_container_width=True,
                )

    # Chat input for follow-ups
    user_question = st.chat_input("Ask a follow-up about this brief...")
    if user_question:
        # Optimistically render the user's question
        with st.chat_message("user"):
            st.markdown(user_question)
        # Show a streaming indicator while waiting on the LLM
        with st.chat_message("assistant"):
            with st.spinner("Thinking..."):
                try:
                    result = run_publisher_followup(
                        st.session_state["active_conversation_id"],
                        user_question,
                    )
                    if result.get("error"):
                        st.error(result["error"])
                    else:
                        st.markdown(result["response"], unsafe_allow_html=True)
                except Exception as e:
                    st.error(f"Follow-up failed: {e}")

        # Refresh session state from server (canonical truth)
        conv = get_conversation(st.session_state["active_conversation_id"])
        st.session_state["turns"] = conv.get("turns", [])
        st.rerun()


# ---------- Past brief viewer (unchanged from Day 3) ----------
if st.session_state.get("viewing_brief"):
    st.divider()
    st.subheader("📖 Viewing Past Brief")
    try:
        brief = get_brief(st.session_state["viewing_brief"])
        if brief.get("error"):
            st.error(brief["error"])
        else:
            cols = st.columns(3)
            cols[0].metric("Topic", brief.get("topic", "?")[:30])
            cols[1].metric("City", brief.get("city", "?"))
            cols[2].metric("Created", (brief.get("created_at") or "?")[:10])
            st.markdown(brief.get("article", ""), unsafe_allow_html=True)
    except Exception as e:
        st.error(f"Could not load brief: {e}")
    if st.button("Close past brief"):
        st.session_state.pop("viewing_brief", None)
        st.rerun()