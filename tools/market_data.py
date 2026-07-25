# Tool 1 (aggregator) - fetch_market_data(company)
# Merges Yahoo Finance, Screener.in, NSE and BSE into one dict. Each source
# has exactly one job so nothing is fetched or parsed twice:
#   - Yahoo Finance (fetch_fundamentals.py): historical price context + the
#     ratios it already exposes - unchanged, kept as the base dict.
#   - Screener.in: ROCE and promoter shareholding % (Yahoo has neither),
#     pros/cons, and document links - "financials and documents in one place".
#   - NSE: live/official current price + day/52-week range.
#   - BSE: a second live-price cross-check, via the scrip code Screener's
#     page links to.
# agent.py and score_fundamentals.py only ever call this, not the individual
# source modules directly - one call in, one merged dict out.

from tools import fetch_bse, fetch_fundamentals as fetch_yahoo, fetch_nse, fetch_screener

PRICE_DISAGREEMENT_THRESHOLD_PCT = 5.0


def fetch_market_data(company: str) -> dict:
    yahoo = fetch_yahoo.fetch_fundamentals(company)
    nse_symbol = yahoo["ticker"].removesuffix(".NS") if yahoo.get("ticker") else None

    if nse_symbol:
        screener = fetch_screener.fetch_screener(nse_symbol)
        nse = fetch_nse.fetch_nse_quote(nse_symbol)
    else:
        screener = {"source": "screener.in", "fetch_error": "No NSE symbol resolved for this company."}
        nse = {"source": "nse", "fetch_error": "No NSE symbol resolved for this company."}

    bse = fetch_bse.fetch_bse_quote(screener.get("bse_scrip_code"))

    price_by_source = {
        "yahoo_finance": yahoo.get("current_price"),
        "nse": nse.get("last_price"),
        "bse": bse.get("last_price"),
    }

    merged = dict(yahoo)  # keeps every field agent.py/score_fundamentals.py already read
    merged["screener"] = screener
    merged["nse"] = nse
    merged["bse"] = bse
    merged["price_by_source"] = price_by_source
    merged["price_cross_check"] = _cross_check_prices(price_by_source)

    roce_raw = screener.get("ratios", {}).get("ROCE")
    if roce_raw:
        merged["return_on_capital_employed"] = fetch_screener.parse_ratio_value(roce_raw)
    if screener.get("promoter_holding_pct") is not None:
        merged["promoter_holding_pct"] = screener["promoter_holding_pct"]
        merged["promoter_holding_trend"] = screener.get("promoter_holding_trend")
        merged["promoter_holding_period_verified"] = screener.get("promoter_holding_period_verified", False)

    # Yahoo's debtToEquity (kept above as part of `dict(yahoo)`) has an
    # undocumented, ticker-dependent scaling convention (raw ratio vs.
    # pre-multiplied by 100) - Principle 5 (conservative balance sheet) is too
    # important to risk scoring on a guessed unit. Screener's own balance
    # sheet gives Borrowings/Equity Capital/Reserves in the same Rs. Crore
    # units, so this ratio is unit-unambiguous by construction - prefer it,
    # and keep Yahoo's raw field under a clearly-labeled name for reference
    # only (score_fundamentals.py does not read that field).
    merged["debt_to_equity_yahoo_raw_unverified_units"] = merged.pop("debt_to_equity", None)
    if screener.get("debt_to_equity") is not None:
        merged["debt_to_equity"] = screener["debt_to_equity"]
        merged["debt_to_equity_period_verified"] = screener.get("debt_to_equity_period_verified", False)
        merged["debt_to_equity_source"] = "screener.in balance sheet (Borrowings / (Equity Capital + Reserves))"
    else:
        merged["debt_to_equity"] = None
        merged["debt_to_equity_period_verified"] = False
        merged["debt_to_equity_source"] = None

    return merged


def _cross_check_prices(price_by_source: dict) -> dict:
    """Sanity check on the market data itself, same spirit as tool 6's
    hallucination check on the LLM's claims - if two live sources disagree by
    a lot, that is worth flagging before anything downstream trusts either one."""
    available = {source: price for source, price in price_by_source.items() if price is not None}
    if len(available) < 2:
        return {"performed": False, "reason": "Fewer than 2 sources returned a price - nothing to cross-check."}

    values = list(available.values())
    spread_pct = (max(values) - min(values)) / min(values) * 100 if min(values) else None
    agree = spread_pct is not None and spread_pct <= PRICE_DISAGREEMENT_THRESHOLD_PCT

    return {
        "performed": True,
        "sources_compared": available,
        "spread_pct": round(spread_pct, 2) if spread_pct is not None else None,
        "agree": agree,
        "note": (
            "Prices agree within threshold."
            if agree
            else f"Sources disagree by more than {PRICE_DISAGREEMENT_THRESHOLD_PCT}% - verify manually before trusting either."
        ),
    }
