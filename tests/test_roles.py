"""Uji classify_roles: deteksi peran MikroTik dari bukti konfigurasi (murni)."""

from __future__ import annotations

from mikroclaw.roles import classify_roles


def _names(out):
    return {r["nama"] for r in out["peran"]}


def test_empty_router_has_no_roles():
    out = classify_roles({"identity": "kosong", "version": "7.0"})
    assert out["jumlah_peran"] == 0
    assert "Tidak ada peran" in out["ringkasan"]


def test_gateway_nat_and_portforward():
    out = classify_roles({"nat": [
        {"action": "masquerade"}, {"action": "dst-nat"}, {"action": "accept"}]})
    names = _names(out)
    assert "Gateway internet (NAT/masquerade)" in names
    assert "Port forwarding (DSTNAT)" in names


def test_disabled_nat_ignored():
    out = classify_roles({"nat": [{"action": "masquerade", "disabled": "true"}]})
    assert out["jumlah_peran"] == 0


def test_firewall_confidence_high_with_input_drop():
    out = classify_roles({"filter": [{"chain": "input", "action": "drop"}]})
    fw = next(r for r in out["peran"] if r["nama"].startswith("Firewall"))
    assert fw["keyakinan"] == "tinggi"


def test_firewall_confidence_medium_without_input_protect():
    out = classify_roles({"filter": [{"chain": "forward", "action": "accept"}]})
    fw = next(r for r in out["peran"] if r["nama"].startswith("Firewall"))
    assert fw["keyakinan"] == "sedang"


def test_bgp_and_ospf():
    out = classify_roles({"bgp": [{"name": "p"}], "ospf": [{"name": "n"}]})
    names = _names(out)
    assert "BGP router" in names and "OSPF router" in names


def test_switch_and_vlan():
    out = classify_roles({
        "bridges": [{"name": "bridge1", "vlan-filtering": "true"}],
        "bridge_ports": [{}, {}, {}, {}],
        "vlans": [{"name": "vlan10"}],
    })
    names = _names(out)
    assert "Switch / bridge L2" in names
    assert "VLAN trunk (802.1Q)" in names
    sw = next(r for r in out["peran"] if r["nama"].startswith("Switch"))
    assert sw["keyakinan"] == "tinggi"  # >=3 port
    assert any("VLAN filtering" in b for b in sw["bukti"])


def test_wifi_ap_vs_station_modes():
    ap = classify_roles({"wifi": [{"mode": "ap-bridge"}]})
    assert "Access Point WiFi" in _names(ap)
    # wifiwave2 nested configuration.mode
    ap2 = classify_roles({"wifi": [{"configuration": {"mode": "ap"}}]})
    assert "Access Point WiFi" in _names(ap2)
    sta = classify_roles({"wifi": [{"mode": "station"}]})
    assert "WiFi station/klien (uplink)" in _names(sta)


def test_pppoe_server_bras_with_sessions():
    out = classify_roles({
        "pppoe_server": [{"service-name": "isp"}],
        "ppp_active": [{"service": "pppoe"}, {"service": "l2tp"}],
    })
    bras = next(r for r in out["peran"] if "PPPoE server" in r["nama"])
    assert bras["keyakinan"] == "tinggi"
    assert any("1 sesi PPPoE" in b for b in bras["bukti"])


def test_dns_resolver_and_proxy_objects():
    out = classify_roles({
        "dns": {"allow-remote-requests": "true"},
        "proxy": {"enabled": "true"},
    })
    names = _names(out)
    assert "DNS resolver (allow-remote-requests)" in names
    assert "Web proxy" in names


def test_vpn_servers_grouped():
    out = classify_roles({
        "wireguard": [{"name": "wg0"}], "wg_peers": [{}, {}],
        "ipsec_active": [{"src-address": "x"}],
        "l2tp_server": [{"disabled": "false"}],
        "sstp_server": [{"disabled": "false"}],
        "ovpn_server": [{"disabled": "true"}],  # diabaikan
    })
    names = _names(out)
    assert "VPN WireGuard" in names
    assert "VPN IPsec" in names
    vpn_srv = next(r for r in out["peran"] if r["nama"].startswith("VPN server"))
    assert "L2TP" in vpn_srv["nama"] and "SSTP" in vpn_srv["nama"]
    assert "OpenVPN" not in vpn_srv["nama"]


def test_qos_and_vrrp_and_container():
    out = classify_roles({
        "queues_simple": [{"name": "q1"}, {"name": "q2"}],
        "vrrp": [{"name": "vrrp1"}],
        "container": [{"name": "c1"}],
    })
    names = _names(out)
    assert "QoS / traffic shaping" in names
    assert "High availability (VRRP)" in names
    assert "Container host (RouterOS v7)" in names


def test_summary_echoes_identity_version():
    out = classify_roles({"identity": "core-rtr", "version": "7.15.3",
                          "bgp": [{"name": "p"}]})
    assert out["identitas"] == "core-rtr"
    assert out["versi"] == "7.15.3"
    assert "BGP router" in out["ringkasan"]
