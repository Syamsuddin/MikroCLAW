"""MikroCLAW Pulse — Starlette app + Server-Sent Events.

Endpoint:
  GET  /              -> laman dashboard (static/index.html)
  GET  /api/snapshot  -> state live sekali ambil (JSON) — debugging
  GET  /api/stream    -> SSE; push state tiap kali poller memperbarui (~1 dtk)
  POST /api/analyze   -> picu satu analisis lapis AI sekarang (Fase 2)
  POST /api/remediate -> eksekusi 1 aksi remediasi (Fase 3); butuh ALLOW_WRITE

Jalankan:  uv run mikroclaw-web   (atau)  python -m mikroclaw.web
ENV opsional: MIKROCLAW_WEB_HOST (default 127.0.0.1), MIKROCLAW_WEB_PORT (8800).
Lapis AI (Fase 2, opsional): ANTHROPIC_API_KEY, MIKROCLAW_AI_MODEL,
MIKROCLAW_AI_INTERVAL, MIKROCLAW_AI_MAX_TOKENS (lihat analyst.py).
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
from .actions import execute_action, validate_action
from .analyst import Analyst, AnalystConfig
from .poller import Poller

STATIC = Path(__file__).parent / "static"

_poller: Poller | None = None
_analyst: Analyst | None = None


async def index(request: Request) -> FileResponse:
    return FileResponse(STATIC / "index.html")


async def snapshot(request: Request) -> JSONResponse:
    return JSONResponse(_poller.snapshot() if _poller else {"error": "poller belum siap"})


async def analyze(request: Request) -> JSONResponse:
    """Picu satu analisis AI sekarang. Hasil dikirim lewat SSE saat siap."""
    if _analyst is None:
        return JSONResponse({"ok": False, "error": "lapis AI tidak aktif"}, status_code=503)
    _analyst.request_now()
    return JSONResponse({"ok": True})


def _action_in_proposal(action: dict[str, Any], ai: Any) -> bool:
    """Defense-in-depth: aksi hanya boleh dieksekusi bila persis salah satu yang
    DIUSULKAN lapis AI saat ini (cocok tipe + parameter wajib)."""
    if not isinstance(ai, dict):
        return False
    at = action.get("tipe")
    ap = action.get("parameter") or {}
    for r in ai.get("remediasi") or []:
        if r.get("tipe") != at:
            continue
        rp = r.get("parameter") or {}
        if all(str(ap.get(k, "")).strip() == str(rp.get(k, "")).strip()
               for k in ("address", "list", "service")):
            return True
    return False


async def remediate(request: Request) -> JSONResponse:
    """Eksekusi SATU aksi remediasi 1-klik (Fase 3) — di-gate ganda.

    Syarat: poller siap · MIKROCLAW_ALLOW_WRITE=true · aksi lolos allowlist ·
    aksi memang diusulkan AI saat ini.
    """
    if _poller is None:
        return JSONResponse({"ok": False, "error": "poller belum siap"}, status_code=503)
    if not _poller.state.get("allow_write"):
        return JSONResponse(
            {"ok": False, "error": "remediasi dinonaktifkan — set MIKROCLAW_ALLOW_WRITE=true lalu mulai ulang Pulse"},
            status_code=403,
        )
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"ok": False, "error": "body JSON tidak valid"}, status_code=400)
    action = body.get("action") if isinstance(body, dict) else None
    err = validate_action(action)
    if err:
        return JSONResponse({"ok": False, "error": err}, status_code=400)
    if not _action_in_proposal(action, _poller.state.get("ai")):
        return JSONResponse(
            {"ok": False, "error": "aksi tidak cocok dengan usulan AI terkini"},
            status_code=409,
        )
    result = await execute_action(_poller.ros, action)
    await _poller._notify()
    return JSONResponse(result, status_code=200 if result.get("ok") else 502)


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
    global _poller, _analyst
    cfg = Config.from_env()  # raise jelas bila MIKROTIK_HOST belum di-set
    _poller = Poller(RouterOSClient(cfg))
    _poller.state["allow_write"] = cfg.allow_write  # gerbang remediasi (Fase 3)
    await _poller.start()
    _analyst = Analyst(_poller, AnalystConfig.from_env())
    await _analyst.start()
    try:
        yield
    finally:
        await _analyst.stop()
        _analyst = None
        await _poller.stop()
        _poller = None


app = Starlette(
    lifespan=lifespan,
    routes=[
        Route("/", index),
        Route("/api/snapshot", snapshot),
        Route("/api/stream", stream),
        Route("/api/analyze", analyze, methods=["POST"]),
        Route("/api/remediate", remediate, methods=["POST"]),
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
