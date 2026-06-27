"""Remediasi 1-klik MikroCLAW Pulse (Fase 3) — allowlist aksi write yang aman.

Lapis AI hanya boleh MENGUSULKAN aksi dari daftar terbatas di bawah; eksekusi
nyata di-gate ganda:
  1. ``MIKROCLAW_ALLOW_WRITE=true`` (gerbang write yang sama dengan tool MCP), dan
  2. validasi server-side terhadap allowlist ini (tipe + parameter wajib).

Setiap aksi dipetakan ke SATU panggilan REST yang sudah terbukti (mirip tool
write MCP) dan diberi komentar audit ``added-by-pulse-ai`` agar mudah ditelusuri
& dihapus. Aksi di luar allowlist ditolak — bukan dieksekusi.
"""

from __future__ import annotations

from typing import Any

from ..client import RouterOSError

AUDIT = "added-by-pulse-ai"

# tipe aksi -> daftar parameter wajib (semua string).
ALLOWED: dict[str, list[str]] = {
    "blokir_ip": ["address"],
    "tambah_address_list": ["address", "list"],
    "nonaktifkan_service": ["service"],
}


def validate_action(action: Any) -> str | None:
    """Kembalikan pesan error bila aksi tidak valid; ``None`` bila lolos."""
    if not isinstance(action, dict):
        return "aksi harus berupa objek"
    tipe = action.get("tipe")
    if tipe not in ALLOWED:
        return f"tipe aksi tidak diizinkan: {tipe!r}"
    params = action.get("parameter") or {}
    if not isinstance(params, dict):
        return "parameter harus berupa objek"
    for req in ALLOWED[tipe]:
        val = params.get(req)
        if not val or not str(val).strip():
            return f"parameter wajib '{req}' kosong untuk aksi {tipe}"
    return None


async def execute_action(ros: Any, action: dict[str, Any]) -> dict[str, Any]:
    """Jalankan satu aksi remediasi tervalidasi. Pemanggil WAJIB sudah cek
    write-gate & ``validate_action`` lebih dulu."""
    tipe = action["tipe"]
    p = action.get("parameter") or {}
    comment = str(action.get("comment") or "").strip() or AUDIT
    try:
        if tipe == "blokir_ip":
            addr = str(p["address"]).strip()
            await ros.put("/ip/firewall/filter", {
                "chain": "forward", "action": "drop",
                "src-address": addr, "comment": comment,
            })
            return {"ok": True, "pesan": f"IP {addr} diblokir (chain forward, DROP)."}

        if tipe == "tambah_address_list":
            addr = str(p["address"]).strip()
            lst = str(p["list"]).strip()
            await ros.put("/ip/firewall/address-list", {
                "address": addr, "list": lst, "comment": comment,
            })
            return {"ok": True, "pesan": f"{addr} ditambahkan ke address-list '{lst}'."}

        if tipe == "nonaktifkan_service":
            name = str(p["service"]).strip().lower()
            rows = await ros.get("/ip/service")
            svc = next(
                (s for s in (rows or []) if str(s.get("name", "")).lower() == name),
                None,
            )
            if not svc:
                return {"ok": False, "pesan": f"service '{name}' tidak ditemukan"}
            await ros.patch(f"/ip/service/{svc.get('.id')}", {"disabled": "yes"})
            return {"ok": True, "pesan": f"service '{name}' dinonaktifkan."}
    except RouterOSError as exc:
        return {"ok": False, "pesan": f"gagal eksekusi: {exc}"}

    return {"ok": False, "pesan": f"tipe aksi tidak ditangani: {tipe}"}
