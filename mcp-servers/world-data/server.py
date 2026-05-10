import os
import requests
from fastmcp import FastMCP
from dotenv import load_dotenv

load_dotenv()
mcp = FastMCP("World Data Server")

@mcp.tool
def search_news(query: str) -> dict:
    api_key = os.getenv("NEWSAPI_KEY")
    if not api_key:
        return {"error": "NEWSAPI_KEY not set in environment"}

    params = {
        "q": query,
        "language": "en",
        "sortBy": "publishedAt",
        "pageSize": 1,
        "apiKey": api_key
    }

    NEWS_API_URL = "https://newsapi.org/v2/everything"
    response = requests.get(NEWS_API_URL, params=params, timeout=10)

    if response.status_code != 200:
        return {
            "error": "NewsAPI request failed",
            "status_code": response.status_code,
            "details": response.text
        }

    data = response.json()
    articles = data.get("articles", [])

    if not articles:
        return {"query": query, "articles": []}

    article = articles[0]
    return {
        "query": query,
        "headline": article.get("title"),
        "description": article.get("description"),
        "source": article.get("source", {}).get("name"),
        "url": article.get("url"),
        "published_at": article.get("publishedAt")
    }

OPENWEATHER_API_URL = "https://api.openweathermap.org/data/2.5/weather"

@mcp.tool
def get_weather(city: str, units: str = "metric") -> dict:
    weather_api_key = os.getenv("OPENWEATHER_API_KEY")
    if not weather_api_key:
        return {"error": "OPENWEATHER_API_KEY not set in environment"}

    params = {"q": city, "appid": weather_api_key, "units": units}

    try:
        response = requests.get(OPENWEATHER_API_URL, params=params, timeout=10)

        if response.status_code == 404:
            return {"error": f"City '{city}' not found."}
        elif response.status_code == 401:
            return {"error": "Invalid API Key."}

        response.raise_for_status()
        data = response.json()

        return {
            "city": data.get("name"),
            "country": data.get("sys", {}).get("country"),
            "temperature": data.get("main", {}).get("temp"),
            "feels_like": data.get("main", {}).get("feels_like"),
            "humidity": data.get("main", {}).get("humidity"),
            "description": data.get("weather", [{}])[0].get("description"),
            "wind_speed": data.get("wind", {}).get("speed"),
            "units": "°C" if units == "metric" else "°F" if units == "imperial" else "K"
        }

    except requests.exceptions.RequestException as e:
        return {"error": f"API request failed: {str(e)}"}

if __name__ == "__main__":
    mcp.run(transport="http", host="0.0.0.0", port=8001)