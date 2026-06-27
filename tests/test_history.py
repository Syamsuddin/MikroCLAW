"""Uji Replay: rekaman riwayat, jendela waktu, ringkasan & anomali (offline)."""

from __future__ import annotations

from mikroclaw.web import history as H
from mikroclaw.web.history import (
    HistoryWriter,
    build_record,
    downsample,
    parse_lines,
    query_window,
    read_history,
    summarize_window,
)


def test_build_record_extracts_metrics():
    state = {
        "system": {"cpu_load": 42, "mem_used_pct": 60},
        "wan": {"gateway_ping_ms": 3.2, "internet_ping_ms": 18.0, "rx_bps": 1000, "tx_bps": 500},
        "counters": {"conntrack": 1200, "fw_drops_per_s": 2.0, "clients_total": 14},
    }
    rec = build_record(state, now=1000.0)
    assert rec["t"] == 1000.0
    assert rec["cpu"] == 42 and rec["mem"] == 60
    assert rec["gw"] == 3.2 and rec["inet"] == 18.0
    assert rec["cl"] == 14


def test_query_window_filters_and_sorts():
    recs = [{"t": 30, "cpu": 1}, {"t": 10, "cpu": 2}, {"t": 50, "cpu": 3}]
    win = query_window(recs, 5, 35)
    assert [r["t"] for r in win] == [10, 30]


def test_parse_lines_skips_garbage():
    lines = ['{"t": 1, "cpu": 5}', "bukan json", "", '{"no_t": 1}']
    out = parse_lines(lines)
    assert len(out) == 1 and out[0]["t"] == 1


def test_summarize_empty():
    out = summarize_window([])
    assert out["kosong"] is True


def test_summarize_detects_ping_spike():
    recs = [{"t": i, "inet": 15.0} for i in range(10)]
    recs.append({"t": 10, "inet": 480.0})  # lonjakan
    out = summarize_window(recs)
    assert out["kosong"] is False
    assert any("RTT internet" in a["metrik"] for a in out["anomali"])


def test_summarize_detects_ping_timeout_outage():
    recs = [{"t": i, "inet": None} for i in range(6)] + [{"t": 6, "inet": 20.0}]
    out = summarize_window(recs)
    crit = [a for a in out["anomali"] if a["severity"] == "critical"]
    assert crit and "putus" in crit[0]["detail"]


def test_summarize_detects_sustained_cpu():
    recs = [{"t": i, "cpu": 88.0} for i in range(8)]
    out = summarize_window(recs)
    assert any("CPU" in a["metrik"] for a in out["anomali"])


def test_summarize_conntrack_spike():
    recs = [{"t": i, "ct": 800.0} for i in range(8)] + [{"t": 8, "ct": 5000.0}]
    out = summarize_window(recs)
    assert any("Conntrack" in a["metrik"] for a in out["anomali"])


def test_downsample_caps_points():
    recs = [{"t": i} for i in range(500)]
    assert len(downsample(recs, maks=60)) == 60
    assert len(downsample(recs[:30], maks=60)) == 30


def test_writer_roundtrip(tmp_path, monkeypatch):
    monkeypatch.setenv("MIKROCLAW_STATE_DIR", str(tmp_path))
    w = HistoryWriter()
    w.append({"t": 1000.0, "cpu": 10})
    w.append({"t": 1030.0, "cpu": 20})
    got = read_history(900.0, 1100.0)
    assert len(got) == 2
    assert got[0]["cpu"] == 10 and got[1]["cpu"] == 20


def test_read_history_empty_when_no_files(tmp_path, monkeypatch):
    monkeypatch.setenv("MIKROCLAW_STATE_DIR", str(tmp_path))
    assert read_history(0.0, 100.0) == []
