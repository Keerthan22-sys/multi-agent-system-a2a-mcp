# Task 9: Build Publisher Agent to Generate Articles
import os
import json
import asyncio
from fastmcp import Client, FastMCP
from dotenv import load_dotenv

# Load environment variables such as OPENAI_API_KEY
load_dotenv()

# Initialize the MCP server for the Publisher agent
mcp = FastMCP("Publisher Agent")


@mcp.tool
async def publish_brief(payload: dict) -> dict:
    """
    Generate a daily brief article using an LLM based on aggregated signals.
    """
    # Build the prompt using the aggregated payload
    prompt = f"""
You are a news writer.

Using the news below, write a short daily brief article.
Use a neutral journalistic tone. At the end of the article,
add info about city, its conversion rate and current weather.

Data:
{json.dumps(payload, indent=2)}

Rules:
- Do not invent facts.
- If data is missing, say "Not available."
- Include:
  - headline
  - 2-3 paragraphs
  - a short "Why it matters" section.
  - A section "About the place of news" section mentioning the weather, conversion rate
"""

    # Initialize the OpenAI client with the API key
    from openai import OpenAI
    client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

    # Call the OpenAI Responses API to generate the article
    response = client.responses.create(
        model="gpt-5-nano",
        input=prompt,
        max_output_tokens=1500,
        reasoning={
        "effort": "low" 
        }
    )

    # Extract the generated article text
    article = response.output_text.strip()

    # Return the article along with the original payload
    return {
        "article": article,
        "payload": payload
    }


if __name__ == "__main__":
    # Start the Publisher MCP server
    mcp.run(
        transport="http",
        host="0.0.0.0",
        port=8005
    )