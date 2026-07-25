# WISDOM Intelligent Portfolio Manager

An advisory buy/hold/sell assistant for a concentrated, long-term equity portfolio
(Amber, DBL, Welspun, Zee). It combines deterministic financial checks with two
LLM-based agents, and always requires human review before anything is acted on -
nothing in this app places trades.

See [architecture.md](architecture.md) for the full system design and
[WRITEUP.md](WRITEUP.md) for the reasoning behind it (thresholds, LLM usage, guardrails).

## Prerequisites

- Python 3.12 (see `.python-version`)
- An API key for at least one LLM provider: [Groq](https://console.groq.com),
  [OpenRouter](https://openrouter.ai) or [Google AI Studio](https://aistudio.google.com)
  (Gemini). Groq and OpenRouter both have usable free tiers.

## Setup

```powershell
# from the repo root
uv sync                      # installs everything from pyproject.toml / uv.lock
# or, without uv:
pip install -r requirements.txt

copy .env.example .env       # then fill in your API key(s) in .env
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

- **Single stock (Agent 1):** pick one of the four companies and run its
  individual buy/hold/sell analysis (fundamentals, quant scorecard, business
  quality, valuation check, grounding/hallucination check).
- **Full portfolio (Agent 2):** run Agent 1 on all four stocks, then the
  orchestrator, to get relative sizing guidance across the whole portfolio.

The first run for a company builds a local FAISS vector index from its
research PDF (`outputs/vector_store/`) - this can take a few seconds the first
time and is cached afterwards.

## Running without the UI

Both agents are plain Python functions and can be called directly, e.g. for
scripting or testing:

```python
from agent import run_analysis
from orchestrator import run_portfolio_review

result = run_analysis("amber")           # Agent 1, one stock
portfolio = run_portfolio_review()       # Agent 2, all stocks
```

## Configuration

All configuration lives in `.env` (see `.env.example` for every variable and
what it does):

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

## Deploying to Streamlit Community Cloud

`config.py` already reads secrets from `st.secrets` when there is no local
`.env` file, so no code changes are needed. In the app's dashboard, add the
same keys from `.env.example` under **Settings -> Secrets**, then point the
deployment at `app.py`. Note that NSE's quote endpoint (`tools/fetch_nse.py`)
frequently blocks requests from cloud/datacenter IP ranges - if NSE
consistently fails only in the cloud deployment (and works locally), that is a
known limitation of the free public endpoint, not a bug in this app.
