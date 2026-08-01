"""Prompt loader.

Loads model-specific prompt text from ``theloom/prompts/{profile}/``. The local
model name maps to a profile directory; unknown models fall back to the safe
default profile. Loaded prompts are cached and stripped of trailing ``# NOTE:``
metadata.
"""

from __future__ import annotations

import re
from pathlib import Path

_PROMPTS_DIR = Path(__file__).resolve().parent.parent / "prompts"

# mlx / ollama model name -> prompt profile directory.
MODEL_TO_PROFILE: dict[str, str] = {
    "mlx-community/Qwen3.5-4B-4bit": "qwen-4b-4bit",
    "mlx-community/Qwen3.5-9B-4bit": "qwen-9b-4bit",
    "mlx-community/Qwen3.5-9B-8bit": "qwen-9b-4bit",
    "mlx-community/Qwen3.5-27B-4bit": "qwen-9b-4bit",
    "mlx-community/phi-4-4bit": "phi-4-4bit",
    "qwen3.5:4b": "qwen-4b-4bit",
}

DEFAULT_PROFILE = "qwen-4b-4bit"

_cache: dict[str, str] = {}

_NOTE_RE = re.compile(r"\n# NOTE:.*$", re.S)


def resolve_profile(model_name: str) -> str:
    return MODEL_TO_PROFILE.get(model_name, DEFAULT_PROFILE)


def load_prompt(prompt_name: str, model_name: str) -> str:
    """Load ``prompts/<profile>/<prompt_name>.txt`` (cached, note-stripped)."""
    profile = resolve_profile(model_name)
    cache_key = f"{profile}/{prompt_name}"
    cached = _cache.get(cache_key)
    if cached is not None:
        return cached
    path = _PROMPTS_DIR / profile / f"{prompt_name}.txt"
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise RuntimeError(
            f"Prompt not found: {path} (model={model_name}, profile={profile})"
        ) from exc
    cleaned = _NOTE_RE.sub("", text).rstrip()
    _cache[cache_key] = cleaned
    return cleaned


def get_prompt_profile(model_name: str) -> str:
    return resolve_profile(model_name)
