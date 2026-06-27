"""MikroCLAW Sentinel — sidik-jari perilaku per-perangkat (murni, tanpa I/O).

Membangun profil perilaku tiap klien dari connection-tracking RouterOS lalu
menilai deviasi DALAM KONTEKS tipe perangkat (ditebak dari vendor OUI/hostname).
Tujuannya menangkap perangkat yang dikompromi — kamera/IoT yang direkrut botnet,
host penambang kripto, mesin yang memindai jaringan — TANPA signature/threshold
statis. Kelas perangkat dipakai sebagai konteks: kamera CCTV yang tiba-tiba
membuka ratusan koneksi Telnet keluar jauh lebih mencurigakan daripada sebuah PC.

Murni seperti `roles.py`: menerima baris `/ip/firewall/connection` + metadata
klien (ip/mac/vendor/host), mengembalikan temuan terstruktur. Pengumpulan data &
narasi device-appropriate dilakukan di tool `analyze_client_behavior` pada
server.py; modul ini hanya menalar.
"""

from __future__ import annotations

from typing import Any

# Port keluar yang menjadi sinyal kuat perilaku jahat bila fan-out tinggi.
SUSPECT_PORTS: dict[int, str] = {
    23: "Telnet (rekrutmen botnet Mirai/IoT)",
    2323: "Telnet alt (botnet IoT)",
    22: "SSH (pemindaian/brute-force)",
    3389: "RDP (pemindaian/lateral)",
    445: "SMB (worm/lateral movement)",
    139: "NetBIOS/SMB (worm)",
    1433: "MSSQL (pemindaian)",
    25: "SMTP (bot spam)",
    465: "SMTPS (bot spam)",
    587: "SMTP submission (bot spam)",
}

# Port pool penambang kripto yang umum (heuristik).
MINER_PORTS: set[int] = {3333, 4444, 5555, 7777, 8333, 9999, 14444, 45560, 45700, 3334}

# Ambang fan-out (jumlah tujuan unik dari satu host) yang menandai pemindaian.
FANOUT_SCAN = 40
FANOUT_HIGH = 100


def device_class(vendor: str = "", host: str = "") -> str:
    """Tebak kelas perangkat dari vendor OUI & hostname (untuk konteks penilaian)."""
    v = (vendor or "").lower()
    h = (host or "").lower()
    blob = f"{v} {h}"
    if any(k in blob for k in ("hikvision", "dahua", "cctv", "camera", "ipcam", "nvr", "dvr")):
        return "kamera/CCTV"
    if any(k in blob for k in ("mikrotik", "cisco", "tp-link", "netgear", "asus", "ubiquiti", "router", "ap-")):
        return "router/AP"
    if any(k in blob for k in ("synology", "western digital", "qnap", "nas")):
        return "NAS/server"
    if any(k in blob for k in ("raspberry", "esp", "tuya", "sonoff", "smart", "iot", "google", "alexa", "echo")):
        return "IoT/embedded"
    if any(k in blob for k in ("apple", "iphone", "ipad", "samsung", "xiaomi", "android", "redmi", "oppo", "vivo")):
        return "ponsel/tablet"
    if any(k in blob for k in ("vmware", "qemu", "kvm", "parallels", "vbox")):
        return "VM/server"
    return "tak dikenal"


def _split_ip_port(value: Any) -> tuple[str, int | None]:
    """Pisah field RouterOS 'ip:port' (mendukung IPv6 [..]:port secara kasar)."""
    s = str(value or "").strip()
    if not s:
        return "", None
    if s.startswith("["):  # IPv6 [addr]:port
        host, _, port = s[1:].partition("]")
        port = port.lstrip(":")
        return host, int(port) if port.isdigit() else None
    if s.count(":") == 1:  # IPv4:port
        host, _, port = s.partition(":")
        return host, int(port) if port.isdigit() else None
    return s, None  # IPv6 tanpa port, atau ip polos


def fingerprint_ip(conns: list[dict[str, Any]], ip: str) -> dict[str, Any]:
    """Bangun profil perilaku koneksi KELUAR dari satu IP klien."""
    out_dsts: set[str] = set()
    out_ports: dict[int, int] = {}
    protocols: dict[str, int] = {}
    total_out = 0
    for c in conns or []:
        src_ip, _ = _split_ip_port(c.get("src-address"))
        if src_ip != ip:
            continue
        total_out += 1
        dst_ip, dst_port = _split_ip_port(c.get("dst-address"))
        if dst_ip:
            out_dsts.add(dst_ip)
        if dst_port is not None:
            out_ports[dst_port] = out_ports.get(dst_port, 0) + 1
        proto = str(c.get("protocol", "")).strip().lower() or "?"
        protocols[proto] = protocols.get(proto, 0) + 1

    top_ports = sorted(out_ports.items(), key=lambda kv: -kv[1])[:8]
    fanout = len(out_dsts)
    return {
        "ip": ip,
        "koneksi_keluar": total_out,
        "tujuan_unik": fanout,
        "port_unik": len(out_ports),
        "top_port": [{"port": p, "hits": n} for p, n in top_ports],
        "protokol": protocols,
        "_ports": out_ports,
    }


def score_behavior(profile: dict[str, Any], dev_class: str) -> list[dict[str, Any]]:
    """Nilai profil → daftar temuan {severity,judul,detail} berkonteks perangkat."""
    findings: list[dict[str, Any]] = []
    ports: dict[int, int] = profile.get("_ports", {})
    fanout = profile.get("tujuan_unik", 0)
    total = profile.get("koneksi_keluar", 0)
    sensitif = dev_class in ("kamera/CCTV", "IoT/embedded", "NAS/server")

    def add(sev: str, judul: str, detail: str) -> None:
        findings.append({"severity": sev, "judul": judul, "detail": detail})

    # 1) Telnet/botnet — tujuan Telnet banyak = perekrutan IoT
    telnet_hits = ports.get(23, 0) + ports.get(2323, 0)
    if telnet_hits >= 5:
        sev = "critical" if (sensitif or telnet_hits >= 20) else "warning"
        add(sev, "Pola perekrutan botnet (Telnet keluar)",
            f"{telnet_hits} koneksi keluar ke port 23/2323. "
            + ("Perangkat kelas " + dev_class + " yang memindai Telnet adalah ciri kuat "
               "perangkat terinfeksi botnet IoT (mis. Mirai)." if sensitif else
               "Lazimnya host bersih tak memindai Telnet massal."))

    # 2) Penambang kripto — koneksi ke port pool penambang
    miner_hits = sum(n for p, n in ports.items() if p in MINER_PORTS)
    if miner_hits >= 3:
        add("warning", "Dugaan penambang kripto",
            f"{miner_hits} koneksi ke port pool penambang yang dikenal "
            f"({', '.join(str(p) for p in ports if p in MINER_PORTS)}).")

    # 3) Spam bot — SMTP keluar dari perangkat non-mail
    smtp_hits = ports.get(25, 0) + ports.get(465, 0) + ports.get(587, 0)
    if smtp_hits >= 5 and dev_class not in ("NAS/server", "VM/server"):
        add("warning", "Dugaan bot spam (SMTP keluar)",
            f"{smtp_hits} koneksi SMTP keluar dari perangkat kelas {dev_class} "
            "yang seharusnya tak mengirim email langsung.")

    # 4) Lateral movement / pemindaian SMB-RDP
    lateral = ports.get(445, 0) + ports.get(139, 0) + ports.get(3389, 0) + ports.get(1433, 0)
    if lateral >= 10:
        add("warning", "Pemindaian internal (SMB/RDP/MSSQL)",
            f"{lateral} koneksi ke port 445/139/3389/1433 — ciri worm/lateral movement.")

    # 5) Fan-out tinggi — pemindaian umum
    if fanout >= FANOUT_HIGH:
        sev = "critical" if sensitif else "warning"
        add(sev, "Fan-out sangat tinggi (pemindaian jaringan)",
            f"{fanout} tujuan unik dari satu host. "
            + (f"Perangkat {dev_class} normal hanya bicara dengan segelintir tujuan; "
               "ini sangat tidak wajar." if sensitif else
               "Bisa P2P/CDN yang sah, tapi juga ciri pemindai/bot — perlu dicek."))
    elif fanout >= FANOUT_SCAN:
        add("info", "Fan-out di atas normal",
            f"{fanout} tujuan unik. Wajar untuk PC/ponsel aktif; "
            f"perlu perhatian bila ini perangkat {dev_class}.")

    # 6) Perangkat sensitif dengan banyak port unik = anomali profil
    if sensitif and profile.get("port_unik", 0) >= 15:
        add("warning", "Profil port tak wajar untuk perangkat sederhana",
            f"Perangkat kelas {dev_class} menghubungi {profile['port_unik']} port "
            "berbeda — perangkat semacam ini biasanya hanya memakai sedikit port tetap.")

    return findings


def _severity_rank(sev: str) -> int:
    return {"critical": 0, "warning": 1, "info": 2}.get(sev, 3)


def analyze_clients(
    conns: list[dict[str, Any]],
    clients: list[dict[str, Any]],
) -> dict[str, Any]:
    """Profilkan & nilai semua klien; kembalikan temuan terurut tingkat keparahan.

    Args:
        conns: baris `/ip/firewall/connection`.
        clients: daftar klien — tiap item {ip, mac, vendor, host}.
    """
    laporan: list[dict[str, Any]] = []
    worst = "info"
    for cl in clients or []:
        ip = str(cl.get("ip", "")).strip()
        if not ip:
            continue
        dev = device_class(cl.get("vendor", ""), cl.get("host", ""))
        prof = fingerprint_ip(conns, ip)
        if prof["koneksi_keluar"] == 0:
            continue
        findings = score_behavior(prof, dev)
        prof.pop("_ports", None)
        if not findings:
            continue
        sev = min((f["severity"] for f in findings), key=_severity_rank)
        if _severity_rank(sev) < _severity_rank(worst):
            worst = sev
        laporan.append({
            "ip": ip,
            "mac": cl.get("mac", ""),
            "host": cl.get("host", ""),
            "vendor": cl.get("vendor", ""),
            "kelas_perangkat": dev,
            "keparahan": sev,
            "profil": prof,
            "temuan": findings,
        })

    laporan.sort(key=lambda r: (_severity_rank(r["keparahan"]), -r["profil"]["tujuan_unik"]))
    return {
        "total_klien_dianalisis": len([c for c in (clients or []) if c.get("ip")]),
        "klien_mencurigakan": len(laporan),
        "keparahan_tertinggi": worst if laporan else "bersih",
        "laporan": laporan,
        "ringkasan": (
            f"{len(laporan)} perangkat menunjukkan perilaku mencurigakan "
            f"(tertinggi: {worst})." if laporan else
            "Tidak ada perilaku perangkat yang mencurigakan terdeteksi."
        ),
    }
