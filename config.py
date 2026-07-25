# Central place for paths, env vars and the LLM model fallback chain.
# Everything else in this app imports from here instead of reading os.environ directly.

import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

# On Streamlit Cloud there is no .env file - secrets are set in the app dashboard
# and exposed through st.secrets instead. Copy them into os.environ here so the
# rest of the code can just use os.getenv() either way, without caring where it runs.
try:
    import streamlit as st

    for key, value in st.secrets.items():
        os.environ.setdefault(key, str(value))
except Exception:
    pass

BASE_DIR = Path(__file__).parent
INPUTS_DIR = BASE_DIR / "inputs"
OUTPUTS_DIR = BASE_DIR / "outputs"
TRADE_METRICS_PATH = OUTPUTS_DIR / "trade_metrics.xlsx"

# Local FAISS indexes + their chunk text, one pair of files per company - built
# once from inputs/ and rebuilt automatically if the source PDFs change.
VECTOR_STORE_DIR = OUTPUTS_DIR / "vector_store"

# The four sheets in trade_metrics.xlsx that hold per-company metrics (the rest,
# "Portfolio", is a rollup and isn't tied to one company).
NON_COMPANY_SHEETS = {"portfolio"}

# Each provider speaks the OpenAI chat-completions API, so one client class
# handles all of them - only the base_url and api key change.
PROVIDER_ENDPOINTS = {
    "groq": ("https://api.groq.com/openai/v1", "GROQ_API_KEY"),
    "openrouter": ("https://openrouter.ai/api/v1", "OPENROUTER_API_KEY"),
    "gemini": ("https://generativelanguage.googleapis.com/v1beta/openai/", "GOOGLE_API_KEY"),
}

MODEL_CHAIN_ENV_VARS = [
    "LLM_PRIMARY_MODEL",
    "LLM_FALLBACK_MODEL_1",
    "LLM_FALLBACK_MODEL_2",
    "LLM_FALLBACK_MODEL_3",
]

# Tool 6 / Agent 1 hallucination gate - see .env for what these mean.
HALLUCINATION_THRESHOLD = float(os.getenv("HALLUCINATION_THRESHOLD", "0.3"))
MAX_HALLUCINATION_REGENERATIONS = int(os.getenv("MAX_HALLUCINATION_REGENERATIONS", "1"))


def get_model_chain() -> list[tuple[str, str]]:
    """Read the model chain from env, in priority order, as (provider, model_name) pairs."""
    chain = []
    for env_var in MODEL_CHAIN_ENV_VARS:
        raw = os.getenv(env_var)
        if not raw:
            continue
        # values look like "groq/llama-3.1-8b-instant" or
        # "openrouter/meta-llama/llama-3.3-70b-instruct:free" - the model name
        # itself can contain slashes, so only split on the first one.
        provider, model_name = raw.split("/", 1)
        chain.append((provider, model_name))
    return chain
