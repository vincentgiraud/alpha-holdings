"""Codex CLI client using the user's ChatGPT account."""

from __future__ import annotations

import json
import logging
import os
import shlex
import subprocess
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

load_dotenv()

log = logging.getLogger(__name__)

DEBUG_DUMP = False  # set to True via --debug CLI flag
_debug_counter = 0

_MAX_RETRIES = 5
_RETRY_BASE_DELAY = 2  # seconds
_NON_RETRYABLE_ERROR_MARKERS = (
    "you've hit your usage limit",
    "you have hit your usage limit",
    "purchase more credits",
)

DEFAULT_CODEX_CLI_COMMAND = "codex"
DEFAULT_CODEX_CLI_TIMEOUT = 600
DEFAULT_CODEX_MODEL = "gpt-5.6-luna"
DEFAULT_CODEX_REASONING_EFFORT = "medium"
DEFAULT_CODEX_MINI_REASONING_EFFORT = "low"


@dataclass(frozen=True)
class CodexResponse:
    """Small response object matching the fields used by the debug logger."""

    output_text: str
    output: tuple[Any, ...] = ()


class CodexCLIError(RuntimeError):
    """Raised when the Codex CLI cannot produce a response."""

    def __init__(self, message: str, *, retryable: bool = True) -> None:
        super().__init__(message)
        self.retryable = retryable


def get_cli_command() -> list[str]:
    """Return the configured Codex executable and any fixed arguments."""
    raw_command = os.environ.get(
        "CODEX_CLI_COMMAND", DEFAULT_CODEX_CLI_COMMAND
    ).strip()
    if not raw_command:
        raise ValueError("CODEX_CLI_COMMAND cannot be empty")
    return shlex.split(raw_command)


def get_cli_timeout() -> int:
    """Return the maximum duration for one Codex CLI invocation."""
    raw_timeout = os.environ.get(
        "CODEX_CLI_TIMEOUT", str(DEFAULT_CODEX_CLI_TIMEOUT)
    )
    try:
        timeout = int(raw_timeout)
    except ValueError as exc:
        raise ValueError("CODEX_CLI_TIMEOUT must be a positive integer") from exc
    if timeout <= 0:
        raise ValueError("CODEX_CLI_TIMEOUT must be a positive integer")
    return timeout


def get_model(mini: bool = False) -> str:
    """Return the configured Codex model.

    ``mini`` remains part of the function signature for callers that use the
    lightweight request path. The Codex CLI uses the same configured model for
    every request.
    """
    del mini
    return os.environ.get("CODEX_MODEL", DEFAULT_CODEX_MODEL)


def get_reasoning_effort(
    reasoning: str | None = None,
    *,
    mini: bool = False,
) -> str:
    """Return a per-call override or the configured request-class effort."""
    if reasoning:
        return reasoning
    if mini:
        return os.environ.get(
            "CODEX_MINI_REASONING_EFFORT",
            DEFAULT_CODEX_MINI_REASONING_EFFORT,
        )
    return os.environ.get(
        "CODEX_REASONING_EFFORT", DEFAULT_CODEX_REASONING_EFFORT
    )


def _build_command(
    *, model: str, reasoning: str, web_search: bool
) -> list[str]:
    """Build a safe argv list for a non-interactive Codex request."""
    command = get_cli_command()
    # `--search` is a global Codex option and must precede the `exec`
    # subcommand in the currently supported CLI versions.
    if web_search:
        command.append("--search")
    command.extend(
        [
            "exec",
            "--ephemeral",
            "--sandbox",
            "read-only",
            "--model",
            model,
            "--config",
            f"model_reasoning_effort={json.dumps(reasoning)}",
            "--color",
            "never",
        ]
    )
    # A lone '-' makes codex exec read the prompt from stdin.
    command.append("-")
    return command


def _prepare_prompt(
    prompt: str,
    *,
    domain_filter: list[str] | None,
    structured: dict | None,
) -> str:
    """Preserve request constraints that the CLI cannot pass as API fields."""
    additions: list[str] = []
    if domain_filter:
        domains = ", ".join(domain_filter)
        additions.append(
            "When using web search, prefer and restrict sources to these domains: "
            f"{domains}."
        )
    if structured:
        additions.append(
            "Return output conforming to this JSON schema/configuration: "
            + json.dumps(structured, sort_keys=True)
        )
    if not additions:
        return prompt
    return prompt.rstrip() + "\n\n" + "\n".join(additions)


def _run_codex(
    prompt: str,
    *,
    model: str,
    reasoning: str,
    web_search: bool,
) -> CodexResponse:
    """Run one Codex CLI request and return its final text response."""
    command = _build_command(
        model=model, reasoning=reasoning, web_search=web_search
    )
    timeout = get_cli_timeout()
    log.debug("Running Codex CLI: %s", shlex.join(command[:-1]) + " -")
    log.info(
        "Starting Codex CLI request (web search: %s, reasoning: %s, timeout: %ss).",
        "on" if web_search else "off",
        reasoning,
        timeout,
    )

    try:
        completed = subprocess.run(
            command,
            input=prompt,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except FileNotFoundError as exc:
        executable = command[0] if command else DEFAULT_CODEX_CLI_COMMAND
        raise CodexCLIError(
            f"Codex CLI executable not found: {executable}. "
            "Install Codex CLI or set CODEX_CLI_COMMAND.",
            retryable=False,
        ) from exc
    except subprocess.TimeoutExpired as exc:
        raise CodexCLIError(
            f"Codex CLI timed out after {timeout} seconds. "
            "The request was not retried; lower the reasoning effort or increase "
            "CODEX_CLI_TIMEOUT.",
            retryable=False,
        ) from exc
    except OSError as exc:
        raise CodexCLIError(f"Unable to start Codex CLI: {exc}", retryable=False) from exc

    if completed.returncode != 0:
        details = completed.stderr.strip() or completed.stdout.strip()
        normalized_details = details.lower()
        if len(details) > 1000:
            details = details[-1000:]
        suffix = f": {details}" if details else ""
        retryable = not any(
            marker in normalized_details
            for marker in _NON_RETRYABLE_ERROR_MARKERS
        )
        raise CodexCLIError(
            f"Codex CLI exited with status {completed.returncode}{suffix}",
            retryable=retryable,
        )

    output = completed.stdout.strip()
    if not output:
        raise CodexCLIError("Codex CLI returned an empty response.")
    return CodexResponse(output_text=output)


def _debug_dump_response(prompt: str, kwargs: dict, response: CodexResponse) -> None:
    """Log debug info inline and save to data/debug/ when DEBUG_DUMP is enabled."""
    if not DEBUG_DUMP:
        return
    global _debug_counter
    _debug_counter += 1

    output_text = response.output_text
    model = kwargs.get("model", "?")
    tools = ["web_search"] if kwargs.get("web_search") else []
    n_items = len(response.output)

    tools_str = f" +{','.join(tools)}" if tools else ""
    prompt_short = prompt[:80].replace("\n", " ")
    output_short = output_text[:120].replace("\n", " ") if output_text else "<empty>"
    log.info(
        "[DEBUG #%03d] %s%s | prompt: %s... | output: %d chars, %d items | %s...",
        _debug_counter,
        model,
        tools_str,
        prompt_short,
        len(output_text),
        n_items,
        output_short,
    )

    debug_dir = Path("data/debug")
    debug_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.utcnow().strftime("%H%M%S")
    filename = debug_dir / f"{ts}_{_debug_counter:03d}.json"

    dump = {
        "timestamp": datetime.utcnow().isoformat(),
        "model": model,
        "tools": tools,
        "prompt_preview": prompt[:300],
        "output_text_preview": output_text[:1000],
        "output_text_length": len(output_text),
        "output_items_count": n_items,
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
) -> CodexResponse:
    """Call the Codex CLI with optional web search and output constraints."""
    effective_prompt = _prepare_prompt(
        prompt,
        domain_filter=domain_filter if web_search else None,
        structured=structured,
    )
    model = get_model(mini=mini)
    effort = get_reasoning_effort(reasoning, mini=mini)
    kwargs: dict[str, Any] = {
        "model": model,
        "reasoning": effort,
        "web_search": web_search,
    }

    last_exc: CodexCLIError | None = None
    for attempt in range(_MAX_RETRIES):
        try:
            response = _run_codex(
                effective_prompt,
                model=model,
                reasoning=effort,
                web_search=web_search,
            )
            _debug_dump_response(effective_prompt, kwargs, response)
            return response
        except CodexCLIError as exc:
            last_exc = exc
            if not exc.retryable or attempt >= _MAX_RETRIES - 1:
                break
            delay = _RETRY_BASE_DELAY * (2**attempt)
            log.warning(
                "Codex CLI call failed (attempt %d/%d): %s. Retrying in %ds...",
                attempt + 1,
                _MAX_RETRIES,
                exc,
                delay,
            )
            time.sleep(delay)

    assert last_exc is not None
    log.error("Codex CLI call failed after %d attempts.", attempt + 1)
    raise last_exc


def respond_text(
    prompt: str,
    *,
    mini: bool = False,
    web_search: bool = False,
    domain_filter: list[str] | None = None,
    reasoning: str | None = None,
) -> str:
    """Convenience wrapper that returns just the output text."""
    return respond(
        prompt,
        mini=mini,
        web_search=web_search,
        domain_filter=domain_filter,
        reasoning=reasoning,
    ).output_text
