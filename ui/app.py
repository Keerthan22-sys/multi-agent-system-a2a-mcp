# Task 10 + 12 + 13 + 14 + 15 + 17: Streamlit UI — now shows critique refinement (Day 8).
from synapse.tracing import setup_tracing, tracer
setup_tracing("ui")

from dotenv import load_dotenv
from openai import OpenAI
import json
import os
import asyncio

import streamlit as st
from fastmcp import Client

load_dotenv()

SCOUT_URL = "http://0.0.0.0:8004/mcp"
PUBLISHER_URL = "http://0.0.0.0:8005/mcp"
MEMORY_URL = "http://0.0.0.0:8006/mcp"
CONVERSATION_URL = "http://0.0.0.0:8007/mcp"
ROUTER_URL = "http://0.0.0.0:8008/mcp"
PHOENIX_UI_URL = os.getenv("PHOENIX_COLLECTOR_ENDPOINT", "http://localhost:6006")

PIVOT_CONFIDENCE_THRESHOLD = 0.70

api_key = os.getenv("OPENAI_API_KEY")
client = OpenAI(api_key=api_key)


async def call_tool(url, tool, params):
    async with Client(url) as c:
        res = await c.call_tool(tool, params)
        return res.data

def run_scout(topic, city):
    with tracer.start_as_current_span("ui.run_scout") as span:
        span.set_attribute("topic", topic)
        span.set_attribute("city", city)
        return asyncio.run(call_tool(SCOUT_URL, "scout", {"topic": topic, "city": city}))

def run_publisher_initial(payload):
    with tracer.start_as_current_span("ui.run_publisher_initial"):
        return asyncio.run(call_tool(PUBLISHER_URL, "publish_brief", {"payload": payload}))

def run_publisher_followup(conversation_id, user_question):
    with tracer.start_as_current_span("ui.run_publisher_followup") as span:
        span.set_attribute("conversation_id", conversation_id)
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
    with tracer.start_as_current_span("ui.run_router_intent"):
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


for key, default in [
    ("active_conversation_id", None), ("turns", []),
    ("initial_image_url", None), ("memory_used", 0),
    ("topic", ""), ("city", ""),
    ("routing_decision", None), ("pending_pivot", None),
    ("prefilled_topic", ""),
    # NEW (Day 8)
    ("revision_count", 0),
    ("critique_history", []),
    ("critic_enabled", True),
    ("approved_on_attempt", 1),
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
    # Critique history is ephemeral — only available right after initial generation.
    st.session_state["revision_count"] = 0
    st.session_state["critique_history"] = []
    try:
        st.session_state["initial_image_url"] = (
            conv["initial_payload"]["media"]["images"][0]["src"]["url"]
        )
    except Exception:
        st.session_state["initial_image_url"] = None
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
    st.session_state["prefilled_topic"] = keep_prefilled_topic
    st.session_state["revision_count"] = 0
    st.session_state["critique_history"] = []


def _format_routing_line(routing: dict) -> str:
    if not routing: return ""
    enabled, skipped = [], []
    for k, label in [("use_news", "news"), ("use_weather", "weather"),
                     ("use_fx", "fx"), ("use_media", "media")]:
        (enabled if routing.get(k) else skipped).append(label)
    parts = [f"✅ {', '.join(enabled) if enabled else 'none'}"]
    if skipped:
        parts.append(f"⏭️ skipped {', '.join(skipped)}")
    return " · ".join(parts)


def _render_critique_history(history: list):
    """NEW (Day 8): Render the critique loop's history as a stack of expanders."""
    if not history:
        return
    for round_data in history:
        attempt = round_data.get("attempt", "?")
        decision = round_data.get("decision", "?")
        issues = round_data.get("issues", [])
        emoji = "✅" if decision == "approve" else "🔄"
        label = (
            f"{emoji} Round {attempt}: {decision.upper()}"
            + (f" — {len(issues)} issue(s) flagged" if issues else "")
        )
        with st.expander(label, expanded=False):
            reasoning = round_data.get("reasoning", "")
            if reasoning:
                st.caption(f"_Critic's reasoning:_ {reasoning}")
            if issues:
                st.markdown("**Issues flagged:**")
                for issue in issues:
                    st.markdown(f"- {issue}")
            excerpt = round_data.get("draft_excerpt", "")
            if excerpt:
                st.markdown("**Draft excerpt at this round:**")
                st.code(excerpt, language="markdown")


st.set_page_config(page_title="SYNAPSE", layout="wide")
st.title("SYNAPSE — Context-Aware Reports")

with st.sidebar:
    st.link_button(
        "🔭 View traces in Phoenix",
        PHOENIX_UI_URL,
        use_container_width=True,
        help="Opens the Phoenix observability dashboard",
    )
    st.divider()

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


if st.session_state["active_conversation_id"] is None:
    st.subheader("Start a new brief")
    default_topic = st.session_state["prefilled_topic"] or "Semiconductor factory opening in Japan"
    topic = st.text_input("Topic", default_topic)
    st.session_state["prefilled_topic"] = ""

    try:
        city_guess = get_location_context(topic).get("capital", "Tokyo")
    except Exception:
        city_guess = "Tokyo"
    st.caption(f"Auto-detected city: **{city_guess}**")

    if st.button("Generate Brief", type="primary"):
        with tracer.start_as_current_span("ui.generate_brief") as ui_span:
            ui_span.set_attribute("topic", topic)
            ui_span.set_attribute("city", city_guess)
            with st.status("Running pipeline...", expanded=True) as status:
                st.write("🔍 Scout: gathering context, media, and memory...")
                scout_data = run_scout(topic, city_guess)
                scout_data = normalize_payload(scout_data)
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
                st.write("✍️ Publisher: drafting + critiquing + finalizing...")
                pub_data = run_publisher_initial(scout_data)
                # NEW (Day 8): announce critique outcome inline
                rev = pub_data.get("revision_count", 0)
                if pub_data.get("critic_enabled", True):
                    if rev == 0:
                        st.write("👀 Critic: approved on first draft.")
                    else:
                        st.write(
                            f"👀 Critic: requested {rev} revision(s); "
                            f"approved on attempt {pub_data.get('approved_on_attempt', '?')}."
                        )
                else:
                    st.write("👀 Critic: disabled (env var).")
                status.update(label="Done", state="complete", expanded=False)

            st.info(
                f"📊 [View this run's traces in Phoenix]({PHOENIX_UI_URL})  ·  "
                f"Look under projects → `synapse`"
            )

            st.session_state["active_conversation_id"] = pub_data["conversation_id"]
            st.session_state["topic"] = topic
            st.session_state["city"] = city_guess
            st.session_state["memory_used"] = pub_data.get("memory_used", 0)
            st.session_state["routing_decision"] = routing
            # NEW (Day 8)
            st.session_state["critic_enabled"] = pub_data.get("critic_enabled", True)
            st.session_state["revision_count"] = pub_data.get("revision_count", 0)
            st.session_state["critique_history"] = pub_data.get("critique_history", [])
            st.session_state["approved_on_attempt"] = pub_data.get("approved_on_attempt", 1)
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

    # NEW (Day 8): Critique badges & history (only shown for the current freshly-generated brief)
    if st.session_state["critique_history"]:
        badge_cols = st.columns([1, 1, 4])
        if st.session_state["revision_count"] > 0:
            badge_cols[0].markdown(
                f"🔄 **Refined {st.session_state['revision_count']}×**"
            )
        else:
            badge_cols[0].markdown("✅ **Approved on first draft**")
        badge_cols[1].markdown(
            f"👀 _Approved on attempt {st.session_state['approved_on_attempt']}_"
        )
        with st.expander("🧐 View critique history", expanded=False):
            _render_critique_history(st.session_state["critique_history"])

    if st.session_state["memory_used"] > 0:
        st.success(
            f"✨ Initial brief drew on {st.session_state['memory_used']} "
            f"related past brief(s) from memory."
        )

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
            held_msg = pivot["message"]
            st.session_state["pending_pivot"] = None
            try:
                result = run_publisher_followup(
                    st.session_state["active_conversation_id"], held_msg,
                )
                if not result.get("error"):
                    conv = get_conversation(st.session_state["active_conversation_id"])
                    st.session_state["turns"] = conv.get("turns", [])
            except Exception as e:
                st.error(f"Follow-up failed: {e}")
            st.rerun()
    else:
        user_question = st.chat_input("Ask a follow-up about this brief...")
        if user_question:
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