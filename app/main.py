"""FastAPI entrypoint for the Store Intelligence API.

Wires routers, initialises the database on startup, and (in later steps) attaches
structured-logging and graceful-degradation middleware.
"""
from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI

from .config import get_settings
from .db import init_db
from .routers import events

settings = get_settings()


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    yield


app = FastAPI(
    title="Apex Store Intelligence API",
    version="0.1.0",
    description="Real-time offline-store analytics from CCTV-derived events.",
    lifespan=lifespan,
)

app.include_router(events.router)


@app.get("/", tags=["meta"])
async def root() -> dict:
    return {"service": "store-intelligence", "version": app.version, "env": settings.app_env}
