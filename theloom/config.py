"""The one config loader, shared by everything.

There is a single config path — the CLI — and only this loader. Precedence,
highest wins:

    CLI flags > environment > ~/.loom/config.json > defaults

Environment variables: GRAPH_HOST, GRAPH_PORT, DEFAULT_GRAPH, ANTHROPIC_API_KEY,
LOOM_LLM_* (see below), and LOOM_CONFIG (alternate config-file path).
Config-file keys are camelCase (defaultGraph, anthropicApiKey) plus
graphHost/graphPort for the FalkorDB substrate.

LLM routing (optional, beyond the default Anthropic path): an
optional ``llm`` config section routes synthesis LLM calls to any
OpenAI-compatible chat-completions server — Ollama and mlx_lm.server both
speak that protocol — or explicitly to Anthropic:

    {"llm": {"provider": "ollama", "model": "orinth"}}
    {"llm": {"provider": "mlx", "model": "gemma-4", "baseUrl": "http://localhost:8080/v1"}}
    {"llm": {"provider": "anthropic", "model": "claude-haiku-4-5-20251001"}}

Providers: anthropic | openai | ollama | mlx (the last three differ only in
their default baseUrl; all use the OpenAI chat-completions wire format).
Sub-keys: baseUrl, model, apiKey, maxTokens, timeoutSeconds. Environment
overrides: LOOM_LLM_PROVIDER, LOOM_LLM_BASE_URL, LOOM_LLM_MODEL,
LOOM_LLM_API_KEY, LOOM_LLM_MAX_TOKENS, LOOM_LLM_TIMEOUT_SECONDS. With no
``llm`` section at all, the default behavior stands: ANTHROPIC_API_KEY
present -> Anthropic, absent -> deterministic no-LLM templates.

Security: a group- or world-readable config file draws a JSON warning on
stderr. An unreadable or invalid config file is silently ignored.
"""

from __future__ import annotations

import json
import os
import stat
import sys
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

DEFAULT_HOST = "localhost"
DEFAULT_PORT = 6379
DEFAULT_GRAPH = "default"


class LoomConfigError(Exception):
    """A configuration error, carrying the CLI's typed error code."""

    code = "CONFIG_ERROR"


LLM_PROVIDERS = ("anthropic", "openai", "ollama", "mlx")

# OpenAI-compatible default endpoints per provider alias.
LLM_DEFAULT_BASE_URLS = {
    "ollama": "http://localhost:11434/v1",
    "mlx": "http://localhost:8080/v1",
}


@dataclass(frozen=True)
class LlmConfig:
    """Resolved LLM routing for synthesis (the optional ``llm`` section)."""

    provider: str
    base_url: str | None = None
    model: str | None = None
    api_key: str | None = None
    max_tokens: int | None = None
    timeout_seconds: float | None = None
    # Passed through to OpenAI-compatible servers when set; 0.0 makes JSON
    # tasks (decomposition) far more reliable on small local models.
    temperature: float | None = None


@dataclass(frozen=True)
class LoomConfig:
    """Resolved configuration for a CLI invocation."""

    host: str = DEFAULT_HOST
    port: int = DEFAULT_PORT
    default_graph: str = DEFAULT_GRAPH
    anthropic_api_key: str | None = None
    llm: LlmConfig | None = None


def default_config_path(env: Mapping[str, str]) -> Path:
    """~/.loom/config.json, unless LOOM_CONFIG points elsewhere."""
    override = env.get("LOOM_CONFIG")
    return Path(override) if override else Path.home() / ".loom" / "config.json"


def _coerce_port(value: object, source: str) -> int:
    if isinstance(value, int) and not isinstance(value, bool):
        return value
    if isinstance(value, str):
        try:
            return int(value)
        except ValueError as exc:
            raise LoomConfigError(f"Invalid GRAPH_PORT from {source}: {value!r}") from exc
    raise LoomConfigError(f"Invalid GRAPH_PORT from {source}: {value!r}")


def _read_config_file(path: Path) -> dict[str, object]:
    """Read the config file; warn on permissive modes; ignore invalid files."""
    try:
        mode = path.stat().st_mode
    except OSError:
        return {}
    if mode & (stat.S_IRGRP | stat.S_IROTH):  # group- or world-readable
        sys.stderr.write(
            json.dumps(
                {
                    "warning": (
                        f"Config file {path} is group- or world-readable. "
                        f"Consider: chmod 600 {path}"
                    )
                }
            )
            + "\n"
        )
    try:
        loaded = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}  # invalid config is silently ignored
    return loaded if isinstance(loaded, dict) else {}


def load_config(
    flags: Mapping[str, object] | None = None,
    *,
    config_path: Path | str | None = None,
    env: Mapping[str, str] | None = None,
) -> LoomConfig:
    """Resolve configuration with precedence flags > env > file > defaults.

    ``flags`` accepts the resolved-field names: host, port, default_graph,
    anthropic_api_key. ``env``/``config_path`` default to the real environment
    and ~/.loom/config.json; tests inject both.
    """
    environment = os.environ if env is None else env
    path = Path(config_path) if config_path is not None else default_config_path(environment)

    file_config = _read_config_file(path)

    host = DEFAULT_HOST
    port = DEFAULT_PORT
    default_graph = DEFAULT_GRAPH
    anthropic_api_key: str | None = None

    # Layer 1: config file
    if isinstance(file_config.get("graphHost"), str):
        host = str(file_config["graphHost"])
    if "graphPort" in file_config:
        port = _coerce_port(file_config["graphPort"], str(path))
    if isinstance(file_config.get("defaultGraph"), str):
        default_graph = str(file_config["defaultGraph"])
    if isinstance(file_config.get("anthropicApiKey"), str):
        anthropic_api_key = str(file_config["anthropicApiKey"])

    llm = _resolve_llm(file_config.get("llm"), environment)

    # Layer 2: environment
    if environment.get("GRAPH_HOST"):
        host = environment["GRAPH_HOST"]
    if environment.get("GRAPH_PORT"):
        port = _coerce_port(environment["GRAPH_PORT"], "environment")
    if environment.get("DEFAULT_GRAPH"):
        default_graph = environment["DEFAULT_GRAPH"]
    if environment.get("ANTHROPIC_API_KEY"):
        anthropic_api_key = environment["ANTHROPIC_API_KEY"]

    # Layer 3: CLI flags
    if flags:
        if isinstance(flags.get("host"), str):
            host = str(flags["host"])
        if flags.get("port") is not None:
            port = _coerce_port(flags["port"], "flags")
        if isinstance(flags.get("default_graph"), str):
            default_graph = str(flags["default_graph"])
        if isinstance(flags.get("anthropic_api_key"), str):
            anthropic_api_key = str(flags["anthropic_api_key"])

    return LoomConfig(
        host=host,
        port=port,
        default_graph=default_graph,
        anthropic_api_key=anthropic_api_key,
        llm=llm,
    )


def _resolve_llm(file_section: object, environment: Mapping[str, str]) -> LlmConfig | None:
    """Merge the file's ``llm`` section with LOOM_LLM_* environment overrides
    (env wins per-field, matching the loader's layering). Returns None when
    neither source configures a provider — the legacy ANTHROPIC_API_KEY path."""
    section = file_section if isinstance(file_section, dict) else {}

    def _str(file_key: str, env_key: str) -> str | None:
        env_value = environment.get(env_key)
        if env_value:
            return env_value
        value = section.get(file_key)
        return value if isinstance(value, str) and value else None

    provider = _str("provider", "LOOM_LLM_PROVIDER")
    if provider is None:
        return None
    provider = provider.lower()
    if provider not in LLM_PROVIDERS:
        raise LoomConfigError(
            f"Invalid llm.provider {provider!r}. Valid options: {', '.join(LLM_PROVIDERS)}"
        )

    max_tokens: int | None = None
    raw_max = _str("maxTokens", "LOOM_LLM_MAX_TOKENS")
    if raw_max is not None:
        try:
            max_tokens = int(raw_max)
        except ValueError as exc:
            raise LoomConfigError(f"Invalid llm.maxTokens: {raw_max!r}") from exc
    elif isinstance(section.get("maxTokens"), int):
        max_tokens = int(section["maxTokens"])

    timeout_seconds: float | None = None
    raw_timeout = _str("timeoutSeconds", "LOOM_LLM_TIMEOUT_SECONDS")
    if raw_timeout is not None:
        try:
            timeout_seconds = float(raw_timeout)
        except ValueError as exc:
            raise LoomConfigError(f"Invalid llm.timeoutSeconds: {raw_timeout!r}") from exc
    elif isinstance(section.get("timeoutSeconds"), int | float):
        timeout_seconds = float(section["timeoutSeconds"])

    temperature: float | None = None
    raw_temperature = _str("temperature", "LOOM_LLM_TEMPERATURE")
    if raw_temperature is not None:
        try:
            temperature = float(raw_temperature)
        except ValueError as exc:
            raise LoomConfigError(f"Invalid llm.temperature: {raw_temperature!r}") from exc
    elif isinstance(section.get("temperature"), int | float):
        temperature = float(section["temperature"])

    return LlmConfig(
        provider=provider,
        base_url=_str("baseUrl", "LOOM_LLM_BASE_URL") or LLM_DEFAULT_BASE_URLS.get(provider),
        model=_str("model", "LOOM_LLM_MODEL"),
        api_key=_str("apiKey", "LOOM_LLM_API_KEY"),
        max_tokens=max_tokens,
        timeout_seconds=timeout_seconds,
        temperature=temperature,
    )
