# Streamlit front end for the WISDOM analyst agent.
# Pick a company, run the analysis, review the recommendation - nothing here
# executes a trade, it only displays the agent's advisory output.

import streamlit as st

from agent import run_analysis
from orchestrator import run_portfolio_review
from tools import trade_metrics

st.set_page_config(page_title="WISDOM Portfolio Manager", page_icon="\U0001F4CA")

st.title("WISDOM Portfolio Manager")
st.caption(
    "Advisory only. Every recommendation below requires human review before "
    "it is acted on - nothing in this app places trades."
)

try:
    companies = trade_metrics.list_companies()
except Exception as exc:
    st.error(f"Could not read outputs/trade_metrics.xlsx: {exc}")
    st.stop()

view = st.radio("View", ["Single stock (Agent 1)", "Full portfolio (Agent 2 - all 4 stocks)"], horizontal=True)

if view == "Full portfolio (Agent 2 - all 4 stocks)":
    if st.button("Run portfolio review", type="primary"):
        with st.spinner("Running Agent 1 on all 4 stocks, then the orchestrator..."):
            try:
                portfolio = run_portfolio_review(companies)
            except Exception as exc:
                st.error(f"Portfolio review failed: {exc}")
                st.stop()

        st.caption(f"As of {portfolio['as_of_date']}")

        if portfolio.get("stage1_failures"):
            st.subheader("Stage 1 failures")
            st.caption("These companies could not be analyzed and are excluded from the review below.")
            for failed_company, error in portfolio["stage1_failures"].items():
                st.error(f"{failed_company}: {error}")

        summary = portfolio["portfolio_summary"]
        st.subheader("Portfolio summary")
        if summary.get("style_strengths"):
            st.write("**Style strengths:**")
            for item in summary["style_strengths"]:
                st.write(f"- {item}")
        if summary.get("style_biases"):
            st.write("**Style biases:**")
            for item in summary["style_biases"]:
                st.write(f"- {item}")
        if summary.get("portfolio_risks"):
            st.write("**Portfolio risks:**")
            for item in summary["portfolio_risks"]:
                st.write(f"- {item}")
        if summary.get("cash_view"):
            st.write(f"**Cash view:** {summary['cash_view']}")

        st.subheader("Per-stock sizing")
        for review in portfolio["stock_reviews"]:
            st.markdown(f"**{review['company']}** - Stage 1 decision: `{review['stage1_decision']}` -> sizing: `{review['sizing_guidance']}`")
            if review.get("sizing_override"):
                st.warning(review["sizing_override"])
            st.write(review.get("sizing_rationale", ""))

        if portfolio["next_actions"]:
            st.subheader("Next actions")
            for action in portfolio["next_actions"]:
                st.write(f"- {action}")

        st.info("This portfolio review requires human review before any action is taken.")
    st.stop()

company = st.selectbox("Company", companies)
run_clicked = st.button("Run analysis", type="primary")

if not run_clicked:
    st.stop()

with st.spinner(f"Fetching fundamentals, reading documents and trade metrics for {company}..."):
    try:
        result = run_analysis(company)
    except FileNotFoundError as exc:
        st.error(str(exc))
        st.stop()
    except Exception as exc:
        st.error(f"Analysis failed: {exc}")
        st.stop()

REC_COLOR = {"buy": "green", "hold": "orange", "sell": "red"}
color = REC_COLOR.get(result["recommendation"], "gray")

st.markdown(
    f"### :{color}[{result['recommendation'].upper()}] "
    f"&nbsp;·&nbsp; conviction: {result['conviction']}"
)
if result["guardrail_override"]:
    st.warning(f"Guardrail applied: {result['guardrail_override']}")

position_label = "Open position (hold/trim/exit question)" if result["holding_open"] else "No open position (buy/skip question)"
st.caption(f"Sources: {', '.join(result['report_sources'])} · {position_label} · as of {result['as_of_date']}")

st.subheader("Business quality")
st.write(result["business_quality"])

st.subheader("Management alignment")
st.write(result["management_alignment"])

st.subheader("Reinvestment runway")
st.write(result["reinvestment_runway"])

st.subheader("Structural vs. cyclical growth")
st.write(result["structural_vs_cyclical"])

if result["red_flags"]:
    st.subheader("Red flags")
    for flag in result["red_flags"]:
        st.error(flag)

if result["key_risks"]:
    st.subheader("Key risks")
    for risk in result["key_risks"]:
        st.write(f"- {risk}")

if result["trade_behavior_notes"]:
    st.subheader("Trade behavior calibration")
    st.caption("Based on this investor's own past trades - sizing/timing tone only, not a business quality signal.")
    for note in result["trade_behavior_notes"]:
        st.write(f"- {note}")

st.subheader("Market fundamentals (Yahoo Finance, Screener.in, NSE, BSE)")
fundamentals = result["fundamentals"]
if fundamentals.get("fetch_error"):
    st.warning(f"Fundamentals not available: {fundamentals['fetch_error']}")
else:
    st.caption(f"Ticker: {fundamentals['ticker']} (verified)")

    price_check = fundamentals.get("price_cross_check", {})
    if price_check.get("performed"):
        note_fn = st.success if price_check["agree"] else st.warning
        note_fn(f"Price cross-check across {list(price_check['sources_compared'].keys())}: {price_check['note']}")
    else:
        st.caption(f"Price cross-check skipped: {price_check.get('reason', 'not enough sources returned a price.')}")

    screener = fundamentals.get("screener", {})
    if screener.get("fetch_error"):
        st.caption(f"Screener.in: {screener['fetch_error']}")
    else:
        if screener.get("promoter_holding_pct") is not None:
            st.write(f"**Promoter holding:** {screener['promoter_holding_pct']:.1%} (trend: {screener.get('promoter_holding_trend')})")
            if not screener.get("promoter_holding_period_verified", False):
                st.caption(
                    "⚠️ Latest-quarter column could not be independently confirmed from the table headers - "
                    "verify manually on Screener.in before trusting this as the current figure."
                )
        if fundamentals.get("debt_to_equity") is not None:
            st.write(f"**Debt/Equity:** {fundamentals['debt_to_equity']:.2f} ({fundamentals.get('debt_to_equity_source')})")
            if not fundamentals.get("debt_to_equity_period_verified", False):
                st.caption(
                    "⚠️ Balance sheet column order could not be independently confirmed as latest-period-last - "
                    "verify manually on Screener.in before trusting this figure."
                )
        else:
            st.caption("Debt/Equity: not available (Screener's balance sheet section could not be parsed).")
        col1, col2 = st.columns(2)
        if screener.get("pros"):
            with col1:
                st.write("**Pros (Screener):**")
                for item in screener["pros"]:
                    st.write(f"- {item}")
        if screener.get("cons"):
            with col2:
                st.write("**Cons (Screener):**")
                for item in screener["cons"]:
                    st.write(f"- {item}")
        if screener.get("documents"):
            with st.expander("Documents (annual reports, concalls, credit ratings)"):
                for doc in screener["documents"]:
                    st.write(f"- [{doc['label']}]({doc['url']})")

    for source_name, source_key in (("NSE", "nse"), ("BSE", "bse")):
        source_data = fundamentals.get(source_key, {})
        if source_data.get("fetch_error"):
            st.caption(f"{source_name}: {source_data['fetch_error']}")

    st.json(fundamentals, expanded=False)


st.subheader("Quant scorecard")
quant_score = result["quant_score"]
if quant_score.get("checks"):
    st.dataframe(quant_score["checks"], use_container_width=True)
    st.caption(quant_score["capital_allocation_note"])
else:
    st.caption(quant_score.get("note", "No quant checks available."))

st.subheader("Valuation check (Principle 1b)")
valuation = result["valuation_check"]
if valuation["performed"]:
    hurdle_msg = "clears" if valuation["hurdle_pass"] else "misses"
    st.write(f"Implied CAGR {valuation['implied_cagr']:.1%} {hurdle_msg} the {valuation['hurdle']:.0%} hurdle.")
    if valuation.get("price_reliability_warning"):
        st.warning(valuation["price_reliability_warning"])
else:
    st.caption(f"Not performed: {valuation['reason']}")

st.caption(f"Forward growth estimate: {result['forward_growth_estimate'] or 'not stated in report'}")

st.subheader("Grounding check")
confidence = result["grounding_confidence_pct"]
if confidence is not None:
    st.write(f"{confidence}% of cited claims verified against the research report and fundamentals.")

hallucination_score = result["hallucination_score"]
threshold = result["hallucination_threshold"]
st.write(f"Hallucination score: {hallucination_score:.2f} (threshold {threshold:.2f}, 0 = fully grounded, 1 = fully hallucinated).")
if result["regeneration_attempts"] > 1:
    st.caption(f"Analysis was regenerated {result['regeneration_attempts'] - 1} time(s) after the first attempt scored above threshold.")
if not result["hallucination_check_passed"]:
    st.warning("Still above the hallucination threshold after all regeneration attempts - treat this analysis with extra caution.")

if result["claims_checked"]:
    st.dataframe(result["claims_checked"], use_container_width=True)
else:
    st.caption("No claims were cited to check.")

if result["open_questions"]:
    st.subheader("Open questions / gaps in evidence")
    for question in result["open_questions"]:
        st.write(f"- {question}")

st.info("This recommendation requires human review before any action is taken.")
