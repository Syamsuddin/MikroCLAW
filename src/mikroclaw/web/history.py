"""MikroCLAW Replay — persistensi riwayat telemetri & ringkasan jendela waktu.

Pulse poller hanya menyimpan ring-buffer 60 detik, jadi pertanyaan paling sering
operator — "kenapa tadi sore lemot?" — tak terjawab. Modul ini menambah ingatan
jangka-menengah: tiap tick lambat (30 dtk) sebuah rekaman ringkas (cpu, mem,
RTT gateway/internet, conntrack, firewall drops, throughput WAN, jumlah klien)
ditulis sebagai JSON-lines harian ke disk LOKAL (`MIKROCLAW_STATE_DIR/history`).

Tool `explain_incident` (server.py) membaca jendela waktu yang diminta, lalu
`summarize_window(...)` menghitung statistik + menandai anomali deterministik
sehingga Claude bisa merekonstruksi rantai sebab. Bagian analitik murni (tanpa
I/O) agar mudah diuji; penulisan/pembacaan file dipisah & failsafe.
"""

from __future__ import annotations

import datetime
import json
import time
from pathlib import Path
from typing import Any

from ..storage import state_dir

# kunci ringkas record (hemat disk) -> label & ambang anomali
METRICS: dict[str, str] = {
    "cpu": "CPU %", "mem": "Memori %", "gw": "RTT gateway (ms)",
    "inet": "RTT internet (ms)", "ct": "Conntrack", "drop": "Firewall drops/s",
    "wrx": "WAN download (bps)", "wtx": "WAN upload (bps)", "cl": "Jumlah klien",
}


def build_record(state: dict[str, Any], now: float | None = None) -> dict[str, Any]:
    """Susun satu rekaman riwayat ringkas dari state poller (murni)."""
    sysd = state.get("system", {}) or {}
    wan = state.get("wan", {}) or {}
    cnt = state.get("counters", {}) or {}
    return {
        "t": round(now if now is not None else time.time(), 1),
        "cpu": _num(sysd.get("cpu_load")),
        "mem": _num(sysd.get("mem_used_pct")),
        "gw": _num(wan.get("gateway_ping_ms")),
        "inet": _num(wan.get("internet_ping_ms")),
        "ct": _num(cnt.get("conntrack")),
        "drop": _num(cnt.get("fw_drops_per_s")),
        "wrx": _num(wan.get("rx_bps")),
        "wtx": _num(wan.get("tx_bps")),
        "cl": _num(cnt.get("clients_total")),
    }


def _num(v: Any) -> float | None:
    if v is None:
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


# --------------------------------------------------------------------- penulisan
class HistoryWriter:
    """Penulis JSON-lines harian. Tidak menyentuh disk sampai `append` pertama."""

    def __init__(self, subdir: str = "history") -> None:
        self.subdir = subdir

    def _path_for(self, ts: float) -> Path:
        day = datetime.datetime.fromtimestamp(ts).strftime("%Y-%m-%d")
        return state_dir(self.subdir) / f"{day}.jsonl"

    def append(self, record: dict[str, Any]) -> None:
        ts = float(record.get("t") or time.time())
        path = self._path_for(ts)
        with path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, ensure_ascii=False) + "\n")


# --------------------------------------------------------------------- pembacaan
def parse_lines(lines: list[str]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except ValueError:
            continue
        if isinstance(obj, dict) and "t" in obj:
            out.append(obj)
    return out


def read_history(start_ts: float, end_ts: float, subdir: str = "history") -> list[dict[str, Any]]:
    """Baca rekaman pada [start_ts, end_ts] dari file-file harian terkait."""
    d = state_dir(subdir)
    records: list[dict[str, Any]] = []
    day = datetime.datetime.fromtimestamp(start_ts).date()
    end_day = datetime.datetime.fromtimestamp(end_ts).date()
    while day <= end_day:
        f = d / f"{day.strftime('%Y-%m-%d')}.jsonl"
        if f.exists():
            records.extend(parse_lines(f.read_text(encoding="utf-8").splitlines()))
        day += datetime.timedelta(days=1)
    return query_window(records, start_ts, end_ts)


def query_window(records: list[dict[str, Any]], start_ts: float, end_ts: float) -> list[dict[str, Any]]:
    """Saring rekaman dalam jendela waktu, terurut menaik."""
    win = [r for r in records if start_ts <= float(r.get("t", 0)) <= end_ts]
    win.sort(key=lambda r: float(r.get("t", 0)))
    return win


def _stats(values: list[float]) -> dict[str, float]:
    n = len(values)
    s = sorted(values)
    mean = sum(values) / n
    median = s[n // 2] if n % 2 else (s[n // 2 - 1] + s[n // 2]) / 2
    var = sum((v - mean) ** 2 for v in values) / n
    return {
        "min": round(s[0], 1), "max": round(s[-1], 1),
        "mean": round(mean, 1), "median": round(median, 1),
        "std": round(var ** 0.5, 1), "last": round(values[-1], 1), "n": n,
    }


def summarize_window(records: list[dict[str, Any]]) -> dict[str, Any]:
    """Statistik per-metrik + anomali deterministik untuk jendela waktu."""
    if not records:
        return {"kosong": True, "metrik": {}, "anomali": [],
                "ringkasan": "Tidak ada data riwayat pada jendela ini "
                "(Pulse mungkin belum berjalan saat itu)."}

    metrik: dict[str, Any] = {}
    anomali: list[dict[str, Any]] = []
    for key, label in METRICS.items():
        vals = [float(r[key]) for r in records if r.get(key) is not None]
        if not vals:
            continue
        st = _stats(vals)
        metrik[key] = {"label": label, **st}

        # --- aturan anomali deterministik ---
        if key in ("gw", "inet"):
            if st["max"] >= 100 and st["max"] >= max(1.0, st["median"]) * 3:
                anomali.append({"metrik": label, "severity": "warning",
                                "detail": f"Lonjakan RTT ke {st['max']}ms "
                                f"(median {st['median']}ms) — indikasi saturasi/gangguan link."})
            if any(r.get(key) is None for r in records):
                hilang = sum(1 for r in records if r.get(key) is None)
                if hilang >= max(2, len(records) // 4):
                    anomali.append({"metrik": label, "severity": "critical",
                                    "detail": f"{hilang} sampel timeout — koneksi sempat putus."})
        elif key in ("cpu", "mem"):
            if st["mean"] >= 80:
                anomali.append({"metrik": label, "severity": "warning",
                                "detail": f"{label} tinggi berkelanjutan (rata-rata {st['mean']}%)."})
            elif st["max"] >= 95:
                anomali.append({"metrik": label, "severity": "info",
                                "detail": f"Lonjakan {label} sesaat ke {st['max']}%."})
        elif key == "ct":
            if st["max"] >= max(1.0, st["median"]) * 2 and st["max"] >= 2000:
                anomali.append({"metrik": label, "severity": "warning",
                                "detail": f"Conntrack melonjak ke {int(st['max'])} "
                                f"(median {int(st['median'])}) — kemungkinan flood/banyak sesi P2P."})
        elif key == "drop":
            if st["max"] >= max(1.0, st["median"]) * 3 and st["max"] >= 50:
                anomali.append({"metrik": label, "severity": "info",
                                "detail": f"Lonjakan firewall drops ke {st['max']}/s — "
                                "kemungkinan pemindaian/serangan diblokir."})

    sev_rank = {"critical": 0, "warning": 1, "info": 2}
    anomali.sort(key=lambda a: sev_rank.get(a["severity"], 3))
    worst = anomali[0]["severity"] if anomali else "tenang"
    t0, t1 = records[0]["t"], records[-1]["t"]
    return {
        "kosong": False,
        "rentang": {"mulai": t0, "selesai": t1, "sampel": len(records),
                    "durasi_menit": round((t1 - t0) / 60.0, 1)},
        "metrik": metrik,
        "anomali": anomali,
        "keparahan_tertinggi": worst,
        "ringkasan": (
            f"{len(records)} sampel selama {round((t1 - t0)/60.0,1)} menit; "
            + (f"{len(anomali)} anomali (tertinggi: {worst})." if anomali
               else "tak ada anomali menonjol.")
        ),
    }


def downsample(records: list[dict[str, Any]], maks: int = 60) -> list[dict[str, Any]]:
    """Kurangi jumlah titik agar muat di prompt LLM (ambil tiap-n merata)."""
    if len(records) <= maks:
        return records
    step = len(records) / maks
    return [records[int(i * step)] for i in range(maks)]
