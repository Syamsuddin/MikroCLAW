"""MikroCLAW MCP server — tool ber-skema untuk MikroTik RouterOS.

Read-only secara default. Tool yang mengubah konfigurasi (write) hanya jalan
bila MIKROCLAW_ALLOW_WRITE=true.
"""

from __future__ import annotations

from typing import Any

from mcp.server.fastmcp import FastMCP

from .client import RouterOSClient, RouterOSError
from .config import Config
from .roles import classify_roles

mcp = FastMCP("mikroclaw")

_cfg: Config | None = None
_client: RouterOSClient | None = None


def _config() -> Config:
    global _cfg
    if _cfg is None:
        _cfg = Config.from_env()
    return _cfg


def _ros() -> RouterOSClient:
    global _client
    if _client is None:
        _client = RouterOSClient(_config())
    return _client


def _require_write() -> None:
    if not _config().allow_write:
        raise RouterOSError(
            "Operasi write dinonaktifkan. Set MIKROCLAW_ALLOW_WRITE=true di .env "
            "untuk mengizinkan perubahan konfigurasi."
        )


async def _first_ok(*paths: str) -> Any:
    """GET beberapa path berurutan, kembalikan hasil pertama yang sukses.

    Berguna saat nama menu berbeda antar versi RouterOS (mis. wifiwave2
    '/interface/wifi' vs legacy '/interface/wireless').
    """
    last: RouterOSError | None = None
    for path in paths:
        try:
            return await _ros().get(path)
        except RouterOSError as exc:
            last = exc
    raise last if last else RouterOSError("tidak ada path untuk dicoba")


async def _safe_get(path: str) -> Any:
    """GET yang menelan error (404/menu tak ada) jadi None — untuk deteksi peran."""
    try:
        return await _ros().get(path)
    except RouterOSError:
        return None


async def _safe_any(*paths: str) -> Any:
    """Coba beberapa path; kembalikan hasil non-error pertama, atau None."""
    for path in paths:
        try:
            return await _ros().get(path)
        except RouterOSError:
            continue
    return None


# ---------------------------------------------------------------------------
# READ — monitoring & inventarisasi (selalu aktif)
# ---------------------------------------------------------------------------


@mcp.tool()
async def system_resource() -> Any:
    """Info sistem: versi RouterOS, CPU, memori, uptime, board, arsitektur."""
    return await _ros().get("/system/resource")


@mcp.tool()
async def system_identity() -> Any:
    """Nama/identitas perangkat RouterOS."""
    return await _ros().get("/system/identity")


@mcp.tool()
async def list_interfaces() -> Any:
    """Daftar semua interface beserta status running/disabled dan statistik."""
    return await _ros().get("/interface")


@mcp.tool()
async def list_ip_addresses() -> Any:
    """Daftar alamat IP yang terpasang di tiap interface."""
    return await _ros().get("/ip/address")


@mcp.tool()
async def dhcp_leases() -> Any:
    """Daftar DHCP lease (klien yang dapat IP dari router)."""
    return await _ros().get("/ip/dhcp-server/lease")


@mcp.tool()
async def arp_table() -> Any:
    """Tabel ARP (pemetaan IP <-> MAC yang terlihat router)."""
    return await _ros().get("/ip/arp")


@mcp.tool()
async def firewall_filter_rules() -> Any:
    """Aturan firewall filter (chain input/forward/output)."""
    return await _ros().get("/ip/firewall/filter")


@mcp.tool()
async def firewall_nat_rules() -> Any:
    """Aturan NAT (srcnat/dstnat), mis. masquerade & port forward."""
    return await _ros().get("/ip/firewall/nat")


@mcp.tool()
async def routing_table() -> Any:
    """Tabel routing IP (route aktif & statis)."""
    return await _ros().get("/ip/route")


@mcp.tool()
async def simple_queues() -> Any:
    """Daftar simple queue — pembatasan bandwidth per IP/target."""
    return await _ros().get("/queue/simple")


@mcp.tool()
async def address_lists() -> Any:
    """Isi semua firewall address-list (grup IP yang dirujuk aturan firewall)."""
    return await _ros().get("/ip/firewall/address-list")


@mcp.tool()
async def dns_settings() -> Any:
    """Konfigurasi DNS router: server upstream, cache, allow-remote-requests."""
    return await _ros().get("/ip/dns")


@mcp.tool()
async def dhcp_servers() -> Any:
    """Daftar DHCP server beserta interface & address-pool-nya."""
    return await _ros().get("/ip/dhcp-server")


@mcp.tool()
async def ppp_active() -> Any:
    """Sesi PPP aktif (PPPoE/L2TP/PPTP/SSTP) — siapa yang sedang dial-in."""
    return await _ros().get("/ppp/active")


@mcp.tool()
async def bridge_hosts() -> Any:
    """Tabel host bridge (MAC yang dipelajari tiap port bridge)."""
    return await _ros().get("/interface/bridge/host")


@mcp.tool()
async def neighbors() -> Any:
    """Perangkat tetangga terdeteksi (MNDP/CDP/LLDP) — router/switch di sekitar."""
    return await _ros().get("/ip/neighbor")


@mcp.tool()
async def system_health() -> Any:
    """Sensor perangkat keras: suhu, tegangan, kipas (jika didukung board)."""
    return await _ros().get("/system/health")


@mcp.tool()
async def netwatch() -> Any:
    """Daftar host yang dipantau Netwatch beserta status up/down."""
    return await _ros().get("/tool/netwatch")


@mcp.tool()
async def router_users() -> Any:
    """Daftar user RouterOS beserta grup/hak aksesnya."""
    return await _ros().get("/user")


@mcp.tool()
async def wifi_interfaces() -> Any:
    """Daftar interface WiFi. Auto-deteksi wifiwave2 (/interface/wifi) atau legacy (/interface/wireless)."""
    return await _first_ok("/interface/wifi", "/interface/wireless")


@mcp.tool()
async def wifi_registrations() -> Any:
    """Klien WiFi yang sedang terhubung (registration table). Auto wifiwave2/legacy."""
    return await _first_ok(
        "/interface/wifi/registration-table",
        "/interface/wireless/registration-table",
    )


@mcp.tool()
async def wireguard_interfaces() -> Any:
    """Daftar interface WireGuard (VPN) beserta public key & listen-port."""
    return await _ros().get("/interface/wireguard")


@mcp.tool()
async def wireguard_peers() -> Any:
    """Daftar peer WireGuard beserta allowed-address & handshake terakhir."""
    return await _ros().get("/interface/wireguard/peers")


@mcp.tool()
async def ppp_secrets() -> Any:
    """Daftar akun PPP (PPPoE/VPN): name, service, profile. Catatan: berisi kredensial."""
    return await _ros().get("/ppp/secret")


@mcp.tool()
async def ip_pools() -> Any:
    """Daftar IP pool (rentang IP untuk DHCP/PPP)."""
    return await _ros().get("/ip/pool")


@mcp.tool()
async def dns_static() -> Any:
    """Entri DNS statis (A/CNAME/dll yang dilayani router)."""
    return await _ros().get("/ip/dns/static")


@mcp.tool()
async def ntp_client() -> Any:
    """Status & konfigurasi NTP client (sinkronisasi waktu)."""
    return await _ros().get("/system/ntp/client")


@mcp.tool()
async def schedulers() -> Any:
    """Daftar scheduler (tugas terjadwal RouterOS)."""
    return await _ros().get("/system/scheduler")


@mcp.tool()
async def scripts() -> Any:
    """Daftar script tersimpan di RouterOS."""
    return await _ros().get("/system/script")


@mcp.tool()
async def vlans() -> Any:
    """Daftar interface VLAN beserta vlan-id & interface induk."""
    return await _ros().get("/interface/vlan")


@mcp.tool()
async def ip_services() -> Any:
    """Daftar service IP (api, ssh, www, telnet, winbox) + status & port."""
    return await _ros().get("/ip/service")


@mcp.tool()
async def dhcp_client() -> Any:
    """Status DHCP client (mis. IP WAN yang didapat dari ISP) per interface."""
    return await _ros().get("/ip/dhcp-client")


@mcp.tool()
async def ip_cloud() -> Any:
    """IP publik & DDNS MikroTik (/ip/cloud) — penting untuk remote access."""
    return await _ros().get("/ip/cloud")


@mcp.tool()
async def system_packages() -> Any:
    """Paket RouterOS terpasang (nama, versi, enabled/disabled)."""
    return await _ros().get("/system/package")


@mcp.tool()
async def routerboard_info() -> Any:
    """Info RouterBOARD: model, serial, firmware terpasang vs tersedia."""
    return await _ros().get("/system/routerboard")


@mcp.tool()
async def active_sessions() -> Any:
    """User yang sedang login ke router (via, alamat, kapan) — audit keamanan."""
    return await _ros().get("/user/active")


@mcp.tool()
async def list_files() -> Any:
    """Daftar file di penyimpanan router (backup, export, dll) + ukuran & waktu."""
    return await _ros().get("/file")


@mcp.tool()
async def firewall_connections() -> Any:
    """Connection tracking aktif (src/dst, protokol, state) — untuk troubleshooting."""
    return await _ros().get("/ip/firewall/connection")


@mcp.tool()
async def bridge_ports() -> Any:
    """Pemetaan port ke bridge (interface mana ikut bridge mana)."""
    return await _ros().get("/interface/bridge/port")


@mcp.tool()
async def certificates() -> Any:
    """Daftar sertifikat di router beserta masa berlaku (audit kedaluwarsa)."""
    return await _ros().get("/certificate")


@mcp.tool()
async def dns_cache() -> Any:
    """Isi cache DNS resolver router (entri yang sedang di-cache)."""
    return await _ros().get("/ip/dns/cache")


@mcp.tool()
async def dhcp_networks() -> Any:
    """Konfigurasi network DHCP: gateway, DNS, netmask yang ditawarkan ke klien."""
    return await _ros().get("/ip/dhcp-server/network")


@mcp.tool()
async def firewall_mangle() -> Any:
    """Aturan firewall mangle (marking koneksi/paket untuk QoS/policy routing)."""
    return await _ros().get("/ip/firewall/mangle")


@mcp.tool()
async def queue_tree() -> Any:
    """Queue tree (pembatasan bandwidth hierarkis berbasis mark)."""
    return await _ros().get("/queue/tree")


@mcp.tool()
async def ppp_profiles() -> Any:
    """Profil PPP (rate-limit, address pool, DNS untuk akun PPPoE/VPN)."""
    return await _ros().get("/ppp/profile")


@mcp.tool()
async def user_groups() -> Any:
    """Grup hak akses RouterOS beserta policy-nya (audit keamanan)."""
    return await _ros().get("/user/group")


@mcp.tool()
async def ethernet_ports() -> Any:
    """Detail port ethernet: link speed, auto-negotiation, status fisik."""
    return await _ros().get("/interface/ethernet")


@mcp.tool()
async def ipsec_peers() -> Any:
    """Konfigurasi peer IPsec (alamat, exchange-mode, profil)."""
    return await _ros().get("/ip/ipsec/peer")


@mcp.tool()
async def ipsec_active_peers() -> Any:
    """Peer IPsec yang sedang aktif (tunnel yang sedang berjalan)."""
    return await _ros().get("/ip/ipsec/active-peers")


@mcp.tool()
async def ipv6_addresses() -> Any:
    """Daftar alamat IPv6 yang terpasang per interface."""
    return await _ros().get("/ipv6/address")


@mcp.tool()
async def ipv6_routes() -> Any:
    """Tabel routing IPv6 (route aktif & statis)."""
    return await _ros().get("/ipv6/route")


@mcp.tool()
async def ipv6_firewall_filter() -> Any:
    """Aturan firewall filter IPv6."""
    return await _ros().get("/ipv6/firewall/filter")


@mcp.tool()
async def ipv6_neighbors() -> Any:
    """Tabel neighbor IPv6 (NDP) — pemetaan IPv6 <-> MAC."""
    return await _ros().get("/ipv6/neighbor")


@mcp.tool()
async def hotspot_servers() -> Any:
    """Daftar server hotspot beserta interface & profilnya."""
    return await _ros().get("/ip/hotspot")


@mcp.tool()
async def hotspot_active() -> Any:
    """User hotspot yang sedang login (aktif)."""
    return await _ros().get("/ip/hotspot/active")


@mcp.tool()
async def hotspot_users() -> Any:
    """Daftar akun user hotspot (name, profil, kuota)."""
    return await _ros().get("/ip/hotspot/user")


@mcp.tool()
async def capsman_remote_caps() -> Any:
    """Daftar CAP (AP) yang dikelola CAPsMAN. Auto legacy (/caps-man) atau wifiwave2."""
    return await _first_ok(
        "/caps-man/remote-cap", "/interface/wifi/capsman/remote-cap"
    )


@mcp.tool()
async def capsman_registrations() -> Any:
    """Klien yang terhubung lewat CAPsMAN. Auto legacy atau wifiwave2."""
    return await _first_ok(
        "/caps-man/registration-table", "/interface/wifi/registration-table"
    )


@mcp.tool()
async def wifi_radios() -> Any:
    """Daftar radio WiFi fisik (wifiwave2)."""
    return await _ros().get("/interface/wifi/radio")


@mcp.tool()
async def bgp_sessions() -> Any:
    """Sesi BGP (status/peer) — RouterOS v7."""
    return await _ros().get("/routing/bgp/session")


@mcp.tool()
async def ospf_neighbors() -> Any:
    """Neighbor OSPF beserta state adjacency — RouterOS v7."""
    return await _ros().get("/routing/ospf/neighbor")


@mcp.tool()
async def radius_servers() -> Any:
    """Daftar server RADIUS yang dikonfigurasi (untuk AAA)."""
    return await _ros().get("/radius")


@mcp.tool()
async def system_history() -> Any:
    """Riwayat perubahan konfigurasi yang dapat di-undo (/system/history)."""
    return await _ros().get("/system/history")


@mcp.tool()
async def system_license() -> Any:
    """Info lisensi (level/CHR) perangkat."""
    return await _ros().get("/system/license")


@mcp.tool()
async def recent_logs(limit: int = 50) -> Any:
    """Ambil log terbaru dari RouterOS.

    Args:
        limit: jumlah baris terakhir yang dikembalikan (default 50).
    """
    logs = await _ros().get("/log")
    if isinstance(logs, list) and limit > 0:
        return logs[-limit:]
    return logs


@mcp.tool()
async def rest_get(path: str) -> Any:
    """Generic GET read-only ke path REST apa pun untuk hal yang belum punya tool khusus.

    Contoh path: 'interface/wireless', 'ip/dns', 'system/clock', 'ppp/active'.
    Hanya membaca; tidak mengubah konfigurasi.

    Args:
        path: path REST tanpa awalan /rest, mis. 'ip/dns'.
    """
    return await _ros().get(path)


@mcp.tool()
async def ping(address: str, count: int = 3) -> Any:
    """Jalankan ping dari router ke sebuah alamat (diagnostik konektivitas).

    Args:
        address: host/IP tujuan, mis. '8.8.8.8'.
        count: jumlah paket (default 3).
    """
    return await _ros().post(
        "/ping", {"address": address, "count": str(count)}
    )


@mcp.tool()
async def traceroute(address: str, count: int = 3) -> Any:
    """Traceroute dari router ke sebuah alamat (jejak hop). Bisa makan beberapa detik.

    Args:
        address: host/IP tujuan, mis. '1.1.1.1'.
        count: jumlah probe per putaran sebelum berhenti (default 3).
    """
    return await _ros().post(
        "/tool/traceroute", {"address": address, "count": str(count)}
    )


@mcp.tool()
async def interface_traffic_live(interface: str) -> Any:
    """Ambil satu sampel throughput real-time interface (rx/tx bit-per-detik).

    Args:
        interface: nama interface, mis. 'ether1'.
    """
    return await _ros().post(
        "/interface/monitor-traffic", {"interface": interface, "once": "true"}
    )


@mcp.tool()
async def check_for_updates() -> Any:
    """Cek ketersediaan update RouterOS dari channel saat ini (menghubungi server MikroTik).

    Tidak mengubah konfigurasi; mengembalikan versi terpasang & versi terbaru bila ada.
    """
    await _ros().post("/system/package/update/check-for-updates")
    return await _ros().get("/system/package/update")


@mcp.tool()
async def detect_roles() -> Any:
    """Deteksi peran/fungsi yang sedang dijalankan router (read-only).

    Mengumpulkan bukti dari banyak menu RouterOS (firewall/NAT, routing BGP/OSPF,
    bridge/VLAN, WiFi/CAPsMAN, hotspot, PPPoE, DHCP, DNS, VPN/tunnel, QoS, VRRP,
    container, dll), lalu mengklasifikasikan peran beserta tingkat keyakinan &
    bukti. Berguna untuk mengenali apakah perangkat berperan sebagai gateway NAT,
    firewall, BGP/OSPF router, switch/AP, BRAS PPPoE, konsentrator VPN, dan lainnya.

    Mengembalikan: identitas, versi, jumlah_peran, daftar `peran`
    (nama/kategori/keyakinan/bukti), dan `ringkasan`.
    """
    ident = await _safe_get("/system/identity")
    res = await _safe_get("/system/resource")

    def _name(obj: Any, key: str) -> str:
        row = obj[0] if isinstance(obj, list) and obj else obj
        return str(row.get(key, "")) if isinstance(row, dict) else ""

    ev: dict[str, Any] = {
        "identity": _name(ident, "name"),
        "version": _name(res, "version"),
        "nat": await _safe_get("/ip/firewall/nat"),
        "filter": await _safe_get("/ip/firewall/filter"),
        "routes": await _safe_get("/ip/route"),
        "bgp": await _safe_any("/routing/bgp/session", "/routing/bgp/connection", "/routing/bgp/peer"),
        "ospf": await _safe_any("/routing/ospf/neighbor", "/routing/ospf/instance"),
        "bridges": await _safe_get("/interface/bridge"),
        "bridge_ports": await _safe_get("/interface/bridge/port"),
        "vlans": await _safe_get("/interface/vlan"),
        "wifi": await _safe_any("/interface/wifi", "/interface/wireless"),
        "capsman": await _safe_any("/interface/wifi/capsman", "/caps-man/manager"),
        "hotspot": await _safe_get("/ip/hotspot"),
        "pppoe_server": await _safe_get("/interface/pppoe-server/server"),
        "ppp_active": await _safe_get("/ppp/active"),
        "pppoe_client": await _safe_get("/interface/pppoe-client"),
        "dhcp_client": await _safe_get("/ip/dhcp-client"),
        "dhcp_server": await _safe_get("/ip/dhcp-server"),
        "dns": await _safe_get("/ip/dns"),
        "proxy": await _safe_get("/ip/proxy"),
        "container": await _safe_any("/container"),
        "wireguard": await _safe_any("/interface/wireguard"),
        "wg_peers": await _safe_any("/interface/wireguard/peers"),
        "ipsec_peer": await _safe_get("/ip/ipsec/peer"),
        "ipsec_active": await _safe_get("/ip/ipsec/active-peers"),
        "l2tp_server": await _safe_get("/interface/l2tp-server/server"),
        "sstp_server": await _safe_get("/interface/sstp-server/server"),
        "ovpn_server": await _safe_get("/interface/ovpn-server/server"),
        "gre": await _safe_get("/interface/gre"),
        "eoip": await _safe_get("/interface/eoip"),
        "ipip": await _safe_get("/interface/ipip"),
        "vrrp": await _safe_get("/interface/vrrp"),
        "queues_simple": await _safe_get("/queue/simple"),
        "queue_tree": await _safe_get("/queue/tree"),
    }
    return classify_roles(ev)


# ---------------------------------------------------------------------------
# WRITE — mengubah konfigurasi (digerbang MIKROCLAW_ALLOW_WRITE)
# ---------------------------------------------------------------------------


@mcp.tool()
async def set_interface_enabled(interface_id: str, enabled: bool) -> Any:
    """Aktif/nonaktifkan sebuah interface. BUTUH MIKROCLAW_ALLOW_WRITE=true.

    Args:
        interface_id: id internal RouterOS (".id", mis. '*1') atau nama interface.
        enabled: True untuk mengaktifkan, False untuk menonaktifkan.
    """
    _require_write()
    return await _ros().patch(
        f"/interface/{interface_id}",
        {"disabled": "false" if enabled else "true"},
    )


@mcp.tool()
async def add_firewall_drop(
    src_address: str, chain: str = "forward", comment: str = "added-by-mikroclaw"
) -> Any:
    """Tambah aturan firewall untuk DROP trafik dari src_address. BUTUH ALLOW_WRITE.

    Args:
        src_address: IP/subnet sumber yang akan diblok, mis. '10.0.0.5'.
        chain: chain firewall (default 'forward').
        comment: catatan pada aturan.
    """
    _require_write()
    return await _ros().put(
        "/ip/firewall/filter",
        {
            "chain": chain,
            "action": "drop",
            "src-address": src_address,
            "comment": comment,
        },
    )


@mcp.tool()
async def add_address_list_entry(
    address: str,
    address_list: str,
    comment: str = "added-by-mikroclaw",
    timeout: str = "",
) -> Any:
    """Tambah IP/subnet ke sebuah firewall address-list. BUTUH MIKROCLAW_ALLOW_WRITE=true.

    Cocok untuk blokir/izin massal: satu aturan firewall cukup merujuk ke list ini.

    Args:
        address: IP/subnet, mis. '10.0.0.5' atau '192.168.10.0/24'.
        address_list: nama list tujuan, mis. 'blocked'.
        comment: catatan pada entri.
        timeout: durasi auto-hapus (mis. '1h', '30m'); kosong = permanen.
    """
    _require_write()
    payload: dict[str, Any] = {
        "address": address,
        "list": address_list,
        "comment": comment,
    }
    if timeout:
        payload["timeout"] = timeout
    return await _ros().put("/ip/firewall/address-list", payload)


@mcp.tool()
async def delete_firewall_rule(rule_id: str) -> Any:
    """Hapus satu aturan firewall filter berdasarkan id. BUTUH MIKROCLAW_ALLOW_WRITE=true.

    Args:
        rule_id: nilai '.id' aturan (mis. '*5'); ambil dulu dari firewall_filter_rules.
    """
    _require_write()
    return await _ros().delete(f"/ip/firewall/filter/{rule_id}")


@mcp.tool()
async def set_firewall_rule_enabled(rule_id: str, enabled: bool) -> Any:
    """Aktif/nonaktifkan satu aturan firewall filter. BUTUH MIKROCLAW_ALLOW_WRITE=true.

    Args:
        rule_id: nilai '.id' aturan (mis. '*5').
        enabled: True mengaktifkan, False menonaktifkan.
    """
    _require_write()
    return await _ros().patch(
        f"/ip/firewall/filter/{rule_id}",
        {"disabled": "false" if enabled else "true"},
    )


@mcp.tool()
async def add_simple_queue(name: str, target: str, max_limit: str) -> Any:
    """Tambah simple queue untuk membatasi bandwidth target. BUTUH MIKROCLAW_ALLOW_WRITE=true.

    Args:
        name: nama queue, mis. 'limit-tamu'.
        target: IP/subnet/interface target, mis. '192.168.88.50/32'.
        max_limit: batas upload/download 'tx/rx', mis. '5M/10M'.
    """
    _require_write()
    return await _ros().put(
        "/queue/simple",
        {"name": name, "target": target, "max-limit": max_limit},
    )


@mcp.tool()
async def create_backup(name: str = "mikroclaw") -> Any:
    """Buat file backup konfigurasi (.backup) di penyimpanan router. BUTUH MIKROCLAW_ALLOW_WRITE=true.

    Args:
        name: nama file backup (tanpa ekstensi).
    """
    _require_write()
    return await _ros().post("/system/backup/save", {"name": name})


@mcp.tool()
async def reboot_router() -> Any:
    """Reboot router SEKARANG. Operasi mengganggu — BUTUH MIKROCLAW_ALLOW_WRITE=true.

    Koneksi ke router akan terputus sementara saat proses restart berlangsung.
    """
    _require_write()
    return await _ros().post("/system/reboot")


@mcp.tool()
async def add_dns_static(name: str, address: str, ttl: str = "1d") -> Any:
    """Tambah entri DNS statis (A record: name -> address). BUTUH MIKROCLAW_ALLOW_WRITE=true.

    Args:
        name: nama host, mis. 'nas.lan'.
        address: IP tujuan, mis. '192.168.88.10'.
        ttl: time-to-live, mis. '1d', '1h'.
    """
    _require_write()
    return await _ros().put(
        "/ip/dns/static", {"name": name, "address": address, "ttl": ttl}
    )


@mcp.tool()
async def add_ppp_secret(
    name: str,
    password: str,
    service: str = "any",
    profile: str = "default",
) -> Any:
    """Tambah akun PPP (PPPoE/VPN). BUTUH MIKROCLAW_ALLOW_WRITE=true.

    Args:
        name: username akun.
        password: password akun.
        service: jenis service ('any','pppoe','l2tp','pptp','sstp','ovpn').
        profile: nama PPP profile (default 'default').
    """
    _require_write()
    return await _ros().put(
        "/ppp/secret",
        {
            "name": name,
            "password": password,
            "service": service,
            "profile": profile,
        },
    )


@mcp.tool()
async def add_wireguard_peer(
    interface: str,
    public_key: str,
    allowed_address: str,
    endpoint_address: str = "",
    endpoint_port: str = "",
) -> Any:
    """Tambah peer WireGuard ke sebuah interface. BUTUH MIKROCLAW_ALLOW_WRITE=true.

    Args:
        interface: nama interface WireGuard, mis. 'wg0'.
        public_key: public key milik peer.
        allowed_address: subnet yang diizinkan, mis. '10.10.0.2/32'.
        endpoint_address: (opsional) alamat endpoint peer.
        endpoint_port: (opsional) port endpoint peer.
    """
    _require_write()
    payload: dict[str, Any] = {
        "interface": interface,
        "public-key": public_key,
        "allowed-address": allowed_address,
    }
    if endpoint_address:
        payload["endpoint-address"] = endpoint_address
    if endpoint_port:
        payload["endpoint-port"] = endpoint_port
    return await _ros().put("/interface/wireguard/peers", payload)


@mcp.tool()
async def set_ip_service_enabled(service_id: str, enabled: bool) -> Any:
    """Aktif/nonaktifkan sebuah IP service (mis. matikan telnet/ftp). BUTUH MIKROCLAW_ALLOW_WRITE=true.

    Args:
        service_id: '.id' atau nama service (mis. 'telnet'); lihat ip_services.
        enabled: True mengaktifkan, False menonaktifkan.
    """
    _require_write()
    return await _ros().patch(
        f"/ip/service/{service_id}",
        {"disabled": "false" if enabled else "true"},
    )


@mcp.tool()
async def add_nat_rule(
    chain: str,
    action: str,
    protocol: str = "",
    dst_port: str = "",
    to_addresses: str = "",
    to_ports: str = "",
    src_address: str = "",
    dst_address: str = "",
    in_interface: str = "",
    out_interface: str = "",
    comment: str = "added-by-mikroclaw",
) -> Any:
    """Tambah aturan NAT (port-forward / masquerade). BUTUH MIKROCLAW_ALLOW_WRITE=true.

    Contoh port-forward (TCP 8080 publik -> 192.168.88.10:80):
        chain='dstnat', action='dst-nat', protocol='tcp', dst_port='8080',
        to_addresses='192.168.88.10', to_ports='80'
    Contoh masquerade WAN:
        chain='srcnat', action='masquerade', out_interface='ether1'

    Args:
        chain: 'dstnat' atau 'srcnat'.
        action: mis. 'dst-nat', 'src-nat', 'masquerade'.
        protocol/dst_port/to_addresses/to_ports/src_address/dst_address/
        in_interface/out_interface: opsional, isi sesuai kebutuhan.
        comment: catatan pada aturan.
    """
    _require_write()
    fields = {
        "chain": chain,
        "action": action,
        "protocol": protocol,
        "dst-port": dst_port,
        "to-addresses": to_addresses,
        "to-ports": to_ports,
        "src-address": src_address,
        "dst-address": dst_address,
        "in-interface": in_interface,
        "out-interface": out_interface,
        "comment": comment,
    }
    payload = {k: v for k, v in fields.items() if v}
    return await _ros().put("/ip/firewall/nat", payload)


@mcp.tool()
async def add_static_route(
    dst_address: str,
    gateway: str,
    distance: str = "",
    comment: str = "added-by-mikroclaw",
) -> Any:
    """Tambah route statis. BUTUH MIKROCLAW_ALLOW_WRITE=true.

    Args:
        dst_address: subnet tujuan, mis. '10.20.0.0/24' atau '0.0.0.0/0' (default route).
        gateway: gateway/next-hop, mis. '192.168.88.1' atau nama interface.
        distance: (opsional) administrative distance, mis. '1'.
        comment: catatan pada route.
    """
    _require_write()
    payload: dict[str, Any] = {
        "dst-address": dst_address,
        "gateway": gateway,
        "comment": comment,
    }
    if distance:
        payload["distance"] = distance
    return await _ros().put("/ip/route", payload)


@mcp.tool()
async def add_static_dhcp_lease(
    address: str,
    mac_address: str,
    server: str = "",
    comment: str = "added-by-mikroclaw",
) -> Any:
    """Pin IP statis ke sebuah MAC (static DHCP lease). BUTUH MIKROCLAW_ALLOW_WRITE=true.

    Args:
        address: IP yang dipatok, mis. '192.168.88.50'.
        mac_address: MAC klien, mis. 'AA:BB:CC:DD:EE:FF'.
        server: (opsional) nama DHCP server; lihat dhcp_servers.
        comment: catatan pada lease.
    """
    _require_write()
    payload: dict[str, Any] = {
        "address": address,
        "mac-address": mac_address,
        "comment": comment,
    }
    if server:
        payload["server"] = server
    return await _ros().put("/ip/dhcp-server/lease", payload)


@mcp.tool()
async def assign_ip_address(
    address: str, interface: str, comment: str = "added-by-mikroclaw"
) -> Any:
    """Pasang IP address (CIDR) ke sebuah interface. BUTUH MIKROCLAW_ALLOW_WRITE=true.

    Args:
        address: IP dengan prefix, mis. '192.168.50.1/24'.
        interface: nama interface, mis. 'bridge1' atau 'ether2'.
        comment: catatan.
    """
    _require_write()
    return await _ros().put(
        "/ip/address",
        {"address": address, "interface": interface, "comment": comment},
    )


@mcp.tool()
async def set_identity(name: str) -> Any:
    """Ganti nama/identitas router. BUTUH MIKROCLAW_ALLOW_WRITE=true.

    Args:
        name: nama baru perangkat.
    """
    _require_write()
    return await _ros().post("/system/identity/set", {"name": name})


@mcp.tool()
async def set_dns_servers(
    servers: str, allow_remote_requests: bool | None = None
) -> Any:
    """Set server DNS upstream router. BUTUH MIKROCLAW_ALLOW_WRITE=true.

    Args:
        servers: daftar IP dipisah koma, mis. '1.1.1.1,8.8.8.8'.
        allow_remote_requests: (opsional) jadikan router sebagai DNS resolver LAN.
    """
    _require_write()
    payload: dict[str, Any] = {"servers": servers}
    if allow_remote_requests is not None:
        payload["allow-remote-requests"] = (
            "true" if allow_remote_requests else "false"
        )
    return await _ros().post("/ip/dns/set", payload)


@mcp.tool()
async def remove_address_list_entry(entry_id: str) -> Any:
    """Hapus entri firewall address-list berdasarkan id. BUTUH MIKROCLAW_ALLOW_WRITE=true.

    Args:
        entry_id: nilai '.id' entri; ambil dulu dari address_lists.
    """
    _require_write()
    return await _ros().delete(f"/ip/firewall/address-list/{entry_id}")


@mcp.tool()
async def add_hotspot_user(
    name: str,
    password: str = "",
    profile: str = "default",
    comment: str = "added-by-mikroclaw",
) -> Any:
    """Tambah akun user hotspot. BUTUH MIKROCLAW_ALLOW_WRITE=true.

    Args:
        name: username hotspot.
        password: password (opsional; kosong = tanpa password).
        profile: nama user-profile hotspot.
        comment: catatan.
    """
    _require_write()
    payload: dict[str, Any] = {
        "name": name,
        "profile": profile,
        "comment": comment,
    }
    if password:
        payload["password"] = password
    return await _ros().put("/ip/hotspot/user", payload)


@mcp.tool()
async def add_ipv6_address(
    address: str, interface: str, comment: str = "added-by-mikroclaw"
) -> Any:
    """Pasang alamat IPv6 ke sebuah interface. BUTUH MIKROCLAW_ALLOW_WRITE=true.

    Args:
        address: IPv6 dengan prefix, mis. '2001:db8::1/64'.
        interface: nama interface, mis. 'bridge1'.
        comment: catatan.
    """
    _require_write()
    return await _ros().put(
        "/ipv6/address",
        {"address": address, "interface": interface, "comment": comment},
    )


@mcp.tool()
async def rest_write(method: str, path: str, body: dict[str, Any] | None = None) -> Any:
    """Operasi write generic (PUT/PATCH/DELETE/POST). BUTUH MIKROCLAW_ALLOW_WRITE=true.

    Untuk operasi lanjutan yang belum punya tool khusus. Gunakan hati-hati.

    Args:
        method: 'PUT' (tambah), 'PATCH' (ubah by id), 'DELETE' (hapus by id), 'POST' (command).
        path: path REST tanpa /rest, mis. 'ip/firewall/filter' atau 'ip/firewall/filter/*5'.
        body: payload JSON (opsional, untuk PUT/PATCH/POST).
    """
    _require_write()
    m = method.strip().upper()
    if m == "PUT":
        return await _ros().put(path, body or {})
    if m == "PATCH":
        return await _ros().patch(path, body or {})
    if m == "DELETE":
        return await _ros().delete(path)
    if m == "POST":
        return await _ros().post(path, body or {})
    raise RouterOSError(f"Method tidak didukung untuk write: {method!r}")


def main() -> None:
    """Entry point — jalankan MCP server via stdio."""
    mcp.run()


if __name__ == "__main__":
    main()
