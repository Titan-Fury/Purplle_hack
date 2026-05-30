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
    # Retry DB init: on hosts like Railway the app can boot before Postgres is ready.
    import asyncio

    for attempt in range(1, 11):
        try:
            await init_db()
            break
        except SQLAlchemyError as exc:
            LOG.error("db_init_retry", extra={"attempt": attempt, "error": str(exc)})
            await asyncio.sleep(min(2 * attempt, 10))
    yield


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


_DASHBOARD = Path(__file__).parent / "static" / "dashboard.html"


@app.get("/dashboard", tags=["meta"], include_in_schema=False)
async def dashboard() -> FileResponse:
    return FileResponse(_DASHBOARD, media_type="text/html")
