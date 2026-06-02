# Save as diagnose_memory.py in your repo root, run with venv active
import asyncio
from fastmcp import Client

MEMORY_URL = "http://0.0.0.0:8006/mcp"
TEST_QUERIES = [
    "Indian semiconductor policy 2026",
    "India chip manufacturing subsidies",
    "K-pop tour in Bangkok",
    "EU AI regulation",
    "weather in Mumbai",
]

async def main():
    async with Client(MEMORY_URL) as c:
        for query in TEST_QUERIES:
            r = await c.call_tool("search_briefs", {"query": query, "k": 5})
            data = r.data
            print(f"\nQuery: '{query}'")
            if not data.get("briefs"):
                print("  → No results (empty memory or all filtered)")
                continue
            for b in data["briefs"]:
                print(f"  dist={b['distance']:.3f}  '{b['topic']}'")

asyncio.run(main())