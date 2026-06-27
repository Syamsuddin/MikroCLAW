"""MikroCLAW Pulse — lapis kecerdasan AI (Fase 2).

Memanggil Anthropic Messages API LANGSUNG via httpx (tanpa SDK, konsisten dengan
etos Fase 1 "tanpa dependency baru") untuk menarasikan kondisi jaringan,
mendeteksi anomali tanpa ambang tetap, mengkorelasikan akar masalah lintas
subsistem, dan menandai log penting.

Read-only: hanya MEMBACA snapshot dari Poller; tak pernah menyentuh router.
Output dipaksa terstruktur lewat tool-use (`tool_choice` + `strict`).

ENV:
  ANTHROPIC_API_KEY     wajib agar lapis AI aktif. Tanpa ini, Pulse tetap jalan
                        dan kartu AI menampilkan pesan "set key".
  MIKROCLAW_AI_MODEL    default 'claude-sonnet-4-6'.
  MIKROCLAW_AI_INTERVAL detik antar-analisis otomatis (default 60).
  MIKROCLAW_AI_MAX_TOKENS default 2048.
"""

from __future__ import annotations

import asyncio
import json
import os
import time
from dataclasses import dataclass
from typing import Any

import httpx

API_URL = "https://api.anthropic.com/v1/messages"
ANTHROPIC_VERSION = "2023-06-01"

TOOL_NAME = "laporkan_analisis"

SYSTEM_PROMPT = (
    "Anda analis NOC (Network Operations Center) untuk router MikroTik RouterOS. "
    "Anda menerima snapshot kondisi LIVE (read-only) beserta log terbaru. Tugas:\n"
    "- Nilai status keseluruhan: sehat / perhatian / kritis.\n"
    "- Deteksi anomali TANPA bergantung pada ambang tetap — pertimbangkan konteks "
    "(CPU/memori tinggi berkelanjutan vs lonjakan sesaat, interface WAN/penting down, "
    "RTT gateway/internet melonjak atau timeout, firewall drops melonjak, conntrack "
    "tinggi, sertifikat hampir kedaluwarsa, service berisiko terbuka tanpa batasan, "
    "error/critical pada log).\n"
    "- Korelasikan lintas-subsistem untuk menebak akar masalah, bukan sekadar daftar gejala.\n"
    "- Beri rekomendasi ringkas yang dapat ditindaklanjuti.\n"
    "- Tandai baris log yang paling penting.\n"
    "- PREDIKSI: pakai field 'forecast' (tren %/jam & ETA deterministik untuk "
    "cpu/mem/disk) untuk menarasikan ke mana arah kondisi. Hanya sebutkan prediksi "
    "yang didukung data; jangan mengarang.\n"
    "- REMEDIASI 1-KLIK: bila ADA masalah jelas yang bisa ditindak, usulkan aksi "
    "HANYA dari tipe berikut: 'blokir_ip' (parameter.address), 'tambah_address_list' "
    "(parameter.address + parameter.list), 'nonaktifkan_service' (parameter.service, "
    "mis. telnet/ftp/www/api yang terbuka tanpa batasan). Isi parameter yang tak "
    "dipakai dengan string kosong. KONSERVATIF: kosongkan array bila tak ada aksi "
    "yang benar-benar perlu. Aksi tetap perlu konfirmasi manusia & write-gate.\n"
    "Bersikap tenang dan akurat: JANGAN membunyikan alarm untuk kondisi normal. "
    "Bila semua baik, nyatakan demikian dengan status 'sehat' dan array kosong. "
    "SELALU jawab melalui tool '" + TOOL_NAME + "'. Tulis dalam Bahasa Indonesia."
)

_SEV = {"type": "string", "enum": ["info", "warning", "critical"]}

TOOL: dict[str, Any] = {
    "name": TOOL_NAME,
    "description": "Laporkan hasil analisis kondisi jaringan secara terstruktur.",
    "strict": True,
    "input_schema": {
        "type": "object",
        "properties": {
            "status": {"type": "string", "enum": ["sehat", "perhatian", "kritis"]},
            "ringkasan": {
                "type": "string",
                "description": "1-2 kalimat naratif kondisi jaringan saat ini.",
            },
            "anomali": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "severity": _SEV,
                        "judul": {"type": "string"},
                        "detail": {"type": "string"},
                    },
                    "required": ["severity", "judul", "detail"],
                    "additionalProperties": False,
                },
            },
            "rekomendasi": {"type": "array", "items": {"type": "string"}},
            "prediksi": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "metrik": {"type": "string"},
                        "arah": {"type": "string", "enum": ["naik", "turun", "stabil"]},
                        "horizon": {"type": "string"},
                        "detail": {"type": "string"},
                    },
                    "required": ["metrik", "arah", "horizon", "detail"],
                    "additionalProperties": False,
                },
            },
            "remediasi": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "tipe": {
                            "type": "string",
                            "enum": [
                                "blokir_ip",
                                "tambah_address_list",
                                "nonaktifkan_service",
                            ],
                        },
                        "judul": {"type": "string"},
                        "alasan": {"type": "string"},
                        "parameter": {
                            "type": "object",
                            "properties": {
                                "address": {"type": "string"},
                                "list": {"type": "string"},
                                "service": {"type": "string"},
                            },
                            "required": ["address", "list", "service"],
                            "additionalProperties": False,
                        },
                    },
                    "required": ["tipe", "judul", "alasan", "parameter"],
                    "additionalProperties": False,
                },
            },
            "log_penting": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "severity": _SEV,
                        "pesan": {"type": "string"},
                    },
                    "required": ["severity", "pesan"],
                    "additionalProperties": False,
                },
            },
        },
        "required": [
            "status", "ringkasan", "anomali", "rekomendasi",
            "prediksi", "remediasi", "log_penting",
        ],
        "additionalProperties": False,
    },
}


@dataclass(frozen=True)
class AnalystConfig:
    api_key: str
    model: str
    interval: float
    max_tokens: int

    @classmethod
    def from_env(cls) -> "AnalystConfig":
        return cls(
            api_key=os.environ.get("ANTHROPIC_API_KEY", "").strip(),
            model=os.environ.get("MIKROCLAW_AI_MODEL", "claude-sonnet-4-6").strip()
            or "claude-sonnet-4-6",
            interval=_as_float(os.environ.get("MIKROCLAW_AI_INTERVAL"), 60.0),
            max_tokens=_as_int(os.environ.get("MIKROCLAW_AI_MAX_TOKENS"), 2048),
        )


def _as_float(v: str | None, default: float) -> float:
    try:
        return float(v) if v and v.strip() else default
    except ValueError:
        return default


def _as_int(v: str | None, default: int) -> int:
    try:
        return int(v) if v and v.strip() else default
    except ValueError:
        return default


def _brief(snap: dict[str, Any]) -> dict[str, Any]:
    """Pangkas snapshot jadi ringkasan hemat-token untuk dikirim ke model."""
    sysd = snap.get("system", {}) or {}
    cnt = snap.get("counters", {}) or {}
    wan = snap.get("wan", {}) or {}
    health = snap.get("health", {}) or {}
    ifaces = snap.get("interfaces", []) or []
    clients = snap.get("clients", []) or []
    logs = snap.get("logs", []) or []

    notable_if = [
        i
        for i in ifaces
        if not i.get("disabled")
        and (
            not i.get("running")
            or i.get("rx_error")
            or i.get("tx_error")
            or (i.get("rx_bps", 0) + i.get("tx_bps", 0)) > 0
        )
    ][:12]

    return {
        "connected": snap.get("connected"),
        "system": {
            k: sysd.get(k)
            for k in (
                "identity", "version", "board", "uptime", "cpu_load",
                "cpu_count", "mem_used_pct", "disk_used_pct", "license",
            )
        },
        "health": {
            k: (v.get("value") if isinstance(v, dict) else v)
            for k, v in health.items()
        },
        "counters": cnt,
        "forecast": snap.get("forecast"),
        "wan": {
            k: wan.get(k)
            for k in (
                "iface", "public_address", "ddns", "gateway",
                "gateway_ping_ms", "internet_ping_ms", "rx_bps", "tx_bps",
            )
        },
        "interfaces": [
            {
                "name": i.get("name"), "type": i.get("type"),
                "running": i.get("running"), "rx_bps": i.get("rx_bps"),
                "tx_bps": i.get("tx_bps"), "rx_error": i.get("rx_error"),
                "tx_error": i.get("tx_error"), "speed": i.get("speed"),
            }
            for i in notable_if
        ],
        "clients_total": cnt.get("clients_total"),
        "top_clients": [
            {
                "ip": c.get("ip"), "host": c.get("host"), "kind": c.get("kind"),
                "signal": c.get("signal"), "rx_bps": c.get("rx_bps"),
                "tx_bps": c.get("tx_bps"),
            }
            for c in clients[:5]
        ],
        "services": snap.get("services", []),
        "recent_logs": [
            {
                "t": l.get("time"), "sev": l.get("severity"),
                "topics": l.get("topics"), "msg": l.get("message"),
            }
            for l in logs[-25:]
        ],
    }


def _disabled(reason: str) -> dict[str, Any]:
    return {"enabled": False, "ok": False, "ts": time.time(), "error": reason}


def _error(reason: str) -> dict[str, Any]:
    return {"enabled": True, "ok": False, "ts": time.time(), "error": reason}


class Analyst:
    """Loop analisis AI periodik + on-demand, menulis hasil ke state poller."""

    def __init__(self, poller: Any, cfg: AnalystConfig) -> None:
        self.poller = poller
        self.cfg = cfg
        self._client = httpx.AsyncClient(timeout=45.0)
        self._trigger = asyncio.Event()
        self._running = False
        self._task: asyncio.Task[Any] | None = None

    # ------------------------------------------------------------------ lifecycle
    async def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._task = asyncio.create_task(self._loop(), name="pulse-ai")

    async def stop(self) -> None:
        self._running = False
        self._trigger.set()
        if self._task:
            self._task.cancel()
            await asyncio.gather(self._task, return_exceptions=True)
            self._task = None
        await self._client.aclose()

    def request_now(self) -> None:
        """Picu satu analisis segera (tombol 'Analisa sekarang')."""
        self._trigger.set()

    # ------------------------------------------------------------------ loop
    async def _loop(self) -> None:
        if not self.cfg.api_key:
            await self.poller.push_ai(
                _disabled("ANTHROPIC_API_KEY belum di-set — lapis AI nonaktif.")
            )
            return
        await asyncio.sleep(5)  # beri waktu poller mengisi snapshot pertama
        while self._running:
            try:
                ai = await self.analyze(self.poller.snapshot())
            except Exception as exc:  # apa pun, jangan matikan loop
                ai = _error(str(exc))
            await self.poller.push_ai(ai)
            self._trigger.clear()
            try:
                await asyncio.wait_for(self._trigger.wait(), timeout=self.cfg.interval)
            except asyncio.TimeoutError:
                pass

    # ------------------------------------------------------------------ inference
    async def analyze(self, snap: dict[str, Any]) -> dict[str, Any]:
        if not self.cfg.api_key:
            return _disabled("ANTHROPIC_API_KEY belum di-set — lapis AI nonaktif.")

        brief = json.dumps(_brief(snap), ensure_ascii=False, default=str)
        payload = {
            "model": self.cfg.model,
            "max_tokens": self.cfg.max_tokens,
            "system": SYSTEM_PROMPT,
            "tools": [TOOL],
            "tool_choice": {"type": "tool", "name": TOOL_NAME},
            "messages": [
                {
                    "role": "user",
                    "content": (
                        "Snapshot kondisi router (JSON):\n```json\n"
                        + brief
                        + "\n```\nAnalisa dan laporkan via tool."
                    ),
                }
            ],
        }
        headers = {
            "x-api-key": self.cfg.api_key,
            "anthropic-version": ANTHROPIC_VERSION,
            "content-type": "application/json",
        }
        try:
            resp = await self._client.post(API_URL, headers=headers, json=payload)
        except httpx.HTTPError as exc:
            return _error(f"gagal menghubungi Anthropic API: {exc}")

        if resp.status_code >= 400:
            detail = resp.text
            try:
                obj = resp.json()
                detail = obj.get("error", {}).get("message", detail)
            except ValueError:
                pass
            hint = " (cek ANTHROPIC_API_KEY)" if resp.status_code in (401, 403) else ""
            return _error(f"Anthropic API {resp.status_code}: {detail}{hint}")

        data = resp.json()
        block = next(
            (b for b in data.get("content", []) if b.get("type") == "tool_use"), None
        )
        if not block or not isinstance(block.get("input"), dict):
            return _error(f"respons tanpa tool_use (stop={data.get('stop_reason')})")

        result = block["input"]
        usage = data.get("usage", {}) or {}
        return {
            "enabled": True,
            "ok": True,
            "ts": time.time(),
            "model": data.get("model", self.cfg.model),
            "status": result.get("status", ""),
            "ringkasan": result.get("ringkasan", ""),
            "anomali": result.get("anomali", []),
            "rekomendasi": result.get("rekomendasi", []),
            "prediksi": result.get("prediksi", []),
            "remediasi": result.get("remediasi", []),
            "log_penting": result.get("log_penting", []),
            "usage": {
                "in": usage.get("input_tokens"),
                "out": usage.get("output_tokens"),
            },
        }
