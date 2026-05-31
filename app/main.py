"""FastAPI entrypoint for the Store Intelligence API.

Wires routers, initialises the database on startup, installs structured-logging
middleware, and converts infrastructure failures into clean, structured responses
(no raw stack traces; DB outage -> HTTP 503).
"""
from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse
from sqlalchemy.exc import SQLAlchemyError

from . import health
from .config import get_settings
from .db import init_db
from .logging_setup import LOG
from .middleware import RequestContextMiddleware
from .routers import events, stores

settings = get_settings()


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Initialise the DB in the BACKGROUND so the app starts serving /health immediately
    # (a cloud platform's health check must not wait on DB warm-up). Endpoints that need
    # the DB degrade to 503 until init succeeds; /health stays available throughout.
    import asyncio

    async def _init_with_retry():
        for attempt in range(1, 31):
            try:
                await init_db()
                LOG.info("db_init_ok", extra={"attempt": attempt})
                await _maybe_seed()
                return
            except SQLAlchemyError as exc:
                LOG.error("db_init_retry", extra={"attempt": attempt, "error": str(exc)})
                await asyncio.sleep(min(2 * attempt, 15))

    task = asyncio.create_task(_init_with_retry())
    yield
    task.cancel()


async def _maybe_seed() -> None:
    """If enabled and the events table is empty, ingest the configured seed file once."""
    import json
    import os

    from sqlalchemy import func, select

    from .db import EventRow, SessionLocal
    from .ingestion import ingest_events

    if not settings.seed_on_start or not os.path.exists(settings.seed_file):
        return
    async with SessionLocal() as session:
        count = (await session.execute(select(func.count()).select_from(EventRow))).scalar_one()
        if count:
            return
        events = [json.loads(line) for line in open(settings.seed_file, encoding="utf-8") if line.strip()]
        total = 0
        for i in range(0, len(events), 500):
            res = await ingest_events(session, events[i:i + 500])
            total += res.accepted
        LOG.info("seeded_events", extra={"file": settings.seed_file, "accepted": total})


app = FastAPI(
    title="Apex Store Intelligence API",
    version="0.1.0",
    description="Real-time offline-store analytics from CCTV-derived events.",
    lifespan=lifespan,
)

app.add_middleware(RequestContextMiddleware)
app.include_router(events.router)
app.include_router(stores.router)
app.include_router(health.router)


@app.exception_handler(SQLAlchemyError)
async def _db_error_handler(request: Request, exc: SQLAlchemyError) -> JSONResponse:
    LOG.error("db_unavailable", extra={
        "trace_id": getattr(request.state, "trace_id", None),
        "endpoint": request.url.path,
        "error": exc.__class__.__name__,
    })
    return JSONResponse(
        status_code=503,
        content={"error": "service_unavailable", "code": "DB_UNAVAILABLE",
                 "detail": "The analytics datastore is temporarily unavailable. Retry shortly."},
    )


@app.exception_handler(Exception)
async def _unhandled_handler(request: Request, exc: Exception) -> JSONResponse:
    LOG.error("unhandled_error", extra={
        "trace_id": getattr(request.state, "trace_id", None),
        "endpoint": request.url.path,
        "error": exc.__class__.__name__,
    })
    return JSONResponse(
        status_code=500,
        content={"error": "internal_error", "code": "INTERNAL", "detail": "Unexpected error."},
    )


@app.get("/", tags=["meta"])
async def root() -> dict:
    return {"service": "store-intelligence", "version": app.version, "env": settings.app_env}


@app.get("/stats", tags=["meta"])
async def stats() -> dict:
    """In-process observability counters (request volume, per-endpoint latency, errors,
    events ingested) — the app-metrics complement to structured logs and /health."""
    from .observability import snapshot
    return snapshot()


_DASHBOARD = Path(__file__).parent / "static" / "dashboard.html"


@app.get("/dashboard", tags=["meta"], include_in_schema=False)
async def dashboard() -> FileResponse:
    return FileResponse(_DASHBOARD, media_type="text/html")
