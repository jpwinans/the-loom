"""Config-routed LLM serving: the ``llm`` section / LOOM_LLM_* env resolve to
an OpenAI-compatible client (Ollama, mlx_lm.server) or Anthropic, and the
wire format matches the OpenAI chat-completions protocol."""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest

from theloom.config import LoomConfigError, load_config
from theloom.synthesis.llm import (
    AnthropicSynthesisClient,
    OpenAICompatSynthesisClient,
    create_synthesis_client,
)

NO_ENV: dict[str, str] = {}


def _write_config(tmp_path: Path, body: dict[str, object]) -> Path:
    path = tmp_path / "config.json"
    path.write_text(json.dumps(body), encoding="utf-8")
    path.chmod(0o600)
    return path


class TestConfigResolution:
    def test_ollama_defaults(self, tmp_path: Path) -> None:
        path = _write_config(tmp_path, {"llm": {"provider": "ollama", "model": "orinth"}})
        config = load_config(config_path=path, env=NO_ENV)
        assert config.llm is not None
        assert config.llm.provider == "ollama"
        assert config.llm.base_url == "http://localhost:11434/v1"
        assert config.llm.model == "orinth"

    def test_mlx_defaults(self, tmp_path: Path) -> None:
        path = _write_config(tmp_path, {"llm": {"provider": "mlx", "model": "gemma-4"}})
        config = load_config(config_path=path, env=NO_ENV)
        assert config.llm is not None
        assert config.llm.base_url == "http://localhost:8080/v1"

    def test_explicit_base_url_and_limits(self, tmp_path: Path) -> None:
        path = _write_config(
            tmp_path,
            {
                "llm": {
                    "provider": "openai",
                    "baseUrl": "http://gpu-box:8000/v1",
                    "model": "orinth",
                    "maxTokens": 2048,
                    "timeoutSeconds": 60,
                }
            },
        )
        config = load_config(config_path=path, env=NO_ENV)
        assert config.llm is not None
        assert config.llm.base_url == "http://gpu-box:8000/v1"
        assert config.llm.max_tokens == 2048
        assert config.llm.timeout_seconds == 60.0

    def test_env_overrides_file(self, tmp_path: Path) -> None:
        path = _write_config(tmp_path, {"llm": {"provider": "ollama", "model": "orinth"}})
        config = load_config(
            config_path=path,
            env={"LOOM_LLM_MODEL": "gemma-4", "LOOM_LLM_BASE_URL": "http://localhost:9999/v1"},
        )
        assert config.llm is not None
        assert config.llm.model == "gemma-4"
        assert config.llm.base_url == "http://localhost:9999/v1"

    def test_env_alone_configures_routing(self, tmp_path: Path) -> None:
        config = load_config(
            config_path=tmp_path / "missing.json",
            env={"LOOM_LLM_PROVIDER": "ollama", "LOOM_LLM_MODEL": "orinth"},
        )
        assert config.llm is not None
        assert config.llm.provider == "ollama"

    def test_no_llm_section_is_none(self, tmp_path: Path) -> None:
        path = _write_config(tmp_path, {"defaultGraph": "default"})
        assert load_config(config_path=path, env=NO_ENV).llm is None

    def test_invalid_provider_is_config_error(self, tmp_path: Path) -> None:
        path = _write_config(tmp_path, {"llm": {"provider": "bard", "model": "x"}})
        with pytest.raises(LoomConfigError, match="Invalid llm.provider"):
            load_config(config_path=path, env=NO_ENV)


class TestClientResolution:
    def test_ollama_config_builds_openai_compat_client(self, tmp_path: Path) -> None:
        path = _write_config(tmp_path, {"llm": {"provider": "ollama", "model": "orinth"}})
        client = create_synthesis_client(config=load_config(config_path=path, env=NO_ENV))
        assert isinstance(client, OpenAICompatSynthesisClient)
        assert client.get_model() == "orinth"

    def test_missing_model_is_config_error(self, tmp_path: Path) -> None:
        path = _write_config(tmp_path, {"llm": {"provider": "ollama"}})
        with pytest.raises(LoomConfigError, match="llm.model is required"):
            create_synthesis_client(config=load_config(config_path=path, env=NO_ENV))

    def test_anthropic_provider_without_key_is_template_mode(self, tmp_path: Path) -> None:
        path = _write_config(tmp_path, {"llm": {"provider": "anthropic"}})
        assert create_synthesis_client(config=load_config(config_path=path, env=NO_ENV)) is None

    def test_anthropic_provider_with_key(self, tmp_path: Path) -> None:
        path = _write_config(
            tmp_path,
            {"llm": {"provider": "anthropic", "apiKey": "sk-test-not-real", "model": "m"}},
        )
        client = create_synthesis_client(config=load_config(config_path=path, env=NO_ENV))
        assert isinstance(client, AnthropicSynthesisClient)
        assert client.get_model() == "m"

    def test_legacy_key_fallback(self, tmp_path: Path) -> None:
        path = _write_config(tmp_path, {})
        config = load_config(config_path=path, env={"ANTHROPIC_API_KEY": "sk-test-not-real"})
        client = create_synthesis_client(config=config)
        assert isinstance(client, AnthropicSynthesisClient)


class TestOpenAICompatWire:
    def _client(self, handler: httpx.MockTransport) -> OpenAICompatSynthesisClient:
        return OpenAICompatSynthesisClient(
            base_url="http://localhost:11434/v1",
            model="orinth",
            api_key="local-token",
            max_tokens=512,
            transport=handler,
        )

    def test_complete_request_and_response(self) -> None:
        captured: dict[str, object] = {}

        def handle(request: httpx.Request) -> httpx.Response:
            captured["url"] = str(request.url)
            captured["auth"] = request.headers.get("authorization")
            captured["body"] = json.loads(request.content)
            return httpx.Response(
                200,
                json={
                    "model": "orinth:latest",
                    "choices": [{"message": {"role": "assistant", "content": "hello"}}],
                    "usage": {"prompt_tokens": 12, "completion_tokens": 3},
                },
            )

        client = self._client(httpx.MockTransport(handle))
        result = client.complete("system says", "user asks")

        assert captured["url"] == "http://localhost:11434/v1/chat/completions"
        assert captured["auth"] == "Bearer local-token"
        body = captured["body"]
        assert body["model"] == "orinth"  # type: ignore[index]
        assert body["max_tokens"] == 512  # type: ignore[index]
        assert body["messages"] == [  # type: ignore[index]
            {"role": "system", "content": "system says"},
            {"role": "user", "content": "user asks"},
        ]
        assert result == {
            "text": "hello",
            "inputTokens": 12,
            "outputTokens": 3,
            "model": "orinth:latest",
        }

    def test_empty_content_raises(self) -> None:
        def handle(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={"choices": [{"message": {"content": ""}}]})

        client = self._client(httpx.MockTransport(handle))
        with pytest.raises(RuntimeError, match="No text response"):
            client.complete("s", "u")

    def test_http_error_raises(self) -> None:
        def handle(request: httpx.Request) -> httpx.Response:
            return httpx.Response(500, json={"error": "model not loaded"})

        client = self._client(httpx.MockTransport(handle))
        with pytest.raises(httpx.HTTPStatusError):
            client.complete("s", "u")

    def test_missing_usage_defaults_to_zero(self) -> None:
        def handle(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={"choices": [{"message": {"content": "x"}}]})

        client = self._client(httpx.MockTransport(handle))
        result = client.complete("s", "u")
        assert result["inputTokens"] == 0
        assert result["outputTokens"] == 0
        assert result["model"] == "orinth"


class TestReasoningCleanup:
    def _client_returning(self, content: str) -> OpenAICompatSynthesisClient:
        def handle(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={"choices": [{"message": {"content": content}}]})

        return OpenAICompatSynthesisClient(
            base_url="http://localhost:11434/v1",
            model="orinth",
            transport=httpx.MockTransport(handle),
        )

    def test_think_blocks_stripped(self) -> None:
        client = self._client_returning("<think>chain of thought</think>\nThe answer.")
        assert client.complete("s", "u")["text"] == "The answer."

    def test_channel_markers_keep_last_segment(self) -> None:
        content = '<|channel>thought\n\nreasoning here..."<channel|>The final narrative.'
        client = self._client_returning(content)
        assert client.complete("s", "u")["text"] == "The final narrative."

    def test_plain_content_untouched(self) -> None:
        client = self._client_returning("Just a normal answer.")
        assert client.complete("s", "u")["text"] == "Just a normal answer."

    def test_all_reasoning_no_answer_raises(self) -> None:
        client = self._client_returning("<think>never finished")
        # An unclosed think block is not stripped; content survives as-is —
        # only genuinely empty content raises.
        assert client.complete("s", "u")["text"] == "<think>never finished"

    def test_empty_after_think_strip_raises(self) -> None:
        client = self._client_returning("<think>only thoughts</think>")
        with pytest.raises(RuntimeError, match="No text response"):
            client.complete("s", "u")


class TestTemperature:
    def test_temperature_passed_through(self, tmp_path: Path) -> None:
        captured: dict[str, object] = {}

        def handle(request: httpx.Request) -> httpx.Response:
            captured["body"] = json.loads(request.content)
            return httpx.Response(200, json={"choices": [{"message": {"content": "x"}}]})

        client = OpenAICompatSynthesisClient(
            base_url="http://localhost:11434/v1",
            model="orinth",
            temperature=0.0,
            transport=httpx.MockTransport(handle),
        )
        client.complete("s", "u")
        assert captured["body"]["temperature"] == 0.0  # type: ignore[index]

    def test_temperature_omitted_by_default(self) -> None:
        captured: dict[str, object] = {}

        def handle(request: httpx.Request) -> httpx.Response:
            captured["body"] = json.loads(request.content)
            return httpx.Response(200, json={"choices": [{"message": {"content": "x"}}]})

        client = OpenAICompatSynthesisClient(
            base_url="http://localhost:11434/v1",
            model="orinth",
            transport=httpx.MockTransport(handle),
        )
        client.complete("s", "u")
        assert "temperature" not in captured["body"]  # type: ignore[operator]

    def test_temperature_config_resolution(self, tmp_path: Path) -> None:
        path = _write_config(
            tmp_path,
            {"llm": {"provider": "ollama", "model": "orinth", "temperature": 0}},
        )
        config = load_config(config_path=path, env=NO_ENV)
        assert config.llm is not None
        assert config.llm.temperature == 0.0
