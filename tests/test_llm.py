from __future__ import annotations

import subprocess

import pytest

from alpha_holdings import llm


def _clear_codex_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in (
        "CODEX_CLI_COMMAND",
        "CODEX_CLI_TIMEOUT",
        "CODEX_MODEL",
        "CODEX_REASONING_EFFORT",
        "CODEX_MINI_REASONING_EFFORT",
    ):
        monkeypatch.delenv(name, raising=False)


def test_codex_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear_codex_env(monkeypatch)

    assert llm.get_cli_command() == ["codex"]
    assert llm.get_cli_timeout() == 600
    assert llm.get_model() == "gpt-5.6-luna"
    assert llm.get_model(mini=True) == "gpt-5.6-luna"
    assert llm.get_reasoning_effort() == "medium"
    assert llm.get_reasoning_effort(mini=True) == "low"


def test_command_passes_model_effort_and_search(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CODEX_CLI_COMMAND", "codex --some-fixed-arg")

    command = llm._build_command(
        model="custom-model", reasoning="high", web_search=True
    )

    assert command[:4] == ["codex", "--some-fixed-arg", "--search", "exec"]
    assert command.index("--search") < command.index("exec")
    assert command[command.index("--model") + 1] == "custom-model"
    assert command[command.index("--config") + 1] == 'model_reasoning_effort="high"'
    assert command[-1] == "-"


def test_respond_text_uses_stdin_and_domain_hint(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("CODEX_CLI_TIMEOUT", "7")
    monkeypatch.setenv("CODEX_MODEL", "test-model")
    monkeypatch.setenv("CODEX_REASONING_EFFORT", "low")
    calls: dict[str, object] = {}

    def fake_run(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        calls["command"] = command
        calls.update(kwargs)
        return subprocess.CompletedProcess(command, 0, stdout="  {\"ok\": true}  ", stderr="")

    monkeypatch.setattr(llm.subprocess, "run", fake_run)

    result = llm.respond_text(
        "Return JSON.", web_search=True, domain_filter=["example.com"]
    )

    assert result == '{"ok": true}'
    assert calls["input"] == (
        "Return JSON.\n\n"
        "When using web search, prefer and restrict sources to these domains: example.com."
    )
    command = calls["command"]
    assert isinstance(command, list)
    assert command[command.index("--model") + 1] == "test-model"
    assert command[command.index("--config") + 1] == 'model_reasoning_effort="low"'
    assert "--search" in command
    assert calls["timeout"] == 7


def test_missing_cli_is_reported_without_retry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def missing_cli(*args: object, **kwargs: object) -> None:
        raise FileNotFoundError("codex")

    monkeypatch.setattr(llm.subprocess, "run", missing_cli)

    with pytest.raises(llm.CodexCLIError, match="executable not found"):
        llm.respond_text("hello")


def test_timeout_is_reported_without_retry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    attempts = 0

    def timed_out(command: list[str], **kwargs: object) -> None:
        nonlocal attempts
        attempts += 1
        raise subprocess.TimeoutExpired(command, kwargs["timeout"])

    monkeypatch.setattr(llm.subprocess, "run", timed_out)
    monkeypatch.setattr(llm.time, "sleep", lambda _: None)

    with pytest.raises(llm.CodexCLIError, match="timed out"):
        llm.respond_text("hello", web_search=True)

    assert attempts == 1


def test_usage_limit_is_reported_without_retry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    attempts = 0

    def usage_limited(
        command: list[str], **kwargs: object
    ) -> subprocess.CompletedProcess[str]:
        nonlocal attempts
        attempts += 1
        return subprocess.CompletedProcess(
            command,
            1,
            stdout="",
            stderr="ERROR: You've hit your usage limit. Try again later.",
        )

    monkeypatch.setattr(llm.subprocess, "run", usage_limited)
    monkeypatch.setattr(llm.time, "sleep", lambda _: None)

    with pytest.raises(llm.CodexCLIError, match="usage limit") as error:
        llm.respond_text("hello", web_search=True)

    assert error.value.retryable is False
    assert attempts == 1


def test_mini_requests_use_the_lightweight_reasoning_effort(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("CODEX_REASONING_EFFORT", "high")
    monkeypatch.setenv("CODEX_MINI_REASONING_EFFORT", "low")
    calls: dict[str, object] = {}

    def fake_run(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        calls["command"] = command
        return subprocess.CompletedProcess(command, 0, stdout="ok", stderr="")

    monkeypatch.setattr(llm.subprocess, "run", fake_run)

    assert llm.respond_text("hello", mini=True) == "ok"
    command = calls["command"]
    assert isinstance(command, list)
    assert command[command.index("--config") + 1] == 'model_reasoning_effort="low"'
