"""Static HTML emission — inject the bundle into the built SPA template.

The template is the committed single-file Vite build; its data block holds the
sentinel this module replaces. `</` is escaped so bundle content can never
terminate the script block."""

from __future__ import annotations

import json
from importlib import resources
from pathlib import Path
from typing import Any

from theloom.errors import ConfigError, ValidationError
from theloom.store.multigraph import MultiGraph
from theloom.viz.bundle import ExportBundleInput, assemble_bundle

DATA_SENTINEL = "__TAPESTRY_BUNDLE__"
_THEMES = ("auto", "dark", "light")


class VisualizeInput(ExportBundleInput):
    output: str | None = None
    theme: str = "auto"


def render_html(bundle: dict[str, Any], template_text: str) -> str:
    if DATA_SENTINEL not in template_text:
        raise ConfigError(
            "Tapestry template is missing its data sentinel — rebuild the frontend: "
            "cd tapestry && npm ci && npm run build"
        )
    payload = json.dumps(bundle, ensure_ascii=False).replace("</", "<\\/")
    return template_text.replace(DATA_SENTINEL, payload)


def load_template() -> str:
    resource = resources.files("theloom.viz").joinpath("static/tapestry.html")
    try:
        return resource.read_text(encoding="utf-8")
    except (FileNotFoundError, ModuleNotFoundError) as exc:
        raise ConfigError(
            "Tapestry template missing — build the frontend: cd tapestry && npm ci && npm run build"
        ) from exc


def write_visualization(params: VisualizeInput, multi: MultiGraph) -> dict[str, Any]:
    if params.theme not in _THEMES:
        raise ValidationError(
            f"Invalid theme: '{params.theme}'. Must be one of: {', '.join(_THEMES)}"
        )
    bundle = assemble_bundle(params, multi)
    bundle["meta"]["theme"] = params.theme
    html = render_html(bundle, load_template())
    target = Path(params.output or f"loom-viz/{bundle['meta']['graph']}.html")
    target.parent.mkdir(parents=True, exist_ok=True)
    data = html.encode("utf-8")
    target.write_bytes(data)
    return {
        "path": str(target),
        "entityCount": bundle["meta"]["entityCount"],
        "relationCount": bundle["meta"]["relationCount"],
        "bytes": len(data),
        "sections": bundle["meta"]["sections"],
    }
