"""Synthesis LLM clients, with config-routed local serving.

Routing, highest precedence first:

1. An explicit ``llm`` config section (or LOOM_LLM_* env) — provider
   ``anthropic`` uses the Anthropic SDK; ``openai`` / ``ollama`` / ``mlx``
   all speak the OpenAI chat-completions protocol (Ollama and mlx_lm.server
   both expose it), differing only in default baseUrl.
2. Legacy fallback: ANTHROPIC_API_KEY present -> Anthropic.
3. Nothing configured -> None, and every synthesis caller takes its
   deterministic template path.

The test harness points LOOM_CONFIG at a nonexistent file and strips the
key/env overrides, so the tests always exercise path 3.
"""

from __future__ import annotations

import os
import re
from typing import Any

from theloom.config import LlmConfig, LoomConfig, LoomConfigError, load_config

# Reasoning models (Qwen family and friends) may emit chain-of-thought inline
# as <think>...</think> before the answer; it is never part of the completion.
_THINK_BLOCK_RE = re.compile(r"<think>[\s\S]*?</think>")

# Some local servers leak harmony-style channel markers into content when the
# chat template isn't reasoning-aware (observed with gemma-4 on mlx/llamacpp:
# `<|channel>thought ...reasoning... <channel|> final answer`). The answer is
# the text after the LAST marker. Configuring the server's reasoning format
# is the real fix; this keeps output usable either way.
_CHANNEL_MARKER_RE = re.compile(
    r"<[|]?channel[|]?>\s*(?:thought|analysis|final|commentary)?\b[:\s]*", re.IGNORECASE
)


def _strip_reasoning(content: str) -> str:
    """Remove leaked chain-of-thought: <think> blocks, then keep only the last
    channel segment when channel markers are present."""
    cleaned = _THINK_BLOCK_RE.sub("", content)
    if _CHANNEL_MARKER_RE.search(cleaned):
        segments = [s for s in _CHANNEL_MARKER_RE.split(cleaned) if s and s.strip()]
        if segments:
            cleaned = segments[-1]
    return cleaned.strip()


DEFAULT_SYNTHESIS_MODEL = "claude-haiku-4-5-20251001"
DEFAULT_SYNTHESIS_MAX_TOKENS = 4096
# Local reasoning models (Qwen family) spend thousands of tokens thinking
# before the answer; tokens are free locally, so default the budget high.
DEFAULT_LOCAL_MAX_TOKENS = 16384
DEFAULT_LOCAL_TIMEOUT_SECONDS = 300.0  # local MoE first-load can be slow


class SynthesisLlmClient:
    """Completion interface every synthesis caller depends on."""

    def complete(self, system_prompt: str, user_prompt: str) -> dict[str, Any]:
        raise NotImplementedError

    def get_model(self) -> str:
        raise NotImplementedError


class AnthropicSynthesisClient(SynthesisLlmClient):
    """Anthropic SDK client; OAuth tokens (containing '-oat')
    authenticate via authToken + beta headers."""

    def __init__(self, key: str, model: str | None = None, max_tokens: int | None = None) -> None:
        import anthropic

        self._model = model or DEFAULT_SYNTHESIS_MODEL
        self._max_tokens = max_tokens or DEFAULT_SYNTHESIS_MAX_TOKENS
        if "-oat" in key:
            self._client = anthropic.Anthropic(
                auth_token=key,
                api_key=None,
                default_headers={
                    "anthropic-beta": "oauth-2025-04-20",
                    "anthropic-dangerous-direct-browser-access": "true",
                },
            )
        else:
            self._client = anthropic.Anthropic(api_key=key)

    def complete(self, system_prompt: str, user_prompt: str) -> dict[str, Any]:
        response = self._client.messages.create(
            model=self._model,
            max_tokens=self._max_tokens,
            system=system_prompt,
            messages=[{"role": "user", "content": user_prompt}],
        )
        text_block = next((b for b in response.content if b.type == "text"), None)
        if text_block is None:
            raise RuntimeError("No text response from Anthropic API")
        return {
            "text": text_block.text,
            "inputTokens": response.usage.input_tokens,
            "outputTokens": response.usage.output_tokens,
            "model": self._model,
        }

    def get_model(self) -> str:
        return self._model


class OpenAICompatSynthesisClient(SynthesisLlmClient):
    """Chat-completions client for any OpenAI-compatible server (Ollama,
    mlx_lm.server, vLLM, ...). The bearer token is optional — local servers
    typically ignore auth."""

    def __init__(
        self,
        base_url: str,
        model: str,
        api_key: str | None = None,
        max_tokens: int | None = None,
        timeout_seconds: float | None = None,
        temperature: float | None = None,
        transport: Any | None = None,
    ) -> None:
        import httpx

        self._base_url = base_url.rstrip("/")
        self._model = model
        self._max_tokens = max_tokens or DEFAULT_LOCAL_MAX_TOKENS
        self._temperature = temperature
        headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
        self._client = httpx.Client(
            timeout=timeout_seconds or DEFAULT_LOCAL_TIMEOUT_SECONDS,
            headers=headers,
            transport=transport,
        )

    def complete(self, system_prompt: str, user_prompt: str) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "model": self._model,
            "max_tokens": self._max_tokens,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
        }
        if self._temperature is not None:
            payload["temperature"] = self._temperature
        response = self._client.post(f"{self._base_url}/chat/completions", json=payload)
        response.raise_for_status()
        body = response.json()
        choices = body.get("choices") or []
        content = choices[0].get("message", {}).get("content") if choices else None
        if isinstance(content, str):
            content = _strip_reasoning(content)
        if not isinstance(content, str) or not content:
            # Reasoning models can exhaust max_tokens before emitting an
            # answer (finish_reason=length with only `reasoning` populated) —
            # surface it so callers take their template fallback.
            raise RuntimeError(f"No text response from LLM server at {self._base_url}")
        usage = body.get("usage") or {}
        return {
            "text": content,
            "inputTokens": int(usage.get("prompt_tokens") or 0),
            "outputTokens": int(usage.get("completion_tokens") or 0),
            "model": str(body.get("model") or self._model),
        }

    def get_model(self) -> str:
        return self._model


def _client_from_llm_config(
    llm: LlmConfig, fallback_anthropic_key: str | None
) -> SynthesisLlmClient | None:
    if llm.provider == "anthropic":
        key = llm.api_key or fallback_anthropic_key
        if not key:
            return None
        return AnthropicSynthesisClient(key, llm.model, llm.max_tokens)
    if not llm.base_url:
        raise LoomConfigError(f"llm.baseUrl is required for provider {llm.provider!r}")
    if not llm.model:
        raise LoomConfigError(f"llm.model is required for provider {llm.provider!r}")
    return OpenAICompatSynthesisClient(
        base_url=llm.base_url,
        model=llm.model,
        api_key=llm.api_key,
        max_tokens=llm.max_tokens,
        timeout_seconds=llm.timeout_seconds,
        temperature=llm.temperature,
    )


def create_synthesis_client(
    api_key: str | None = None,
    model: str | None = None,
    max_tokens: int | None = None,
    *,
    config: LoomConfig | None = None,
) -> SynthesisLlmClient | None:
    """Resolve the synthesis LLM client, or None for template mode.

    An explicit ``api_key`` argument short-circuits to Anthropic; otherwise
    routing follows the ``llm`` config section, then the legacy
    ANTHROPIC_API_KEY behavior.
    """
    if api_key:
        return AnthropicSynthesisClient(api_key, model, max_tokens)

    resolved = config if config is not None else load_config()
    if resolved.llm is not None:
        return _client_from_llm_config(resolved.llm, resolved.anthropic_api_key)

    key = resolved.anthropic_api_key or os.environ.get("ANTHROPIC_API_KEY")
    if not key:
        return None
    return AnthropicSynthesisClient(key, model, max_tokens)
