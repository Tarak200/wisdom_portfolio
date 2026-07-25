# Tool 2 - score_quant_fundamentals(financials, sector)
# Plain threshold checks, no LLM. The thresholds below are generic
# quality-investing defaults, NOT the fund's actual sector-specific numbers
# from "3 Investment Principles.pdf" (that PDF isn't parsed anywhere in this
# app) - treat this as a rough first pass and tune THRESHOLDS once the real
# numbers are available.
THRESHOLDS = {
    "return_on_equity": 0.15,  # ROE >= 15%
    "return_on_capital_employed": 0.15,  # ROCE >= 15% (from Screener.in, via tools/market_data.py)
    "revenue_growth": 0.10,  # revenue growing >= 10% YoY
    # D/E <= 1.0 (debt no more than 1x equity). This is now Borrowings /
    # (Equity Capital + Reserves), computed directly from Screener's
    # consolidated balance sheet (tools/fetch_screener.py) - a plain decimal
    # ratio, not yfinance's debtToEquity field, whose pre-multiplied-by-100
    # convention isn't reliably documented and can silently differ by ticker.
    "debt_to_equity": 1.0,
}


def score_fundamentals(financials: dict) -> dict:
    """Pass/fail each available metric against THRESHOLDS. Missing data is
    reported as 'insufficient_data', never silently treated as a pass or fail."""
    if financials.get("fetch_error"):
        return {"checks": [], "note": f"Skipped - fundamentals fetch failed: {financials['fetch_error']}"}

    checks = [
        _check("return_on_equity", financials.get("return_on_equity"), THRESHOLDS["return_on_equity"], higher_is_better=True),
        _check("return_on_capital_employed", financials.get("return_on_capital_employed"), THRESHOLDS["return_on_capital_employed"], higher_is_better=True),
        _check("revenue_growth", financials.get("revenue_growth"), THRESHOLDS["revenue_growth"], higher_is_better=True),
        _check(
            "debt_to_equity",
            financials.get("debt_to_equity"),
            THRESHOLDS["debt_to_equity"],
            higher_is_better=False,
            unverified_period=financials.get("debt_to_equity") is not None and not financials.get("debt_to_equity_period_verified", False),
        ),
        _check_positive("free_cash_flow", financials.get("free_cash_flow")),
    ]

    # Capital allocation ratios architecture.md also wants (capex/FCF, buyback
    # history, external-financing frequency) still need multi-year cash-flow
    # statement data none of Yahoo/Screener/NSE/BSE expose in the form this
    # app pulls them - not faked here.
    capital_allocation_note = (
        "capex/FCF, buyback history and external-financing frequency are not computed - "
        "they need multi-year cash flow statements this app does not pull."
    )

    return {
        "checks": checks,
        "passed": sum(1 for c in checks if c["result"] == "pass"),
        "failed": sum(1 for c in checks if c["result"] == "fail"),
        "insufficient_data": sum(1 for c in checks if c["result"] == "insufficient_data"),
        "capital_allocation_note": capital_allocation_note,
    }


def _check(metric: str, value, threshold: float, higher_is_better: bool, unverified_period: bool = False) -> dict:
    if value is None:
        return {"metric": metric, "value": None, "threshold": threshold, "result": "insufficient_data"}

    passed = value >= threshold if higher_is_better else value <= threshold
    check = {"metric": metric, "value": value, "threshold": threshold, "result": "pass" if passed else "fail"}
    if unverified_period:
        check["note"] = (
            "Screener's balance sheet column order could not be independently confirmed as latest-period-last for "
            "this company - treat this figure with caution until checked manually."
        )
    return check


def _check_positive(metric: str, value) -> dict:
    if value is None:
        return {"metric": metric, "value": None, "threshold": "> 0", "result": "insufficient_data"}
    return {"metric": metric, "value": value, "threshold": "> 0", "result": "pass" if value > 0 else "fail"}
