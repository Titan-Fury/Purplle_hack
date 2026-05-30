"""Event emission: raw tracker dict -> schema event -> JSONL (+ optional live POST).

Generates the globally-unique event_id and the ISO-8601 UTC timestamp, assembles the
nested schema, and (optionally) validates each event against `app.models.Event` so the
pipeline never writes a schema-non-compliant event. Live streaming to the API is
batched and best-effort so a slow/unavailable API never stalls detection.
"""
from __future__ import annotations

import json
import os
import uuid
from datetime import datetime, timezone
from typing import Optional

import requests

from .config import CONFIG

# Optional strict validation — present on hosts that installed the API deps.
try:
    from app.models import Event  # type: ignore

    _VALIDATE = True
except Exception:
    _VALIDATE = False


def _iso(ts_epoch: float) -> str:
    return datetime.fromtimestamp(ts_epoch, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class EventEmitter:
    def __init__(self, output_path: str, api_url: str = "", batch_size: int = 200):
        self.output_path = output_path
        self.api_url = api_url or CONFIG.api_url
        self.batch_size = batch_size or CONFIG.ingest_batch_size
        os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)
        self._fh = open(output_path, "w", encoding="utf-8")
        self._buffer: list[dict] = []
        self.written = 0
        self.invalid = 0

    def __call__(self, raw: dict) -> None:
        event = {
            "event_id": str(uuid.uuid4()),
            "store_id": raw["store_id"],
            "camera_id": raw["camera_id"],
            "visitor_id": raw["visitor_id"],
            "event_type": raw["event_type"],
            "timestamp": _iso(raw["ts_epoch"]),
            "zone_id": raw.get("zone_id"),
            "dwell_ms": raw.get("dwell_ms", 0),
            "is_staff": raw.get("is_staff", False),
            "confidence": raw.get("confidence", 0.0),
            "metadata": {
                "queue_depth": raw.get("queue_depth"),
                "sku_zone": raw.get("sku_zone"),
                "session_seq": raw.get("session_seq", 0),
            },
        }
        if _VALIDATE:
            try:
                Event.model_validate(event)
            except Exception:
                self.invalid += 1
                return  # do not write schema-invalid events

        self._fh.write(json.dumps(event) + "\n")
        self.written += 1
        if self.api_url:
            self._buffer.append(event)
            if len(self._buffer) >= self.batch_size:
                self._flush_api()

    def _flush_api(self) -> None:
        if not self._buffer:
            return
        try:
            requests.post(
                f"{self.api_url.rstrip('/')}/events/ingest",
                json={"events": self._buffer},
                timeout=5,
            )
        except requests.RequestException:
            pass  # best-effort live streaming; JSONL remains the source of truth
        finally:
            self._buffer.clear()

    def close(self) -> None:
        self._flush_api()
        self._fh.close()
