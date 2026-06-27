"""MikroCLAW Chronicle — mesin waktu konfigurasi (snapshot · diff · nilai risiko).

Mengubah backup pasif menjadi tata-kelola perubahan aktif. `snapshot_config(ev)`
menormalkan menu RouterOS yang relevan-keamanan menjadi snapshot kanonik &
hashable. `diff_snapshots(old, new)` menghasilkan daftar perubahan per-bagian
(ditambah/dihapus/diubah). `assess_change(...)` memberi tingkat risiko + alasan
untuk tiap perubahan — mendeteksi pola berbahaya (port manajemen dibuka ke
0.0.0.0/0, user baru, scheduler/script persistensi, aturan firewall
dinonaktifkan, open resolver). `narrate_diff(...)` merangkai semuanya.

Bagian inti murni (tanpa I/O) agar mudah diuji. Penyimpanan snapshot ke disk
LOKAL (bukan router) dilayani helper save/load di bawah — tak melewati write-gate
router. Field volatil (counter bytes/packets, .id, dynamic, uptime) sengaja
dibuang agar diff hanya menyorot perubahan KONFIGURASI yang bermakna.
"""

from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path
from typing import Any

# Port manajemen yang berbahaya bila dibuka tanpa batasan sumber.
MGMT_PORTS = {"21", "22", "23", "80", "443", "3128", "8080", "8291", "8728", "8729"}


def _truthy(v: Any) -> bool:
    return str(v).strip().lower() in ("true", "yes", "1")


def _as_list(v: Any) -> list[dict[str, Any]]:
    if isinstance(v, list):
        return [r for r in v if isinstance(r, dict)]
    if isinstance(v, dict):
        return [v]
    return []


def _pick(row: dict[str, Any], fields: tuple[str, ...]) -> dict[str, str]:
    """Ambil subset field (sebagai string) — buang yang volatil/kosong."""
    out: dict[str, str] = {}
    for f in fields:
        if f in row and str(row[f]).strip() != "":
            out[f] = str(row[f])
    return out


# field bermakna per bagian (volatil seperti bytes/packets/.id tak masuk)
_RULE_FIELDS = (
    "chain", "action", "protocol", "src-address", "dst-address",
    "src-address-list", "dst-address-list", "src-port", "dst-port",
    "in-interface", "out-interface", "connection-state",
    "to-addresses", "to-ports", "disabled", "comment", "jump-target",
)


def _rule_identity(row: dict[str, Any]) -> str:
    """Identitas stabil aturan firewall — tahan terhadap pengurutan ulang.

    Pakai comment bila ada (paling stabil); selain itu tuple field bermakna.
    """
    comment = str(row.get("comment", "")).strip()
    if comment:
        return f"#{comment}"
    sig = "|".join(
        f"{f}={row.get(f)}" for f in _RULE_FIELDS
        if f not in ("disabled", "comment") and str(row.get(f, "")).strip()
    )
    return sig or "(kosong)"


def _index_rules(rows: Any) -> dict[str, dict[str, str]]:
    out: dict[str, dict[str, str]] = {}
    for r in _as_list(rows):
        ident = _rule_identity(r)
        # bila identitas bentrok, gabungkan dengan indeks agar tak saling timpa
        key = ident
        i = 1
        while key in out:
            i += 1
            key = f"{ident}~{i}"
        out[key] = _pick(r, _RULE_FIELDS)
    return out


def _index_by(rows: Any, key_field: str, fields: tuple[str, ...]) -> dict[str, dict[str, str]]:
    out: dict[str, dict[str, str]] = {}
    for r in _as_list(rows):
        k = str(r.get(key_field, "")).strip()
        if not k:
            continue
        out[k] = _pick(r, fields)
    return out


def _index_addr_list(rows: Any) -> dict[str, dict[str, str]]:
    out: dict[str, dict[str, str]] = {}
    for r in _as_list(rows):
        lst = str(r.get("list", "")).strip()
        addr = str(r.get("address", "")).strip()
        if lst and addr:
            out[f"{lst}:{addr}"] = _pick(r, ("list", "address", "disabled", "comment", "timeout"))
    return out


def _singleton(rows: Any, fields: tuple[str, ...]) -> dict[str, dict[str, str]]:
    obj = (_as_list(rows) or [{}])[0]
    return {"(global)": _pick(obj, fields)}


def snapshot_config(ev: dict[str, Any]) -> dict[str, Any]:
    """Bangun snapshot konfigurasi kanonik dari bukti GET REST.

    Bagian yang dipantau (relevan-keamanan & rawan berubah): firewall filter/nat,
    ip service, user, user group, scheduler, script, ip dns, address-list.
    """
    sections: dict[str, dict[str, dict[str, str]]] = {
        "firewall_filter": _index_rules(ev.get("filter")),
        "firewall_nat": _index_rules(ev.get("nat")),
        "ip_service": _index_by(ev.get("services"), "name", ("name", "disabled", "port", "address")),
        "user": _index_by(ev.get("users"), "name", ("name", "group", "address")),
        "user_group": _index_by(ev.get("groups"), "name", ("name", "policy")),
        "scheduler": _index_by(ev.get("schedulers"), "name", ("name", "on-event", "interval", "start-time", "policy", "disabled")),
        "script": _index_by(ev.get("scripts"), "name", ("name", "policy", "source")),
        "ip_dns": _singleton(ev.get("dns"), ("servers", "dynamic-servers", "allow-remote-requests")),
        "address_list": _index_addr_list(ev.get("address_lists")),
    }
    payload = json.dumps(sections, sort_keys=True, ensure_ascii=False)
    digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]
    return {
        "meta": {
            "ts": time.time(),
            "identity": str(ev.get("identity", "")),
            "version": str(ev.get("version", "")),
            "label": str(ev.get("label", "")),
        },
        "hash": digest,
        "sections": sections,
    }


def diff_snapshots(old: dict[str, Any], new: dict[str, Any]) -> dict[str, Any]:
    """Bandingkan dua snapshot → perubahan per-bagian (ditambah/dihapus/diubah)."""
    o = old.get("sections", {}) if isinstance(old, dict) else {}
    n = new.get("sections", {}) if isinstance(new, dict) else {}
    perubahan: list[dict[str, Any]] = []

    for section in sorted(set(o) | set(n)):
        os_, ns_ = o.get(section, {}), n.get(section, {})
        for ident in ns_.keys() - os_.keys():
            perubahan.append({"bagian": section, "jenis": "ditambah",
                              "identitas": ident, "nilai": ns_[ident]})
        for ident in os_.keys() - ns_.keys():
            perubahan.append({"bagian": section, "jenis": "dihapus",
                              "identitas": ident, "nilai": os_[ident]})
        for ident in os_.keys() & ns_.keys():
            if os_[ident] != ns_[ident]:
                fields = sorted(set(os_[ident]) | set(ns_[ident]))
                delta = {f: {"dari": os_[ident].get(f, ""), "ke": ns_[ident].get(f, "")}
                         for f in fields if os_[ident].get(f) != ns_[ident].get(f)}
                perubahan.append({"bagian": section, "jenis": "diubah",
                                  "identitas": ident, "delta": delta,
                                  "nilai": ns_[ident]})
    return {
        "hash_lama": old.get("hash") if isinstance(old, dict) else None,
        "hash_baru": new.get("hash") if isinstance(new, dict) else None,
        "identik": (old.get("hash") == new.get("hash")) if isinstance(old, dict) and isinstance(new, dict) else False,
        "jumlah_perubahan": len(perubahan),
        "perubahan": perubahan,
    }


def assess_change(change: dict[str, Any]) -> dict[str, str]:
    """Nilai satu perubahan → {severity, alasan}. Murni, deterministik."""
    bagian = change.get("bagian", "")
    jenis = change.get("jenis", "")
    nilai = change.get("nilai", {}) or {}
    delta = change.get("delta", {}) or {}

    def r(sev: str, alasan: str) -> dict[str, str]:
        return {"severity": sev, "alasan": alasan}

    # --- user / akses ---
    if bagian == "user" and jenis == "ditambah":
        return r("critical", f"User RouterOS BARU '{nilai.get('name','')}' "
                 f"(grup {nilai.get('group','?')}). Akun baru tak terjadwal bisa backdoor.")
    if bagian == "user_group" and (jenis in ("ditambah", "diubah")):
        return r("warning", "Perubahan grup hak akses — periksa eskalasi privilege.")

    # --- ip service ---
    if bagian == "ip_service":
        if jenis == "diubah" and delta.get("disabled", {}).get("ke") in ("false", "no"):
            return r("warning", f"Service '{nilai.get('name','')}' DIAKTIFKAN kembali.")
        if jenis == "diubah" and "address" in delta and not str(delta["address"].get("ke")).strip():
            return r("critical", f"Pembatasan alamat service '{nilai.get('name','')}' "
                     "DIHAPUS — kini terbuka dari mana saja.")

    # --- scheduler / script (mekanisme persistensi favorit penyerang) ---
    if bagian in ("scheduler", "script") and jenis == "ditambah":
        return r("warning", f"{bagian} BARU '{nilai.get('name','')}' — "
                 "scheduler/script baru sering dipakai untuk persistensi malware.")

    # --- DNS open resolver ---
    if bagian == "ip_dns" and "allow-remote-requests" in delta:
        if str(delta["allow-remote-requests"].get("ke")).lower() in ("true", "yes"):
            return r("warning", "allow-remote-requests DIAKTIFKAN — risiko open DNS resolver "
                     "(amplifikasi DDoS) bila tak dibatasi firewall.")

    # --- firewall filter ---
    if bagian == "firewall_filter":
        action = str(nilai.get("action", "")).lower()
        chain = str(nilai.get("chain", "")).lower()
        dport = str(nilai.get("dst-port", ""))
        src = str(nilai.get("src-address", "")).strip()
        opens_mgmt = (
            chain == "input" and action == "accept"
            and any(p in MGMT_PORTS for p in dport.replace("-", ",").split(","))
            and src in ("", "0.0.0.0/0")
        )
        if jenis == "ditambah" and opens_mgmt:
            return r("critical", f"Aturan input BARU membuka port manajemen {dport} "
                     "tanpa batasan sumber — ciri backdoor pasca-kompromi.")
        if jenis == "dihapus" and action in ("drop", "reject"):
            return r("warning", "Aturan firewall DROP/REJECT dihapus — proteksi berkurang.")
        if jenis == "diubah" and delta.get("disabled", {}).get("ke") in ("true", "yes") \
                and action in ("drop", "reject"):
            return r("warning", "Aturan DROP/REJECT dinonaktifkan — proteksi berkurang.")
        if jenis == "ditambah":
            return r("info", f"Aturan filter baru ({chain}/{action}).")

    # --- NAT (port forward) ---
    if bagian == "firewall_nat" and jenis == "ditambah" and str(nilai.get("action")) == "dst-nat":
        return r("info", f"Port-forward baru ke {nilai.get('to-addresses','?')}"
                 f":{nilai.get('to-ports','')} — pastikan disengaja.")

    return r("info", f"{jenis} pada {bagian}.")


def _sev_rank(sev: str) -> int:
    return {"critical": 0, "warning": 1, "info": 2}.get(sev, 3)


def narrate_diff(diff: dict[str, Any]) -> dict[str, Any]:
    """Lampirkan penilaian risiko ke tiap perubahan + ringkasan & severity tertinggi."""
    out: list[dict[str, Any]] = []
    worst = "info"
    for ch in diff.get("perubahan", []):
        risiko = assess_change(ch)
        if _sev_rank(risiko["severity"]) < _sev_rank(worst):
            worst = risiko["severity"]
        out.append({**ch, "risiko": risiko})
    out.sort(key=lambda c: _sev_rank(c["risiko"]["severity"]))
    n = len(out)
    return {
        "identik": diff.get("identik", False),
        "jumlah_perubahan": n,
        "keparahan_tertinggi": worst if n else "tidak ada perubahan",
        "perubahan": out,
        "ringkasan": (
            "Konfigurasi identik dengan snapshot pembanding." if diff.get("identik")
            else f"{n} perubahan konfigurasi terdeteksi (tertinggi: {worst})."
        ),
    }


# --------------------------------------------------------------------- persistensi
def save_snapshot(snap: dict[str, Any], directory: str | Path, label: str = "") -> Path:
    """Simpan snapshot ke file JSON ber-timestamp; kembalikan path."""
    d = Path(directory)
    d.mkdir(parents=True, exist_ok=True)
    ts = int(snap.get("meta", {}).get("ts", time.time()))
    safe = "".join(c for c in label if c.isalnum() or c in "-_") or "snap"
    path = d / f"{ts}-{safe}-{snap.get('hash','')}.json"
    path.write_text(json.dumps(snap, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def list_snapshots(directory: str | Path) -> list[Path]:
    """Daftar file snapshot terurut waktu (terbaru terakhir)."""
    d = Path(directory)
    if not d.exists():
        return []
    return sorted(d.glob("*.json"), key=lambda p: p.stat().st_mtime)


def load_latest(directory: str | Path) -> dict[str, Any] | None:
    files = list_snapshots(directory)
    if not files:
        return None
    return json.loads(files[-1].read_text(encoding="utf-8"))
