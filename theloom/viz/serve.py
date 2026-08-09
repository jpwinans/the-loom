"""Live mode — a read-only FastAPI server over the same assemblers the static
bundle uses (assemble_bundle / resolve_scope / semantic_search / read_entity).

FastAPI and uvicorn are the optional `viz-serve` extra: the imports are lazy
(inside create_app / run_uvicorn) and the type-only imports are TYPE_CHECKING,
so importing this module never requires the extra. Typed LoomError codes map to
HTTP statuses through one exception handler — never by substring-matching prose."""

from __future__ import annotations

import sys
from typing import TYPE_CHECKING, Any

import pydantic

from theloom.errors import LoomError, NotFoundError, ValidationError
from theloom.operations.common import CommandInput
from theloom.operations.semantic import SemanticSearchInput, semantic_search
from theloom.store.multigraph import MultiGraph
from theloom.viz.bundle import ExportBundleInput, assemble_bundle
from theloom.viz.html import load_template, render_html
from theloom.viz.scope import ScopeInput, resolve_scope

if TYPE_CHECKING:  # the extra may be absent; annotations are strings under __future__
    from fastapi import FastAPI

# Typed CLI code → HTTP status. Single source; no prose matching.
_STATUS: dict[str, int] = {
    "PARSE_ERROR": 400,
    "INPUT_REQUIRED": 400,
    "VALIDATION_ERROR": 422,
    "NOT_FOUND": 404,
    "OPERATION_ERROR": 500,
    "CONFIG_ERROR": 500,
}

# Replaces the template's __TAPESTRY_BUNDLE__ sentinel via the same render_html
# the static path uses. The frontend detects live mode by this parsed shape
# (`live === true`), never by the sentinel literal.
_LIVE_MARKER: dict[str, Any] = {"live": True, "apiBase": "/api"}


class ServeInput(CommandInput):
    graph: str | None = None
    host: str = "127.0.0.1"
    port: int = 8000
    # Test hook: build the app and return the handshake WITHOUT binding a port.
    check: bool = False


def _build_bundle_input(
    *,
    graph: str | None,
    mode: str,
    center: str | None,
    depth: int,
    entity_type: str | None,
    relation_type: str | None,
    query: str | None,
    analytics: bool,
    temporal: bool,
    semantic: bool,
    as_of: str | None,
    title: str | None,
) -> ExportBundleInput:
    doc: dict[str, Any] = {
        "graph": graph,
        "scope": {
            "mode": mode,
            "center": center,
            "depth": depth,
            "entityType": entity_type,
            "relationType": relation_type,
            "query": query,
        },
        "include": {"analytics": analytics, "temporal": temporal, "semantic": semantic},
        "asOf": as_of,
        "title": title,
    }
    try:
        return ExportBundleInput.model_validate(doc)
    except pydantic.ValidationError as exc:  # mirror run_handler's mapping
        raise ValidationError(str(exc)) from exc


def create_app(multi: MultiGraph, default_graph: str | None = None) -> FastAPI:
    """Build the read-only live-mode app. `default_graph` is the fallback target
    for bundle routes when no `?graph=` is supplied."""
    from fastapi import FastAPI, Query
    from fastapi.requests import Request
    from fastapi.responses import JSONResponse

    app = FastAPI(title="Tapestry (The Loom live server)", docs_url=None, redoc_url=None)
    target_default = default_graph or multi.default_graph

    async def _loom_error(_: Request, exc: Exception) -> JSONResponse:
        assert isinstance(exc, LoomError)  # only ever registered for LoomError
        return JSONResponse(
            {"error": exc.message, "code": exc.code},
            status_code=_STATUS.get(exc.code, 500),
        )

    app.add_exception_handler(LoomError, _loom_error)

    def graphs() -> list[dict[str, Any]]:
        return multi.list_graphs()

    app.add_api_route("/api/graphs", graphs, methods=["GET"])

    def _bundle(
        graph: str | None = Query(default=None),
        mode: str = Query(default="full"),
        center: str | None = Query(default=None),
        depth: int = Query(default=1),
        entityType: str | None = Query(default=None),
        relationType: str | None = Query(default=None),
        query: str | None = Query(default=None),
        analytics: bool = Query(default=True),
        temporal: bool = Query(default=True),
        semantic: bool = Query(default=True),
        asOf: str | None = Query(default=None),
        title: str | None = Query(default=None),
    ) -> dict[str, Any]:
        params = _build_bundle_input(
            graph=graph if graph is not None else target_default,
            mode=mode,
            center=center,
            depth=depth,
            entity_type=entityType,
            relation_type=relationType,
            query=query,
            analytics=analytics,
            temporal=temporal,
            semantic=semantic,
            as_of=asOf,
            title=title,
        )
        return assemble_bundle(params, multi)

    app.add_api_route("/api/bundle", _bundle, methods=["GET"])

    def _as_of(
        asOf: str | None = Query(default=None),
        graph: str | None = Query(default=None),
    ) -> dict[str, Any]:
        if asOf is None or not asOf.strip():
            raise ValidationError(
                "Endpoint /api/as-of requires a non-empty 'asOf' query parameter."
            )
        params = _build_bundle_input(
            graph=graph if graph is not None else target_default,
            mode="full",
            center=None,
            depth=1,
            entity_type=None,
            relation_type=None,
            query=None,
            analytics=True,
            temporal=True,
            semantic=True,
            as_of=asOf,
            title=None,
        )
        return assemble_bundle(params, multi)

    app.add_api_route("/api/as-of", _as_of, methods=["GET"])

    def _neighbors(
        id: str = Query(...),
        depth: int = Query(default=1),
        graph: str | None = Query(default=None),
    ) -> dict[str, Any]:
        store = multi.get_store(graph if graph is not None else target_default)
        try:
            scope = ScopeInput(mode="ego", center=id, depth=depth)
        except pydantic.ValidationError as exc:  # e.g. depth outside the 1-5 range
            raise ValidationError(str(exc)) from exc
        entities, relations, _ = resolve_scope(scope, store)
        return {"entities": entities, "relations": relations}

    app.add_api_route("/api/neighbors", _neighbors, methods=["GET"])

    def _search(
        q: str = Query(...),
        limit: int = Query(default=10),
        graph: str | None = Query(default=None),
    ) -> list[dict[str, Any]]:
        # This REST endpoint's own contract predates (and is independent of)
        # the CLI's {items, count} envelope (desire 9) — unwrap to keep it a
        # bare array, matching every other /api/* route here.
        items: list[dict[str, Any]] = semantic_search(
            SemanticSearchInput.model_validate(
                {
                    "query": q,
                    "limit": limit,
                    "graph": graph if graph is not None else target_default,
                }
            ),
            multi,
        )["items"]
        return items

    app.add_api_route("/api/search", _search, methods=["GET"])

    def _entity(entity_id: str, graph: str | None = Query(default=None)) -> dict[str, Any]:
        store = multi.get_store(graph if graph is not None else target_default)
        entity = store.read_entity(entity_id)
        if entity is None:
            raise NotFoundError(f"Entity not found with ID: {entity_id}")
        return entity.model_dump(by_alias=True, exclude_unset=True)

    app.add_api_route("/api/entity/{entity_id}", _entity, methods=["GET"])

    from fastapi.responses import HTMLResponse

    def _index() -> HTMLResponse:
        html = render_html(_LIVE_MARKER, load_template())
        return HTMLResponse(content=html)

    app.add_api_route("/", _index, methods=["GET"], response_class=HTMLResponse)

    return app


def run_uvicorn(app: FastAPI, host: str, port: int) -> None:  # thin wrapper — untested
    import uvicorn

    uvicorn.run(app, host=host, port=port)


def serve(params: ServeInput, multi: MultiGraph) -> dict[str, Any]:
    """Start the read-only live server. Prints the {host, port, url, graph}
    handshake, then blocks in uvicorn until shutdown. `check: true` returns the
    handshake without building the app or binding a port — the registry-level
    test path, runnable even when the viz-serve extra is absent.

    Calls through this module's own names (not re-imported into the CLI
    registry) so tests can monkeypatch `theloom.viz.serve.run_uvicorn` and
    have it take effect here."""
    graph = params.graph or multi.default_graph
    envelope: dict[str, Any] = {
        "host": params.host,
        "port": params.port,
        "url": f"http://{params.host}:{params.port}",
        "graph": graph,
    }
    if params.check:
        return envelope
    from theloom.cli.io import output_success

    app = create_app(multi, default_graph=params.graph)
    output_success(envelope)  # the handshake — uvicorn.run below never returns to the CLI
    sys.stdout.flush()  # stdout is block-buffered off a TTY; run_uvicorn blocks forever below
    run_uvicorn(app, params.host, params.port)
    return envelope  # reached only after shutdown (Ctrl-C)
