"""Deteksi peran/fungsi MikroTik dari bukti konfigurasi.

`classify_roles(ev)` adalah fungsi MURNI (tanpa I/O): menerima dict berisi hasil
GET beberapa endpoint REST (tiap nilai biasanya list; `/ip/dns` & `/ip/proxy`
berupa dict/objek) lalu mengembalikan daftar peran yang TERDETEKSI beserta
tingkat keyakinan + bukti. Karena murni, mudah diuji tanpa router.

Pengumpulan bukti (banyak panggilan REST) dilakukan di tool `detect_roles`
pada server.py — modul ini hanya menalar.
"""

from __future__ import annotations

from typing import Any


def _truthy(v: Any) -> bool:
    return str(v).strip().lower() in ("true", "yes", "1")


def _as_list(v: Any) -> list[dict[str, Any]]:
    if isinstance(v, list):
        return [r for r in v if isinstance(r, dict)]
    if isinstance(v, dict):
        return [v]
    return []


def _enabled(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Baris yang tidak disabled (RouterOS pakai disabled=true/false)."""
    return [r for r in rows if not _truthy(r.get("disabled"))]


def _first(v: Any) -> dict[str, Any]:
    rows = _as_list(v)
    return rows[0] if rows else {}


def classify_roles(ev: dict[str, Any]) -> dict[str, Any]:
    """Kembalikan ringkasan peran router dari bukti `ev`."""
    g = lambda k: _as_list(ev.get(k))  # noqa: E731
    roles: list[dict[str, Any]] = []

    def add(nama: str, kategori: str, keyakinan: str, bukti: list[str]) -> None:
        roles.append({
            "nama": nama, "kategori": kategori,
            "keyakinan": keyakinan, "bukti": [b for b in bukti if b],
        })

    # ---- Routing & NAT --------------------------------------------------
    nat = _enabled(g("nat"))
    masq = [r for r in nat if str(r.get("action")) in ("masquerade", "src-nat")]
    dnat = [r for r in nat if str(r.get("action")) == "dst-nat"]
    if masq:
        add("Gateway internet (NAT/masquerade)", "Routing & NAT", "tinggi",
            [f"{len(masq)} aturan srcnat/masquerade"])
    if dnat:
        add("Port forwarding (DSTNAT)", "Routing & NAT", "tinggi",
            [f"{len(dnat)} aturan dstnat"])

    routes = g("routes")
    static_routes = [r for r in routes if _truthy(r.get("static"))]
    if static_routes:
        add("Router statis", "Routing & NAT", "sedang",
            [f"{len(static_routes)} route statis"])

    if g("bgp"):
        add("BGP router", "Routing dinamis", "tinggi",
            [f"{len(g('bgp'))} sesi/peer BGP"])
    if g("ospf"):
        add("OSPF router", "Routing dinamis", "tinggi",
            [f"{len(g('ospf'))} entri OSPF"])

    # ---- Firewall -------------------------------------------------------
    filt = _enabled(g("filter"))
    if filt:
        input_protect = [r for r in filt if str(r.get("chain")) == "input"
                         and str(r.get("action")) in ("drop", "reject")]
        key = "tinggi" if input_protect else "sedang"
        add("Firewall (stateful filter)", "Keamanan", key,
            [f"{len(filt)} aturan filter aktif",
             f"{len(input_protect)} proteksi chain input" if input_protect else ""])

    # ---- Switching / L2 -------------------------------------------------
    bridges = g("bridges")
    ports = g("bridge_ports")
    if bridges:
        vlan_filt = any(_truthy(b.get("vlan-filtering")) for b in bridges)
        key = "tinggi" if len(ports) >= 3 else "sedang"
        add("Switch / bridge L2", "Switching", key,
            [f"{len(bridges)} bridge, {len(ports)} port",
             "VLAN filtering aktif" if vlan_filt else ""])
    if g("vlans"):
        add("VLAN trunk (802.1Q)", "Switching", "tinggi",
            [f"{len(g('vlans'))} interface VLAN"])

    # ---- Wireless -------------------------------------------------------
    wifi = _enabled(g("wifi"))
    def _mode(w: dict[str, Any]) -> str:
        cfg = w.get("configuration")
        if isinstance(cfg, dict) and cfg.get("mode"):
            return str(cfg.get("mode")).lower()
        return str(w.get("mode", "")).lower()
    if wifi:
        modes = [_mode(w) for w in wifi]
        is_ap = any("ap" in m for m in modes)
        is_sta = any("station" in m or "sta" in m for m in modes)
        if is_ap:
            add("Access Point WiFi", "Wireless", "tinggi",
                [f"{len(wifi)} interface WiFi (mode AP)"])
        elif is_sta:
            add("WiFi station/klien (uplink)", "Wireless", "sedang",
                [f"{len(wifi)} interface WiFi (mode station)"])
        else:
            add("WiFi (interface aktif)", "Wireless", "sedang",
                [f"{len(wifi)} interface WiFi"])
    if _enabled(g("capsman")):
        add("CAPsMAN — kontroler WiFi terpusat", "Wireless", "tinggi",
            ["manager CAPsMAN/wifiwave2 aktif"])
    if g("hotspot"):
        add("Hotspot gateway (captive portal)", "Layanan akses", "tinggi",
            [f"{len(g('hotspot'))} server hotspot"])

    # ---- Broadband / akses ---------------------------------------------
    if _enabled(g("pppoe_server")):
        sesi = [s for s in g("ppp_active")
                if "pppoe" in str(s.get("service", "")).lower()]
        add("PPPoE server (BRAS/akses pelanggan)", "Broadband", "tinggi",
            [f"{len(_enabled(g('pppoe_server')))} layanan PPPoE server",
             f"{len(sesi)} sesi PPPoE aktif" if sesi else ""])
    if g("pppoe_client"):
        add("PPPoE client (uplink WAN)", "Broadband", "tinggi",
            [f"{len(g('pppoe_client'))} pppoe-client"])
    if _enabled(g("dhcp_client")):
        add("DHCP client (uplink WAN)", "Broadband", "sedang",
            [f"{len(_enabled(g('dhcp_client')))} dhcp-client"])

    # ---- Layanan jaringan ----------------------------------------------
    if _enabled(g("dhcp_server")):
        add("DHCP server (LAN)", "Layanan jaringan", "tinggi",
            [f"{len(_enabled(g('dhcp_server')))} DHCP server"])
    dns = _first(ev.get("dns"))
    if _truthy(dns.get("allow-remote-requests")):
        add("DNS resolver (allow-remote-requests)", "Layanan jaringan", "tinggi",
            ["DNS melayani permintaan dari jaringan"])
    proxy = _first(ev.get("proxy"))
    if _truthy(proxy.get("enabled")):
        add("Web proxy", "Layanan jaringan", "tinggi", ["/ip/proxy enabled"])
    if g("container"):
        add("Container host (RouterOS v7)", "Layanan jaringan", "tinggi",
            [f"{len(g('container'))} kontainer"])

    # ---- VPN / tunnel ---------------------------------------------------
    wg = g("wireguard")
    if wg:
        add("VPN WireGuard", "VPN & tunnel", "tinggi",
            [f"{len(wg)} interface WireGuard, {len(g('wg_peers'))} peer"])
    if g("ipsec_peer") or g("ipsec_active"):
        add("VPN IPsec", "VPN & tunnel", "tinggi",
            [f"{len(g('ipsec_peer'))} peer, {len(g('ipsec_active'))} tunnel aktif"])
    vpn_servers = []
    if _enabled(g("l2tp_server")):
        vpn_servers.append("L2TP")
    if _enabled(g("sstp_server")):
        vpn_servers.append("SSTP")
    if _enabled(g("ovpn_server")):
        vpn_servers.append("OpenVPN")
    if vpn_servers:
        add(f"VPN server ({', '.join(vpn_servers)})", "VPN & tunnel", "tinggi",
            ["server tunnel aktif: " + ", ".join(vpn_servers)])
    tun = g("gre") + g("eoip") + g("ipip")
    if tun:
        add("Tunnel L2/L3 (GRE/EoIP/IPIP)", "VPN & tunnel", "sedang",
            [f"{len(tun)} interface tunnel"])

    # ---- QoS & HA -------------------------------------------------------
    sq, qt = g("queues_simple"), g("queue_tree")
    if sq or qt:
        add("QoS / traffic shaping", "QoS", "tinggi" if (len(sq) + len(qt)) > 1 else "sedang",
            [f"{len(sq)} simple queue, {len(qt)} queue tree"])
    if g("vrrp"):
        add("High availability (VRRP)", "Ketersediaan", "tinggi",
            [f"{len(g('vrrp'))} interface VRRP"])

    nama_peran = [r["nama"] for r in roles]
    return {
        "identitas": ev.get("identity") or "",
        "versi": ev.get("version") or "",
        "jumlah_peran": len(roles),
        "peran": roles,
        "ringkasan": (
            f"Terdeteksi {len(roles)} peran: " + "; ".join(nama_peran)
            if roles else "Tidak ada peran khas terdeteksi (router minimal/baru)."
        ),
    }
