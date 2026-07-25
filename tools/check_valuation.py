# Tool 5 - check_valuation_threshold(price, growth_estimate, horizon, target_value)
# Principle 1b: implied CAGR vs. the >25% hurdle, and pass/fail vs. a value
# target. Plain arithmetic, no LLM - the LLM only supplies the forward growth
# estimate as an input, it never does the math itself.

HURDLE = 0.25
DEFAULT_HORIZON_YEARS = 5


def check_valuation_threshold(
    current_price: float | None,
    forward_growth_pct: float | None,
    horizon_years: int = DEFAULT_HORIZON_YEARS,
    target_value: float | None = None,
    price_cross_check: dict | None = None,
) -> dict:
    if current_price is None or forward_growth_pct is None:
        return {
            "performed": False,
            "reason": "Missing current price or a numeric forward growth estimate.",
        }

    # The LLM is asked for a decimal (0.18 for 18%), but it sometimes answers
    # with the whole number instead (18). A real long-term CAGR estimate is
    # basically never above 100%, so treat anything past that as a percentage
    # that needs dividing down rather than trusting it at face value.
    if abs(forward_growth_pct) > 1:
        forward_growth_pct = forward_growth_pct / 100

    # Simplification: over the long term, treat the forward growth estimate
    # as the proxy for the CAGR a shareholder could plausibly expect - this is
    # the same simplification architecture.md's tool 5 makes (growth estimate in, CAGR out).
    implied_cagr = forward_growth_pct
    result = {
        "performed": True,
        "current_price": current_price,
        "implied_cagr": round(implied_cagr, 4),
        "hurdle": HURDLE,
        "hurdle_pass": implied_cagr >= HURDLE,
    }

    # current_price above is a single source (Yahoo Finance) - if the live
    # sources actually disagreed by more than the cross-check threshold, this
    # hurdle math is only as reliable as whichever number Yahoo happened to
    # report, so say so explicitly instead of presenting it with unwarranted
    # confidence.
    if price_cross_check and price_cross_check.get("performed") and not price_cross_check.get("agree"):
        result["price_reliability_warning"] = (
            f"Price sources disagreed by {price_cross_check.get('spread_pct')}% (see price_cross_check) - "
            "current_price used here may not be reliable; verify manually before trusting this hurdle check."
        )

    if target_value:
        required_cagr = (target_value / current_price) ** (1 / horizon_years) - 1
        result.update(
            {
                "target_value": target_value,
                "horizon_years": horizon_years,
                "required_cagr_for_target": round(required_cagr, 4),
                "meets_target": implied_cagr >= required_cagr,
            }
        )
    else:
        result["target_value_note"] = "No value target (X Rs in Y Yrs) was given, so only the hurdle check ran."

    return result
