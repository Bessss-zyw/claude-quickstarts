"""
MiniMax API Client
==================

OpenAI-compatible client for MiniMax M2.7 API.
Supports tool calling (function calling) via the OpenAI SDK.
"""

import os
from openai import OpenAI


# ---------------------------------------------------------------------------
# Supported providers (all OpenAI-compatible)
# ---------------------------------------------------------------------------
PROVIDERS = {
    "minimax": {
        "base_url": "https://api.minimax.chat/v1",
        "default_model": "MiniMax-Text-01",
        "env_key": "MINIMAX_API_KEY",
    },
    "openai": {
        "base_url": "https://api.openai.com/v1",
        "default_model": "gpt-4o",
        "env_key": "OPENAI_API_KEY",
    },
    "deepseek": {
        "base_url": "https://api.deepseek.com",
        "default_model": "deepseek-chat",
        "env_key": "DEEPSEEK_API_KEY",
    },
    "custom": {
        "base_url": None,  # must set via --base-url
        "default_model": None,  # must set via --model
        "env_key": "LLM_API_KEY",
    },
}


def create_client(
    provider: str = "minimax",
    api_key: str | None = None,
    base_url: str | None = None,
    model: str | None = None,
) -> tuple[OpenAI, str]:
    """
    Create an OpenAI-compatible client for the given provider.

    Args:
        provider: Provider name (minimax, openai, deepseek, custom)
        api_key: API key override (defaults to env var)
        base_url: Base URL override
        model: Model name override

    Returns:
        (client, model_name) tuple
    """
    if provider not in PROVIDERS:
        raise ValueError(f"Unknown provider '{provider}'. Choose from: {list(PROVIDERS.keys())}")

    cfg = PROVIDERS[provider]

    # Resolve API key
    resolved_key = api_key or os.environ.get(cfg["env_key"], "")
    if not resolved_key:
        raise ValueError(
            f"API key not found. Set {cfg['env_key']} environment variable "
            f"or pass --api-key."
        )

    # Resolve base URL
    resolved_url = base_url or cfg["base_url"]
    if not resolved_url:
        raise ValueError(
            f"Base URL required for provider '{provider}'. Pass --base-url."
        )

    # Resolve model
    resolved_model = model or cfg["default_model"]
    if not resolved_model:
        raise ValueError(
            f"Model name required for provider '{provider}'. Pass --model."
        )

    client = OpenAI(api_key=resolved_key, base_url=resolved_url)
    return client, resolved_model
