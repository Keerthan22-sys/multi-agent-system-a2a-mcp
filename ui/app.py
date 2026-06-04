# Task 10 + 12 + 13 + 14: Streamlit UI — Router-aware (Day 5).
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
MEMORY_URL = "http://0.0.0.0:8006/mcp"
CONVERSATION_URL = "http://0.0.0.0:8007/mcp"
ROUTER_URL = "http://0.0.0.0:8008/mcp"  # NEW

# Pivot detection: only act on confident pivots
PIVOT_CONFIDENCE_THRESHOLD = 0.70

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
        PUBLISHER_URL, "follow_up",
        {"conversation_id": conversation_id, "user_question": user_question},
    ))

def list_recent_briefs(limit=10):
    return asyncio.run(call_tool(MEMORY_URL, "list_recent_briefs", {"limit": limit}))

def get_brief(brief_id):
    return asyncio.run(call_tool(MEMORY_URL, "get_brief", {"brief_id": brief_id}))

def list_conversations(limit=10):
    return asyncio.run(call_tool(CONVERSATION_URL, "list_conversations", {"limit": limit}))

def get_conversation(conversation_id):
    return asyncio.run(call_tool(CONVERSATION_URL, "get_conversation",
                                 {"conversation_id": conversation_id}))

def run_router_intent(message, conversation_topic, recent_turns):
    return asyncio.run(call_tool(
        ROUTER_URL, "route_intent",
        {
            "message": message,
            "conversation_topic": conversation_topic,
            "recent_turns": recent_turns,
        },
    ))


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
for key, default in [
    ("active_conversation_id", None),
    ("turns", []),
    ("initial_image_url", None),
    ("memory_used", 0),
    ("topic", ""),
    ("city", ""),
    ("routing_decision", None),     # NEW
    ("pending_pivot", None),        # NEW
    ("new_brief_topic", ""),        # widget key for Topic input
    ("topic_prefill", None),        # one-shot value applied before widget mounts
]:
    if key not in st.session_state:
        st.session_state[key] = default


def _hydrate_from_conversation(conversation_id: str):
    conv = get_conversation(conversation_id)
    if conv.get("error"):
        st.error(conv["error"])
        return
    st.session_state["active_conversation_id"] = conversation_id
    st.session_state["turns"] = conv.get("turns", [])
    st.session_state["topic"] = conv.get("topic", "")
    st.session_state["city"] = conv.get("city", "")
    st.session_state["memory_used"] = 0
    st.session_state["pending_pivot"] = None
    try:
        st.session_state["initial_image_url"] = (
            conv["initial_payload"]["media"]["images"][0]["src"]["url"]
        )
    except Exception:
        st.session_state["initial_image_url"] = None
    # Recover routing decision if it was saved with the payload
    st.session_state["routing_decision"] = (
        conv.get("initial_payload", {}).get("routing_decision")
    )


def _reset_session(keep_prefilled_topic: str = ""):
    st.session_state["active_conversation_id"] = None
    st.session_state["turns"] = []
    st.session_state["initial_image_url"] = None
    st.session_state["memory_used"] = 0
    st.session_state["topic"] = ""
    st.session_state["city"] = ""
    st.session_state["routing_decision"] = None
    st.session_state["pending_pivot"] = None
    st.session_state["topic_prefill"] = keep_prefilled_topic


def _format_routing_line(routing: dict) -> str:
    """Render routing decision as a compact one-liner."""
    if not routing:
        return ""
    enabled = []
    skipped = []
    for k, label in [("use_news", "news"), ("use_weather", "weather"),
                     ("use_fx", "fx"), ("use_media", "media")]:
        (enabled if routing.get(k) else skipped).append(label)
    parts = [f"✅ {', '.join(enabled) if enabled else 'none'}"]
    if skipped:
        parts.append(f"⏭️ skipped {', '.join(skipped)}")
    return " · ".join(parts)


# -------------------- UI --------------------
st.set_page_config(page_title="SYNAPSE", layout="wide")
st.title("SYNAPSE — Context-Aware Reports")

# ---------- Sidebar (unchanged from Day 4) ----------
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


# ---------- Main ----------

if st.session_state["active_conversation_id"] is None:
    st.subheader("Start a new brief")
    # Apply prefill before the widget mounts (cannot set widget key after text_input).
    if st.session_state["topic_prefill"] is not None:
        st.session_state["new_brief_topic"] = st.session_state["topic_prefill"]
        st.session_state["topic_prefill"] = None
    topic = st.text_input("Topic", key="new_brief_topic")

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

            # NEW: surface routing decision
            routing = scout_data.get("routing_decision") or {}
            if routing:
                st.write(f"🧭 Router: {_format_routing_line(routing)}")
                if routing.get("reasoning"):
                    st.caption(f"   _{routing['reasoning']}_")

            mem_count = (scout_data.get("memory_context") or {}).get("count", 0)
            if mem_count > 0:
                st.write(f"🧠 Memory: found {mem_count} related past brief(s)")
            else:
                st.write("🧠 Memory: no related past briefs")

            st.write("✍️ Publisher: generating brief + starting conversation...")
            pub_data = run_publisher_initial(scout_data)
            status.update(label="Done", state="complete", expanded=False)

        st.session_state["active_conversation_id"] = pub_data["conversation_id"]
        st.session_state["topic"] = topic
        st.session_state["city"] = city_guess
        st.session_state["memory_used"] = pub_data.get("memory_used", 0)
        st.session_state["routing_decision"] = routing
        try:
            st.session_state["initial_image_url"] = (
                scout_data["media"]["images"][0]["src"]["url"]
            )
        except Exception:
            st.session_state["initial_image_url"] = None
        conv = get_conversation(pub_data["conversation_id"])
        st.session_state["turns"] = conv.get("turns", [])
        st.rerun()

else:
    # ---------- CONVERSATION MODE ----------
    header_cols = st.columns([6, 1])
    with header_cols[0]:
        st.subheader(f"💬 {st.session_state['topic']}")
        cap = (
            f"City: {st.session_state['city']} · "
            f"Conversation: `{st.session_state['active_conversation_id']}`"
        )
        if st.session_state.get("routing_decision"):
            cap += f" · 🧭 {_format_routing_line(st.session_state['routing_decision'])}"
        st.caption(cap)
    with header_cols[1]:
        if st.button("End", use_container_width=True):
            _reset_session()
            st.rerun()

    if st.session_state["memory_used"] > 0:
        st.success(
            f"✨ Initial brief drew on {st.session_state['memory_used']} "
            f"related past brief(s) from memory."
        )

    # Render the transcript
    for turn in st.session_state["turns"]:
        with st.chat_message(turn.get("role", "assistant")):
            st.markdown(turn.get("content", ""), unsafe_allow_html=True)
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

    # ----- Pivot prompt (NEW) -----
    if st.session_state.get("pending_pivot"):
        pivot = st.session_state["pending_pivot"]
        with st.chat_message("user"):
            st.markdown(pivot["message"])
        st.warning(
            f"🧭 **The router thinks you're pivoting to a new topic.**\n\n"
            f"Suggested new brief topic: **{pivot['suggested_topic']}**\n\n"
            f"_Reason: {pivot['reasoning']}_  \n"
            f"Confidence: {pivot['confidence']:.0%}"
        )
        cols = st.columns(2)
        if cols[0].button("✨ Start fresh brief", type="primary", use_container_width=True):
            new_topic = pivot["suggested_topic"] or pivot["message"]
            _reset_session(keep_prefilled_topic=new_topic)
            st.rerun()
        if cols[1].button("💬 Continue here anyway", use_container_width=True):
            # Treat the held message as a follow-up
            held_msg = pivot["message"]
            st.session_state["pending_pivot"] = None
            try:
                result = run_publisher_followup(
                    st.session_state["active_conversation_id"],
                    held_msg,
                )
                if not result.get("error"):
                    conv = get_conversation(st.session_state["active_conversation_id"])
                    st.session_state["turns"] = conv.get("turns", [])
            except Exception as e:
                st.error(f"Follow-up failed: {e}")
            st.rerun()

    else:
        # ----- Normal chat input -----
        user_question = st.chat_input("Ask a follow-up about this brief...")
        if user_question:
            # Ask the Router first
            intent = {"intent": "follow_up", "confidence": 0.0}
            try:
                intent = run_router_intent(
                    user_question,
                    st.session_state["topic"],
                    st.session_state["turns"],
                )
            except Exception as e:
                print(f"[ui] Intent router failed (non-fatal): {e}")

            is_confident_pivot = (
                intent.get("intent") == "pivot"
                and intent.get("confidence", 0) >= PIVOT_CONFIDENCE_THRESHOLD
            )

            if is_confident_pivot:
                st.session_state["pending_pivot"] = {
                    "message": user_question,
                    "suggested_topic": intent.get("suggested_topic") or user_question,
                    "reasoning": intent.get("reasoning", ""),
                    "confidence": float(intent.get("confidence", 0)),
                }
                st.rerun()
            else:
                # Optimistic render then call publisher
                with st.chat_message("user"):
                    st.markdown(user_question)
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
                conv = get_conversation(st.session_state["active_conversation_id"])
                st.session_state["turns"] = conv.get("turns", [])
                st.rerun()


# ---------- Past brief viewer ----------
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