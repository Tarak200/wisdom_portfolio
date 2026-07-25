# Tool 1 (BSE leg) - fetch_bse_quote(scrip_code)
# BSE has no official public API either. This calls the same internal JSON
# endpoint BSE's own website uses for its quote header (the pattern the
# unofficial "bsedata" package is built on). scrip_code comes from Screener's
# page (fetch_screener.py), not guessed here - guessing a numeric BSE code
# risks silently pulling a different company's price, which is worse than no
# data at all.
#
# This is the lowest-confidence source of the three - BSE's exact response
# field names aren't as widely documented as NSE's, so the raw response is
# kept alongside the parsed fields in case the field names below are off; a
# human reviewer can check "raw" if the parsed numbers look empty or wrong.

import requests

BSE_QUOTE_API = "https://api.bseindia.com/BseIndiaAPI/api/getScripHeaderData/w"
REQUEST_TIMEOUT = 10
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36",
    "Accept": "application/json",
    "Referer": "https://www.bseindia.com/",
}


def fetch_bse_quote(scrip_code: str | None) -> dict:
    if not scrip_code:
        return {"source": "bse", "fetch_error": "No BSE scrip code available (Screener's page didn't have one)."}

    try:
        response = requests.get(
            BSE_QUOTE_API,
            params={"Debtflag": "", "scripcode": scrip_code, "seriesid": ""},
            headers=HEADERS,
            timeout=REQUEST_TIMEOUT,
        )
        response.raise_for_status()
        data = response.json()
    except Exception as exc:
        return {"source": "bse", "fetch_error": f"BSE fetch failed: {exc}"}

    return {
        "source": "bse",
        "scrip_code": scrip_code,
        "last_price": _as_float(data.get("LTP") or data.get("CurrRate")),
        "open": _as_float(data.get("Open")),
        "day_high": _as_float(data.get("High")),
        "day_low": _as_float(data.get("Low")),
        "previous_close": _as_float(data.get("PrevClose")),
        "raw": data,
        "fetch_error": None,
    }


def _as_float(value) -> float | None:
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None
