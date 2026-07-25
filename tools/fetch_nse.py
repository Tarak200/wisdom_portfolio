# Tool 1 (NSE leg) - fetch_nse_quote(nse_symbol)
# NSE has no official public API for third parties. This calls the same JSON
# endpoint NSE's own website calls (visible in its network requests), which
# needs session cookies from a normal page visit first - a bare API call with
# no cookies gets rejected. This is the same pattern used by well-known
# community tools (nsepython, jugaad-data).
#
# Known real risk, not just theoretical: NSE aggressively rate-limits and
# sometimes blocks requests from cloud/datacenter IP ranges (which is what
# Streamlit Community Cloud runs on) regardless of headers/cookies being
# correct. If this consistently returns fetch_error in production but works
# fine locally, that is very likely why - not a bug in this code.

import requests

NSE_HOME_URL = "https://www.nseindia.com"
NSE_QUOTE_API = "https://www.nseindia.com/api/quote-equity"
REQUEST_TIMEOUT = 10
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36",
    "Accept": "application/json",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": "https://www.nseindia.com/get-quotes/equity",
}


def fetch_nse_quote(nse_symbol: str) -> dict:
    """Live/official current price + day range + 52-week range for one
    symbol, used as a cross-check against Yahoo Finance's price."""
    session = requests.Session()
    session.headers.update(HEADERS)
    try:
        session.get(NSE_HOME_URL, timeout=REQUEST_TIMEOUT)  # picks up the cookies the API call needs
        response = session.get(NSE_QUOTE_API, params={"symbol": nse_symbol}, timeout=REQUEST_TIMEOUT)
        response.raise_for_status()
        data = response.json()
    except Exception as exc:
        return {"source": "nse", "fetch_error": f"NSE fetch failed: {exc}"}

    price_info = data.get("priceInfo", {})
    week_high_low = price_info.get("weekHighLow", {})
    intraday = price_info.get("intraDayHighLow", {})

    return {
        "source": "nse",
        "last_price": price_info.get("lastPrice"),
        "open": price_info.get("open"),
        "day_high": intraday.get("max"),
        "day_low": intraday.get("min"),
        "previous_close": price_info.get("previousClose"),
        "week_high": week_high_low.get("max"),
        "week_low": week_high_low.get("min"),
        "fetch_error": None,
    }
