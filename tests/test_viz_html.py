"""HTML rendering tests — sentinel injection, escaping, template errors."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from theloom.errors import LoomError
from theloom.viz.html import DATA_SENTINEL, render_html

REPO_ROOT = Path(__file__).parent.parent
BUILT_INDEX = REPO_ROOT / "tapestry" / "dist" / "index.html"
SERVED_TEMPLATE = REPO_ROOT / "theloom" / "viz" / "static" / "tapestry.html"

TEMPLATE = (
    f'<html><script id="tapestry-data" type="application/json">{DATA_SENTINEL}</script></html>'
)


def test_injects_bundle_json() -> None:
    html = render_html({"meta": {"graph": "g"}}, TEMPLATE)
    assert '"graph": "g"' in html or '"graph":"g"' in html
    assert DATA_SENTINEL not in html


def test_escapes_script_close() -> None:
    html = render_html({"x": "</script><script>alert(1)</script>"}, TEMPLATE)
    assert "</script><script>alert(1)" not in html
    start = html.index(">", html.index("tapestry-data")) + 1
    end = html.index("</script>", start)
    assert json.loads(html[start:end].replace("<\\/", "</"))["x"].startswith("</script>")


def test_template_without_sentinel_is_config_error() -> None:
    with pytest.raises(LoomError) as err:
        render_html({}, "<html></html>")
    assert err.value.code == "CONFIG_ERROR"


def test_served_template_matches_the_built_spa() -> None:
    """The served template (theloom/viz/static/tapestry.html) is a checked-in
    copy of the freshly-built SPA (tapestry/dist/index.html) — nothing wires
    them together automatically, so a rebuild that isn't re-copied leaves a
    stale served template that every other Python test is blind to. Skips
    (rather than fails) when the built dist is absent, e.g. a checkout that
    never ran the frontend build."""
    if not BUILT_INDEX.exists():
        pytest.skip(
            f"{BUILT_INDEX.relative_to(REPO_ROOT)} is absent (frontend not built in this "
            "checkout) — nothing to compare the served template against."
        )
    assert SERVED_TEMPLATE.read_bytes() == BUILT_INDEX.read_bytes(), (
        f"{SERVED_TEMPLATE.relative_to(REPO_ROOT)} has drifted from "
        f"{BUILT_INDEX.relative_to(REPO_ROOT)} — rebuild the frontend and re-copy it."
    )
