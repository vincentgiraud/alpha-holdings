"""Azure OpenAI client via Microsoft Foundry with Entra ID auth."""

from __future__ import annotations

import json
import os
import time
import logging
from datetime import datetime
from functools import lru_cache
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from openai import OpenAI

load_dotenv()

log = logging.getLogger(__name__)

DEBUG_DUMP = False  # set to True via --debug CLI flag
_debug_counter = 0

_MAX_RETRIES = 5
_RETRY_BASE_DELAY = 2  # seconds


@lru_cache(maxsize=1)
def _get_token_provider():
    from azure.identity import DefaultAzureCredential, get_bearer_token_provider

    return get_bearer_token_provider(
        DefaultAzureCredential(), "https://ai.azure.com/.default"
    )


@lru_cache(maxsize=1)
def get_client() -> OpenAI:
    base_url = os.environ["AZURE_OPENAI_BASE_URL"]
    return OpenAI(base_url=base_url, api_key=_get_token_provider())


def get_model(mini: bool = False) -> str:
    if mini:
        return os.environ.get("AZURE_OPENAI_MODEL_MINI", "gpt-5.4-mini-1")
    return os.environ.get("AZURE_OPENAI_MODEL", "gpt-5.4-1")


def _get_retry_after(exc: Exception) -> int | None:
    """Extract Retry-After seconds from a rate-limit (429) exception."""
    from openai import RateLimitError

    if isinstance(exc, RateLimitError):
        # openai SDK exposes response headers
        headers = getattr(getattr(exc, "response", None), "headers", {})
        retry_after = headers.get("retry-after") or headers.get("Retry-After")
        if retry_after:
            try:
                return int(float(retry_after))
            except (ValueError, TypeError):
                pass
        return 10  # default 10s for 429 without Retry-After header
    return None


def _debug_dump_response(prompt: str, kwargs: dict, response: Any) -> None:
    """Log debug info inline and save to data/debug/ when DEBUG_DUMP is enabled."""
    if not DEBUG_DUMP:
        return
    global _debug_counter
    _debug_counter += 1

    output_text = getattr(response, "output_text", None) or ""
    model = kwargs.get("model", "?")
    tools = [str(t.get("type", "?")) for t in kwargs.get("tools", [])]
    n_items = len(getattr(response, "output", []))

    # Inline CLI trace
    tools_str = f" +{','.join(tools)}" if tools else ""
    prompt_short = prompt[:80].replace("\n", " ")
    output_short = output_text[:120].replace("\n", " ") if output_text else "<empty>"
    log.info(
        "[DEBUG #%03d] %s%s | prompt: %s... | output: %d chars, %d items | %s...",
        _debug_counter, model, tools_str, prompt_short, len(output_text), n_items, output_short,
    )

    # Also save to file for post-mortem
    debug_dir = Path("data/debug")
    debug_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.utcnow().strftime("%H%M%S")
    filename = debug_dir / f"{ts}_{_debug_counter:03d}.json"

    output_items = []
    for item in getattr(response, "output", []):
        output_items.append(str(item)[:500])

    dump = {
        "timestamp": datetime.utcnow().isoformat(),
        "model": model,
        "tools": [str(t) for t in kwargs.get("tools", [])],
        "prompt_preview": prompt[:300],
        "output_text_preview": output_text[:1000],
        "output_text_length": len(output_text),
        "output_items_count": n_items,
        "output_items_preview": output_items[:5],
    }
    filename.write_text(json.dumps(dump, indent=2, default=str))


def respond(
    prompt: str,
    *,
    mini: bool = False,
    web_search: bool = False,
    domain_filter: list[str] | None = None,
    structured: dict | None = None,
    reasoning: str | None = None,
) -> Any:
    """Call the Responses API with optional web search and structured output.

    Args:
        prompt: The user input / instruction.
        mini: Use the lightweight model.
        web_search: Enable the web_search tool for real-time grounding.
        domain_filter: Restrict web search to these domains.
        structured: If provided, pass as `text` format for structured output.
        reasoning: Reasoning effort level: 'minimal', 'low', 'medium', 'high'. None = model default.

    Returns:
        The response object from the Responses API.
    """
    client = get_client()
    model = get_model(mini=mini)

    kwargs: dict[str, Any] = {"model": model, "input": prompt}

    if reasoning:
        kwargs["reasoning"] = {"effort": reasoning, "summary": "auto"}

    if web_search:
        tool: dict[str, Any] = {"type": "web_search"}
        if domain_filter:
            tool["filters"] = {"allowed_domains": domain_filter}
        kwargs["tools"] = [tool]

    if structured:
        kwargs["text"] = structured

    last_exc: Exception | None = None
    for attempt in range(_MAX_RETRIES):
        try:
            response = client.responses.create(**kwargs)
            _debug_dump_response(prompt, kwargs, response)
            return response
        except Exception as exc:
            last_exc = exc
            # Check for rate limit (429) with Retry-After header
            retry_after = _get_retry_after(exc)
            if retry_after is not None:
                delay = max(retry_after, 1)
                log.warning(
                    "Rate limited (429) on attempt %d/%d. Waiting %ds (Retry-After)...",
                    attempt + 1,
                    _MAX_RETRIES,
                    delay,
                )
            else:
                delay = _RETRY_BASE_DELAY * (2**attempt)
                log.warning(
                    "Responses API call failed (attempt %d/%d): %s. Retrying in %ds...",
                    attempt + 1,
                    _MAX_RETRIES,
                    exc,
                    delay,
                )

            if attempt < _MAX_RETRIES - 1:
                time.sleep(delay)
            else:
                log.error("Responses API call failed after %d attempts.", _MAX_RETRIES)
    raise last_exc  # type: ignore[misc]


def respond_text(
    prompt: str,
    *,
    mini: bool = False,
    web_search: bool = False,
    domain_filter: list[str] | None = None,
    reasoning: str | None = None,
) -> str:
    """Convenience wrapper that returns just the output text."""
    response = respond(
        prompt,
        mini=mini,
        web_search=web_search,
        domain_filter=domain_filter,
        reasoning=reasoning,
    )
    text = response.output_text
    if not text:
        # output_text can be None if the response only has tool calls
        # Try to extract from the output items directly
        for item in getattr(response, "output", []):
            if hasattr(item, "content"):
                for block in item.content:
                    if hasattr(block, "text") and block.text:
                        return block.text
        log.warning("Response had no output_text. Returning empty string.")
        return ""
    return text
