# Tool 1 - fetch_market_fundamentals(ticker)
# Pulls live price and basic fundamentals from Yahoo Finance via yfinance.
# No API key needed for this one, unlike the LLM providers.
#
# NSE symbol for each company - user-confirmed against Screener.in/NSE listings.
COMPANY_TICKERS = {
    "amber": "AMBER.NS",
    "dbl": "DBL.NS",
    "welspun": "WELCORP.NS",
    "zee": "ZEEL.NS",
}

# Yahoo Finance itself has no field for Indian-style promoter holding/pledge %
# or ROCE - "heldPercentInsiders" is the closest proxy it offers, and it is
# not the same thing. tools/market_data.py fills promoter_holding_pct and
# ROCE in from Screener.in, but promoter PLEDGE % still needs a filings-level
# read Screener's public page doesn't surface either - still genuinely unavailable.
FIELDS_NOT_AVAILABLE = [
    "promoter_pledge_pct (needs a deeper shareholding-pattern filings read, not on Screener's public page)",
]


def fetch_fundamentals(company: str) -> dict:
    """Fetch price + fundamentals for a company. Returns None for anything
    that isn't available rather than guessing - never fabricate a number."""
    ticker_symbol = COMPANY_TICKERS.get(company.lower())
    if not ticker_symbol:
        return {
            "company": company,
            "ticker": None,
            "fetch_error": f"No ticker configured for '{company}' - add it to COMPANY_TICKERS.",
        }

    try:
        import yfinance as yf

        info = yf.Ticker(ticker_symbol).info
    except Exception as exc:
        return {
            "company": company,
            "ticker": ticker_symbol,
            "fetch_error": f"Yahoo Finance fetch failed: {exc}",
        }

    if not info or info.get("regularMarketPrice") is None and info.get("currentPrice") is None:
        return {
            "company": company,
            "ticker": ticker_symbol,
            "fetch_error": "Yahoo Finance returned no usable data for this ticker.",
        }

    return {
        "company": company,
        "ticker": ticker_symbol,
        "ticker_verified": True,  # confirmed against Screener.in/NSE listings
        "current_price": info.get("currentPrice") or info.get("regularMarketPrice"),
        "currency": info.get("currency"),
        "return_on_equity": info.get("returnOnEquity"),
        "return_on_assets": info.get("returnOnAssets"),
        "revenue_growth": info.get("revenueGrowth"),
        "free_cash_flow": info.get("freeCashflow"),
        "total_debt": info.get("totalDebt"),
        "total_cash": info.get("totalCash"),
        "debt_to_equity": info.get("debtToEquity"),
        "dividend_yield": info.get("dividendYield"),
        "payout_ratio": info.get("payoutRatio"),
        "insider_holding_pct_proxy": info.get("heldPercentInsiders"),
        "sector": info.get("sector"),
        "industry": info.get("industry"),
        "market_cap": info.get("marketCap"),
        "trailing_pe": info.get("trailingPE"),
        "fields_not_available": FIELDS_NOT_AVAILABLE,
        "fetch_error": None,
    }
