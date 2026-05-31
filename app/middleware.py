"""Request-context middleware: trace ids, latency, and structured access logs."""
from __future__ import annotations

import time
import uuid

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request

from . import observability
from .logging_setup import LOG


class RequestContextMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        trace_id = request.headers.get("x-trace-id") or uuid.uuid4().hex
        request.state.trace_id = trace_id
        request.state.event_count = None
        start = time.perf_counter()

        response = await call_next(request)

        latency_ms = round((time.perf_counter() - start) * 1000, 2)
        response.headers["x-trace-id"] = trace_id
        # low-cardinality label from the matched route template (e.g. /stores/{store_id}/metrics)
        route = request.scope.get("route")
        endpoint_label = getattr(route, "path", request.url.path)
        event_count = getattr(request.state, "event_count", None)
        observability.record(endpoint_label, response.status_code, latency_ms, event_count)
        LOG.info(
            "request",
            extra={
                "trace_id": trace_id,
                "store_id": request.path_params.get("store_id"),
                "endpoint": request.url.path,
                "method": request.method,
                "status_code": response.status_code,
                "latency_ms": latency_ms,
                "event_count": event_count,
            },
        )
        return response
