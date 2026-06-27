# MikroCLAW

> **Mikro**Tik + **CLAW** (Claude) — MCP server yang membuat **Claude Code bisa
> mengakses, memonitor, dan mengelola perangkat MikroTik RouterOS** lewat tool
> ber-skema, langsung dari percakapan.

MikroCLAW menjembatani Claude Code dengan RouterOS melalui **REST API RouterOS v7**
(HTTPS). Alih-alih Anda mengetik perintah `curl`/`ssh` manual, Claude memanggil
tool seperti `dhcp_leases` atau `firewall_filter_rules` sebagai pemanggilan ber-skema
— aman, terstruktur, dan kredensial tidak pernah bocor ke jendela chat.

- 🔒 **Read-only secara default** — aman untuk eksplorasi & monitoring.
- 🚧 **Operasi write digerbang** oleh flag `MIKROCLAW_ALLOW_WRITE`.
- 🔑 **Kredensial via `.env`** — tidak muncul di chat, tidak ikut ter-commit.
- 🧩 **92 tool** (70 read + 22 write), termasuk dua tool generic (`rest_get` / `rest_write`) untuk hal yang belum punya tool khusus.

---

## Daftar Isi

1. [Bagaimana Claude Code mengakses MikroTik](#bagaimana-claude-code-mengakses-mikrotik)
2. [Prasyarat](#prasyarat)
3. [Persiapan RouterOS](#persiapan-routeros)
4. [Instalasi](#instalasi)
5. [Konfigurasi (.env)](#konfigurasi-env)
6. [Menghubungkan ke Claude Code](#menghubungkan-ke-claude-code)
7. [Daftar tool](#daftar-tool)
8. [Skills (playbook orkestrasi)](#skills-playbook-orkestrasi)
9. [Contoh penggunaan](#contoh-penggunaan)
10. [Uji manual tanpa Claude](#uji-manual-tanpa-claude)
11. [Keamanan](#keamanan)
12. [Troubleshooting](#troubleshooting)
13. [Kompatibilitas RouterOS v6 vs v7](#kompatibilitas-routeros-v6-vs-v7)
14. [Struktur proyek](#struktur-proyek)
15. [Pengembangan: menambah tool](#pengembangan-menambah-tool)

---

## Bagaimana Claude Code mengakses MikroTik

Claude Code tidak punya driver MikroTik bawaan. MikroCLAW berperan sebagai
**MCP server** (Model Context Protocol): proses lokal yang mengekspos sekumpulan
*tool*. Claude Code memanggil tool itu; MikroCLAW menerjemahkannya menjadi
panggilan REST API ke RouterOS, lalu mengembalikan JSON hasilnya.

```
┌────────────┐   panggil tool     ┌──────────────┐   HTTPS /rest/...   ┌────────────┐
│ Claude Code │ ─────────────────▶ │  MikroCLAW    │ ──────────────────▶ │  RouterOS   │
│  (CLI/IDE)  │   (stdio MCP)      │ (MCP server)  │   REST API v7       │  (MikroTik) │
│             │ ◀───────────────── │               │ ◀────────────────── │            │
└────────────┘   hasil JSON       └──────────────┘   JSON               └────────────┘
                                    membaca .env
                                  (host, user, pass)
```

**Alurnya:**

1. Anda menulis prompt biasa, mis. *"siapa saja klien DHCP yang aktif?"*.
2. Claude Code memilih tool `dhcp_leases` dan memanggilnya lewat protokol MCP (stdio).
3. MikroCLAW (`client.py`) mengirim `GET https://<router>/rest/ip/dhcp-server/lease`
   dengan Basic Auth dari `.env`.
4. RouterOS membalas JSON; MikroCLAW meneruskannya ke Claude.
5. Claude meringkas/menyajikan hasil untuk Anda.

RouterOS REST memetakan path konsol ke URL secara langsung, contoh:

| Perintah konsol RouterOS | Operasi REST |
|---|---|
| `/interface print` | `GET /rest/interface` |
| `/ip address print` | `GET /rest/ip/address` |
| tambah item | `PUT /rest/<path>` (+ body JSON) |
| ubah item ber-`.id` | `PATCH /rest/<path>/<id>` |
| hapus item ber-`.id` | `DELETE /rest/<path>/<id>` |
| command (ping, dst.) | `POST /rest/<path>` |

---

## Prasyarat

| Komponen | Versi | Catatan |
|---|---|---|
| **RouterOS** | **v7.1+** | REST API hanya ada di v7. Untuk v6 lihat [kompatibilitas](#kompatibilitas-routeros-v6-vs-v7). |
| **Python** | 3.10+ | Diuji pada 3.14. |
| **uv** | terbaru | Pengelola environment/dependency — https://docs.astral.sh/uv/ |
| Akses jaringan | — | Host yang menjalankan MikroCLAW harus bisa menjangkau port 443/80 router. |

---

## Persiapan RouterOS

Lakukan sekali di router. Disarankan **HTTPS** + **user least-privilege**.

```routeros
# 1) (HTTPS) Aktifkan service www-ssl dengan sertifikat yang sudah ada di /certificate.
#    Jika belum punya sertifikat, buat self-signed dulu (lihat di bawah).
/ip/service/set www-ssl certificate=<nama-sertifikat> disabled=no

#    Alternatif cepat (kurang aman): pakai HTTP biasa.
#    /ip/service/set www disabled=no

# 2) Buat user khusus MikroCLAW — JANGAN pakai 'admin' penuh.
/user/add name=mikroclaw password=<password-kuat> group=read     ;# read-only
#    Untuk mengizinkan operasi write, gunakan group=write atau policy kustom.

# 3) Batasi sumber yang boleh mengakses service (mis. hanya subnet LAN/host admin).
/ip/service/set www-ssl address=192.168.88.0/24
```

Membuat sertifikat self-signed (jika belum ada):

```routeros
/certificate/add name=mikroclaw-ca common-name=mikroclaw-ca key-usage=key-cert-sign,crl-sign
/certificate/sign mikroclaw-ca
/certificate/add name=mikroclaw-https common-name=<ip-atau-hostname-router>
/certificate/sign mikroclaw-https ca=mikroclaw-ca
/ip/service/set www-ssl certificate=mikroclaw-https disabled=no
```

> Karena sertifikat self-signed, biarkan `MIKROTIK_VERIFY_TLS=false` di `.env`
> (default). Set `true` hanya jika memakai sertifikat yang tepercaya.

---

## Instalasi

```bash
cd /Users/syams/PROJECTS/MikroCLAW
cp .env.example .env          # lalu isi host + kredensial router
uv sync                       # pasang dependency (mcp, httpx, python-dotenv)
```

`uv sync` membuat virtualenv `.venv/` dan menginstal proyek beserta dependensinya.

---

## Konfigurasi (.env)

Semua konfigurasi lewat environment / file `.env` (otomatis dibaca saat server start).

| Variabel | Wajib | Default | Keterangan |
|---|---|---|---|
| `MIKROTIK_HOST` | ✅ | — | IP/hostname router, mis. `192.168.88.1`. |
| `MIKROTIK_USER` | — | `admin` | User RouterOS (disarankan user khusus least-privilege). |
| `MIKROTIK_PASSWORD` | — | *(kosong)* | Password user tersebut. |
| `MIKROTIK_USE_TLS` | — | `true` | `true` → HTTPS (www-ssl), `false` → HTTP. |
| `MIKROTIK_PORT` | — | `443`/`80` | Port REST. Default mengikuti `USE_TLS`. |
| `MIKROTIK_VERIFY_TLS` | — | `false` | Verifikasi sertifikat TLS. `false` cocok untuk self-signed. |
| `MIKROTIK_TIMEOUT` | — | `10` | Timeout request (detik). |
| `MIKROCLAW_ALLOW_WRITE` | — | `false` | **Gerbang keamanan.** `true` mengaktifkan tool yang mengubah konfigurasi. |

Contoh `.env` minimal:

```dotenv
MIKROTIK_HOST=192.168.88.1
MIKROTIK_USER=mikroclaw
MIKROTIK_PASSWORD=rahasia-kuat
MIKROTIK_USE_TLS=true
MIKROTIK_VERIFY_TLS=false
MIKROCLAW_ALLOW_WRITE=false
```

---

## Menghubungkan ke Claude Code

File `.mcp.json` sudah disertakan (scope **project**), isinya:

```json
{
  "mcpServers": {
    "mikroclaw": {
      "command": "uv",
      "args": ["run", "--directory", "/Users/syams/PROJECTS/MikroCLAW", "mikroclaw"]
    }
  }
}
```

Server berjalan via **stdio**; kredensial diambil dari `.env` (bukan dari file
ini), jadi `.mcp.json` aman untuk di-commit.

Langkah di Claude Code:

```
/mcp        # cek server "mikroclaw" muncul & status connected
```

Saat pertama kali, Claude Code akan meminta persetujuan untuk menjalankan MCP
server project-scope — setujui untuk mengaktifkannya.

> Ingin dipakai di **semua** proyek, bukan cuma folder ini? Daftarkan sebagai
> user-scope: `claude mcp add mikroclaw -s user -- uv run --directory /Users/syams/PROJECTS/MikroCLAW mikroclaw`

---

## Daftar tool

### Read — selalu aktif

| Tool | Parameter | Fungsi | REST |
|---|---|---|---|
| `system_resource` | — | Versi RouterOS, CPU, memori, uptime, board, arsitektur. | `GET /system/resource` |
| `system_identity` | — | Nama/identitas perangkat. | `GET /system/identity` |
| `list_interfaces` | — | Semua interface + status running/disabled + statistik. | `GET /interface` |
| `list_ip_addresses` | — | Alamat IP per interface. | `GET /ip/address` |
| `dhcp_leases` | — | Klien DHCP yang mendapat IP dari router. | `GET /ip/dhcp-server/lease` |
| `arp_table` | — | Pemetaan IP ↔ MAC yang terlihat router. | `GET /ip/arp` |
| `firewall_filter_rules` | — | Aturan firewall filter (input/forward/output). | `GET /ip/firewall/filter` |
| `firewall_nat_rules` | — | Aturan NAT (masquerade, port forward). | `GET /ip/firewall/nat` |
| `routing_table` | — | Tabel routing IP (route aktif & statis). | `GET /ip/route` |
| `simple_queues` | — | Simple queue — pembatasan bandwidth per IP/target. | `GET /queue/simple` |
| `address_lists` | — | Isi semua firewall address-list. | `GET /ip/firewall/address-list` |
| `dns_settings` | — | Konfigurasi DNS: server upstream, cache, allow-remote. | `GET /ip/dns` |
| `dhcp_servers` | — | DHCP server + interface & address-pool-nya. | `GET /ip/dhcp-server` |
| `ppp_active` | — | Sesi PPP aktif (PPPoE/L2TP/PPTP/SSTP). | `GET /ppp/active` |
| `bridge_hosts` | — | Tabel host bridge (MAC per port). | `GET /interface/bridge/host` |
| `neighbors` | — | Tetangga terdeteksi (MNDP/CDP/LLDP). | `GET /ip/neighbor` |
| `system_health` | — | Sensor HW: suhu, tegangan, kipas (jika ada). | `GET /system/health` |
| `netwatch` | — | Host yang dipantau Netwatch + status up/down. | `GET /tool/netwatch` |
| `router_users` | — | Daftar user RouterOS + grup/hak aksesnya. | `GET /user` |
| `wifi_interfaces` | — | Interface WiFi (auto wifiwave2/legacy). | `GET /interface/wifi` ↻ `/interface/wireless` |
| `wifi_registrations` | — | Klien WiFi yang terhubung (auto wifiwave2/legacy). | `GET .../registration-table` |
| `wireguard_interfaces` | — | Interface WireGuard (VPN) + public key & port. | `GET /interface/wireguard` |
| `wireguard_peers` | — | Peer WireGuard + allowed-address & handshake. | `GET /interface/wireguard/peers` |
| `ppp_secrets` | — | Akun PPP (PPPoE/VPN) — name/service/profile. | `GET /ppp/secret` |
| `ip_pools` | — | IP pool (rentang IP untuk DHCP/PPP). | `GET /ip/pool` |
| `dns_static` | — | Entri DNS statis (A/CNAME) yang dilayani router. | `GET /ip/dns/static` |
| `ntp_client` | — | Status & konfigurasi NTP client. | `GET /system/ntp/client` |
| `schedulers` | — | Tugas terjadwal RouterOS. | `GET /system/scheduler` |
| `scripts` | — | Script tersimpan di RouterOS. | `GET /system/script` |
| `vlans` | — | Interface VLAN + vlan-id & interface induk. | `GET /interface/vlan` |
| `ip_services` | — | Service IP (api/ssh/www/telnet/winbox) + port. | `GET /ip/service` |
| `dhcp_client` | — | Status DHCP client (mis. IP WAN dari ISP). | `GET /ip/dhcp-client` |
| `ip_cloud` | — | IP publik & DDNS MikroTik (remote access). | `GET /ip/cloud` |
| `system_packages` | — | Paket RouterOS terpasang + status. | `GET /system/package` |
| `routerboard_info` | — | Model, serial, firmware terpasang vs tersedia. | `GET /system/routerboard` |
| `active_sessions` | — | User yang sedang login (audit keamanan). | `GET /user/active` |
| `list_files` | — | File di router (backup/export) + ukuran & waktu. | `GET /file` |
| `firewall_connections` | — | Connection tracking aktif (troubleshooting). | `GET /ip/firewall/connection` |
| `bridge_ports` | — | Pemetaan port ke bridge. | `GET /interface/bridge/port` |
| `certificates` | — | Sertifikat + masa berlaku (audit kedaluwarsa). | `GET /certificate` |
| `dns_cache` | — | Isi cache DNS resolver router. | `GET /ip/dns/cache` |
| `dhcp_networks` | — | Gateway/DNS/netmask yang ditawarkan DHCP. | `GET /ip/dhcp-server/network` |
| `firewall_mangle` | — | Aturan mangle (marking QoS/policy routing). | `GET /ip/firewall/mangle` |
| `queue_tree` | — | Queue tree (bandwidth hierarkis berbasis mark). | `GET /queue/tree` |
| `ppp_profiles` | — | Profil PPP (rate-limit, pool, DNS). | `GET /ppp/profile` |
| `user_groups` | — | Grup hak akses + policy (audit keamanan). | `GET /user/group` |
| `ethernet_ports` | — | Detail port ethernet (link speed, auto-neg). | `GET /interface/ethernet` |
| `ipsec_peers` | — | Konfigurasi peer IPsec. | `GET /ip/ipsec/peer` |
| `ipsec_active_peers` | — | Tunnel IPsec yang sedang aktif. | `GET /ip/ipsec/active-peers` |
| `ipv6_addresses` | — | Alamat IPv6 per interface. | `GET /ipv6/address` |
| `ipv6_routes` | — | Tabel routing IPv6. | `GET /ipv6/route` |
| `ipv6_firewall_filter` | — | Aturan firewall filter IPv6. | `GET /ipv6/firewall/filter` |
| `ipv6_neighbors` | — | Tabel neighbor IPv6 (NDP). | `GET /ipv6/neighbor` |
| `hotspot_servers` | — | Server hotspot + interface & profil. | `GET /ip/hotspot` |
| `hotspot_active` | — | User hotspot yang sedang login. | `GET /ip/hotspot/active` |
| `hotspot_users` | — | Akun user hotspot. | `GET /ip/hotspot/user` |
| `capsman_remote_caps` | — | CAP/AP yang dikelola CAPsMAN (auto legacy/wifiwave2). | `GET /caps-man/remote-cap` ↻ wifiwave2 |
| `capsman_registrations` | — | Klien via CAPsMAN (auto legacy/wifiwave2). | `GET .../registration-table` |
| `wifi_radios` | — | Radio WiFi fisik (wifiwave2). | `GET /interface/wifi/radio` |
| `bgp_sessions` | — | Sesi BGP (v7). | `GET /routing/bgp/session` |
| `ospf_neighbors` | — | Neighbor OSPF + state adjacency (v7). | `GET /routing/ospf/neighbor` |
| `radius_servers` | — | Server RADIUS (AAA). | `GET /radius` |
| `system_history` | — | Riwayat perubahan config (undo). | `GET /system/history` |
| `system_license` | — | Info lisensi (level/CHR). | `GET /system/license` |
| `recent_logs` | `limit` (default 50) | Log terbaru RouterOS. | `GET /log` |
| `ping` | `address`, `count` (default 3) | Ping dari router ke sebuah alamat (diagnostik). | `POST /ping` |
| `traceroute` | `address`, `count` (default 3) | Traceroute (jejak hop) dari router. | `POST /tool/traceroute` |
| `interface_traffic_live` | `interface` | Satu sampel throughput real-time (rx/tx bps). | `POST /interface/monitor-traffic` |
| `check_for_updates` | — | Cek update RouterOS (tidak mengubah config). | `POST /system/package/update/check-for-updates` |
| `rest_get` | `path` | **GET generic** ke path REST apa pun (read-only). | `GET /<path>` |

Contoh `rest_get` untuk hal yang belum punya tool khusus:
`ip/dns`, `ppp/active`, `interface/wireless`, `system/clock`, `queue/simple`.

### Write — perlu `MIKROCLAW_ALLOW_WRITE=true`

Jika flag bernilai `false` (default), tool ini mengembalikan error dan **tidak**
menyentuh router.

| Tool | Parameter | Fungsi | REST |
|---|---|---|---|
| `set_interface_enabled` | `interface_id`, `enabled` | Aktif/nonaktifkan interface (by `.id` atau nama). | `PATCH /interface/<id>` |
| `add_firewall_drop` | `src_address`, `chain` (default `forward`), `comment` | Tambah aturan DROP untuk sumber tertentu. | `PUT /ip/firewall/filter` |
| `add_address_list_entry` | `address`, `address_list`, `comment`, `timeout` | Tambah IP/subnet ke firewall address-list. | `PUT /ip/firewall/address-list` |
| `delete_firewall_rule` | `rule_id` | Hapus satu aturan firewall filter by `.id`. | `DELETE /ip/firewall/filter/<id>` |
| `set_firewall_rule_enabled` | `rule_id`, `enabled` | Aktif/nonaktifkan satu aturan firewall by `.id`. | `PATCH /ip/firewall/filter/<id>` |
| `add_simple_queue` | `name`, `target`, `max_limit` | Tambah simple queue (batas bandwidth target). | `PUT /queue/simple` |
| `create_backup` | `name` (default `mikroclaw`) | Buat file backup konfigurasi (.backup) di router. | `POST /system/backup/save` |
| `reboot_router` | — | Reboot router sekarang (mengganggu koneksi). | `POST /system/reboot` |
| `add_dns_static` | `name`, `address`, `ttl` | Tambah entri DNS statis (A record). | `PUT /ip/dns/static` |
| `add_ppp_secret` | `name`, `password`, `service`, `profile` | Tambah akun PPP (PPPoE/VPN). | `PUT /ppp/secret` |
| `add_wireguard_peer` | `interface`, `public_key`, `allowed_address`, `endpoint_address`, `endpoint_port` | Tambah peer WireGuard. | `PUT /interface/wireguard/peers` |
| `set_ip_service_enabled` | `service_id`, `enabled` | Aktif/nonaktifkan IP service (mis. matikan telnet). | `PATCH /ip/service/<id>` |
| `add_nat_rule` | `chain`, `action`, +opsional (`protocol`, `dst_port`, `to_addresses`, `to_ports`, dll) | Tambah NAT: port-forward (dstnat) / masquerade (srcnat). | `PUT /ip/firewall/nat` |
| `add_static_route` | `dst_address`, `gateway`, `distance`, `comment` | Tambah route statis (termasuk default route). | `PUT /ip/route` |
| `add_static_dhcp_lease` | `address`, `mac_address`, `server`, `comment` | Pin IP statis ke MAC (static lease). | `PUT /ip/dhcp-server/lease` |
| `assign_ip_address` | `address`, `interface`, `comment` | Pasang IP (CIDR) ke interface. | `PUT /ip/address` |
| `set_identity` | `name` | Ganti nama/identitas router. | `POST /system/identity/set` |
| `set_dns_servers` | `servers`, `allow_remote_requests` | Set DNS upstream router. | `POST /ip/dns/set` |
| `remove_address_list_entry` | `entry_id` | Hapus entri address-list by `.id`. | `DELETE /ip/firewall/address-list/<id>` |
| `add_hotspot_user` | `name`, `password`, `profile`, `comment` | Tambah akun user hotspot. | `PUT /ip/hotspot/user` |
| `add_ipv6_address` | `address`, `interface`, `comment` | Pasang alamat IPv6 ke interface. | `PUT /ipv6/address` |
| `rest_write` | `method` (PUT/PATCH/DELETE/POST), `path`, `body` | **Write generic** untuk operasi lanjutan. Gunakan hati-hati. | sesuai `method` |

---

## Skills (playbook orkestrasi)

Selain 92 tool atomik, MikroCLAW menyertakan **Agent Skills** di
[`.claude/skills/`](.claude/skills/) — playbook yang mengoordinasikan banyak tool
menjadi alur kerja siap pakai. Claude Code memuatnya otomatis saat frasa pemicunya
muncul; bisa juga dipanggil eksplisit dengan `/<nama-skill>`.

| Skill | Fungsi | Pemicu contoh |
|---|---|---|
| `mikrotik-health-check` | Laporan kesehatan & maintenance (resource, suhu, firmware, update, WAN, NTP). | "cek kesehatan router", "ada update routeros?" |
| `mikrotik-firewall-audit` | Tinjau filter/NAT/mangle, address-list, koneksi; temuan + rekomendasi. | "audit firewall", "firewall monitoring" |
| `mikrotik-security-audit` | Hardening: service terbuka, user/grup, sesi, sertifikat, DNS, proteksi input. | "audit keamanan", "apakah router aman" |
| `mikrotik-network-overview` | Snapshot inventaris: WAN, subnet, interface/VLAN, routing, klien, tetangga. | "overview jaringan", "dokumentasi config" |
| `mikrotik-troubleshoot` | Diagnosa konektivitas berlapis (L1→IP→DNS→firewall). | "internet mati", "tidak bisa browsing" |
| `mikrotik-backup-snapshot` | Backup biner + snapshot JSON konfigurasi kunci untuk diff/dokumentasi. | "backup mikrotik", "snapshot sebelum perubahan" |

Semua skill **read-only secara default**; remediasi yang mengubah konfigurasi selalu
meminta konfirmasi dan tetap butuh `MIKROCLAW_ALLOW_WRITE=true`.

## Contoh penggunaan

Cukup minta dalam bahasa biasa di Claude Code:

- *"Tampilkan versi RouterOS dan pemakaian CPU/memori."* → `system_resource`
- *"Siapa saja klien DHCP yang aktif sekarang?"* → `dhcp_leases`
- *"Interface mana yang sedang down?"* → `list_interfaces`
- *"Tunjukkan 100 baris log terakhir yang mengandung error."* → `recent_logs` + filter
- *"Ping 8.8.8.8 dari router."* → `ping`
- *"Apa konfigurasi DNS router?"* → `rest_get path=ip/dns`
- *"Blokir IP 10.0.0.5 di firewall."* → `add_firewall_drop` *(butuh `ALLOW_WRITE=true`)*
- *"Nonaktifkan interface ether5."* → `set_interface_enabled` *(butuh `ALLOW_WRITE=true`)*

---

## Uji manual tanpa Claude

Memastikan REST hidup & kredensial benar sebelum menyalakan dari Claude:

```bash
source .env
curl -sk -u "$MIKROTIK_USER:$MIKROTIK_PASSWORD" \
  "https://$MIKROTIK_HOST/rest/system/resource" | jq .
```

Uji server MCP-nya sendiri (memuat & mendaftarkan tool, tanpa konek router):

```bash
uv run python -c "
import asyncio
from mikroclaw.server import mcp
tools = asyncio.run(mcp.list_tools())
print(f'{len(tools)} tools:', ', '.join(t.name for t in tools))
"
```

---

## Keamanan

- **User least-privilege** — buat user khusus (mis. grup `read`); jangan pakai `admin` penuh.
- **Pisahkan kredensial** — hanya di `.env`, yang sudah masuk `.gitignore`. Jangan tempel password di chat atau di `.mcp.json`.
- **Gunakan TLS** — `MIKROTIK_USE_TLS=true`. Set `MIKROTIK_VERIFY_TLS=true` setelah memasang sertifikat tepercaya.
- **Batasi sumber akses** di router: `/ip/service/set www-ssl address=<subnet-tepercaya>`.
- **Write off by default** — biarkan `MIKROCLAW_ALLOW_WRITE=false` kecuali memang sedang melakukan perubahan; matikan lagi sesudahnya.
- **Audit** — operasi `add_firewall_drop` menyertakan komentar `added-by-mikroclaw` agar mudah ditelusuri/dihapus.

---

## Troubleshooting

| Gejala | Kemungkinan sebab | Solusi |
|---|---|---|
| `MIKROTIK_HOST belum di-set` | `.env` belum dibuat/diisi | `cp .env.example .env`, isi `MIKROTIK_HOST`. |
| `Gagal menghubungi RouterOS ...` (timeout) | Port REST tertutup / host salah / firewall | Cek `/ip/service`, ketersambungan jaringan, dan `MIKROTIK_PORT`. |
| `RouterOS membalas 401` | User/password salah | Periksa `MIKROTIK_USER`/`MIKROTIK_PASSWORD`. |
| `RouterOS membalas 404` | Path tidak ada di versi RouterOS ini | Cek nama path; sebagian fitur beda antar versi. |
| Error sertifikat / SSL | Self-signed + verify aktif | Set `MIKROTIK_VERIFY_TLS=false`. |
| `Operasi write dinonaktifkan` | Mencoba tool write saat gate off | Set `MIKROCLAW_ALLOW_WRITE=true` di `.env`, restart server. |
| Server tak muncul di `/mcp` | `.mcp.json` belum disetujui | Jalankan `/mcp`, setujui server project-scope; pastikan `uv` ada di PATH. |
| Perubahan `.env` tak terbaca | Server masih pakai proses lama | Restart koneksi MCP (toggle via `/mcp`) agar `.env` dibaca ulang. |

---

## Kompatibilitas RouterOS v6 vs v7

REST API **hanya ada di RouterOS v7**. Jika router Anda v6:

- Antarmuka tool di `server.py` **tidak perlu berubah**.
- Ganti lapisan transport di `client.py` ke **API biner** (port `8728`/`8729` TLS)
  memakai library seperti [`librouteros`](https://github.com/luqasz/librouteros).
- `RouterOSClient.get/put/patch/delete` cukup dipetakan ke perintah API biner;
  sisanya (config, server, daftar tool) tetap sama.

API biner juga bekerja di v7, sehingga bisa dipakai sebagai transport tunggal
lintas versi bila diinginkan.

---

## Struktur proyek

```
MikroCLAW/
├── .mcp.json              # registrasi server untuk Claude Code (project-scope)
├── .env.example           # template variabel environment
├── .env                   # kredensial nyata (di-gitignore, buat sendiri)
├── .gitignore
├── pyproject.toml         # metadata + dependency + entry point `mikroclaw`
├── README.md
├── .claude/skills/        # Agent Skills (playbook orkestrasi tool)
│   ├── mikrotik-health-check/SKILL.md
│   ├── mikrotik-firewall-audit/SKILL.md
│   ├── mikrotik-security-audit/SKILL.md
│   ├── mikrotik-network-overview/SKILL.md
│   ├── mikrotik-troubleshoot/SKILL.md
│   └── mikrotik-backup-snapshot/SKILL.md
└── src/mikroclaw/
    ├── __init__.py        # versi paket
    ├── config.py          # baca .env/env → objek Config + validasi
    ├── client.py          # client REST RouterOS v7 (async httpx)
    └── server.py          # FastMCP + definisi 92 tool + write-gate
```

---

## Pengembangan: menambah tool

Tambahkan fungsi async di `src/mikroclaw/server.py` dengan dekorator `@mcp.tool()`.
Docstring menjadi deskripsi tool yang dilihat Claude — tulis sejelas mungkin.

Contoh menambah daftar simple queue (read):

```python
@mcp.tool()
async def simple_queues() -> Any:
    """Daftar simple queue (pembatasan bandwidth per target)."""
    return await _ros().get("/queue/simple")
```

Contoh tool write (selalu panggil `_require_write()` di awal):

```python
@mcp.tool()
async def reboot_router() -> Any:
    """Reboot router. BUTUH MIKROCLAW_ALLOW_WRITE=true."""
    _require_write()
    return await _ros().post("/system/reboot")
```

Setelah mengubah kode, restart koneksi MCP di Claude Code (`/mcp`) agar tool baru
terdeteksi. Verifikasi cepat:

```bash
uv run python -c "import asyncio; from mikroclaw.server import mcp; print(len(asyncio.run(mcp.list_tools())), 'tools')"
```

---

*MikroCLAW dibuat untuk administrasi MikroTik yang sah pada perangkat milik/dikuasakan
kepada Anda. Gunakan secara bertanggung jawab.*
