"""Smoke-test wiring 7 tool cerdas di server.py (glue di atas engine murni).

Menyuntik FakeRos sebagai client singleton server, tanpa router/jaringan.
Engine murninya diuji terpisah (test_twin/sentinel/chronicle/history/concierge).
"""

from __future__ import annotations

import asyncio

import pytest

from conftest import FakeRos, make_config

from mikroclaw import server


@pytest.fixture
def fake_ros(monkeypatch):
    """Pasang FakeRos sebagai client server + state dir sementara."""
    def _install(responses):
        ros = FakeRos(responses=responses)
        monkeypatch.setattr(server, "_client", ros)
        monkeypatch.setattr(server, "_cfg", make_config())
        return ros
    return _install


def _run(coro):
    return asyncio.run(coro)


def test_simulate_packet_tool(fake_ros):
    fake_ros({
        ("GET", "/ip/firewall/filter"): [
            {"chain": "forward", "action": "drop", "src-address": "192.168.88.10"}
        ],
        ("GET", "/ip/route"): [{"dst-address": "0.0.0.0/0", "gateway": "ether1", "active": "true"}],
        ("GET", "/ip/address"): [{"address": "192.168.88.1/24"}],
    })
    out = _run(server.simulate_packet("192.168.88.10", "8.8.8.8", "tcp", "443"))
    assert out["verdict"] == "drop"
    assert out["chain"] == "forward"


def test_simulate_firewall_change_tool(fake_ros):
    fake_ros({
        ("GET", "/ip/route"): [{"dst-address": "0.0.0.0/0", "gateway": "ether1", "active": "true"}],
        ("GET", "/ip/address"): [{"address": "192.168.88.1/24"}],
    })
    out = _run(server.simulate_firewall_change(
        "192.168.88.10", "8.8.8.8",
        {"chain": "forward", "action": "drop", "src-address": "192.168.88.10"},
        "tcp", "443",
    ))
    assert out["berubah"] is True


def test_analyze_client_behavior_tool(fake_ros):
    conns = [{"src-address": "192.168.1.50:5000", "dst-address": f"10.0.0.{j}:23",
              "protocol": "tcp"} for j in range(25)]
    fake_ros({
        ("GET", "/ip/firewall/connection"): conns,
        ("GET", "/ip/dhcp-server/lease"): [
            {"address": "192.168.1.50", "mac-address": "EC:FA:BC:11:22:33", "host-name": "cam1"}
        ],
        ("GET", "/ip/arp"): [],
    })
    out = _run(server.analyze_client_behavior())
    assert out["klien_mencurigakan"] == 1
    assert out["keparahan_tertinggi"] == "critical"


def test_config_snapshot_and_diff_tools(fake_ros, tmp_path, monkeypatch):
    monkeypatch.setenv("MIKROCLAW_STATE_DIR", str(tmp_path))
    fake_ros({
        ("GET", "/system/identity"): [{"name": "rtr"}],
        ("GET", "/system/resource"): [{"version": "7.15"}],
        ("GET", "/user"): [{"name": "admin", "group": "full"}],
    })
    snap = _run(server.config_snapshot("baseline"))
    assert snap["ok"] and snap["total_snapshot"] == 1

    # diff pertama -> baseline dibuat (sudah ada 1 dari snapshot di atas)
    diff = _run(server.config_diff(simpan=True))
    assert diff["ok"] is True
    # identik karena konfigurasi tak berubah
    assert diff.get("identik") in (True, None) or diff.get("baseline_dibuat")


def test_explain_incident_empty(fake_ros, tmp_path, monkeypatch):
    monkeypatch.setenv("MIKROCLAW_STATE_DIR", str(tmp_path))
    fake_ros({})
    out = _run(server.explain_incident(60, 0))
    assert out["ok"] is True
    assert out["kosong"] is True  # belum ada riwayat


def test_explain_incident_bad_window(fake_ros):
    fake_ros({})
    out = _run(server.explain_incident(0, 60))  # mulai lebih baru dari selesai
    assert out["ok"] is False


def test_business_report_tool(fake_ros):
    fake_ros({
        ("GET", "/ppp/secret"): [{"name": "budi", "profile": "10M"}],
        ("GET", "/ppp/active"): [{"name": "budi", "address": "10.0.0.2"}],
    })
    out = _run(server.business_report(plan_down_mbps=100))
    assert out["pelanggan"]["terdaftar_pppoe"] == 1
    assert out["pelanggan"]["aktif_sekarang"] == 1
