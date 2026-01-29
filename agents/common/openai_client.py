from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass
from typing import Any

from openai import OpenAI

logger = logging.getLogger(__name__)

MAX_RETRIES = 3
RETRY_DELAY_SECONDS = 2


@dataclass
class ToolCall:
    """Represents a tool call from the LLM."""
    id: str
    name: str
    arguments: str


@dataclass
class ChatResponse:
    """Unified response object for chat completions."""
    id: str
    content: str | None
    tool_calls: list[ToolCall]


def build_client(
    *,
    provider: str | None,
    api_token: str | None,
    api_url: str | None,
    verify_ssl: bool = True,
) -> OpenAI:
    """Build an OpenAI-compatible client for any provider."""
    provider_name = (provider or "openai").strip().lower()
    if not api_token:
        raise ValueError("LLM_API_TOKEN is required.")
    
    if provider_name == "openrouter" or (api_url and "openrouter" in api_url.lower()):
        if not api_url:
            api_url = "https://openrouter.ai/api/v1"
        elif api_url.endswith("/api"):
            api_url = f"{api_url}/v1"
        return OpenAI(
            api_key=api_token,
            base_url=api_url,
            default_headers={"HTTP-Referer": "https://github.com/megaschool"},
        )
    
    if api_url:
        return OpenAI(api_key=api_token, base_url=api_url)
    return OpenAI(api_key=api_token)


def create_text(
    *,
    client: OpenAI,
    model: str,
    system_prompt: str,
    user_prompt: str,
) -> str:
    """Create a simple text completion without tools."""
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt},
    ]
    
    for attempt in range(MAX_RETRIES):
        try:
            response = client.chat.completions.create(
                model=model,
                messages=messages,
            )
            content = response.choices[0].message.content
            return (content or "").strip()
        except Exception as e:
            if _should_retry(e) and attempt < MAX_RETRIES - 1:
                logger.warning(
                    "API error, attempt %d/%d, retrying in %ds: %s",
                    attempt + 1, MAX_RETRIES, RETRY_DELAY_SECONDS, e,
                )
                time.sleep(RETRY_DELAY_SECONDS * (attempt + 1))
                continue
            raise


def chat_with_tools(
    *,
    client: OpenAI,
    model: str,
    messages: list[dict[str, Any]],
    tools: list[dict[str, Any]] | None = None,
) -> ChatResponse:
    """Make a chat completion request with optional tools."""
    kwargs: dict[str, Any] = {
        "model": model,
        "messages": messages,
    }
    if tools:
        kwargs["tools"] = tools
        kwargs["tool_choice"] = "auto"
    
    for attempt in range(MAX_RETRIES):
        try:
            response = client.chat.completions.create(**kwargs)
            message = response.choices[0].message
            
            tool_calls = []
            if message.tool_calls:
                for tc in message.tool_calls:
                    tool_calls.append(ToolCall(
                        id=tc.id,
                        name=tc.function.name,
                        arguments=tc.function.arguments or "{}",
                    ))
            
            return ChatResponse(
                id=response.id,
                content=message.content,
                tool_calls=tool_calls,
            )
        except Exception as e:
            if _should_retry(e) and attempt < MAX_RETRIES - 1:
                logger.warning(
                    "API error, attempt %d/%d, retrying in %ds: %s",
                    attempt + 1, MAX_RETRIES, RETRY_DELAY_SECONDS, e,
                )
                time.sleep(RETRY_DELAY_SECONDS * (attempt + 1))
                continue
            raise


def _should_retry(error: Exception) -> bool:
    """Check if an error is retryable (server errors, timeouts, etc.)."""
    error_str = str(error).lower()
    return any(x in error_str for x in ["500", "502", "503", "504", "timeout", "connection"])
