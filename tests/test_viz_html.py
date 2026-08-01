"""HTML rendering tests — sentinel injection, escaping, template errors."""

from __future__ import annotations

import json

import pytest

from theloom.errors import LoomError
from theloom.viz.html import DATA_SENTINEL, render_html

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
