# Agent 2 from architecture.md - the Portfolio Manager / Orchestrator.
# Runs once, across the whole universe, after Agent 1 (agent.py) has already
# produced a per-stock buy/hold/sell. This agent does NOT re-judge business
# quality - it only compares the 4 already-graded stocks against each other
# and turns each into relative sizing guidance (overweight/market-weight/
# underweight/exit/skip).
#
# Hard rule, enforced in code below and not just asked for in the prompt:
# this agent can only hold or downgrade a stock's Stage-1 decision, never
# upgrade it. A red flag or a contradicted claim from Stage 1 is never
# averaged away by a good-looking comparison to the other 3 stocks.

import json
from concurrent.futures import ThreadPoolExecutor
from datetime import date

from agent import run_analysis
from llm_client import chat
from tools import trade_metrics

# Ceiling on sizing_guidance per Stage 1 decision - a stock can be sized at or
# below its own decision, never above it. "sell" isn't in here because it's
# handled as a full override below (get out entirely), not a rank cap.
SIZING_RANK = {"exit position": 0, "skip": 0, "underweight": 1, "market-weight": 2, "overweight": 3}
MAX_SIZING_RANK_FOR_DECISION = {"hold": 2, "buy": 3}  # market-weight, overweight

ORCHESTRATOR_PROMPT = """You are the portfolio manager reviewing a concentrated,
4-stock long-term equity portfolio. Each stock below has already been graded
independently (Agent 1) - your job is to compare them against each other and
decide relative sizing, not to re-judge any single business from scratch.

STAGE 1 RESULTS FOR ALL 4 STOCKS (includes each stock's current market price and
key fundamentals from tool 1, and the deterministic pass/fail quant scorecard from
tool 2 - weigh these numbers yourself when comparing stocks, don't just defer to
Agent 1's prose):
{stage1_json}

HISTORICAL TRADE METRICS (this investor's own past buy/sell behavior - tools 4a/4b,
factual and behavioral, NOT a business-quality signal). Per-scrip realized CAGR
(avg_cagr_30d_plus) and win_rate are here so you can sanity-check sizing against the
>25% hurdle this investor has actually cleared or missed historically - e.g. if a scrip's
own realized CAGR history undershot 25%, don't recommend the same sizing/timing pattern
again without saying so. Use this ONLY to calibrate sizing/timing framing, never to judge
whether a business is good:
{trade_metrics_json}

PORTFOLIO-WIDE ROLLUP OF THE SAME METRICS (across all 4 scrips together):
{portfolio_metrics_json}

For each stock, pick sizing_guidance from: "overweight", "market-weight",
"underweight", "exit position", "skip" (skip = no position, don't buy).
A stock with a "sell" decision or unresolved red flags should never be
"overweight". Your sizing_rationale must weigh four things together, not any
one in isolation: (1) the current price and fundamentals/quant scorecard for
that stock vs. the other 3, (2) the valuation check's implied CAGR against the
25% hurdle, (3) this investor's own historical trade metrics and
trade_behavior_notes for that scrip (e.g. if the investor has a history of
over-concentrating after a stock has already run up, say so explicitly instead
of just repeating the same pattern), and (4) how well-grounded Stage 1's own
analysis was - hallucination_check_passed and grounding_confidence_pct. If a
stock's hallucination_check_passed is false or grounding_confidence_pct is low,
treat that stock's business-quality conclusions with extra caution and say so
explicitly in sizing_rationale - don't size it as confidently as a stock whose
Stage 1 analysis was cleanly grounded, even if the two look similar on paper.

Respond with ONLY a JSON object, no other text, in this exact shape:
{{
  "portfolio_summary": {{
    "style_strengths": ["patterns worth keeping, drawn from trade_behavior_notes across all 4 stocks"],
    "style_biases": ["behavioral biases worth correcting, e.g. disposition effect, over-concentration"],
    "portfolio_risks": ["risks that only show up when looking at all 4 stocks together, e.g. sector overlap"],
    "cash_view": "one sentence on whether the portfolio looks fully deployed or has room to add"
  }},
  "stock_reviews": [
    {{
      "company": "...",
      "sizing_guidance": "overweight|market-weight|underweight|exit position|skip",
      "sizing_rationale": "short paragraph, relative to the other 3 stocks"
    }}
  ],
  "next_actions": ["concrete next steps for the human reviewer"]
}}
"""


def run_portfolio_review(companies: list[str] | None = None) -> dict:
    companies = companies or trade_metrics.list_companies()

    # Stage 1 is independent per company (separate LLM calls, separate tool
    # calls, no shared state) - running them concurrently instead of one at a
    # time cuts wall-clock time roughly by the number of companies, since each
    # call spends most of its time waiting on network I/O (LLM APIs, yfinance).
    with ThreadPoolExecutor(max_workers=len(companies)) as executor:
        stage1_raw = dict(zip(companies, executor.map(_run_stage1_safe, companies)))

    # Isolate per-company Stage 1 failures (missing documents, fetch errors, LLM
    # errors, etc.) so one bad company doesn't take down the whole portfolio
    # review - the other companies' results still get reviewed and returned.
    stage1_failures = {company: result["stage1_error"] for company, result in stage1_raw.items() if "stage1_error" in result}
    stage1_results = {company: result for company, result in stage1_raw.items() if "stage1_error" not in result}

    if not stage1_results:
        raise ValueError(
            "Stage 1 analysis failed for every company - cannot run the portfolio review:\n"
            + "\n".join(f"- {company}: {error}" for company, error in stage1_failures.items())
        )

    stage1_summaries = {company: _summarize_for_orchestrator(result) for company, result in stage1_results.items()}

    # Tools 4a/4b's raw outputs, fetched once here (not paraphrased through
    # Agent 1) so Agent 2 can reason with the actual historical numbers -
    # per architecture.md, these are Stage 0 outputs injected straight into
    # Agent 2's prompt as static context. Only for companies Stage 1 actually
    # succeeded for - the orchestrator can't reason about a company it has no
    # Stage 1 result for.
    raw_trade_metrics = {company: trade_metrics.load_metrics(company) for company in stage1_results}
    portfolio_metrics = trade_metrics.load_portfolio_metrics()

    prompt = ORCHESTRATOR_PROMPT.format(
        stage1_json=json.dumps(stage1_summaries, indent=2, default=str),
        trade_metrics_json=json.dumps(raw_trade_metrics, indent=2, default=str),
        portfolio_metrics_json=json.dumps(portfolio_metrics, indent=2, default=str),
    )
    raw_reply = chat(
        [{"role": "user", "content": prompt}],
        validate=lambda text: _parse_json_object(text) is not None,
    )
    orchestrator_output = _parse_json_object(raw_reply)
    if orchestrator_output is None:
        raise ValueError(f"Orchestrator LLM did not return valid JSON:\n{raw_reply}")

    stock_reviews = _apply_sizing_guardrail(orchestrator_output.get("stock_reviews", []), stage1_results)

    return {
        "as_of_date": date.today().isoformat(),
        "portfolio_summary": orchestrator_output.get("portfolio_summary", {}),
        "stock_reviews": stock_reviews,
        "stage1_failures": stage1_failures,
        "next_actions": orchestrator_output.get("next_actions", []),
        "overall_review_required": True,
    }


def _run_stage1_safe(company: str) -> dict:
    """Wrap run_analysis so one company's exception (missing documents, a
    data-source outage, every LLM in the chain failing, etc.) doesn't
    propagate out of ThreadPoolExecutor.map and abort every other company's
    Stage 1 result along with it."""
    try:
        return run_analysis(company)
    except Exception as exc:
        return {"stage1_error": str(exc)}


def _summarize_for_orchestrator(stage1_result: dict) -> dict:
    """Strip Stage 1's output down to what Agent 2 actually needs - leaving out
    the full report text and claim-by-claim grounding table keeps the prompt
    short. fundamentals and quant_score are tools 1/2's raw output, reused
    as-is from Stage 1 (no re-fetching) so Agent 2 can weigh current price and
    the quant scorecard itself, per architecture.md's Agent 2 input list."""
    return {
        "recommendation": stage1_result["recommendation"],
        "conviction": stage1_result["conviction"],
        "business_quality": stage1_result["business_quality"],
        "structural_vs_cyclical": stage1_result["structural_vs_cyclical"],
        "red_flags": stage1_result["red_flags"],
        "key_risks": stage1_result["key_risks"],
        "holding_open": stage1_result["holding_open"],
        "trade_behavior_notes": stage1_result["trade_behavior_notes"],
        "grounding_confidence_pct": stage1_result["grounding_confidence_pct"],
        "hallucination_score": stage1_result["hallucination_score"],
        "hallucination_check_passed": stage1_result["hallucination_check_passed"],
        "guardrail_override": stage1_result["guardrail_override"],
        "fundamentals": stage1_result["fundamentals"],
        "quant_score": stage1_result["quant_score"],
        "valuation_check": stage1_result["valuation_check"],
    }


def _apply_sizing_guardrail(stock_reviews: list[dict], stage1_results: dict) -> list[dict]:
    """A stock can only be held at or downgraded from its Stage 1 decision here,
    never upgraded - this is the 'never averaged away' rule from architecture.md.
    Matching is case-insensitive, and falls back to substring matching, since
    the LLM doesn't always echo names back with the exact casing/spelling it
    was given. If a review still can't be matched to a Stage 1 result, sizing
    is capped at 'underweight' rather than passed through unguarded - we can't
    verify it isn't a disguised 'sell', so the safe default is conservative,
    not permissive. Every review always gets stage1_decision/sizing_override
    set so app.py never hits a missing key."""
    stage1_by_lower_name = {name.lower(): result for name, result in stage1_results.items()}

    checked = []
    for review in stock_reviews:
        company = review.get("company", "")
        stage1 = _find_stage1_match(company, stage1_by_lower_name)

        if stage1 is None:
            review["stage1_decision"] = None
            sizing = review.get("sizing_guidance", "market-weight")
            if SIZING_RANK.get(sizing, 2) > SIZING_RANK["underweight"]:
                review["sizing_guidance"] = "underweight"
            review["sizing_override"] = (
                f"No Stage 1 result could be matched for '{company}' - sizing capped at 'underweight' as a "
                "safe default until this name mismatch is reconciled manually. Not treated as unguarded."
            )
            checked.append(review)
            continue

        stage1_decision = stage1["recommendation"]
        sizing = review.get("sizing_guidance", "market-weight")
        review["stage1_decision"] = stage1_decision

        if stage1_decision == "sell":
            # Sell means get out, full stop - not just "reduce" - so this is an
            # override, not a rank cap: force the only sizing that matches "sell".
            forced_sizing = "exit position" if stage1["holding_open"] else "skip"
            if sizing != forced_sizing:
                review["sizing_guidance"] = forced_sizing
                review["sizing_override"] = (
                    f"Overridden to '{forced_sizing}': Stage 1 decision for {company} was 'sell' - "
                    "sizing can't outrank the underlying business call."
                )
            else:
                review["sizing_override"] = None
            checked.append(review)
            continue

        max_rank = MAX_SIZING_RANK_FOR_DECISION.get(stage1_decision, 0)
        if SIZING_RANK.get(sizing, 0) > max_rank:
            capped_sizing = next(name for name, rank in SIZING_RANK.items() if rank == max_rank)
            review["sizing_guidance"] = capped_sizing
            review["sizing_override"] = (
                f"Downgraded to '{capped_sizing}': Stage 1 decision for {company} was '{stage1_decision}' - "
                "sizing can't outrank the underlying business call."
            )
        else:
            review["sizing_override"] = None

        checked.append(review)

    return checked


def _find_stage1_match(company: str, stage1_by_lower_name: dict) -> dict | None:
    """Exact case-insensitive match first, falling back to substring containment
    in either direction (e.g. LLM says 'Amber Enterprises' but the Stage 1 key
    is 'amber') - so a naming mismatch doesn't silently defeat the guardrail
    above by never finding a match at all."""
    lower_company = company.lower()
    if lower_company in stage1_by_lower_name:
        return stage1_by_lower_name[lower_company]
    for name, result in stage1_by_lower_name.items():
        if name in lower_company or lower_company in name:
            return result
    return None


def _parse_json_object(raw_text: str) -> dict | None:
    cleaned = raw_text.strip().removeprefix("```json").removeprefix("```").removesuffix("```").strip()
    try:
        parsed = json.loads(cleaned)
        return parsed if isinstance(parsed, dict) else None
    except json.JSONDecodeError:
        return None
