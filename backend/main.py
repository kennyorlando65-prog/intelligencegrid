"""IntelligenceGrid API -- FastAPI service root.

This is the single entrypoint for the Python service described in the
repository's root ``vercel.json`` (``entrypoint: "main:app"``). It is
deliberately declared at ``backend/main.py`` rather than in a root ``api/``
directory: on a project whose framework preset is Next.js, a top-level
``api/`` folder is *not* a routing override -- Next.js owns the whole routing
table and the Python file is never mounted, so ``/api/*`` falls through to
Next.js and answers with its own HTML 404. Declaring the app here and mapping
it through the Vercel *Services* config is what actually gets it deployed.

## Prefix agnosticism

Routes are declared WITHOUT an ``/api`` prefix and a middleware strips a
leading ``/api`` from the incoming path. That way one app serves both:

* ``uvicorn main:app`` locally, where there is no prefix, and
* the deployed service, which receives the prefix forwarded by the rewrite.

A test asserts ``/health`` and ``/api/health`` return an identical body, so
the two paths cannot silently drift apart.

## A note on what an empty response means

Every data endpoint returns whatever the pipeline currently holds, together
with a ``meta`` block reporting source health. A module that shows nothing is
not the same claim as "nothing happened" -- the metadata is what lets a
reader tell the difference, and it is attached to every response for exactly
that reason.
"""

from __future__ import annotations

import csv
import io
import json
import sys
from pathlib import Path
from typing import Any

# The service root must be importable regardless of how the runtime sets cwd.
sys.path.insert(0, str(Path(__file__).resolve().parent))

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, PlainTextResponse

from ig import __version__
from ig.models import utcnow
from ig.modules import MODULE_INDEX, WatchlistStore, run_module
from ig.modules.registry import MODULES, news_detail
from ig.pipeline import Pipeline

app = FastAPI(
    title="IntelligenceGrid API",
    version=__version__,
    description=(
        "Open-source global intelligence and situational-awareness platform. "
        "Fourteen modules over free, key-less public sources, with explicit "
        "coverage-confidence so a quiet region reads as low-data rather than "
        "as confirmed calm."
    ),
    docs_url="/docs",
    redoc_url="/redoc",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["GET", "POST", "DELETE", "OPTIONS"],
    allow_headers=["*"],
)


@app.middleware("http")
async def strip_api_prefix(request: Request, call_next: Any) -> Any:
    """Make ``/api/x`` and ``/x`` the same route.

    The deployed service is reached through a rewrite that prepends ``/api``;
    a local ``uvicorn main:app`` is not. Normalising here means the route
    table is written once, with no prefix, and both environments work.
    """
    path = request.scope.get("path", "")
    if path == "/api":
        request.scope["path"] = "/"
    elif path.startswith("/api/"):
        request.scope["path"] = path[len("/api") :]
    return await call_next(request)


# -- process-scoped singletons ----------------------------------------------
# A serverless invocation is one process, so these live for its duration. The
# pipeline is built lazily because constructing it starts an HTTP client.

_STATE: dict[str, Any] = {}


def _pipeline() -> Pipeline:
    pipe = _STATE.get("pipeline")
    if pipe is None:
        pipe = Pipeline()
        _STATE["pipeline"] = pipe
    return pipe


def _watchlists() -> WatchlistStore:
    store = _STATE.get("watchlists")
    if store is None:
        store = WatchlistStore()
        _STATE["watchlists"] = store
    return store


def _params(request: Request) -> dict[str, Any]:
    """Flatten query parameters, preserving repeated keys as lists."""
    out: dict[str, Any] = {}
    for key in request.query_params.keys():
        values = request.query_params.getlist(key)
        out[key] = values if len(values) > 1 else values[0]
    return out


async def _dispatch(module_id: str, request: Request) -> dict[str, Any]:
    """Run one module, warming the pipeline first if it has not been built."""
    pipe = _pipeline()
    try:
        await pipe.ensure_ready()
    except Exception as exc:  # noqa: BLE001 - a dead feed must not 500 the API
        payload = run_module(module_id, pipe, _params(request), _watchlists())
        if payload is None:
            raise HTTPException(status_code=404, detail=f"unknown module {module_id}") from exc
        payload.setdefault("meta", {})["warmup_error"] = f"{type(exc).__name__}: {exc}"
        return payload

    payload = run_module(module_id, pipe, _params(request), _watchlists())
    if payload is None:
        raise HTTPException(status_code=404, detail=f"unknown module {module_id}")
    return payload


# -- service endpoints ------------------------------------------------------


@app.get("/", include_in_schema=False)
async def root() -> dict[str, Any]:
    return {
        "service": "IntelligenceGrid API",
        "version": __version__,
        "modules": [m["id"] for m in MODULE_INDEX],
        "module_count": len(MODULE_INDEX),
        "docs": "/docs",
        "note": (
            "All upstream sources are free and require no API key. Confidence "
            "values express evidence strength, never certainty."
        ),
    }


@app.get("/health")
async def health() -> dict[str, Any]:
    """Operational state, answered cheaply.

    Deliberately does NOT trigger a collection: a health probe must stay
    fast, and on a serverless platform a probe that runs a 60-second
    multi-source fetch is how a health check becomes an outage.
    """
    pipe = _pipeline()
    store_summary = pipe.store.summary()
    registry = pipe.registry
    sources = registry.health_report() if registry is not None else None
    built = bool(store_summary["datasets"])
    return {
        "status": "ok",
        "version": __version__,
        "checked_at": utcnow().isoformat(),
        "datasets_built": built,
        "dataset_count": len(store_summary["datasets"]),
        "sources": {
            "registered": sources["total"] if sources else 0,
            "usable": sources["usable"] if sources else 0,
            "degraded": sources["degraded"] if sources else False,
        },
        "last_run": pipe.last_report.as_dict(),
        "note": (
            "No collection is performed by this endpoint. Query a data module "
            "(or POST /refresh) to build the datasets."
        ),
    }


@app.get("/modules")
async def modules() -> dict[str, Any]:
    return {
        "count": len(MODULE_INDEX),
        "modules": MODULE_INDEX,
        "ids": list(MODULES.keys()),
    }


@app.get("/sources")
async def sources() -> dict[str, Any]:
    """Per-source health, including the ones that failed and why."""
    pipe = _pipeline()
    registry = pipe.registry
    if registry is None:
        return {
            "sources": [],
            "total": 0,
            "usable": 0,
            "degraded": False,
            "note": (
                "No collection has run yet in this process, so no source has "
                "reported health. Trigger one with POST /refresh."
            ),
        }
    report = registry.health_report()
    report["store"] = pipe.store.summary()
    report["last_run"] = pipe.last_report.as_dict()
    return report


@app.post("/refresh")
@app.get("/refresh")
async def refresh(force: bool = True, only: str = "") -> dict[str, Any]:
    """Run collection and rebuild every dataset."""
    pipe = _pipeline()
    targets = [s.strip() for s in only.split(",") if s.strip()] or None
    report = await pipe.refresh(force=force, only=targets)
    payload = report.as_dict()
    payload["store"] = pipe.store.summary()
    return payload


# -- the fourteen modules ---------------------------------------------------


@app.get("/news")
async def news(request: Request) -> dict[str, Any]:
    return await _dispatch("news", request)


@app.get("/news/{article_id}")
async def news_article(article_id: str) -> Any:
    pipe = _pipeline()
    await pipe.ensure_ready()
    detail = news_detail(pipe, article_id)
    if detail is None:
        raise HTTPException(status_code=404, detail=f"article {article_id} not found")
    return detail


@app.get("/events")
async def events(request: Request) -> dict[str, Any]:
    return await _dispatch("events", request)


@app.get("/signals")
async def signals(request: Request) -> dict[str, Any]:
    return await _dispatch("signals", request)


@app.get("/threats")
async def threats(request: Request) -> dict[str, Any]:
    return await _dispatch("threats", request)


@app.get("/sanctions")
async def sanctions(request: Request) -> dict[str, Any]:
    return await _dispatch("sanctions", request)


@app.get("/flights")
async def flights(request: Request) -> dict[str, Any]:
    return await _dispatch("flights", request)


@app.get("/maritime")
async def maritime(request: Request) -> dict[str, Any]:
    return await _dispatch("maritime", request)


@app.get("/timeline")
async def timeline(request: Request) -> dict[str, Any]:
    return await _dispatch("timeline", request)


@app.get("/evidence")
async def evidence(request: Request) -> dict[str, Any]:
    return await _dispatch("evidence", request)


@app.get("/map")
async def map_view(request: Request) -> dict[str, Any]:
    return await _dispatch("map", request)


@app.get("/entities")
async def entities(request: Request) -> dict[str, Any]:
    return await _dispatch("entities", request)


@app.get("/backtest")
async def backtest(request: Request) -> dict[str, Any]:
    return await _dispatch("backtest", request)


@app.get("/coverage")
async def coverage(request: Request) -> dict[str, Any]:
    return await _dispatch("coverage", request)


# -- watchlists (list / create / delete / digest) --------------------------


@app.get("/watchlists")
async def watchlists_list(request: Request) -> dict[str, Any]:
    return await _dispatch("watchlists", request)


@app.post("/watchlists")
async def watchlists_create(request: Request) -> dict[str, Any]:
    params = _params(request)
    params["action"] = "create"
    pipe = _pipeline()
    await pipe.ensure_ready()
    return run_module("watchlists", pipe, params, _watchlists()) or {}


@app.delete("/watchlists/{watchlist_id}")
async def watchlists_delete(watchlist_id: str) -> dict[str, Any]:
    pipe = _pipeline()
    await pipe.ensure_ready()
    params = {"action": "delete", "id": watchlist_id}
    payload = run_module("watchlists", pipe, params, _watchlists()) or {}
    if not payload.get("removed"):
        raise HTTPException(status_code=404, detail=f"watchlist {watchlist_id} not found")
    return payload


@app.get("/watchlists/{watchlist_id}/digest")
async def watchlists_digest(watchlist_id: str) -> dict[str, Any]:
    pipe = _pipeline()
    await pipe.ensure_ready()
    params = {"action": "digest", "id": watchlist_id}
    payload = run_module("watchlists", pipe, params, _watchlists()) or {}
    if "error" in payload:
        raise HTTPException(status_code=404, detail=payload["error"])
    return payload


# -- export -----------------------------------------------------------------

#: Which list in each module's payload becomes the exported row set.
_EXPORT_ROWS: dict[str, str] = {
    "news": "articles",
    "events": "events",
    "signals": "signals",
    "threats": "alerts",
    "sanctions": "entities",
    "flights": "observations",
    "maritime": "sanctioned_vessels",
    "timeline": "frames",
    "evidence": "signals",
    "map": "points",
    "entities": "nodes",
    "coverage": "countries",
    "watchlists": "watchlists",
}


def _flatten(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False, default=str)
    return str(value)


@app.get("/export")
async def export(request: Request, module: str = "news", format: str = "json") -> Any:
    """Export any module's primary row set as JSON or CSV.

    Every export is the *module's own* output, so the accompanying caveats and
    coverage metadata travel with the data rather than being dropped at the
    boundary -- an export that strips its own uncertainty is worse than none.
    """
    if module not in MODULES:
        raise HTTPException(status_code=404, detail=f"unknown module {module}")
    fmt = format.lower()
    if fmt not in ("json", "csv"):
        raise HTTPException(status_code=400, detail="format must be 'json' or 'csv'")

    payload = await _dispatch(module, request)
    if fmt == "json":
        return JSONResponse(payload)

    key = _EXPORT_ROWS.get(module)
    rows = payload.get(key) if key else None
    if not isinstance(rows, list):
        # Modules without a tabular row set (e.g. backtest) export as a
        # single-row CSV of their scalar fields rather than erroring out.
        rows = [{k: v for k, v in payload.items() if not isinstance(v, (dict, list))}]

    buffer = io.StringIO()
    if rows:
        fieldnames: list[str] = []
        if isinstance(rows[0], dict):
            for row in rows:
                if isinstance(row, dict):
                    for k in row:
                        if k not in fieldnames:
                            fieldnames.append(k)
        else:
            fieldnames = ["value"]
            rows = [{"value": r} for r in rows]
        writer = csv.DictWriter(buffer, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow({k: _flatten(row.get(k)) for k in fieldnames})

    filename = f"intelligencegrid_{module}_{utcnow().strftime('%Y%m%dT%H%M%SZ')}.csv"
    return PlainTextResponse(
        buffer.getvalue(),
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


if __name__ == "__main__":  # pragma: no cover - local convenience only
    import uvicorn

    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=False)
