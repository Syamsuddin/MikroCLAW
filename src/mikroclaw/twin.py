"""MikroCLAW Twin — simulator "what-if" paket di atas ruleset MikroTik (murni).

`simulate_packet(ev, pkt)` menelusuri sebuah paket HIPOTETIS menembus alur paket
RouterOS yang disederhanakan namun setia pada urutannya:

    mangle prerouting → dst-nat → keputusan routing (input vs forward)
        → filter (chain terkait, ikuti jump/return) → src-nat

lalu mengembalikan verdict akhir (diterima / drop / reject) beserta JEJAK tiap
tahap + aturan mana yang cocok. `simulate_change(...)` menyisipkan satu aturan
hipotetis, menjalankan ulang penelusuran, dan melaporkan apakah & bagaimana
perilaku berubah — sebuah uji pra-terbang sebelum menyentuh perangkat.

Murni (tanpa I/O) seperti `roles.py`: menerima dict bukti hasil GET REST
(filter/nat/mangle/route/address-list/address) + spesifikasi paket, mudah diuji
tanpa router. Pengumpulan bukti & narasi dilakukan di tool `simulate_packet`
pada server.py — modul ini hanya menalar.

Catatan kesetiaan: ini model yang BERGUNA, bukan replika sempurna RouterOS.
Subset yang didukung mencakup match paling umum (chain, src/dst-address(+list),
protocol, src/dst-port, in/out-interface, connection-state pada paket "new",
negasi `!`). Fitur tepi (interface-list, layer7, per-connection-classifier, dll)
diabaikan dengan aman dan ditandai pada jejak.
"""

from __future__ import annotations

import ipaddress
from typing import Any

# Aksi yang MENGAKHIRI penelusuran chain filter.
_TERMINAL = {"accept", "drop", "reject", "tarpit"}
# Aksi filter non-terminal yang dilewati (paket lanjut ke aturan berikutnya).
_PASSTHROUGH = {
    "log", "passthrough", "add-src-to-address-list", "add-dst-to-address-list",
    "fasttrack-connection",
}


def _truthy(v: Any) -> bool:
    return str(v).strip().lower() in ("true", "yes", "1")


def _as_list(v: Any) -> list[dict[str, Any]]:
    if isinstance(v, list):
        return [r for r in v if isinstance(r, dict)]
    if isinstance(v, dict):
        return [v]
    return []


def _enabled(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [r for r in rows if not _truthy(r.get("disabled"))]


def _ip(addr: str) -> ipaddress._BaseAddress | None:
    try:
        return ipaddress.ip_address(addr.strip())
    except ValueError:
        return None


def _addr_match(spec: Any, ip_str: str) -> bool | None:
    """Apakah `ip_str` cocok dengan spesifikasi address RouterOS?

    Dukungan: IP tunggal, CIDR (`10.0.0.0/24`), rentang (`10.0.0.1-10.0.0.9`),
    dan negasi awalan `!`. Kembalikan None bila spec kosong (= tak membatasi).
    """
    if spec is None or str(spec).strip() == "":
        return None
    raw = str(spec).strip()
    neg = raw.startswith("!")
    if neg:
        raw = raw[1:].strip()
    ip = _ip(ip_str)
    if ip is None:
        return None
    res = _addr_contains(raw, ip)
    return (not res) if neg else res


def _addr_contains(spec: str, ip: ipaddress._BaseAddress) -> bool:
    if "-" in spec and "/" not in spec:
        lo, _, hi = spec.partition("-")
        a, b = _ip(lo), _ip(hi)
        if a is not None and b is not None:
            try:
                return a <= ip <= b  # type: ignore[operator]
            except TypeError:
                return False
        return False
    try:
        net = ipaddress.ip_network(spec, strict=False)
    except ValueError:
        return False
    return ip in net


def _ports(spec: Any) -> list[tuple[int, int]] | None:
    """Parse spesifikasi port RouterOS (`80`, `80,443`, `1000-2000`) -> rentang."""
    if spec is None or str(spec).strip() == "":
        return None
    out: list[tuple[int, int]] = []
    for part in str(spec).split(","):
        part = part.strip()
        if not part:
            continue
        if "-" in part:
            lo, _, hi = part.partition("-")
            try:
                out.append((int(lo), int(hi)))
            except ValueError:
                continue
        else:
            try:
                out.append((int(part), int(part)))
            except ValueError:
                continue
    return out or None


def _port_match(spec: Any, port: Any) -> bool | None:
    ranges = _ports(spec)
    if ranges is None:
        return None
    try:
        p = int(port)
    except (TypeError, ValueError):
        return None
    return any(lo <= p <= hi for lo, hi in ranges)


def _iface_match(spec: Any, iface: Any) -> bool | None:
    if spec is None or str(spec).strip() == "":
        return None
    raw = str(spec).strip()
    neg = raw.startswith("!")
    if neg:
        raw = raw[1:].strip()
    res = raw == str(iface or "").strip()
    return (not res) if neg else res


def _list_member(list_name: Any, ip_str: str, addr_lists: dict[str, list[str]]) -> bool | None:
    if list_name is None or str(list_name).strip() == "":
        return None
    raw = str(list_name).strip()
    neg = raw.startswith("!")
    if neg:
        raw = raw[1:].strip()
    ip = _ip(ip_str)
    if ip is None:
        return None
    member = any(
        _addr_contains(entry, ip) for entry in addr_lists.get(raw, [])
    )
    return (not member) if neg else member


def _index_address_lists(rows: Any) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    for r in _enabled(_as_list(rows)):
        name = str(r.get("list", "")).strip()
        addr = str(r.get("address", "")).strip()
        if name and addr:
            out.setdefault(name, []).append(addr)
    return out


def _local_ips(addresses: Any) -> set[str]:
    """Host-IP milik router sendiri (dari /ip/address) untuk membedakan input vs forward."""
    out: set[str] = set()
    for a in _as_list(addresses):
        raw = str(a.get("address", "")).split("/")[0].strip()
        if _ip(raw) is not None:
            out.add(raw)
    return out


def _rule_matches(
    rule: dict[str, Any],
    pkt: dict[str, Any],
    chain: str,
    addr_lists: dict[str, list[str]],
    state: str,
) -> tuple[bool, list[str]]:
    """Apakah `rule` cocok dengan paket pada `chain`? Kembalikan (cocok, alasan_tak_cocok)."""
    why: list[str] = []
    if str(rule.get("chain", "")).strip() != chain:
        return False, ["chain beda"]

    checks: list[tuple[str, bool | None]] = [
        ("src-address", _addr_match(rule.get("src-address"), pkt.get("src", ""))),
        ("dst-address", _addr_match(rule.get("dst-address"), pkt.get("dst", ""))),
        ("src-address-list", _list_member(rule.get("src-address-list"), pkt.get("src", ""), addr_lists)),
        ("dst-address-list", _list_member(rule.get("dst-address-list"), pkt.get("dst", ""), addr_lists)),
        ("dst-port", _port_match(rule.get("dst-port"), pkt.get("dst_port"))),
        ("src-port", _port_match(rule.get("src-port"), pkt.get("src_port"))),
        ("in-interface", _iface_match(rule.get("in-interface"), pkt.get("in_interface"))),
        ("out-interface", _iface_match(rule.get("out-interface"), pkt.get("out_interface"))),
    ]

    proto = str(rule.get("protocol", "")).strip().lower()
    if proto:
        checks.append(("protocol", proto == str(pkt.get("protocol", "")).strip().lower()))

    cs = str(rule.get("connection-state", "")).strip().lower()
    if cs:
        wanted = {s.strip() for s in cs.lstrip("!").split(",") if s.strip()}
        member = state in wanted
        checks.append(("connection-state", (not member) if cs.startswith("!") else member))

    for field, res in checks:
        if res is False:
            why.append(f"{field} tak cocok")
            return False, why
    return True, []


def _walk_filter(
    rules: list[dict[str, Any]],
    pkt: dict[str, Any],
    chain: str,
    addr_lists: dict[str, list[str]],
    state: str,
    trace: list[dict[str, Any]],
    depth: int = 0,
) -> str | None:
    """Telusuri satu chain filter; ikuti jump/return. Kembalikan aksi terminal atau None."""
    if depth > 8:  # cegah rekursi jump tak wajar
        trace.append({"tahap": "filter", "chain": chain, "catatan": "kedalaman jump maksimum"})
        return None
    for idx, rule in enumerate(rules):
        ok, _ = _rule_matches(rule, pkt, chain, addr_lists, state)
        if not ok:
            continue
        action = str(rule.get("action", "accept")).strip().lower()
        ev = {
            "tahap": "filter", "chain": chain, "indeks": idx,
            "action": action, "comment": rule.get("comment", ""),
            "ringkas": _rule_brief(rule),
        }
        if action == "return":
            ev["catatan"] = "return — keluar dari chain ini"
            trace.append(ev)
            return None
        if action == "jump":
            target = str(rule.get("jump-target", "")).strip()
            ev["catatan"] = f"jump ke chain '{target}'"
            trace.append(ev)
            res = _walk_filter(rules, pkt, target, addr_lists, state, trace, depth + 1)
            if res is not None:
                return res
            continue
        if action in _PASSTHROUGH or action.startswith("mark-"):
            ev["catatan"] = "non-terminal — lanjut ke aturan berikutnya"
            trace.append(ev)
            continue
        trace.append(ev)
        if action in _TERMINAL:
            return action
        # aksi tak dikenal di filter -> perlakukan non-terminal & tandai
        trace[-1]["catatan"] = "aksi tak dikenal — dianggap non-terminal"
    return None


def _rule_brief(rule: dict[str, Any]) -> str:
    keys = (
        "chain", "action", "protocol", "src-address", "dst-address",
        "src-address-list", "dst-address-list", "dst-port", "src-port",
        "in-interface", "out-interface", "connection-state", "jump-target",
    )
    parts = [f"{k}={rule.get(k)}" for k in keys if str(rule.get(k, "")).strip()]
    return " ".join(parts)


def _route_lookup(routes: list[dict[str, Any]], dst: str) -> dict[str, Any] | None:
    """Longest-prefix match di antara route aktif untuk `dst`."""
    ip = _ip(dst)
    if ip is None:
        return None
    best: tuple[int, dict[str, Any]] | None = None
    for r in routes:
        if str(r.get("active", "true")).strip().lower() in ("false", "no"):
            continue
        d = str(r.get("dst-address", "")).strip()
        if not d:
            continue
        try:
            net = ipaddress.ip_network(d, strict=False)
        except ValueError:
            continue
        if ip in net:
            plen = net.prefixlen
            if best is None or plen > best[0]:
                best = (plen, r)
    return best[1] if best else None


def simulate_packet(ev: dict[str, Any], pkt: dict[str, Any]) -> dict[str, Any]:
    """Simulasikan perjalanan satu paket; kembalikan verdict + jejak.

    Args:
        ev: bukti dari router — kunci yang dipakai: ``filter``, ``nat``,
            ``mangle``, ``routes``, ``address_lists``, ``addresses``.
        pkt: spesifikasi paket — ``src``, ``dst``, ``protocol`` (tcp/udp/icmp),
            ``dst_port``, ``src_port`` (opsional), ``in_interface`` (opsional),
            ``state`` (opsional, default 'new').
    """
    pkt = dict(pkt)
    pkt.setdefault("protocol", "tcp")
    state = str(pkt.get("state", "new")).strip().lower() or "new"
    addr_lists = _index_address_lists(ev.get("address_lists"))
    local = _local_ips(ev.get("addresses"))
    trace: list[dict[str, Any]] = []

    filter_rules = _enabled(_as_list(ev.get("filter")))
    nat_rules = _enabled(_as_list(ev.get("nat")))
    mangle_rules = _enabled(_as_list(ev.get("mangle")))
    routes = _as_list(ev.get("routes"))

    # 1) mangle prerouting — kumpulkan mark (informasi untuk routing/narasi)
    marks: dict[str, str] = {}
    for idx, r in enumerate(mangle_rules):
        if str(r.get("chain", "")).strip() != "prerouting":
            continue
        ok, _ = _rule_matches(r, pkt, "prerouting", addr_lists, state)
        if not ok:
            continue
        act = str(r.get("action", "")).strip().lower()
        if act.startswith("mark-"):
            label = {"mark-routing": "new-routing-mark",
                     "mark-connection": "new-connection-mark",
                     "mark-packet": "new-packet-mark"}.get(act, "mark")
            val = str(r.get(label, "")) or str(r.get("new-routing-mark", ""))
            marks[act] = val
            trace.append({"tahap": "mangle", "chain": "prerouting", "indeks": idx,
                          "action": act, "nilai": val, "ringkas": _rule_brief(r)})

    # 2) dst-nat — aturan pertama yang cocok menulis ulang tujuan
    dnat_applied = None
    eff_dst = str(pkt.get("dst", ""))
    eff_dport = pkt.get("dst_port")
    for idx, r in enumerate(nat_rules):
        if str(r.get("chain", "")).strip() != "dstnat":
            continue
        ok, _ = _rule_matches(r, pkt, "dstnat", addr_lists, state)
        if not ok:
            continue
        act = str(r.get("action", "")).strip().lower()
        if act in ("dst-nat", "netmap", "redirect"):
            to_addr = str(r.get("to-addresses", "")).strip()
            to_ports = str(r.get("to-ports", "")).strip()
            if act == "redirect":
                to_addr = to_addr or "router (redirect lokal)"
            if to_addr:
                eff_dst = to_addr.split(",")[0] if "router" not in to_addr else eff_dst
            if to_ports:
                eff_dport = to_ports
            dnat_applied = {"indeks": idx, "action": act, "to_addresses": to_addr,
                            "to_ports": to_ports, "ringkas": _rule_brief(r)}
            trace.append({"tahap": "dst-nat", "chain": "dstnat", **dnat_applied})
            break

    routed_pkt = dict(pkt)
    routed_pkt["dst"] = eff_dst
    routed_pkt["dst_port"] = eff_dport

    # 3) keputusan routing -> input (ke router) atau forward (transit) + out-iface
    to_router = eff_dst in local
    route = _route_lookup(routes, eff_dst)
    out_iface = ""
    gateway = ""
    if route:
        gateway = str(route.get("gateway", "")).split(",")[0].split("%")[0].strip()
        # gateway berupa nama interface (bukan IP) -> itu out-interface
        if gateway and _ip(gateway) is None:
            out_iface = gateway
        routed_pkt["out_interface"] = out_iface
        trace.append({"tahap": "routing", "dst_efektif": eff_dst,
                      "route": str(route.get("dst-address", "")),
                      "gateway": gateway, "out_interface": out_iface,
                      "tujuan": "router (input)" if to_router else "transit (forward)"})
    else:
        trace.append({"tahap": "routing", "dst_efektif": eff_dst,
                      "catatan": "tak ada route cocok (unreachable?)",
                      "tujuan": "router (input)" if to_router else "transit (forward)"})

    chain = "input" if to_router else "forward"
    routed_pkt["out_interface"] = "" if to_router else out_iface

    # 4) filter
    action = _walk_filter(filter_rules, routed_pkt, chain, addr_lists, state, trace)
    if action is None:
        action = "accept"  # default policy RouterOS bila tak ada aturan terminal
        trace.append({"tahap": "filter", "chain": chain,
                      "catatan": "tak ada aturan terminal cocok — default accept"})
        default_accept = True
    else:
        default_accept = False

    # 5) src-nat (hanya relevan bila paket diteruskan keluar)
    snat_applied = None
    if action == "accept" and not to_router:
        for idx, r in enumerate(nat_rules):
            if str(r.get("chain", "")).strip() != "srcnat":
                continue
            ok, _ = _rule_matches(r, routed_pkt, "srcnat", addr_lists, state)
            if not ok:
                continue
            act = str(r.get("action", "")).strip().lower()
            if act in ("masquerade", "src-nat", "netmap"):
                snat_applied = {"indeks": idx, "action": act,
                                "to_addresses": r.get("to-addresses", ""),
                                "ringkas": _rule_brief(r)}
                trace.append({"tahap": "src-nat", "chain": "srcnat", **snat_applied})
                break

    diteruskan = action == "accept"
    verdict_id = {
        "accept": "DITERUSKAN", "drop": "DI-DROP",
        "reject": "DI-REJECT", "tarpit": "DI-TARPIT",
    }.get(action, action.upper())

    return {
        "paket": {
            "src": pkt.get("src"), "dst": pkt.get("dst"),
            "protocol": pkt.get("protocol"), "dst_port": pkt.get("dst_port"),
            "src_port": pkt.get("src_port"), "in_interface": pkt.get("in_interface"),
            "state": state,
        },
        "verdict": action,
        "verdict_label": verdict_id,
        "diteruskan": diteruskan,
        "chain": chain,
        "ke_router": to_router,
        "default_accept": default_accept,
        "dst_efektif": eff_dst,
        "dst_port_efektif": eff_dport,
        "dst_nat": dnat_applied,
        "src_nat": snat_applied,
        "marks": marks,
        "out_interface": out_iface,
        "gateway": gateway,
        "jejak": trace,
        "ringkasan": _verdict_summary(verdict_id, chain, to_router, dnat_applied, snat_applied),
    }


def _verdict_summary(
    verdict: str, chain: str, to_router: bool,
    dnat: Any, snat: Any,
) -> str:
    arah = "ke router (input)" if to_router else "transit (forward)"
    bits = [f"Paket {arah} → {verdict} di chain {chain}."]
    if dnat:
        bits.append("Melewati dst-nat (tujuan ditulis ulang).")
    if snat:
        bits.append("Disamarkan src-nat saat keluar.")
    return " ".join(bits)


def simulate_change(
    ev: dict[str, Any],
    pkt: dict[str, Any],
    new_rule: dict[str, Any],
    table: str = "filter",
    position: int = 0,
) -> dict[str, Any]:
    """Sisipkan satu aturan hipotetis lalu bandingkan verdict sebelum vs sesudah.

    Args:
        ev: bukti ruleset live (sama seperti `simulate_packet`).
        pkt: paket uji.
        new_rule: aturan baru (dict gaya RouterOS, mis.
            ``{"chain":"forward","action":"drop","src-address":"10.0.0.5"}``).
        table: ``filter`` atau ``nat`` — tabel tempat aturan disisipkan.
        position: indeks penyisipan (0 = paling atas/prioritas tertinggi).
    """
    before = simulate_packet(ev, pkt)
    key = "nat" if table == "nat" else "filter"
    modified = dict(ev)
    rules = list(_as_list(ev.get(key)))
    pos = max(0, min(int(position), len(rules)))
    rules.insert(pos, dict(new_rule))
    modified[key] = rules
    after = simulate_packet(modified, pkt)

    berubah = before["verdict"] != after["verdict"]
    return {
        "aturan_baru": new_rule,
        "tabel": table,
        "posisi": pos,
        "sebelum": before["verdict_label"],
        "sesudah": after["verdict_label"],
        "berubah": berubah,
        "dampak": (
            f"Perubahan MENGUBAH nasib paket: {before['verdict_label']} → "
            f"{after['verdict_label']}."
            if berubah else
            f"Tidak ada perubahan nasib paket (tetap {after['verdict_label']})."
        ),
        "detail_sebelum": before,
        "detail_sesudah": after,
    }
