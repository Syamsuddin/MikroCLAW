"""Penyimpanan state lokal MikroCLAW (bukan di router) — dipakai fitur yang
butuh ingatan lintas-waktu: Chronicle (snapshot konfigurasi) & Replay (riwayat
telemetri).

Semua file ditaruh di bawah satu direktori state yang bisa diatur lewat ENV
``MIKROCLAW_STATE_DIR`` (default ``~/.mikroclaw``). Ini state milik operator di
mesin lokal — TIDAK pernah ditulis ke RouterOS, jadi tak melewati write-gate
(write-gate khusus mutasi konfigurasi router).
"""

from __future__ import annotations

import os
from pathlib import Path


def state_dir(*sub: str) -> Path:
    """Kembalikan path direktori state (dibuat bila belum ada).

    Args:
        sub: sub-direktori opsional, mis. ``state_dir("snapshots")``.
    """
    base = os.environ.get("MIKROCLAW_STATE_DIR", "").strip()
    root = Path(base).expanduser() if base else Path.home() / ".mikroclaw"
    path = root.joinpath(*sub) if sub else root
    path.mkdir(parents=True, exist_ok=True)
    return path
