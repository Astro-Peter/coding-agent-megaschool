"""OpenAI Agents SDK configuration for OpenRouter and other providers."""
from __future__ import annotations

import os


def configure_sdk() -> None:
    """Configure OpenAI Agents SDK for OpenRouter via LiteLLM.
    
    This uses LiteLLM integration which has better compatibility with
    OpenRouter and other non-OpenAI providers.
    
    Required environment variables:
    - LLM_API_TOKEN: Your OpenRouter API key
    
    Optional environment variables:
    - LLM_MODEL: Model to use (default: openrouter/openai/gpt-4o-mini)
    """
    from agents import set_tracing_disabled

    # Disable tracing since we're not using OpenAI directly
    set_tracing_disabled(True)
    
    # Set the OpenRouter API key for LiteLLM
    # LiteLLM uses OPENROUTER_API_KEY environment variable
    token = os.getenv("LLM_API_TOKEN")
    if token and not os.getenv("OPENROUTER_API_KEY"):
        os.environ["OPENROUTER_API_KEY"] = token


def get_model_name() -> str:
    """Get the LiteLLM model name for OpenRouter.
    
    Returns a model name with the litellm/ prefix for use with the SDK.
    """
    model = os.getenv("LLM_MODEL", "gpt-4o-mini")
    
    # If already has litellm/ prefix, use as-is
    if model.startswith("litellm/"):
        return model
    
    # If already has openrouter/ prefix, add litellm/
    if model.startswith("openrouter/"):
        return f"litellm/{model}"
    
    # Otherwise, add full litellm/openrouter/ prefix
    return f"litellm/openrouter/{model}"
