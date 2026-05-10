import os
import requests
from fastmcp import FastMCP
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()

# Initialize the MCP server for media-related tools
mcp = FastMCP("Media Engine Server")

# Base URL for searching images on Pexels
PEXELS_SEARCH_URL = "https://api.pexels.com/v1/search"


@mcp.tool
def search_images(query: str, per_page: int = 1) -> dict:
    """
    Search Pexels for high-quality images based on a query.
    
    Args:
        query: The topic to search for images.
        per_page: Number of images to return.
    """
    # Read the Pexels API key from the environment
    api_key = os.getenv("PEXELS_API_KEY")
    if not api_key:
        return {"error": "PEXELS_API_KEY not set in environment"}

    # Pexels expects the API key in the Authorization header
    headers = {
        "Authorization": api_key
    }
    
    # Query parameters for the search request
    params = {
        "query": query,
        "per_page": per_page
    }

    try:
        # Call the Pexels search API
        response = requests.get(
            PEXELS_SEARCH_URL,
            headers=headers,
            params=params,
            timeout=10
        )

        # Handle invalid API key explicitly
        if response.status_code == 401:
            return {"error": "Invalid Pexels API Key."}

        # Raise an exception for other HTTP errors
        response.raise_for_status()
        data = response.json()

        photos = data.get("photos", [])
        if not photos:
            return {
                "query": query,
                "images": [],
                "message": "No images found."
            }

        # Format the response into a cleaner structure for agents
        results = []
        for photo in photos:
            results.append({
                "id": photo.get("id"),
                "url": photo.get("url"),
                "photographer": photo.get("photographer"),
                "alt": photo.get("alt"),
                "src": {
                    "url": photo.get("src", {}).get("large"),
                    "type": "image"
                }
            })

        return {
            "query": query,
            "images": results,
            "total_results": data.get("total_results")
        }

    except requests.exceptions.RequestException as e:
        return {"error": f"Pexels API request failed: {str(e)}"}


if __name__ == "__main__":
    # Start the MCP server for the Media Engine
    mcp.run(
        transport="http",
        host="0.0.0.0",
        port=8003
    )