# Tool 1 (Screener.in leg) - fetch_screener(nse_symbol)
# Screener.in has no official public API, but its company pages are public
# (no login needed for the ratios/shareholding/documents shown here) - this
# scrapes that page directly with requests + BeautifulSoup.
#
# This is what closes two gaps Yahoo Finance (tools/fetch_fundamentals.py)
# explicitly flags as unavailable: ROCE and promoter shareholding %. It also
# pulls the pros/cons summary and links to annual reports/concalls/credit
# ratings ("financials and documents at one place"), plus the BSE scrip code
# embedded in Screener's own link to the BSE quote page - used downstream by
# fetch_bse.py instead of guessing a scrip code.
#
# Caveat, same spirit as the ticker-verification warning in fetch_fundamentals.py:
# this depends on Screener's current page markup and WILL break if they
# redesign it - if the numbers below look empty or wrong, check
# https://www.screener.in/company/{symbol}/consolidated/ by hand before
# trusting anything downstream of this.

import re

import requests
from bs4 import BeautifulSoup

SCREENER_URL_TEMPLATE = "https://www.screener.in/company/{symbol}/consolidated/"
REQUEST_TIMEOUT = 15
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36",
    "Accept-Language": "en-US,en;q=0.9",
}


def fetch_screener(nse_symbol: str) -> dict:
    """Fetch ratios, pros/cons, promoter shareholding trend, balance-sheet-based
    debt/equity, and document links for one company from its Screener.in
    consolidated page."""
    url = SCREENER_URL_TEMPLATE.format(symbol=nse_symbol)
    try:
        response = requests.get(url, headers=HEADERS, timeout=REQUEST_TIMEOUT)
        response.raise_for_status()
    except Exception as exc:
        return {"source": "screener.in", "url": url, "fetch_error": f"Screener.in fetch failed: {exc}"}

    soup = BeautifulSoup(response.text, "html.parser")
    ratios = _parse_top_ratios(soup)
    pros, cons = _parse_pros_cons(soup)
    shareholding = _parse_shareholding(soup)
    debt_equity = _parse_balance_sheet_debt_equity(soup)
    bse_scrip_code, bse_url, nse_url = _parse_exchange_links(soup)

    return {
        "source": "screener.in",
        "url": url,
        "ratios": ratios,
        "pros": pros,
        "cons": cons,
        "promoter_holding_pct": shareholding.get("promoter_latest"),
        "promoter_holding_trend": shareholding.get("promoter_trend"),
        "promoter_holding_period_verified": shareholding.get("promoter_trend_order_verified", False),
        "debt_to_equity": debt_equity.get("debt_to_equity"),
        "debt_to_equity_period_verified": debt_equity.get("debt_to_equity_period_verified", False),
        "debt_to_equity_inputs": debt_equity.get("debt_to_equity_inputs"),
        "bse_scrip_code": bse_scrip_code,
        "bse_url": bse_url,
        "nse_url": nse_url,
        "documents": _parse_documents(soup),
        "fetch_error": None,
    }


def parse_ratio_value(raw: str) -> float | None:
    """Screener ratio text looks like '₹ 10,130 Cr.', '2.75 %', '36.3' - pull
    out the leading number. Percent values come back as a 0-1 fraction to
    match the rest of the app's convention (0.15 for 15%), not the raw number."""
    if not raw:
        return None
    match = re.search(r"-?[\d,]+\.?\d*", raw)
    if not match:
        return None
    try:
        value = float(match.group().replace(",", ""))
    except ValueError:
        return None
    return value / 100 if "%" in raw else value


def _parse_top_ratios(soup: BeautifulSoup) -> dict:
    """The ratio grid at the top of every Screener company page (Market Cap,
    Current Price, Stock P/E, Book Value, Dividend Yield, ROCE, ROE, etc)."""
    ratios = {}
    container = soup.find("ul", id="top-ratios")
    if not container:
        return ratios
    for item in container.find_all("li"):
        name_el = item.find("span", class_="name")
        value_el = item.find("span", class_="value")
        if not name_el or not value_el:
            continue
        ratios[name_el.get_text(strip=True)] = value_el.get_text(" ", strip=True)
    return ratios


def _parse_pros_cons(soup: BeautifulSoup) -> tuple[list[str], list[str]]:
    pros_el = soup.find(class_="pros")
    cons_el = soup.find(class_="cons")
    pros = [li.get_text(strip=True) for li in pros_el.find_all("li")] if pros_el else []
    cons = [li.get_text(strip=True) for li in cons_el.find_all("li")] if cons_el else []
    return pros, cons


def _parse_period_label(label: str) -> tuple[int, int] | None:
    """Parse a Screener column header like 'Mar 2023', 'Sep'22' into a
    sortable (year, month) tuple. Returns None for anything that doesn't look
    like a calendar period (e.g. a 'TTM' column some pages add)."""
    match = re.search(r"([A-Za-z]{3,9})\s*'?\s*(\d{2,4})", label)
    if not match:
        return None
    month_str, year_str = match.groups()
    months = ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"]
    month_lower = month_str.lower()[:3]
    if month_lower not in months:
        return None
    year = int(year_str)
    if year < 100:
        year += 2000
    return (year, months.index(month_lower))


def _latest_column_index(table) -> tuple[int | None, bool]:
    """Figure out which data column is actually the most recent period by
    parsing the table's header row as calendar periods, instead of assuming
    Screener always lays these out left-to-right oldest-to-newest. If the
    scrape ever breaks or the layout changes, this fails loudly (verified=False)
    rather than silently reporting a stale figure as current.

    Returns (index_into_the_per-row_value_list, verified)."""
    header_source = table.find("thead") or table.find("tr")
    if header_source is None:
        return None, False
    header_cells = header_source.find_all("th")
    labels = [th.get_text(strip=True) for th in header_cells][1:]  # skip the row-label column
    if not labels:
        return None, False

    periods = [_parse_period_label(label) for label in labels]
    if all(p is not None for p in periods):
        return max(range(len(periods)), key=lambda i: periods[i]), True

    # Couldn't parse every header as a calendar period - fall back to
    # Screener's usual left-to-right oldest-to-newest layout, but flag that
    # this wasn't independently confirmed.
    return len(labels) - 1, False


def _parse_shareholding(soup: BeautifulSoup) -> dict:
    """Latest-quarter promoter holding % and the full quarterly trend, from
    the shareholding pattern table - used for Agent 1's management
    skin-in-the-game verdict, which previously had no real data to work with.
    Which column counts as "latest" is confirmed from the header dates
    themselves (see _latest_column_index), not assumed from position."""
    section = soup.find("section", id="shareholding")
    if not section:
        return {}
    table = section.find("table")
    if not table:
        return {}

    latest_index, verified = _latest_column_index(table)

    for row in table.find_all("tr"):
        cells = row.find_all("td")
        if not cells:
            continue
        label = cells[0].get_text(strip=True).rstrip("+").strip()
        if label.lower().startswith("promoter"):
            values = [parse_ratio_value(c.get_text(strip=True)) for c in cells[1:]]
            trend = [v for v in values if v is not None]

            promoter_latest = values[latest_index] if latest_index is not None and latest_index < len(values) else None
            if promoter_latest is None:
                # Header parsing didn't line up with this row's cell count -
                # fall back to the last non-null value, same as before, but
                # this path is never silently treated as verified.
                promoter_latest = trend[-1] if trend else None
                verified = False

            return {"promoter_latest": promoter_latest, "promoter_trend": trend, "promoter_trend_order_verified": verified}
    return {}


def _parse_balance_sheet_debt_equity(soup: BeautifulSoup) -> dict:
    """Debt/equity computed directly from Screener's own consolidated balance
    sheet - Borrowings / (Equity Capital + Reserves) - instead of trusting
    yfinance's debtToEquity field (tools/fetch_fundamentals.py), whose scaling
    convention (raw ratio vs. pre-multiplied by 100) isn't reliably documented
    and can silently differ by ticker. Both the numerator and denominator here
    come from the same Rs. Crore balance sheet table, so the ratio is
    unitless by construction - no percentage-vs-ratio ambiguity possible.
    This directly feeds Principle 5 (conservative balance sheet, low reliance
    on external capital), so getting the units right matters more here than
    almost anywhere else in the quant scorecard."""
    section = soup.find("section", id="balance-sheet")
    if not section:
        return {}
    table = section.find("table")
    if not table:
        return {}

    latest_index, verified = _latest_column_index(table)
    if latest_index is None:
        return {}

    values_by_label = {}
    for row in table.find_all("tr"):
        cells = row.find_all("td")
        if not cells:
            continue
        label = cells[0].get_text(strip=True).rstrip("+").strip().lower()
        values = [parse_ratio_value(c.get_text(strip=True)) for c in cells[1:]]
        if latest_index < len(values):
            values_by_label[label] = values[latest_index]

    equity_capital = values_by_label.get("equity capital")
    reserves = values_by_label.get("reserves")
    borrowings = values_by_label.get("borrowings")

    if equity_capital is None or reserves is None or borrowings is None:
        return {}

    total_equity = equity_capital + reserves
    if not total_equity:
        return {}

    return {
        "debt_to_equity": round(borrowings / total_equity, 4),
        "debt_to_equity_period_verified": verified,
        "debt_to_equity_inputs": {
            "borrowings_cr": borrowings,
            "equity_capital_cr": equity_capital,
            "reserves_cr": reserves,
        },
    }


def _parse_documents(soup: BeautifulSoup, limit: int = 20) -> list[dict]:
    """Links to annual reports, concall transcripts and credit rating updates."""
    section = soup.find("section", id="documents")
    if not section:
        return []
    documents = []
    for link in section.find_all("a", href=True):
        href = link["href"]
        if href.lower().endswith(".pdf") or "annualreport" in href.lower():
            documents.append({"label": link.get_text(strip=True) or "document", "url": href})
        if len(documents) >= limit:
            break
    return documents


def _parse_exchange_links(soup: BeautifulSoup) -> tuple[str | None, str | None, str | None]:
    """Screener links straight to the BSE and NSE quote pages for this company -
    the BSE link has the scrip code baked into its URL, so fetch_bse.py doesn't
    have to guess one."""
    bse_url, nse_url = None, None
    for link in soup.find_all("a", href=True):
        text = link.get_text(strip=True)
        href = link["href"]
        if text == "BSE" and "bseindia.com" in href:
            bse_url = href
        elif text == "NSE" and "nseindia.com" in href:
            nse_url = href

    bse_scrip_code = None
    if bse_url:
        match = re.search(r"/(\d{5,6})/?$", bse_url.rstrip("/"))
        if match:
            bse_scrip_code = match.group(1)
    return bse_scrip_code, bse_url, nse_url
