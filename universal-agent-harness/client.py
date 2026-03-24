"""OpenAI-compatible LLM client with multi-provider support."""

from __future__ import annotations

import os
from openai import OpenAI


_PROVIDER_MAP: dict[str, tuple[str | None, str]] = {
    # provider -> (base_url env var, api_key env var)
    "nvidia":   ("NVIDIA_BASE_URL",   "NVIDIA_API_KEY"),
    "openai":   (None,                "OPENAI_API_KEY"),
    "deepseek": (None,                "DEEPSEEK_API_KEY"),
}

_DEEPSEEK_BASE_URL = "https://api.deepseek.com"


class LLMClient:
    """Per-agent OpenAI-compatible client."""

    def __init__(self, provider: str, model_name: str) -> None:
        self.provider = provider
        self.model_name = model_name

        base_url: str | None = None
        api_key: str | None = None

        if provider == "nvidia":
            base_url = os.environ.get("NVIDIA_BASE_URL")
            api_key = os.environ.get("NVIDIA_API_KEY", "")
        elif provider == "openai":
            api_key = os.environ.get("OPENAI_API_KEY", "")
        elif provider == "deepseek":
            base_url = _DEEPSEEK_BASE_URL
            api_key = os.environ.get("DEEPSEEK_API_KEY", "")
        else:
            # Generic: try {PROVIDER}_BASE_URL / {PROVIDER}_API_KEY
            prov_upper = provider.upper()
            base_url = os.environ.get(f"{prov_upper}_BASE_URL")
            api_key = os.environ.get(f"{prov_upper}_API_KEY", "")

        kwargs: dict = {}
        if base_url:
            kwargs["base_url"] = base_url
        if api_key:
            kwargs["api_key"] = api_key

        self._client = OpenAI(**kwargs)

    def chat(
        self,
        messages: list[dict],
        tools: list[dict] | None = None,
        temperature: float = 0.0,
    ) -> dict:
        """Call the LLM and return the raw response object (as dict-like)."""
        kwargs: dict = {
            "model": self.model_name,
            "messages": messages,
            "temperature": temperature,
        }
        if tools:
            kwargs["tools"] = tools
            kwargs["tool_choice"] = "auto"

        response = self._client.chat.completions.create(**kwargs)
        return response
