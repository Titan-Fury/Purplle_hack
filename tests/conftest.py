# PROMPT: "Create pytest fixtures for a FastAPI + async SQLAlchemy app: an isolated
#          SQLite database recreated before every test, and an httpx AsyncClient bound
#          to the ASGI app. Provide a helper to build schema-valid events."
# CHANGES MADE: Set env vars BEFORE importing app (get_settings is lru_cached), switched
#          to a per-test drop_all/create_all for isolation, and added make_event() with
#          sensible defaults so each test only specifies the fields it cares about.
from __future__ import annotations

import os
import tempfile
import uuid

# Must be set before importing app (settings are cached at first import).
_DB = os.path.join(tempfile.gettempdir(), "si_pytest.db")
os.environ["DATABASE_URL"] = f"sqlite+aiosqlite:///{_DB.replace(os.sep, '/')}"
os.environ["STORE_LAYOUT_PATH"] = "data/store_layout.json"
os.environ["POS_CSV_PATH"] = "data/pos_transactions.csv"

import httpx  # noqa: E402
import pytest_asyncio  # noqa: E402
from httpx import ASGITransport  # noqa: E402

from app.db import Base, get_engine  # noqa: E402
from app.main import app  # noqa: E402

STORE = "STORE_BLR_002"


@pytest_asyncio.fixture(autouse=True)
async def fresh_db():
    engine = get_engine()
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)
    yield


@pytest_asyncio.fixture
async def client():
    transport = ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


def make_event(event_type="ENTRY", visitor_id="VIS_1", zone_id=None,
               ts="2026-03-03T14:30:00Z", is_staff=False, dwell_ms=0,
               queue_depth=None, sku_zone=None, session_seq=1, store_id=STORE,
               confidence=0.9, event_id=None):
    return {
        "event_id": event_id or str(uuid.uuid4()),
        "store_id": store_id,
        "camera_id": "CAM_X",
        "visitor_id": visitor_id,
        "event_type": event_type,
        "timestamp": ts,
        "zone_id": zone_id,
        "dwell_ms": dwell_ms,
        "is_staff": is_staff,
        "confidence": confidence,
        "metadata": {"queue_depth": queue_depth, "sku_zone": sku_zone, "session_seq": session_seq},
    }
