"""Data plane MikroCLAW Pulse — poll RouterOS bertingkat & susun state live.

Tiga loop cadence (semua read-only):
  - fast (1 dtk): system_resource, system_health, throughput per-interface
  - mid  (5 dtk): klien (DHCP/PPP/hotspot/wifi), counter firewall, queue
  - slow (30 dtk): WAN/IP publik, service, sesi login, sertifikat, gateway
  - ping (5 dtk): RTT gateway & internet (task terpisah agar tak blok loop fast)

Throughput diturunkan dari delta counter rx-byte/tx-byte `/interface` (1 request
untuk semua interface) — jauh lebih murah daripada monitor-traffic per-interface.
State disimpan in-memory + ring-buffer (deque) untuk sparkline 60 detik terakhir.
"""

from __future__ import annotations

import asyncio
import re
import time
from collections import deque
from typing import Any

from ..client import RouterOSClient, RouterOSError

SPARK_LEN = 60  # jumlah sampel sparkline (~60 dtk pada cadence 1 dtk)

# Peta OUI ringkas (3 oktet pertama MAC -> vendor) untuk tebakan perangkat.
# Deterministik & murni offline; bukan database lengkap, hanya yang umum.
OUI: dict[str, str] = {
    "00:0C:29": "VMware", "00:50:56": "VMware", "00:1C:42": "Parallels",
    "DC:A6:32": "Raspberry Pi", "B8:27:EB": "Raspberry Pi", "E4:5F:01": "Raspberry Pi",
    "00:1A:11": "Google", "F4:F5:E8": "Google", "44:07:0B": "Google",
    "3C:5A:B4": "Google", "F8:8F:CA": "Google",
    "AC:DE:48": "Apple", "F0:18:98": "Apple", "A4:83:E7": "Apple",
    "DC:A9:04": "Apple", "98:01:A7": "Apple", "F4:0F:24": "Apple",
    "00:16:6C": "Samsung", "5C:0A:5B": "Samsung", "8C:77:12": "Samsung",
    "C8:3D:DC": "Samsung", "34:23:BA": "Samsung",
    "00:1D:0F": "TP-Link", "50:C7:BF": "TP-Link", "A4:2B:B0": "TP-Link",
    "EC:08:6B": "TP-Link", "B0:48:7A": "TP-Link",
    "B8:69:F4": "Xiaomi", "64:09:80": "Xiaomi", "78:11:DC": "Xiaomi",
    "F8:1A:67": "TP-Link", "00:0C:42": "MikroTik", "18:FD:74": "MikroTik",
    "48:8F:5A": "MikroTik", "64:D1:54": "MikroTik", "DC:2C:6E": "MikroTik",
    "CC:2D:E0": "MikroTik", "74:4D:28": "MikroTik", "2C:C8:1B": "MikroTik",
    "00:1B:44": "Cisco", "00:25:9C": "Cisco", "E0:CB:4E": "Asus",
    "AC:9E:17": "Asus", "00:E0:4C": "Realtek", "52:54:00": "QEMU/KVM",
    "00:11:32": "Synology", "00:90:A9": "Western Digital",
    "B0:7F:B9": "Netgear", "A0:40:A0": "Netgear",
    "EC:FA:BC": "Hikvision", "BC:AD:28": "Hikvision", "C0:56:E3": "Hikvision",
    "00:80:F0": "Panasonic", "FC:FB:FB": "Cisco",
}


def _to_int(v: Any, default: int = 0) -> int:
    try:
        return int(float(v))
    except (TypeError, ValueError):
        return default


def _to_float(v: Any, default: float = 0.0) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def _is_true(v: Any) -> bool:
    return str(v).strip().lower() in ("true", "yes", "1")


def _vendor(mac: str | None) -> str:
    if not mac:
        return ""
    pfx = mac.upper().replace("-", ":")[:8]
    return OUI.get(pfx, "")


_MS_RE = re.compile(r"([\d.]+)\s*ms")
_US_RE = re.compile(r"([\d.]+)\s*us")


def _parse_rtt_ms(value: Any) -> float | None:
    """Ambil RTT dalam milidetik dari field seperti '12ms', '8ms764us', '1.2ms'."""
    if value is None:
        return None
    s = str(value)
    m = _MS_RE.search(s)
    if m:
        return round(_to_float(m.group(1)), 1)
    u = _US_RE.search(s)
    if u:
        return round(_to_float(u.group(1)) / 1000.0, 2)
    # angka polos -> anggap ms
    f = _to_float(s, default=-1.0)
    return round(f, 1) if f >= 0 else None


class Poller:
    """Mengorkestrasi loop polling & menyimpan state live untuk SSE."""

    def __init__(self, ros: RouterOSClient) -> None:
        self.ros = ros
        self._running = False
        self._tasks: list[asyncio.Task[Any]] = []
        self._cond = asyncio.Condition()

        # state turunan untuk hitung delta
        self._prev_if: dict[str, tuple[int, int, float]] = {}
        self._prev_fw: tuple[int, float] | None = None
        self._prev_q: dict[str, tuple[int, int, float]] = {}
        self._eth_speed: dict[str, str] = {}
        self._wan_iface: str | None = None
        self._gateway: str | None = None

        self.spark: dict[str, deque[int]] = {
            "cpu": deque(maxlen=SPARK_LEN),
            "mem": deque(maxlen=SPARK_LEN),
            "wan_rx": deque(maxlen=SPARK_LEN),
            "wan_tx": deque(maxlen=SPARK_LEN),
        }

        self.state: dict[str, Any] = {
            "ts": 0.0,
            "connected": False,
            "error": None,
            "system": {
                "identity": "", "version": "", "board": "", "arch": "",
                "uptime": "", "cpu_load": 0, "cpu_count": 0,
                "mem_used": 0, "mem_total": 0, "mem_used_pct": 0,
                "disk_used": 0, "disk_total": 0, "disk_used_pct": 0,
                "license": "",
            },
            "health": {},  # name -> {"value","unit"}
            "wan": {
                "iface": "", "dhcp_address": "", "public_address": "",
                "ddns": "", "gateway": "", "gateway_ping_ms": None,
                "internet_ping_ms": None, "rx_bps": 0, "tx_bps": 0,
            },
            "interfaces": [],
            "clients": [],
            "services": [],
            "counters": {
                "clients_total": 0, "interfaces_up": 0, "interfaces_down": 0,
                "conntrack": None, "fw_drops_per_s": 0, "login_sessions": 0,
                "cert_nearest_days": None,
            },
        }

    # ------------------------------------------------------------------ lifecycle
    async def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._tasks = [
            asyncio.create_task(self._loop_fast(), name="pulse-fast"),
            asyncio.create_task(self._loop_mid(), name="pulse-mid"),
            asyncio.create_task(self._loop_slow(), name="pulse-slow"),
            asyncio.create_task(self._loop_ping(), name="pulse-ping"),
        ]

    async def stop(self) -> None:
        self._running = False
        for t in self._tasks:
            t.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)
        self._tasks = []
        await self.ros.aclose()

    async def _notify(self) -> None:
        async with self._cond:
            self._cond.notify_all()

    async def wait(self) -> None:
        """Tunggu pembaruan state berikutnya (dipakai generator SSE)."""
        async with self._cond:
            await self._cond.wait()

    def snapshot(self) -> dict[str, Any]:
        snap = {k: v for k, v in self.state.items()}
        snap["ts"] = time.time()
        snap["sparklines"] = {k: list(v) for k, v in self.spark.items()}
        return snap

    # ------------------------------------------------------------------ loops
    async def _loop_fast(self) -> None:
        while self._running:
            t0 = time.monotonic()
            try:
                res = await self.ros.get("/system/resource")
                obj = (res[0] if res else None) if isinstance(res, list) else res
                if isinstance(obj, dict):
                    self._update_system(obj)
                ifaces = await self.ros.get("/interface")
                self._update_interfaces(ifaces if isinstance(ifaces, list) else [])
                try:
                    health = await self.ros.get("/system/health")
                    self._update_health(health)
                except RouterOSError:
                    pass  # board tanpa sensor
                self.state["connected"] = True
                self.state["error"] = None
            except Exception as exc:  # loop tak boleh mati karena satu respons aneh
                self.state["connected"] = False
                self.state["error"] = str(exc)
            await self._notify()
            await asyncio.sleep(max(0.0, 1.0 - (time.monotonic() - t0)))

    async def _loop_mid(self) -> None:
        while self._running:
            t0 = time.monotonic()
            try:
                leases = await self._safe_get("/ip/dhcp-server/lease")
                arp = await self._safe_get("/ip/arp")
                ppp = await self._safe_get("/ppp/active")
                hotspot = await self._safe_get("/ip/hotspot/active")
                wifi = await self._safe_first(
                    "/interface/wifi/registration-table",
                    "/interface/wireless/registration-table",
                )
                queues = await self._safe_get("/queue/simple")
                fw = await self._safe_get("/ip/firewall/filter")
                self._update_clients(leases, arp, ppp, hotspot, wifi, queues)
                self._update_firewall(fw)
                await self._update_conntrack()
            except Exception as exc:  # loop tak boleh mati karena satu respons aneh
                self.state["error"] = str(exc)
            await self._notify()
            await asyncio.sleep(max(0.0, 5.0 - (time.monotonic() - t0)))

    async def _loop_slow(self) -> None:
        while self._running:
            t0 = time.monotonic()
            try:
                ident = await self._safe_get("/system/identity")
                if ident:
                    obj = ident[0] if isinstance(ident, list) else ident
                    self.state["system"]["identity"] = obj.get("name", "")
                lic = await self._safe_get("/system/license")
                if lic:
                    obj = lic[0] if isinstance(lic, list) else lic
                    self.state["system"]["license"] = obj.get("level") or obj.get("nlevel") or ""

                eth = await self._safe_get("/interface/ethernet")
                self._eth_speed = {
                    e.get("name", ""): (e.get("speed") or "")
                    for e in eth or []
                }

                self._resolve_wan(
                    await self._safe_get("/ip/dhcp-client"),
                    await self._safe_get("/ip/cloud"),
                    await self._safe_get("/ip/route"),
                )
                self._update_services(await self._safe_get("/ip/service"))

                sessions = await self._safe_get("/user/active")
                self.state["counters"]["login_sessions"] = len(sessions or [])

                self._update_certs(await self._safe_get("/certificate"))
            except Exception as exc:  # loop tak boleh mati karena satu respons aneh
                self.state["error"] = str(exc)
            await self._notify()
            await asyncio.sleep(max(0.0, 30.0 - (time.monotonic() - t0)))

    async def _loop_ping(self) -> None:
        # beri jeda awal supaya slow-loop sempat menentukan gateway
        await asyncio.sleep(3)
        while self._running:
            t0 = time.monotonic()
            try:
                if self._gateway:
                    self.state["wan"]["gateway_ping_ms"] = await self._ping(self._gateway)
                self.state["wan"]["internet_ping_ms"] = await self._ping("8.8.8.8")
            except Exception:  # jaga loop tetap hidup; kegagalan ping sudah jadi None
                pass
            await self._notify()
            await asyncio.sleep(max(0.0, 5.0 - (time.monotonic() - t0)))

    # ------------------------------------------------------------------ helpers
    async def _safe_get(self, path: str) -> Any:
        try:
            out = await self.ros.get(path)
            return out if out is not None else []
        except RouterOSError:
            return []

    async def _safe_first(self, *paths: str) -> Any:
        for p in paths:
            try:
                out = await self.ros.get(p)
                if out is not None:
                    return out
            except RouterOSError:
                continue
        return []

    async def _ping(self, address: str) -> float | None:
        try:
            res = await self.ros.post("/ping", {"address": address, "count": "1"})
        except RouterOSError:
            return None
        if not isinstance(res, list) or not res:
            return None
        last = res[-1]
        if _to_int(last.get("received")) == 0:
            return None
        return _parse_rtt_ms(last.get("avg-rtt") or last.get("time"))

    # ------------------------------------------------------------------ updaters
    def _update_system(self, r: dict[str, Any]) -> None:
        total_mem = _to_int(r.get("total-memory"))
        free_mem = _to_int(r.get("free-memory"))
        total_hdd = _to_int(r.get("total-hdd-space"))
        free_hdd = _to_int(r.get("free-hdd-space"))
        cpu = _to_int(r.get("cpu-load"))
        sysd = self.state["system"]
        sysd["version"] = r.get("version", "")
        sysd["board"] = r.get("board-name", "")
        sysd["arch"] = r.get("architecture-name", "")
        sysd["uptime"] = r.get("uptime", "")
        sysd["cpu_load"] = cpu
        sysd["cpu_count"] = _to_int(r.get("cpu-count"))
        sysd["mem_total"] = total_mem
        sysd["mem_used"] = total_mem - free_mem
        sysd["mem_used_pct"] = round((total_mem - free_mem) / total_mem * 100) if total_mem else 0
        sysd["disk_total"] = total_hdd
        sysd["disk_used"] = total_hdd - free_hdd
        sysd["disk_used_pct"] = round((total_hdd - free_hdd) / total_hdd * 100) if total_hdd else 0
        self.spark["cpu"].append(cpu)
        self.spark["mem"].append(sysd["mem_used_pct"])

    def _update_health(self, health: Any) -> None:
        out: dict[str, dict[str, str]] = {}
        if isinstance(health, list):
            for item in health:
                name = item.get("name")
                if name:
                    out[name] = {"value": item.get("value", ""), "unit": item.get("type", "")}
        elif isinstance(health, dict):
            for k, v in health.items():
                if not k.startswith("."):
                    out[k] = {"value": str(v), "unit": ""}
        self.state["health"] = out

    def _update_interfaces(self, ifaces: list[dict[str, Any]]) -> None:
        now = time.monotonic()
        out: list[dict[str, Any]] = []
        seen: set[str] = set()
        up = down = 0
        for it in ifaces:
            name = it.get("name", "")
            if not name:
                continue
            rxb = _to_int(it.get("rx-byte"))
            txb = _to_int(it.get("tx-byte"))
            disabled = _is_true(it.get("disabled"))
            running = _is_true(it.get("running"))
            rx_bps = tx_bps = 0
            prev = self._prev_if.get(name)
            if prev:
                dt = now - prev[2]
                if dt > 0:
                    drx, dtx = rxb - prev[0], txb - prev[1]
                    if drx >= 0:
                        rx_bps = int(drx * 8 / dt)
                    if dtx >= 0:
                        tx_bps = int(dtx * 8 / dt)
            self._prev_if[name] = (rxb, txb, now)
            seen.add(name)
            if not disabled:
                if running:
                    up += 1
                else:
                    down += 1
            out.append({
                "name": name,
                "type": it.get("type", ""),
                "running": running,
                "disabled": disabled,
                "rx_bps": rx_bps,
                "tx_bps": tx_bps,
                "rx_byte": rxb,
                "tx_byte": txb,
                "rx_error": _to_int(it.get("rx-error")) + _to_int(it.get("rx-drop")),
                "tx_error": _to_int(it.get("tx-error")) + _to_int(it.get("tx-drop")),
                "speed": self._eth_speed.get(name, ""),
                "comment": it.get("comment", ""),
            })
        out.sort(key=lambda o: (not o["running"], -(o["rx_bps"] + o["tx_bps"]), o["name"]))
        self.state["interfaces"] = out
        self.state["counters"]["interfaces_up"] = up
        self.state["counters"]["interfaces_down"] = down
        for stale in [k for k in self._prev_if if k not in seen]:
            self._prev_if.pop(stale, None)

        if self._wan_iface:
            for o in out:
                if o["name"] == self._wan_iface:
                    self.state["wan"]["rx_bps"] = o["rx_bps"]
                    self.state["wan"]["tx_bps"] = o["tx_bps"]
                    self.spark["wan_rx"].append(o["rx_bps"])
                    self.spark["wan_tx"].append(o["tx_bps"])
                    break

    def _update_clients(
        self,
        leases: list[dict[str, Any]],
        arp: list[dict[str, Any]],
        ppp: list[dict[str, Any]],
        hotspot: list[dict[str, Any]],
        wifi: list[dict[str, Any]],
        queues: list[dict[str, Any]],
    ) -> None:
        clients: dict[str, dict[str, Any]] = {}

        def mac_key(mac: str, ip: str) -> str:
            return mac.upper() if mac else f"ip:{ip}"

        for lease in leases or []:
            if lease.get("status") != "bound":
                continue
            ip = lease.get("active-address") or lease.get("address", "")
            mac = lease.get("active-mac-address") or lease.get("mac-address", "")
            clients[mac_key(mac, ip)] = {
                "ip": ip, "mac": mac,
                "host": lease.get("host-name") or lease.get("comment") or "",
                "kind": "dhcp", "iface": lease.get("server", ""),
                "vendor": _vendor(mac), "signal": None,
                "rx_bps": None, "tx_bps": None, "uptime": "",
                "info": lease.get("expires-after", ""),
            }

        for p in ppp or []:
            ip = p.get("address", "")
            name = p.get("name", "")
            mac = p.get("caller-id", "")
            clients[f"ppp:{name}"] = {
                "ip": ip, "mac": mac if ":" in mac else "",
                "host": name, "kind": "pppoe",
                "iface": p.get("interface", "") or p.get("service", ""),
                "vendor": _vendor(mac if ":" in mac else None), "signal": None,
                "rx_bps": None, "tx_bps": None,
                "uptime": p.get("uptime", ""), "info": p.get("service", ""),
            }

        for h in hotspot or []:
            ip = h.get("address", "")
            mac = h.get("mac-address", "")
            k = mac_key(mac, ip)
            entry = clients.get(k, {
                "ip": ip, "mac": mac, "vendor": _vendor(mac),
                "signal": None, "rx_bps": None, "tx_bps": None,
            })
            entry.update({
                "host": h.get("user", "") or entry.get("host", ""),
                "kind": "hotspot", "iface": h.get("server", ""),
                "uptime": h.get("uptime", ""), "info": h.get("user", ""),
            })
            clients[k] = entry

        # enrich sinyal wifi by MAC
        for w in wifi or []:
            mac = (w.get("mac-address") or "").upper()
            if not mac:
                continue
            sig = w.get("signal") or w.get("signal-strength") or w.get("rx-signal")
            for k, c in clients.items():
                if c.get("mac", "").upper() == mac:
                    c["signal"] = _signal_dbm(sig)
                    c["wireless"] = True
                    break
            else:
                clients[mac] = {
                    "ip": "", "mac": mac, "host": w.get("comment", ""),
                    "kind": "wifi", "iface": w.get("interface", ""),
                    "vendor": _vendor(mac), "signal": _signal_dbm(sig),
                    "rx_bps": None, "tx_bps": None,
                    "uptime": w.get("uptime", ""), "info": w.get("interface", ""),
                    "wireless": True,
                }

        self._apply_queue_bandwidth(clients, queues)

        lst = list(clients.values())
        lst.sort(key=lambda c: (-((c.get("rx_bps") or 0) + (c.get("tx_bps") or 0)), c.get("ip", "")))
        self.state["clients"] = lst
        self.state["counters"]["clients_total"] = len(lst)

    def _apply_queue_bandwidth(
        self, clients: dict[str, dict[str, Any]], queues: list[dict[str, Any]]
    ) -> None:
        now = time.monotonic()
        ip_to_client = {c["ip"]: c for c in clients.values() if c.get("ip")}
        seen: set[str] = set()
        for q in queues or []:
            name = q.get("name", "")
            target = (q.get("target") or "").split(",")[0].split("/")[0]
            seen.add(name)
            up_b, down_b = _split_pair(q.get("bytes"))
            prev = self._prev_q.get(name)
            self._prev_q[name] = (up_b, down_b, now)
            c = ip_to_client.get(target)
            if not c or not prev:
                continue
            dt = now - prev[2]
            if dt <= 0:
                continue
            dup, ddown = up_b - prev[0], down_b - prev[1]
            if dup >= 0:
                c["tx_bps"] = int(dup * 8 / dt)
            if ddown >= 0:
                c["rx_bps"] = int(ddown * 8 / dt)
        for stale in [k for k in self._prev_q if k not in seen]:
            self._prev_q.pop(stale, None)

    def _update_firewall(self, fw: list[dict[str, Any]]) -> None:
        now = time.monotonic()
        drop_pkts = 0
        for r in fw or []:
            if r.get("action") in ("drop", "reject", "tarpit") and not _is_true(r.get("disabled")):
                drop_pkts += _to_int(r.get("packets"))
        if self._prev_fw:
            dt = now - self._prev_fw[1]
            if dt > 0:
                delta = drop_pkts - self._prev_fw[0]
                self.state["counters"]["fw_drops_per_s"] = round(delta / dt, 1) if delta >= 0 else 0
        self._prev_fw = (drop_pkts, now)

    async def _update_conntrack(self) -> None:
        # endpoint 'tracking' memberi total-entries tanpa menarik seluruh tabel
        try:
            tr = await self.ros.get("/ip/firewall/connection/tracking")
            obj = tr[0] if isinstance(tr, list) else tr
            if isinstance(obj, dict) and "total-entries" in obj:
                self.state["counters"]["conntrack"] = _to_int(obj.get("total-entries"))
                return
        except RouterOSError:
            pass
        self.state["counters"]["conntrack"] = None

    def _update_services(self, services: list[dict[str, Any]]) -> None:
        out = []
        for s in services or []:
            if not _is_true(s.get("disabled")):
                out.append({
                    "name": s.get("name", ""),
                    "port": s.get("port", ""),
                    "address": s.get("address", ""),
                })
        out.sort(key=lambda x: x["name"])
        self.state["services"] = out

    def _update_certs(self, certs: list[dict[str, Any]]) -> None:
        nearest: int | None = None
        for c in certs or []:
            days = _days_until(c.get("invalid-after"))
            if days is not None and (nearest is None or days < nearest):
                nearest = days
        self.state["counters"]["cert_nearest_days"] = nearest

    def _resolve_wan(
        self, dhcp_client: list[dict[str, Any]], cloud: Any, routes: list[dict[str, Any]]
    ) -> None:
        wan = self.state["wan"]
        iface = None
        addr = ""
        for d in dhcp_client or []:
            if d.get("status") == "bound" or d.get("address"):
                iface = d.get("interface")
                addr = (d.get("address") or "").split("/")[0]
                break
        if not iface:
            # fallback: interface PPPoE-out bila ada
            for o in self.state["interfaces"]:
                if "pppoe" in (o.get("type") or "").lower() or "pppoe" in o["name"].lower():
                    iface = o["name"]
                    break
        gateway = ""
        for r in routes or []:
            if r.get("dst-address") == "0.0.0.0/0" and _is_true(r.get("active", "true")):
                gateway = (r.get("gateway") or "").split(",")[0].split("%")[0]
                break
        self._wan_iface = iface or self._wan_iface
        self._gateway = gateway or self._gateway
        wan["iface"] = self._wan_iface or ""
        wan["dhcp_address"] = addr or wan["dhcp_address"]
        wan["gateway"] = self._gateway or ""

        obj = (cloud[0] if isinstance(cloud, list) else cloud) or {}
        if isinstance(obj, dict):
            wan["public_address"] = obj.get("public-address", "") or wan["public_address"]
            wan["ddns"] = obj.get("dns-name", "") or wan["ddns"]


def _signal_dbm(value: Any) -> int | None:
    if value is None:
        return None
    m = re.search(r"-?\d+", str(value))
    return int(m.group(0)) if m else None


def _split_pair(value: Any) -> tuple[int, int]:
    """RouterOS field gabungan 'a/b' (mis. bytes='123/456') -> (a, b)."""
    if not value:
        return (0, 0)
    parts = str(value).split("/")
    if len(parts) != 2:
        return (0, 0)
    return (_to_int(parts[0]), _to_int(parts[1]))


_DATE_RE = re.compile(r"(\d{4})-(\d{2})-(\d{2})")


def _days_until(invalid_after: Any) -> int | None:
    """Hitung sisa hari sampai tanggal (format RouterOS 'YYYY-MM-DD hh:mm:ss')."""
    if not invalid_after:
        return None
    m = _DATE_RE.search(str(invalid_after))
    if not m:
        return None
    import datetime
    try:
        target = datetime.date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
    except ValueError:
        return None
    return (target - datetime.date.today()).days
