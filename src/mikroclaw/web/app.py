"""MikroCLAW Pulse — Starlette app + Server-Sent Events.

Endpoint:
  GET /              -> laman dashboard (static/index.html)
  GET /api/snapshot  -> state live sekali ambil (JSON) — debugging
  GET /api/stream    -> SSE; push state tiap kali poller memperbarui (~1 dtk)

Jalankan:  uv run mikroclaw-web   (atau)  python -m mikroclaw.web
ENV opsional: MIKROCLAW_WEB_HOST (default 127.0.0.1), MIKROCLAW_WEB_PORT (8800).
Kredensial router tetap dari .env (lihat config.py). Read-only — tak ada write.
"""

from __future__ import annotations

import json
import os
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, AsyncIterator

from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import FileResponse, JSONResponse
from starlette.routing import Route
from sse_starlette.sse import EventSourceResponse

from ..client import RouterOSClient
from ..config import Config
from .poller import Poller

STATIC = Path(__file__).parent / "static"

_poller: Poller | None = None


async def index(request: Request) -> FileResponse:
    return FileResponse(STATIC / "index.html")


async def snapshot(request: Request) -> JSONResponse:
    return JSONResponse(_poller.snapshot() if _poller else {"error": "poller belum siap"})


async def stream(request: Request) -> EventSourceResponse:
    async def gen() -> AsyncIterator[dict[str, Any]]:
        assert _poller is not None
        # kirim state awal langsung agar laman tak menunggu tick pertama
        yield {"event": "state", "data": json.dumps(_poller.snapshot())}
        while True:
            if await request.is_disconnected():
                break
            await _poller.wait()
            yield {"event": "state", "data": json.dumps(_poller.snapshot())}

    return EventSourceResponse(gen())


@asynccontextmanager
async def lifespan(app: Starlette) -> AsyncIterator[None]:
    global _poller
    cfg = Config.from_env()  # raise jelas bila MIKROTIK_HOST belum di-set
    _poller = Poller(RouterOSClient(cfg))
    await _poller.start()
    try:
        yield
    finally:
        await _poller.stop()
        _poller = None


app = Starlette(
    lifespan=lifespan,
    routes=[
        Route("/", index),
        Route("/api/snapshot", snapshot),
        Route("/api/stream", stream),
    ],
)


def main() -> None:
    import uvicorn

    host = os.environ.get("MIKROCLAW_WEB_HOST", "127.0.0.1")
    port = int(os.environ.get("MIKROCLAW_WEB_PORT", "8800"))
    print(f"MikroCLAW Pulse → http://{host}:{port}")
    uvicorn.run("mikroclaw.web.app:app", host=host, port=port, log_level="info")


if __name__ == "__main__":
    main()
