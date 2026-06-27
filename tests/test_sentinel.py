"""Uji Sentinel: sidik-jari perilaku per-perangkat (murni, tanpa router)."""

from __future__ import annotations

from mikroclaw.sentinel import (
    analyze_clients,
    device_class,
    fingerprint_ip,
    score_behavior,
)


def _conn(src, dst, proto="tcp"):
    return {"src-address": src, "dst-address": dst, "protocol": proto}


def test_device_class_inference():
    assert device_class("Hikvision") == "kamera/CCTV"
    assert device_class("MikroTik") == "router/AP"
    assert device_class("Apple", "iPhone-Budi") == "ponsel/tablet"
    assert device_class("Raspberry Pi") == "IoT/embedded"
    assert device_class("Synology") == "NAS/server"
    assert device_class("", "") == "tak dikenal"


def test_fingerprint_counts_outbound_only():
    conns = [
        _conn("192.168.1.5:51000", "8.8.8.8:443"),
        _conn("192.168.1.5:51001", "1.1.1.1:443"),
        _conn("192.168.1.9:51002", "8.8.8.8:443"),  # host lain
        _conn("9.9.9.9:443", "192.168.1.5:51003"),  # inbound -> diabaikan
    ]
    prof = fingerprint_ip(conns, "192.168.1.5")
    assert prof["koneksi_keluar"] == 2
    assert prof["tujuan_unik"] == 2
    assert prof["port_unik"] == 1  # keduanya :443


def test_telnet_botnet_pattern_on_camera_is_critical():
    conns = [_conn("192.168.1.50:50000", f"10.0.{i}.{j}:23")
             for i in range(2) for j in range(15)]  # 30 tujuan Telnet
    prof = fingerprint_ip(conns, "192.168.1.50")
    findings = score_behavior(prof, "kamera/CCTV")
    botnet = [f for f in findings if "botnet" in f["judul"].lower()]
    assert botnet and botnet[0]["severity"] == "critical"


def test_telnet_on_pc_is_warning_not_critical():
    conns = [_conn("192.168.1.20:50000", f"10.0.0.{j}:23") for j in range(6)]
    prof = fingerprint_ip(conns, "192.168.1.20")
    findings = score_behavior(prof, "ponsel/tablet")
    botnet = [f for f in findings if "botnet" in f["judul"].lower()]
    assert botnet and botnet[0]["severity"] == "warning"


def test_miner_ports_flagged():
    conns = [_conn("192.168.1.30:50000", f"5.6.7.8:{p}")
             for p in (3333, 4444, 5555, 7777)]
    prof = fingerprint_ip(conns, "192.168.1.30")
    findings = score_behavior(prof, "tak dikenal")
    assert any("penambang" in f["judul"].lower() for f in findings)


def test_high_fanout_scan_detected():
    conns = [_conn("192.168.1.40:50000", f"172.16.{i}.{j}:80")
             for i in range(2) for j in range(60)]  # 120 tujuan unik
    prof = fingerprint_ip(conns, "192.168.1.40")
    findings = score_behavior(prof, "kamera/CCTV")
    assert any("fan-out" in f["judul"].lower() for f in findings)
    assert prof["tujuan_unik"] >= 100


def test_normal_client_no_findings():
    conns = [
        _conn("192.168.1.10:50000", "142.250.1.1:443"),
        _conn("192.168.1.10:50001", "142.250.1.2:443"),
        _conn("192.168.1.10:50002", "31.13.1.1:443"),
    ]
    prof = fingerprint_ip(conns, "192.168.1.10")
    assert score_behavior(prof, "ponsel/tablet") == []


def test_analyze_clients_sorts_by_severity():
    conns = (
        [_conn("192.168.1.50:5", f"10.0.0.{j}:23") for j in range(25)]  # kamera botnet (critical)
        + [_conn("192.168.1.30:5", f"5.6.7.8:{p}") for p in (3333, 4444, 5555)]  # miner (warning)
    )
    clients = [
        {"ip": "192.168.1.30", "vendor": "", "host": "pc"},
        {"ip": "192.168.1.50", "vendor": "Hikvision", "host": "cam1"},
        {"ip": "192.168.1.10", "vendor": "Apple", "host": "iphone"},  # bersih, tak ada conn
    ]
    out = analyze_clients(conns, clients)
    assert out["klien_mencurigakan"] == 2
    assert out["keparahan_tertinggi"] == "critical"
    assert out["laporan"][0]["ip"] == "192.168.1.50"  # critical di atas


def test_analyze_clients_clean():
    conns = [_conn("192.168.1.10:5", "8.8.8.8:443")]
    out = analyze_clients(conns, [{"ip": "192.168.1.10", "vendor": "Apple"}])
    assert out["klien_mencurigakan"] == 0
    assert out["keparahan_tertinggi"] == "bersih"
