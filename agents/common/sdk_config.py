"""OpenAI Agents SDK configuration for OpenRouter and other providers."""
from __future__ import annotations

import os

from openai import AsyncOpenAI


def configure_sdk(api_token: str | None = None, api_url: str | None = None) -> None:
    """Configure OpenAI Agents SDK for OpenRouter or other OpenAI-compatible providers.
    
    Args:
        api_token: API token for the LLM provider. Defaults to LLM_API_TOKEN env var.
        api_url: Base URL for the API. Defaults to LLM_API_URL env var or OpenRouter.
    """
    # Import here to avoid circular imports and allow lazy loading
    from agents import set_default_openai_api, set_default_openai_client

    token = api_token or os.getenv("LLM_API_TOKEN")
    if not token:
        raise ValueError("LLM_API_TOKEN is required")
    
    url = api_url or os.getenv("LLM_API_URL", "https://openrouter.ai/api/v1")
    
    client = AsyncOpenAI(
        api_key=token,
        base_url=url,
    )
    set_default_openai_client(client, use_for_tracing=False)
    set_default_openai_api("chat_completions")
