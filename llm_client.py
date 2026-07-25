# Thin wrapper that calls the model chain from config.py in order and returns
# the first response that succeeds. Keeps provider fallback logic in one place
# so the agent code doesn't need to know which LLM actually answered.

import os

from openai import OpenAI

from config import PROVIDER_ENDPOINTS, get_model_chain


class AllModelsFailedError(RuntimeError):
    """Raised when every model in the fallback chain errored out."""


def chat(messages: list[dict], temperature: float = 0.2, validate=None) -> str:
    """Send a chat completion request, trying each configured model in turn.

    If `validate` is given, a model's response is only accepted if
    `validate(content)` returns True - otherwise that response is treated the
    same as a network/API failure and the next model in the chain is tried.
    This matters because a model can return HTTP 200 with a perfectly
    successful response that still isn't valid JSON (extra prose around the
    code fence, truncated output, etc.) - without this, callers that need
    JSON would only ever see the first model's raw output and never actually
    benefit from the fallback chain on a parse failure."""
    chain = get_model_chain()
    if not chain:
        raise AllModelsFailedError("No LLM models configured - check LLM_PRIMARY_MODEL in .env")

    errors = []
    for provider, model_name in chain:
        if provider not in PROVIDER_ENDPOINTS:
            errors.append(f"{provider}/{model_name}: unknown provider")
            continue

        base_url, api_key_env = PROVIDER_ENDPOINTS[provider]
        api_key = os.getenv(api_key_env)
        if not api_key:
            errors.append(f"{provider}/{model_name}: {api_key_env} not set")
            continue

        try:
            client = OpenAI(api_key=api_key, base_url=base_url)
            response = client.chat.completions.create(
                model=model_name,
                messages=messages,
                temperature=temperature,
            )
            content = response.choices[0].message.content
            if validate is not None and not validate(content):
                errors.append(f"{provider}/{model_name}: response failed validation (e.g. not valid/complete JSON)")
                continue
            return content
        except Exception as exc:
            errors.append(f"{provider}/{model_name}: {exc}")
            continue

    raise AllModelsFailedError("Every model in the fallback chain failed:\n" + "\n".join(errors))
