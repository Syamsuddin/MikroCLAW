"""Konfigurasi MikroCLAW dari environment / file .env."""

from __future__ import annotations

import os
from dataclasses import dataclass

from dotenv import load_dotenv

# Muat .env dari cwd ke atas (sekali, saat import).
load_dotenv()


def _as_bool(value: str | None, default: bool) -> bool:
    if value is None or value.strip() == "":
        return default
    return value.strip().lower() in ("1", "true", "yes", "on")


@dataclass(frozen=True)
class Config:
    host: str
    user: str
    password: str
    port: int
    use_tls: bool
    verify_tls: bool
    allow_write: bool
    timeout: float

    @classmethod
    def from_env(cls) -> "Config":
        host = os.environ.get("MIKROTIK_HOST", "").strip()
        if not host:
            raise RuntimeError(
                "MIKROTIK_HOST belum di-set. Salin .env.example ke .env dan isi "
                "host/kredensial router."
            )
        use_tls = _as_bool(os.environ.get("MIKROTIK_USE_TLS"), True)
        default_port = 443 if use_tls else 80
        return cls(
            host=host,
            user=os.environ.get("MIKROTIK_USER", "admin"),
            password=os.environ.get("MIKROTIK_PASSWORD", ""),
            port=int(os.environ.get("MIKROTIK_PORT", str(default_port))),
            use_tls=use_tls,
            verify_tls=_as_bool(os.environ.get("MIKROTIK_VERIFY_TLS"), False),
            allow_write=_as_bool(os.environ.get("MIKROCLAW_ALLOW_WRITE"), False),
            timeout=float(os.environ.get("MIKROTIK_TIMEOUT", "10")),
        )

    @property
    def base_url(self) -> str:
        scheme = "https" if self.use_tls else "http"
        return f"{scheme}://{self.host}:{self.port}/rest"
