# Tools 4a/4b - get_current_holdings + score_trade_behavior
# Both read the 10-yr trade log, but that work already happened once in
# notebooks/historical_data_analysis.ipynb and was written out to
# outputs/trade_metrics.xlsx (one sheet per company: open lots, cost basis,
# holding-period stats, buying/selling style, realized CAGR, etc). This just
# reads that finished sheet back in - no need to redo the FIFO-matching here.

import polars as pl

from config import NON_COMPANY_SHEETS, TRADE_METRICS_PATH


def list_companies() -> list[str]:
    """Sheet names in trade_metrics.xlsx, excluding the portfolio-level rollup."""
    sheets = pl.read_excel(TRADE_METRICS_PATH, sheet_id=0)  # sheet_id=0 reads all sheets
    return [name for name in sheets if name.lower() not in NON_COMPANY_SHEETS]


def load_metrics(company: str) -> dict[str, str]:
    """Return the metric/value pairs for one company's sheet, matched case-insensitively.
    This single sheet covers both 4a (open_lots, open_qty, cost basis, days held)
    and 4b (win_rate, buying_style, selling_style, avg_cagr) - they were computed
    together in the notebook, so they're read together here too."""
    all_sheets = pl.read_excel(TRADE_METRICS_PATH, sheet_id=0)
    matching_sheet = next((name for name in all_sheets if name.lower() == company.lower()), None)
    if matching_sheet is None:
        available = [name for name in all_sheets if name.lower() not in NON_COMPANY_SHEETS]
        raise ValueError(f"No trade metrics sheet found for '{company}'. Available: {available}")

    table = all_sheets[matching_sheet]
    return dict(zip(table["metric"].to_list(), table["value"].to_list()))


def has_open_position(metrics: dict) -> bool:
    """Tool 4a's real job: tell the caller whether this is a hold/trim/exit
    question or a buy/skip question, before the recommendation prompt is built."""
    open_lots = metrics.get("open_lots")
    try:
        return open_lots is not None and float(open_lots) > 0
    except (TypeError, ValueError):
        return False


def load_portfolio_metrics() -> dict[str, str]:
    """The one rollup sheet in NON_COMPANY_SHEETS (portfolio-wide totals/averages
    across all 4 scrips) - tool 4b's portfolio-level view, read the same way as
    a per-company sheet. Architecture.md has this injected as static context
    directly into Agent 2's prompt, alongside each company's own sheet."""
    all_sheets = pl.read_excel(TRADE_METRICS_PATH, sheet_id=0)
    matching_sheet = next((name for name in all_sheets if name.lower() in NON_COMPANY_SHEETS), None)
    if matching_sheet is None:
        return {}

    table = all_sheets[matching_sheet]
    return dict(zip(table["metric"].to_list(), table["value"].to_list()))
