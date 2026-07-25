# WISDOM Intelligent Portfolio Manager

An advisory buy/hold/sell assistant for a concentrated, long-term equity portfolio
(Amber, DBL, Welspun, Zee). It combines deterministic financial checks with two
LLM-based agents, and always requires human review before anything is acted on -
nothing in this app places trades.


## Prerequisites

- Python 3.12 (see `.python-version`)
- An API key for at least one LLM provider: [Groq](https://console.groq.com),
  [OpenRouter](https://openrouter.ai) or [Google AI Studio](https://aistudio.google.com)
  (Gemini). Groq and OpenRouter both have usable free tiers.

## Setup

```powershell
# from the repo root
uv sync                      # installs everything from pyproject.toml / uv.lock
```

# then fill in your API key(s) in .env

```powershell
copy .env.example .env     
```

`inputs/` must contain the research PDFs and trade log this app reads:

```
inputs/
  2.1 Research (Amber).pdf
  2.2 Research (DBL).pdf
  2.3 Research (Welspun).pdf
  2.4 Research (Zee).pdf
  trade_data.xlsx
```

`outputs/trade_metrics.xlsx` must already exist (one sheet per company with
open lots, cost basis, holding-period stats, etc., plus a portfolio rollup
sheet). It is generated once from `inputs/trade_data.xlsx` by running
`notebooks/historical_data_analysis.ipynb` top to bottom - re-run the notebook
whenever `trade_data.xlsx` changes.

## Running the agent

```powershell
streamlit run app.py
```

This starts the Streamlit UI at `http://localhost:8501`. From there you can:

- **Single stock:** pick one of the four companies and run its
  individual buy/hold/sell analysis (fundamentals, quant scorecard, business
  quality, valuation check, grounding/hallucination check).
- **Full portfolio:** run Agent 1 on all four stocks, then the
  orchestrator, to get relative sizing guidance across the whole portfolio.

The first run for a company builds a local FAISS vector index from its
research PDF (`outputs/vector_store/`) - this can take a few seconds the first
time and is cached afterwards.

## Configuration

All configuration lives in `.env` which is not in github as it contains sensitive APIs (see `.env.example` for every variable and what it does):

- `LLM_PRIMARY_MODEL` / `LLM_FALLBACK_MODEL_1-3` - the model fallback chain,
  tried in order. `provider/model_name`, provider is one of `groq`,
  `openrouter`, `gemini`.
- `GROQ_API_KEY` / `OPENROUTER_API_KEY` / `GOOGLE_API_KEY` - only the ones for
  providers you actually reference in the model chain need to be set.
- `HALLUCINATION_THRESHOLD` / `MAX_HALLUCINATION_REGENERATIONS` - tool 6's
  grounding gate (see architecture.md).

Free-tier LLM APIs have small per-minute/per-day token budgets. If every model
in the chain fails, the error message lists exactly which provider/model
failed and why (rate limit, invalid model slug, etc.) - check that message
first before assuming the app itself is broken.
