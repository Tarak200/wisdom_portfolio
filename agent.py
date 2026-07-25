# The analyst agent (Agent 1 from architecture.md): runs the deterministic
# tools first, then asks the LLM for a business-quality judgment and a
# buy/hold/sell call grounded in their output, then runs the hallucination
# check (tool 6) before handing anything back.
#
# Trade metrics are the investor's own past buy/sell behavior, not a signal
# about the business - the prompt below says so explicitly, and the LLM is
# only allowed to use them to comment on sizing/timing, never on quality.

import json
from datetime import date

from config import HALLUCINATION_THRESHOLD, MAX_HALLUCINATION_REGENERATIONS
from llm_client import chat
from tools import check_valuation, ingest_documents, market_data, score_fundamentals, trade_metrics, verify_grounding

INVESTMENT_PRINCIPLES = """
WISDOM Investing philosophy - judge every company against this, in order:
1. Business quality above everything else: growth, margins, ROE/ROCE, free cash flow.
2. Management must have skin in the game and be aligned with shareholders - check
   promoter holding, pledging, and whether management's past statements matched
   what they actually delivered.
3. Prefer businesses that can reinvest capital at attractive rates with a long runway.
4. Willing to pay up for durable, STRUCTURAL growth - be explicit about whether growth
   looks structural or just cyclical strength; do not assume cyclical strength is durable.
5. Prefer conservative balance sheets, low dependence on external capital.
6. Hard exclusions (flag as red flags, do not soften): commodity businesses with no
   pricing power, weak/opaque governance, uncertain turnarounds, unclear related-party
   dealings, heavy reliance on external financing.
7. Valuation is judged relative to quality and growth duration, never in isolation -
   cheapness alone is never a reason to like a stock.
Minimum return hurdle for a BUY: an implied CAGR of roughly 25%+ over the long term.
"""

ANALYSIS_PROMPT = """{principles}

You are reviewing {company} for a concentrated, long-term equity portfolio.
{holding_context}

RESEARCH REPORT(S) (the primary source of truth for business quality - cite them for every claim):
{report_text}

MARKET FUNDAMENTALS merged from Yahoo Finance (price history/ratios), Screener.in (ROCE,
promoter shareholding trend, pros/cons), NSE and BSE (live price cross-checks - see
price_cross_check for whether the sources agree). Use for context, note in
open_questions if something here looks off vs. the report:
{fundamentals_json}

QUANT SCORECARD - deterministic pass/fail vs. generic thresholds (NOT the fund's actual
sector-specific thresholds, just a rough first pass):
{quant_score_json}

INVESTOR'S OWN PAST TRADE METRICS for {company} (describes how this investor has
personally bought/sold the stock in the past - use it ONLY to comment on position sizing
or timing tone, e.g. whether to trim gradually vs. all at once. Never use it to judge
whether the business itself is good, and never let it affect the recommendation call):
{trade_metrics_json}
{regeneration_note}
Respond with ONLY a JSON object, no other text, in this exact shape:
{{
  "recommendation": "buy" | "hold" | "sell",
  "conviction": "low" | "medium" | "high",
  "business_quality": "short paragraph",
  "management_alignment": "short paragraph, mention skin-in-the-game and say-do consistency",
  "reinvestment_runway": "short paragraph",
  "structural_vs_cyclical": "short paragraph - be explicit which one this is",
  "red_flags": ["list any hard-exclusion violations, empty list if none"],
  "key_risks": ["list of risks worth watching"],
  "trade_behavior_notes": ["sizing/timing notes derived from the trade metrics only"],
  "forward_growth_estimate": "your best estimate of forward growth from the report, in words, or null",
  "forward_growth_estimate_pct": "same estimate as a decimal, e.g. 0.18 for 18%, or null if no basis for one",
  "open_questions": ["anything the report doesn't answer - be honest about gaps"],
  "claims": [{{"claim": "a specific factual claim you made above", "citation": "where in the report it comes from"}}]
}}

List 4-8 of your most load-bearing factual claims in "claims" - the ones the
recommendation actually depends on, not every sentence.
"""


def run_analysis(company: str) -> dict:
    documents = ingest_documents.ingest(company)
    metrics = trade_metrics.load_metrics(company)
    holding_open = trade_metrics.has_open_position(metrics)
    fundamentals = market_data.fetch_market_data(company)
    quant_score = score_fundamentals.score_fundamentals(fundamentals)

    holding_context = (
        "This position is currently OPEN (open lots exist) - frame this as a hold/trim/exit "
        "question, not a fresh buy/skip decision."
        if holding_open
        else "This position is currently CLOSED (no open lots) - frame this as a fresh buy/skip decision."
    )

    analysis, verified_claims, hallucination, attempts = _generate_grounded_analysis(
        company, documents, fundamentals, quant_score, metrics, holding_context
    )

    confidence = verify_grounding.summarize_confidence(verified_claims)

    valuation = check_valuation.check_valuation_threshold(
        current_price=fundamentals.get("current_price"),
        forward_growth_pct=_as_float(analysis.get("forward_growth_estimate_pct")),
        price_cross_check=fundamentals.get("price_cross_check"),
    )

    hallucination_check_passed = hallucination["score"] <= HALLUCINATION_THRESHOLD
    recommendation, override_note = _apply_guardrail(
        analysis.get("recommendation", "hold"),
        analysis.get("red_flags", []),
        verified_claims,
        hallucination_check_passed,
    )

    return {
        "company": company,
        "as_of_date": date.today().isoformat(),
        "report_sources": [doc["source"] for doc in documents["sources"]],
        "recommendation": recommendation,
        "conviction": analysis.get("conviction", "low"),
        "business_quality": analysis.get("business_quality", ""),
        "management_alignment": analysis.get("management_alignment", ""),
        "reinvestment_runway": analysis.get("reinvestment_runway", ""),
        "structural_vs_cyclical": analysis.get("structural_vs_cyclical", ""),
        "red_flags": analysis.get("red_flags", []),
        "key_risks": analysis.get("key_risks", []),
        "trade_behavior_notes": analysis.get("trade_behavior_notes", []),
        "holding_open": holding_open,
        "forward_growth_estimate": analysis.get("forward_growth_estimate"),
        "open_questions": analysis.get("open_questions", []),
        "claims_checked": verified_claims,
        "grounding_confidence_pct": confidence["confidence_pct"],
        "hallucination_score": hallucination["score"],
        "hallucination_threshold": HALLUCINATION_THRESHOLD,
        "hallucination_check_passed": hallucination_check_passed,
        "regeneration_attempts": attempts,
        "guardrail_override": override_note,
        "fundamentals": fundamentals,
        "quant_score": quant_score,
        "valuation_check": valuation,
        "review_required": True,
    }


def _generate_grounded_analysis(
    company: str, documents: dict, fundamentals: dict, quant_score: dict, metrics: dict, holding_context: str
) -> tuple[dict, list[dict], dict, int]:
    """Generate Agent 1's analysis, verify its claims (tool 6), and regenerate
    (up to MAX_HALLUCINATION_REGENERATIONS times) if the hallucination score
    comes back above HALLUCINATION_THRESHOLD. If every attempt still exceeds
    the threshold, the least-hallucinated attempt seen is returned - the
    caller can see this via hallucination_check_passed being False."""
    regeneration_note = ""
    best = None  # (analysis, verified_claims, attempt_number, hallucination)

    for attempt in range(1, MAX_HALLUCINATION_REGENERATIONS + 2):
        prompt = ANALYSIS_PROMPT.format(
            principles=INVESTMENT_PRINCIPLES,
            company=company,
            holding_context=holding_context,
            report_text=documents["combined_text"],
            fundamentals_json=json.dumps(fundamentals, indent=2, default=str),
            quant_score_json=json.dumps(quant_score, indent=2, default=str),
            trade_metrics_json=json.dumps(metrics, indent=2, default=str),
            regeneration_note=regeneration_note,
        )
        raw_reply = chat(
            [{"role": "user", "content": prompt}],
            validate=lambda text: _parse_json_object(text) is not None,
        )
        analysis = _parse_json_object(raw_reply)
        if analysis is None:
            raise ValueError(f"LLM did not return valid JSON for {company}:\n{raw_reply}")

        claims = analysis.get("claims", [])
        verified_claims = verify_grounding.check_claims(claims, company, quant_data=fundamentals)
        hallucination = verify_grounding.compute_hallucination_score(verified_claims)

        if best is None or hallucination["score"] < best[3]["score"]:
            best = (analysis, verified_claims, attempt, hallucination)

        if hallucination["score"] <= HALLUCINATION_THRESHOLD:
            return analysis, verified_claims, hallucination, attempt

        flagged = [c for c in verified_claims if c.get("status") in ("Contradicted", "Unverified")]
        flagged_lines = "\n".join(
            f"- \"{c.get('claim')}\" (cited as \"{c.get('citation')}\") - {c.get('status')}: {c.get('note', '')}"
            for c in flagged
        ) or "- no claims were cited at all - every recommendation needs at least one citation"
        regeneration_note = (
            f"\nPREVIOUS ATTEMPT FAILED THE HALLUCINATION CHECK (score {hallucination['score']} is above the "
            f"{HALLUCINATION_THRESHOLD} threshold). These claims were not clearly supported by the report:\n"
            f"{flagged_lines}\n"
            "Fix this: only cite claims the report text actually backs up. Drop or rephrase anything flagged "
            "above, and update the recommendation/conviction/red_flags if that changes your judgment.\n"
        )

    # Ran out of regeneration attempts - return the least-hallucinated attempt seen.
    analysis, verified_claims, attempt, hallucination = best
    return analysis, verified_claims, hallucination, attempt


def _apply_guardrail(
    recommendation: str, red_flags: list, verified_claims: list[dict], hallucination_check_passed: bool
) -> tuple[str, str | None]:
    """A red flag, a contradicted claim, or a failed hallucination/grounding
    gate overrides a BUY - none of these get averaged away by good-looking
    prose elsewhere in the analysis."""
    contradicted = [c for c in verified_claims if c.get("status") == "Contradicted"]

    if recommendation == "buy" and red_flags:
        return "hold", f"Downgraded from buy: red flag(s) present ({red_flags[0]})"

    if recommendation == "buy" and contradicted:
        return "hold", f"Downgraded from buy: a cited claim was contradicted by the report ({contradicted[0]['claim']})"

    if recommendation == "buy" and not hallucination_check_passed:
        return "hold", (
            "Downgraded from buy: analysis did not clear the hallucination/grounding check even after "
            "regeneration - too many cited claims are Unverified/Contradicted to trust a buy call."
        )

    return recommendation, None


def _as_float(value) -> float | None:
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _parse_json_object(raw_text: str) -> dict | None:
    cleaned = raw_text.strip().removeprefix("```json").removeprefix("```").removesuffix("```").strip()
    try:
        parsed = json.loads(cleaned)
        return parsed if isinstance(parsed, dict) else None
    except json.JSONDecodeError:
        return None
