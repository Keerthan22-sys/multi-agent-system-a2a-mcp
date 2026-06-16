import os
import requests
from fastmcp import FastMCP
from dotenv import load_dotenv
from synapse import cache

load_dotenv()

mcp = FastMCP("Finance Monitor Server")

# Provided helper function to fetch currency code from a location
def get_currency_code(location: str) -> dict:
    """
    Dynamically find the 3-letter currency code for any country using REST Countries API.
    """
    url = f"https://restcountries.com/v3.1/name/{location}"
    
    try:
        response = requests.get(url, timeout=10)
        
        # If the search fails, try searching as a capital city
        if response.status_code != 200:
            url = f"https://restcountries.com/v3.1/capital/{location}"
            response = requests.get(url, timeout=10)

        if response.status_code == 200:
            payload = response.json()
            if not isinstance(payload, list) or not payload:
                return {
                    "error": f"Could not find currency for '{location}'",
                    "fallback": "USD",
                    "currency_code": "USD",
                }
            data = payload[0]
            currencies = data.get("currencies", {})
            if currencies:
                code = list(currencies.keys())[0]
                return {
                    "location": data.get("name", {}).get("common"),
                    "currency_code": code,
                    "symbol": currencies[code].get("symbol")
                }

        return {
            "error": f"Could not find currency for '{location}'",
            "fallback": "USD",
            "currency_code": "USD",
        }

    except Exception as e:
        return {"error": str(e), "fallback": "USD", "currency_code": "USD"}


@mcp.tool
def get_fx_rate(location: str) -> dict:
    """
    Get FX rate between two currencies.
    """
    cache_params = {"location": location.lower().strip()}
    cached = cache.get_cached("fx", cache_params)
    if cached:
        cached["_cache_hit"] = True
        return cached

    exchange_api_key = os.getenv("EXCHANGE_RATE_API_KEY")

    currency_info = get_currency_code(location)
    target = currency_info.get("currency_code") or currency_info.get("fallback", "USD")

    if target == "USD":
        result = {
            "currency_code": "USD",
            "rate": 1.0,
            "source": "fallback",
            **({"lookup_error": currency_info["error"]} if currency_info.get("error") else {}),
        }
    elif not exchange_api_key:
        return {
            "error": "EXCHANGE_RATE_API_KEY not configured",
            "currency_code": target,
            "fallback": "USD",
        }
    else:
        url = f"https://v6.exchangerate-api.com/v6/{exchange_api_key}/pair/{target}/USD"

        try:
            response = requests.get(url, timeout=10)
            if response.status_code != 200:
                result = {
                    "error": f"Exchange rate lookup failed for '{location}'",
                    "currency_code": target,
                    "fallback": "USD",
                }
            else:
                data = response.json()
                if data.get("result") != "success":
                    result = {
                        "error": data.get("error-type", "Exchange rate lookup failed"),
                        "currency_code": target,
                        "fallback": "USD",
                    }
                else:
                    result = {
                        "currency_code": target,
                        "rate": data["conversion_rate"],
                        "source": "ExchangeRate API",
                        **({"lookup_error": currency_info["error"]} if currency_info.get("error") else {}),
                    }

        except Exception as e:
            result = {"error": str(e), "currency_code": target, "fallback": "USD"}

    cache.set_cached("fx", cache_params, result, ttl_seconds=cache.TTL["fx"])
    result["_cache_hit"] = False
    return result


if __name__ == "__main__":
    mcp.run(
        transport="http",
        host="0.0.0.0",
        port=8002
    )