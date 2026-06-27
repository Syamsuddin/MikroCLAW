"""MikroCLAW Concierge — penerjemah telemetri jaringan → keputusan bisnis (murni).

Basis MikroTik terbesar di Indonesia (RT-RW net, hotspot desa, warnet, UMKM)
dikelola pemilik non-teknis. Modul ini mengubah angka teknis menjadi sinyal
bisnis: berapa pelanggan, siapa aktif, akun mana menganggur (bisa ditagih/dicabut),
perangkat tak terotentikasi (dugaan pencurian bandwidth), serta utilisasi WAN vs
kapasitas paket (kapan harus upgrade). Engine ini deterministik & murni; narasi
ramah-awam + estimasi monetisasi dirangkai Claude di atas sinyal ini.

`business_report(ev)` menerima bukti GET REST (ppp/secret+active+profile, hotspot
user+active, dhcp lease, arp, queue) plus kapasitas paket WAN, dan mengembalikan
ringkasan terstruktur. Tool `business_report` di server.py yang mengumpulkan data.
"""

from __future__ import annotations

import re
from typing import Any


def _truthy(v: Any) -> bool:
    return str(v).strip().lower() in ("true", "yes", "1")


def _as_list(v: Any) -> list[dict[str, Any]]:
    if isinstance(v, list):
        return [r for r in v if isinstance(r, dict)]
    if isinstance(v, dict):
        return [v]
    return []


def parse_speed_mbps(value: Any) -> float | None:
    """Parse string kecepatan ('1Gbps', '100Mbps', '1000Mbit') -> Mbps."""
    if value is None:
        return None
    s = str(value).strip().lower().replace("-", "")
    m = re.search(r"([\d.]+)\s*(g|m|k)?", s)
    if not m:
        return None
    try:
        num = float(m.group(1))
    except ValueError:
        return None
    unit = m.group(2)
    if unit == "g":
        return num * 1000.0
    if unit == "k":
        return num / 1000.0
    return num  # default Mbps


def _mbps(bps: Any) -> float:
    try:
        return round(float(bps) / 1_000_000.0, 2)
    except (TypeError, ValueError):
        return 0.0


def _util_level(pct: float) -> str:
    if pct >= 90:
        return "kritis"
    if pct >= 75:
        return "tinggi"
    if pct >= 50:
        return "sedang"
    return "lega"


def business_report(ev: dict[str, Any]) -> dict[str, Any]:
    """Susun ringkasan bisnis dari bukti jaringan.

    Args:
        ev: kunci yang dipakai — ``ppp_secrets``, ``ppp_active``, ``ppp_profiles``,
            ``hotspot_users``, ``hotspot_active``, ``leases``, ``arp``, ``queues``,
            ``wan`` ({rx_bps, tx_bps, speed}), ``plan_down_mbps``, ``plan_up_mbps``,
            ``clients`` (opsional, untuk top talkers: {ip,host,rx_bps,tx_bps}).
    """
    secrets = _as_list(ev.get("ppp_secrets"))
    active = _as_list(ev.get("ppp_active"))
    hs_users = _as_list(ev.get("hotspot_users"))
    hs_active = _as_list(ev.get("hotspot_active"))

    active_names = {str(a.get("name", "")).strip() for a in active if a.get("name")}
    active_ppp_ips = {str(a.get("address", "")).strip() for a in active if a.get("address")}
    active_hs_ips = {str(a.get("address", "")).strip() for a in hs_active if a.get("address")}

    # ----- pelanggan PPPoE -----
    disabled = [s for s in secrets if _truthy(s.get("disabled"))]
    never = [s for s in secrets
             if not _truthy(s.get("disabled"))
             and str(s.get("name", "")).strip() not in active_names
             and not str(s.get("last-logged-out", "")).strip()]
    profile_mix: dict[str, int] = {}
    for s in secrets:
        prof = str(s.get("profile", "") or "default")
        profile_mix[prof] = profile_mix.get(prof, 0) + 1

    pelanggan = {
        "terdaftar_pppoe": len(secrets),
        "aktif_sekarang": len(active),
        "dinonaktifkan": len(disabled),
        "belum_pernah_konek": len(never),
        "hotspot_terdaftar": len(hs_users),
        "hotspot_aktif": len(hs_active),
        "distribusi_profil": profile_mix,
        "akun_dinonaktifkan": [str(s.get("name", "")) for s in disabled][:20],
        "akun_belum_konek": [str(s.get("name", "")) for s in never][:20],
    }

    # ----- dugaan perangkat tak terotentikasi (pencurian bandwidth) -----
    auth_ips = active_ppp_ips | active_hs_ips
    static_lease_ips = {
        str(le.get("address", "")).strip()
        for le in _as_list(ev.get("leases"))
        if not _truthy(le.get("dynamic"))
    }
    suspects: list[dict[str, str]] = []
    seen: set[str] = set()
    for le in _as_list(ev.get("leases")):
        if not _truthy(le.get("dynamic")):
            continue
        ip = str(le.get("active-address") or le.get("address", "")).strip()
        if not ip or ip in seen:
            continue
        if ip in auth_ips or ip in static_lease_ips:
            continue
        # hanya relevan bila operator memang berbasis PPPoE/hotspot
        if not (active or hs_active or secrets or hs_users):
            continue
        seen.add(ip)
        suspects.append({
            "ip": ip,
            "mac": str(le.get("active-mac-address") or le.get("mac-address", "")),
            "host": str(le.get("host-name") or le.get("comment", "")),
        })

    # ----- utilisasi WAN vs kapasitas paket -----
    wan = ev.get("wan", {}) or {}
    down_mbps = _mbps(wan.get("rx_bps"))
    up_mbps = _mbps(wan.get("tx_bps"))
    cap_down = ev.get("plan_down_mbps") or parse_speed_mbps(wan.get("speed"))
    cap_up = ev.get("plan_up_mbps") or cap_down
    utilisasi: dict[str, Any] = {
        "download_mbps": down_mbps, "upload_mbps": up_mbps,
        "kapasitas_down_mbps": cap_down, "kapasitas_up_mbps": cap_up,
    }
    if cap_down:
        pct = round(down_mbps / cap_down * 100, 1)
        utilisasi["download_pct"] = pct
        utilisasi["level"] = _util_level(pct)
        utilisasi["headroom_mbps"] = round(max(0.0, cap_down - down_mbps), 2)
    else:
        utilisasi["level"] = "tak diketahui (kapasitas paket belum diisi)"

    # ----- top talkers -----
    clients = _as_list(ev.get("clients"))
    if not clients:
        # turunkan dari queue 'rate' bila ada (format 'up/down' bps)
        for q in _as_list(ev.get("queues")):
            rate = str(q.get("rate", "")).strip()
            if "/" in rate:
                up_s, _, down_s = rate.partition("/")
                clients.append({
                    "ip": (q.get("target") or "").split("/")[0],
                    "host": q.get("name", ""),
                    "tx_bps": _to_int(up_s), "rx_bps": _to_int(down_s),
                })
    talkers = sorted(
        clients,
        key=lambda c: -((c.get("rx_bps") or 0) + (c.get("tx_bps") or 0)),
    )[:5]
    top_talkers = [{
        "ip": c.get("ip", ""), "host": c.get("host", ""),
        "download_mbps": _mbps(c.get("rx_bps")), "upload_mbps": _mbps(c.get("tx_bps")),
    } for c in talkers if (c.get("rx_bps") or c.get("tx_bps"))]

    # ----- saran bisnis deterministik (Claude memperkaya jadi narasi awam) -----
    saran: list[dict[str, str]] = []
    if utilisasi.get("download_pct", 0) >= 85:
        saran.append({"prioritas": "tinggi",
                      "saran": f"Utilisasi unduh {utilisasi['download_pct']}% dari kapasitas — "
                      "pelanggan kemungkinan mengeluh lambat saat jam sibuk. "
                      "Pertimbangkan upgrade paket WAN atau tambah load-balance."})
    if pelanggan["dinonaktifkan"] > 0:
        saran.append({"prioritas": "sedang",
                      "saran": f"{pelanggan['dinonaktifkan']} akun PPPoE dinonaktifkan masih "
                      "tersimpan — tagih tunggakan atau cabut untuk merapikan basis pelanggan."})
    if pelanggan["belum_pernah_konek"] > 0:
        saran.append({"prioritas": "rendah",
                      "saran": f"{pelanggan['belum_pernah_konek']} akun belum pernah konek — "
                      "pasang baru yang belum aktif atau akun terlupakan."})
    if suspects:
        saran.append({"prioritas": "tinggi",
                      "saran": f"{len(suspects)} perangkat memakai jaringan tanpa otentikasi "
                      "PPPoE/hotspot — dugaan pemakaian tak tertagih; verifikasi & amankan."})

    return {
        "pelanggan": pelanggan,
        "utilisasi_wan": utilisasi,
        "perangkat_tak_terotentikasi": {
            "jumlah": len(suspects), "daftar": suspects[:20],
        },
        "top_talkers": top_talkers,
        "saran": saran,
        "ringkasan": (
            f"{pelanggan['aktif_sekarang']}/{pelanggan['terdaftar_pppoe']} pelanggan PPPoE aktif"
            + (f", utilisasi WAN {utilisasi['download_pct']}% ({utilisasi['level']})"
               if "download_pct" in utilisasi else "")
            + (f", {len(suspects)} perangkat tak terotentikasi" if suspects else "")
            + "."
        ),
    }


def _to_int(v: Any) -> int:
    try:
        return int(float(v))
    except (TypeError, ValueError):
        return 0
