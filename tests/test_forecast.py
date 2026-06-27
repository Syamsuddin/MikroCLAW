"""Uji Fase 3: regresi/prediksi tren, throughput delta, sampling riwayat."""

from __future__ import annotations

from mikroclaw.web import poller as P
from mikroclaw.web.poller import Poller


def test_linfit_basic():
    # garis y = 2t + 1
    pts = [(0.0, 1.0), (1.0, 3.0), (2.0, 5.0), (3.0, 7.0)]
    slope, intercept = P._linfit(pts)
    assert round(slope, 6) == 2.0
    assert round(intercept, 6) == 1.0


def test_linfit_too_few_or_flat():
    assert P._linfit([(0.0, 1.0), (1.0, 2.0)]) is None      # < 3 titik
    assert P._linfit([(5.0, 9.0)] * 4) is None              # varians x = 0


def test_forecast_rising_gives_eta_and_level():
    # naik 0.5% tiap 30 dtk = 60%/jam, mulai 55% -> ETA ke 100% ~0.75 jam
    series = [(i * 30.0, 55 + i * 0.5) for i in range(11)]
    fc = P._forecast_metric(series, ceil=100.0, warn_h=24.0, crit_h=6.0)
    assert fc["trend"] == "naik"
    assert fc["slope_pph"] == 60.0
    assert fc["eta_hours"] is not None and fc["eta_hours"] < 1.0
    assert fc["level"] == "critical"


def test_forecast_flat_is_stable_no_eta():
    fc = P._forecast_metric([(0, 70), (30, 70), (60, 70), (90, 70)])
    assert fc["trend"] == "stabil"
    assert fc["eta_hours"] is None
    assert fc["level"] == "info"


def test_forecast_insufficient_samples():
    assert P._forecast_metric([(0, 10), (30, 20)]) is None


def test_sample_history_populates_forecast(monkeypatch):
    pol = Poller(ros=None)  # _sample_history tak menyentuh ros
    t = {"v": 1000.0}
    monkeypatch.setattr(P.time, "time", lambda: t["v"])
    pol.state["system"].update(cpu_load=10, mem_used_pct=50, disk_used_pct=80)
    for i in range(4):
        t["v"] = 1000.0 + i * 30.0
        pol.state["system"]["disk_used_pct"] = 80 + i  # naik 1% / sampel
        pol._sample_history()
    fc = pol.state["forecast"]
    assert fc["samples"] == 4
    assert fc["disk"]["trend"] == "naik"
    assert fc["cpu"]["trend"] == "stabil"


def test_throughput_delta(monkeypatch):
    pol = Poller(ros=None)
    clock = {"v": 100.0}
    monkeypatch.setattr(P.time, "monotonic", lambda: clock["v"])

    # tick 1: baseline (rx_bps=0 karena belum ada prev)
    pol._update_interfaces([{"name": "ether1", "rx-byte": "0", "tx-byte": "0",
                             "running": "true", "disabled": "false"}])
    assert pol.state["interfaces"][0]["rx_bps"] == 0

    # tick 2: +1250 byte rx & +625 byte tx dalam 1 dtk -> 10000 / 5000 bps
    clock["v"] = 101.0
    pol._update_interfaces([{"name": "ether1", "rx-byte": "1250", "tx-byte": "625",
                             "running": "true", "disabled": "false"}])
    row = pol.state["interfaces"][0]
    assert row["rx_bps"] == 10000
    assert row["tx_bps"] == 5000
    assert pol.state["counters"]["interfaces_up"] == 1
    assert pol.state["counters"]["interfaces_down"] == 0


def test_interface_error_counters_combine_error_and_drop():
    pol = Poller(ros=None)
    pol._update_interfaces([{"name": "e1", "rx-error": "2", "rx-drop": "3",
                             "tx-error": "1", "tx-drop": "0",
                             "running": "false", "disabled": "false"}])
    row = pol.state["interfaces"][0]
    assert row["rx_error"] == 5 and row["tx_error"] == 1
    assert pol.state["counters"]["interfaces_down"] == 1
