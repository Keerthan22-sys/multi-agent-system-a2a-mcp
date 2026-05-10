# import asyncio
# from fastmcp import Client

# client = Client("http://0.0.0.0:8000/mcp")

# async def call_tool(name: str):
#     async with client:
#         result = await client.call_tool("process_data", {"input": name})
#         print(result)

# asyncio.run(call_tool("Ford"))