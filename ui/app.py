# Task 10 + 12 + 13 + 14 + 15 + 17 + 18: Streamlit UI — now shows cost + cache (Day 9).
from synapse.tracing import setup_tracing, tracer
setup_tracing("ui")

from dotenv import load_dotenv
from openai import OpenAI
import json
import os
import asyncio

import streamlit as st
from fastmcp import Client

from synapse import cache as synapse_cache
from synapse.costs import (
    extract_usage, empty_usage, accumulate, format_cost_inr, USD_TO_INR,
)
from synapse.protocol.post_office import mode as mailbox_mode

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
        span.set_attribute("topic", topic); span.set_attribute("city", city)
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


def get_location_context(news_text: str) -> tuple[dict, dict]:
    """
    NEW (Day 9): city detection now cached by topic + returns usage info.
    Returns (location_dict, usage_dict).
    """
    cache_params = {"topic": news_text.lower().strip()}
    cached = synapse_cache.get_cached("city", cache_params)
    if cached:
        return cached, {**empty_usage(), "_cache_hit": True}

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
    usage = extract_usage(response, model="gpt-5-nano")
    location = json.loads(response.choices[0].message.content)
    synapse_cache.set_cached("city", cache_params, location,
                             ttl_seconds=synapse_cache.TTL["city"])
    return location, usage


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
    ("revision_count", 0), ("critique_history", []),
    ("critic_enabled", True), ("approved_on_attempt", 1),
    # NEW (Day 9)
    ("usage_summary", None),
    ("cache_summary", None),
    ("ui_city_usage", empty_usage()),
]:
    if key not in st.session_state:
        st.session_state[key] = default


def _hydrate_from_conversation(conversation_id: str):
    conv = get_conversation(conversation_id)
    if conv.get("error"):
        st.error(conv["error"]); return
    st.session_state["active_conversation_id"] = conversation_id
    st.session_state["turns"] = conv.get("turns", [])
    st.session_state["topic"] = conv.get("topic", "")
    st.session_state["city"] = conv.get("city", "")
    st.session_state["memory_used"] = 0
    st.session_state["pending_pivot"] = None
    st.session_state["revision_count"] = 0
    st.session_state["critique_history"] = []
    st.session_state["usage_summary"] = None
    st.session_state["cache_summary"] = None
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
    for k in [
        "active_conversation_id", "turns", "initial_image_url", "memory_used",
        "topic", "city", "routing_decision", "pending_pivot",
        "revision_count", "critique_history", "usage_summary", "cache_summary",
    ]:
        if k in st.session_state:
            st.session_state[k] = (
                None if k in ("active_conversation_id", "initial_image_url",
                              "routing_decision", "pending_pivot",
                              "usage_summary", "cache_summary")
                else [] if k in ("turns", "critique_history")
                else 0 if k in ("memory_used", "revision_count")
                else ""
            )
    st.session_state["prefilled_topic"] = keep_prefilled_topic


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
    if not history: return
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


def _render_cost_and_cache(usage_summary: dict, cache_summary: dict,
                            ui_city_usage: dict):
    """NEW (Day 9): show LLM cost + cache hit info for this brief."""
    if not usage_summary:
        return

    # Roll the UI's own city-detection LLM call into the headline number too
    total_cost_usd = usage_summary.get("cost_usd", 0.0) + ui_city_usage.get("cost_usd", 0.0)
    total_tokens = usage_summary.get("total_tokens", 0) + ui_city_usage.get("total_tokens", 0)
    total_calls = usage_summary.get("calls", 0) + (1 if ui_city_usage.get("total_tokens", 0) > 0 else 0)

    st.markdown("### 💰 Cost & Cache")
    cols = st.columns(4)
    cols[0].metric("Total cost", format_cost_inr(total_cost_usd),
                   help=f"~${total_cost_usd:.6f} USD at ₹{USD_TO_INR}/USD")
    cols[1].metric("LLM calls", total_calls)
    cols[2].metric("Total tokens", f"{total_tokens:,}")

    cache_hits = cache_summary or {}
    hit_count = sum(1 for v in cache_hits.values() if v)
    total_targets = len(cache_hits) if cache_hits else 0
    cols[3].metric(
        "Cache hits",
        f"{hit_count}/{total_targets}" if total_targets else "—",
        help="Tool servers that returned cached data instead of hitting external APIs",
    )

    with st.expander("Cost & cache details", expanded=False):
        by_source = usage_summary.get("by_source") or {}
        rows = []
        for src, u in by_source.items():
            if not u or u.get("total_tokens", 0) == 0:
                continue
            rows.append({
                "Source": src.capitalize(),
                "Tokens (in/out)": f"{u.get('input_tokens', 0):,} / {u.get('output_tokens', 0):,}",
                "Total": f"{u.get('total_tokens', 0):,}",
                "Cost (USD)": f"${u.get('cost_usd', 0):.6f}",
            })
        if ui_city_usage.get("total_tokens", 0) > 0:
            rows.append({
                "Source": "UI (city detection)",
                "Tokens (in/out)": f"{ui_city_usage.get('input_tokens', 0):,} / {ui_city_usage.get('output_tokens', 0):,}",
                "Total": f"{ui_city_usage.get('total_tokens', 0):,}",
                "Cost (USD)": f"${ui_city_usage.get('cost_usd', 0):.6f}",
            })
        if rows:
            st.dataframe(rows, use_container_width=True, hide_index=True)
        else:
            st.caption("No LLM usage recorded for this brief.")

        if cache_hits:
            st.markdown("**External API cache:**")
            for k, v in cache_hits.items():
                emoji = "✅" if v else "❌"
                label = "hit (free)" if v else "miss (called API)"
                st.markdown(f"- {emoji} {k}: {label}")


st.set_page_config(page_title="SYNAPSE", layout="wide")
st.title("SYNAPSE — Context-Aware Reports")

with st.sidebar:
    st.link_button("🔭 View traces in Phoenix", PHOENIX_UI_URL,
                   use_container_width=True)

    # NEW (Day 9): cache stats at-a-glance
    cstats = synapse_cache.stats()
    if cstats.get("available"):
        total_ops = cstats["hits"] + cstats["misses"]
        rate = (cstats["hits"] / total_ops * 100) if total_ops else 0
        st.caption(
            f"⚡ Cache: {cstats['total_keys']} keys · "
            f"{rate:.0f}% hit rate ({cstats['hits']} hits / {cstats['misses']} misses)"
        )
    else:
        st.caption("⚡ Redis cache offline")

    # Day 10: show the mailbox backend mode.
    _mailbox_mode = mailbox_mode()
    if _mailbox_mode == "redis":
        st.caption("📬 Mailbox: Redis pub/sub (live)")
    elif _mailbox_mode == "file":
        st.caption("📬 Mailbox: JSON file (Redis fallback)")
    else:
        try:
            import redis
            r = redis.from_url(
                os.getenv("REDIS_URL", "redis://localhost:6379"),
                decode_responses=True, socket_connect_timeout=1,
            )
            r.ping()
            st.caption("📬 Mailbox: Redis pub/sub (live)")
        except Exception:
            st.caption("📬 Mailbox: JSON file (Redis fallback)")

    st.divider()

    st.header("💬 Conversations")
    if st.button("➕ New conversation", use_container_width=True, type="primary"):
        _reset_session(); st.rerun()
    try:
        convs = list_conversations(limit=15)
        if convs.get("total", 0) == 0:
            st.caption("No conversations yet.")
        else:
            for c in convs.get("conversations", []):
                label = c["topic"][:38] + ("..." if len(c["topic"]) > 38 else "")
                is_active = c["conversation_id"] == st.session_state["active_conversation_id"]
                marker = "▶ " if is_active else "📄 "
                if st.button(f"{marker}{label}", key=f"conv-{c['conversation_id']}",
                             use_container_width=True,
                             help=f"{c['turn_count']} turns"):
                    _hydrate_from_conversation(c["conversation_id"]); st.rerun()
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
                if st.button(f"📖 {label}", key=f"brief-{b['brief_id']}",
                             use_container_width=True):
                    st.session_state["viewing_brief"] = b["brief_id"]
    except Exception as e:
        st.caption(f"Memory server unavailable: {e}")


if st.session_state["active_conversation_id"] is None:
    st.subheader("Start a new brief")
    default_topic = st.session_state["prefilled_topic"] or "Semiconductor factory opening in Japan"
    topic = st.text_input("Topic", default_topic)
    st.session_state["prefilled_topic"] = ""

    # City detection now returns usage; track it
    try:
        location, ui_city_usage = get_location_context(topic)
        city_guess = location.get("capital", "Tokyo")
    except Exception:
        city_guess, ui_city_usage = "Tokyo", empty_usage()
    st.caption(f"Auto-detected city: **{city_guess}**"
               + (" (cached)" if ui_city_usage.get("_cache_hit") else ""))

    if st.button("Generate Brief", type="primary"):
        with tracer.start_as_current_span("ui.generate_brief") as ui_span:
            ui_span.set_attribute("topic", topic); ui_span.set_attribute("city", city_guess)
            with st.status("Running pipeline...", expanded=True) as status:
                st.write("🔍 Scout: gathering context, media, and memory...")
                scout_data = run_scout(topic, city_guess)
                scout_data = normalize_payload(scout_data)
                routing = scout_data.get("routing_decision") or {}
                if routing:
                    st.write(f"🧭 Router: {_format_routing_line(routing)}"
                             + (" (cached)" if routing.get("_cache_hit") else ""))
                    if routing.get("reasoning"):
                        st.caption(f"   _{routing['reasoning']}_")
                mem_count = (scout_data.get("memory_context") or {}).get("count", 0)
                st.write(f"🧠 Memory: {'found ' + str(mem_count) + ' related past brief(s)' if mem_count else 'no related past briefs'}")
                st.write("✍️ Publisher: drafting + critiquing + finalizing...")
                pub_data = run_publisher_initial(scout_data)
                rev = pub_data.get("revision_count", 0)
                if pub_data.get("critic_enabled", True):
                    if rev == 0:
                        st.write("👀 Critic: approved on first draft.")
                    else:
                        st.write(f"👀 Critic: requested {rev} revision(s); "
                                 f"approved on attempt {pub_data.get('approved_on_attempt', '?')}.")
                else:
                    st.write("👀 Critic: disabled (env var).")
                status.update(label="Done", state="complete", expanded=False)

            st.info(f"📊 [View this run's traces in Phoenix]({PHOENIX_UI_URL})")

            st.session_state["active_conversation_id"] = pub_data["conversation_id"]
            st.session_state["topic"] = topic
            st.session_state["city"] = city_guess
            st.session_state["memory_used"] = pub_data.get("memory_used", 0)
            st.session_state["routing_decision"] = routing
            st.session_state["critic_enabled"] = pub_data.get("critic_enabled", True)
            st.session_state["revision_count"] = pub_data.get("revision_count", 0)
            st.session_state["critique_history"] = pub_data.get("critique_history", [])
            st.session_state["approved_on_attempt"] = pub_data.get("approved_on_attempt", 1)
            # NEW (Day 9)
            st.session_state["usage_summary"] = pub_data.get("usage")
            st.session_state["cache_summary"] = pub_data.get("cache_hits")
            st.session_state["ui_city_usage"] = ui_city_usage
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
            _reset_session(); st.rerun()

    if st.session_state["critique_history"]:
        badge_cols = st.columns([1, 1, 4])
        if st.session_state["revision_count"] > 0:
            badge_cols[0].markdown(f"🔄 **Refined {st.session_state['revision_count']}×**")
        else:
            badge_cols[0].markdown("✅ **Approved on first draft**")
        badge_cols[1].markdown(f"👀 _Approved on attempt {st.session_state['approved_on_attempt']}_")
        with st.expander("🧐 View critique history", expanded=False):
            _render_critique_history(st.session_state["critique_history"])

    # NEW (Day 9): show cost & cache section
    _render_cost_and_cache(
        st.session_state.get("usage_summary"),
        st.session_state.get("cache_summary"),
        st.session_state.get("ui_city_usage") or empty_usage(),
    )

    if st.session_state["memory_used"] > 0:
        st.success(f"✨ Initial brief drew on {st.session_state['memory_used']} related past brief(s) from memory.")

    for turn in st.session_state["turns"]:
        with st.chat_message(turn.get("role", "assistant")):
            st.markdown(turn.get("content", ""), unsafe_allow_html=True)
            if (
                turn.get("role") == "assistant"
                and turn is st.session_state["turns"][1]
                and st.session_state["initial_image_url"]
            ):
                st.image(st.session_state["initial_image_url"],
                         caption="Related image", use_container_width=True)

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
            _reset_session(keep_prefilled_topic=new_topic); st.rerun()
        if cols[1].button("💬 Continue here anyway", use_container_width=True):
            held_msg = pivot["message"]
            st.session_state["pending_pivot"] = None
            try:
                result = run_publisher_followup(
                    st.session_state["active_conversation_id"], held_msg)
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
                intent = run_router_intent(user_question,
                                           st.session_state["topic"],
                                           st.session_state["turns"])
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
        st.session_state.pop("viewing_brief", None); st.rerun()