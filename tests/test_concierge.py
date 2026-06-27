"""Uji Concierge: penerjemah telemetri → sinyal bisnis (murni, tanpa router)."""

from __future__ import annotations

from mikroclaw.concierge import business_report, parse_speed_mbps


def test_parse_speed():
    assert parse_speed_mbps("1Gbps") == 1000.0
    assert parse_speed_mbps("100Mbps") == 100.0
    assert parse_speed_mbps("1000Mbit") == 1000.0
    assert parse_speed_mbps("") is None


def test_subscriber_counts_and_profiles():
    ev = {
        "ppp_secrets": [
            {"name": "budi", "profile": "10M", "last-logged-out": "jan/01"},
            {"name": "ani", "profile": "20M", "last-logged-out": "jan/02"},
            {"name": "old", "profile": "10M", "disabled": "true"},
            {"name": "baru", "profile": "10M"},  # belum pernah konek
        ],
        "ppp_active": [{"name": "budi", "address": "10.0.0.2"}],
    }
    out = business_report(ev)
    p = out["pelanggan"]
    assert p["terdaftar_pppoe"] == 4
    assert p["aktif_sekarang"] == 1
    assert p["dinonaktifkan"] == 1
    assert p["belum_pernah_konek"] == 1
    assert p["distribusi_profil"]["10M"] == 3


def test_wan_utilization_levels_and_advice():
    ev = {
        "ppp_secrets": [{"name": "a"}],
        "wan": {"rx_bps": 90_000_000, "tx_bps": 10_000_000, "speed": "100Mbps"},
    }
    out = business_report(ev)
    u = out["utilisasi_wan"]
    assert u["kapasitas_down_mbps"] == 100.0
    assert u["download_pct"] == 90.0
    assert u["level"] == "kritis"
    assert any("upgrade" in s["saran"].lower() for s in out["saran"])


def test_plan_override_used_over_link_speed():
    ev = {"wan": {"rx_bps": 25_000_000, "tx_bps": 0, "speed": "1Gbps"},
          "plan_down_mbps": 50}
    out = business_report(ev)
    assert out["utilisasi_wan"]["kapasitas_down_mbps"] == 50
    assert out["utilisasi_wan"]["download_pct"] == 50.0


def test_unauthenticated_device_detected():
    ev = {
        "ppp_secrets": [{"name": "budi"}],
        "ppp_active": [{"name": "budi", "address": "10.0.0.2"}],
        "leases": [
            {"address": "10.0.0.2", "dynamic": "true"},          # terotentikasi (ppp)
            {"address": "192.168.1.77", "dynamic": "true",       # tak terotentikasi -> suspect
             "active-mac-address": "AA:BB:CC:DD:EE:FF", "host-name": "unknown"},
            {"address": "192.168.1.10", "dynamic": "false"},     # static -> diabaikan
        ],
    }
    out = business_report(ev)
    s = out["perangkat_tak_terotentikasi"]
    assert s["jumlah"] == 1
    assert s["daftar"][0]["ip"] == "192.168.1.77"
    assert any("tak tertagih" in x["saran"] for x in out["saran"])


def test_no_suspects_when_not_isp_style():
    # tanpa ppp/hotspot sama sekali -> jangan tuduh perangkat LAN biasa
    ev = {"leases": [{"address": "192.168.88.20", "dynamic": "true"}]}
    out = business_report(ev)
    assert out["perangkat_tak_terotentikasi"]["jumlah"] == 0


def test_top_talkers_from_clients():
    ev = {
        "clients": [
            {"ip": "10.0.0.2", "host": "budi", "rx_bps": 5_000_000, "tx_bps": 1_000_000},
            {"ip": "10.0.0.3", "host": "ani", "rx_bps": 20_000_000, "tx_bps": 2_000_000},
            {"ip": "10.0.0.4", "host": "idle", "rx_bps": 0, "tx_bps": 0},
        ],
    }
    out = business_report(ev)
    assert out["top_talkers"][0]["ip"] == "10.0.0.3"  # terbesar
    assert out["top_talkers"][0]["download_mbps"] == 20.0
    assert len(out["top_talkers"]) == 2  # yang nol dibuang


def test_top_talkers_from_queue_rate():
    ev = {"queues": [
        {"name": "q-budi", "target": "10.0.0.2/32", "rate": "1000000/8000000"},
    ]}
    out = business_report(ev)
    assert out["top_talkers"][0]["ip"] == "10.0.0.2"
    assert out["top_talkers"][0]["download_mbps"] == 8.0
    assert out["top_talkers"][0]["upload_mbps"] == 1.0
