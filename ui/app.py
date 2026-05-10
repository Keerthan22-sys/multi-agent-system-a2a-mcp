# Pre-provided imports
from dotenv import load_dotenv
from openai import OpenAI
import json
import os

load_dotenv()  # Load environment variables

# MCP agent URLs
SCOUT_URL = "http://0.0.0.0:8004/mcp"
PUBLISHER_URL = "http://0.0.0.0:8005/mcp"

# Fetch OpenAI API key from environment
api_key = os.getenv("OPENAI_API_KEY")

# Async helper to call MCP tools
async def call_tool(url, tool, params):
    async with Client(url) as client:
        res = await client.call_tool(tool, params)
        return res.data

# Pre-provided OpenAI client
client = OpenAI(api_key=api_key)

# Pre-provided function to fetch city/country context from topic
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
        response_format={"type": "json_object"}
    )
    return json.loads(response.choices[0].message.content)

# Task 10: Build Streamlit Interface to Trigger Agents
import streamlit as st
import asyncio
from fastmcp import Client
# Call Scout agent
def run_scout(topic, city):
    return asyncio.run(call_tool(SCOUT_URL, "scout", {"topic": topic, "city": city}))

# Call Publisher agent
def run_publisher(payload):
    return asyncio.run(call_tool(PUBLISHER_URL, "publish_brief", {"payload": payload}))

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

# Streamlit UI
st.title("SYNAPSE - Context Aware Reports")

# Topic input
topic = st.text_input("Topic", "Semiconductor factory opening in Japan")
# Auto-fetch city using LLM
city = get_location_context(topic)['capital']

# Task 11: Improve Article Readability in the UI
# Button triggers agents
if st.button("Generate Report"):
    st.write("Running Scout...")
    scout_data = run_scout(topic, city)

    # Normalize image src before sending to publisher
    scout_data = normalize_payload(scout_data)

    st.write("Running Publisher...")
    publisher_data = run_publisher(scout_data)

    # Render Article in an expander to reduce scrolling
    st.subheader("Final Article")
    with st.expander("Read full article"):
        st.markdown(publisher_data.get("article", "No output"), unsafe_allow_html=True)

    # Display related image above/below article
    try:
        image_url = scout_data["media"]["images"][0]["src"]["url"]
        if image_url:
            st.image(image_url, caption="Related Image", use_column_width=True)
    except Exception:
        pass

    # Keep payload visible but secondary
    st.subheader("Payload")
    st.json(publisher_data.get("payload"))

    # Show signal only if valid
    signal = publisher_data.get("signal")
    if isinstance(signal, dict) and "ERROR" not in signal:
        st.subheader("Full Signal")
        st.json(signal)
    else:
        st.write("Signal is invalid or contains errors.")