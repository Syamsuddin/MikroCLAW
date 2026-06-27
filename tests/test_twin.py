"""Uji Twin: simulator what-if packet-walk (murni, tanpa router)."""

from __future__ import annotations

from mikroclaw.twin import simulate_change, simulate_packet


def _ev(**over):
    base = {
        "filter": [], "nat": [], "mangle": [], "routes": [
            {"dst-address": "0.0.0.0/0", "gateway": "ether1", "active": "true"},
        ],
        "address_lists": [], "addresses": [{"address": "192.168.88.1/24"}],
    }
    base.update(over)
    return base


def test_default_accept_when_no_rules():
    out = simulate_packet(_ev(), {"src": "192.168.88.10", "dst": "8.8.8.8",
                                  "protocol": "udp", "dst_port": 53})
    assert out["verdict"] == "accept"
    assert out["default_accept"] is True
    assert out["chain"] == "forward"
    assert out["ke_router"] is False


def test_drop_rule_matches_src():
    ev = _ev(filter=[{"chain": "forward", "action": "drop", "src-address": "192.168.88.10"}])
    out = simulate_packet(ev, {"src": "192.168.88.10", "dst": "8.8.8.8", "dst_port": 443})
    assert out["verdict"] == "drop"
    assert out["diteruskan"] is False


def test_rule_order_first_match_wins():
    # accept di atas drop -> accept menang
    ev = _ev(filter=[
        {"chain": "forward", "action": "accept", "src-address": "192.168.88.0/24"},
        {"chain": "forward", "action": "drop", "src-address": "192.168.88.10"},
    ])
    out = simulate_packet(ev, {"src": "192.168.88.10", "dst": "8.8.8.8", "dst_port": 80})
    assert out["verdict"] == "accept"


def test_disabled_rule_ignored():
    ev = _ev(filter=[{"chain": "forward", "action": "drop",
                      "src-address": "192.168.88.10", "disabled": "true"}])
    out = simulate_packet(ev, {"src": "192.168.88.10", "dst": "8.8.8.8", "dst_port": 80})
    assert out["verdict"] == "accept"


def test_dst_port_and_protocol_match():
    ev = _ev(filter=[{"chain": "forward", "action": "drop",
                      "protocol": "tcp", "dst-port": "22"}])
    blocked = simulate_packet(ev, {"src": "10.0.0.2", "dst": "8.8.8.8",
                                   "protocol": "tcp", "dst_port": 22})
    allowed = simulate_packet(ev, {"src": "10.0.0.2", "dst": "8.8.8.8",
                                   "protocol": "tcp", "dst_port": 80})
    assert blocked["verdict"] == "drop"
    assert allowed["verdict"] == "accept"


def test_port_range_and_list():
    ev = _ev(filter=[{"chain": "forward", "action": "drop", "dst-port": "1000-2000,8080"}])
    assert simulate_packet(ev, {"src": "10.0.0.2", "dst": "1.1.1.1", "dst_port": 1500})["verdict"] == "drop"
    assert simulate_packet(ev, {"src": "10.0.0.2", "dst": "1.1.1.1", "dst_port": 8080})["verdict"] == "drop"
    assert simulate_packet(ev, {"src": "10.0.0.2", "dst": "1.1.1.1", "dst_port": 80})["verdict"] == "accept"


def test_input_chain_when_dst_is_router():
    ev = _ev(filter=[{"chain": "input", "action": "drop", "protocol": "tcp", "dst-port": "23"}])
    out = simulate_packet(ev, {"src": "192.168.88.50", "dst": "192.168.88.1",
                               "protocol": "tcp", "dst_port": 23})
    assert out["chain"] == "input"
    assert out["ke_router"] is True
    assert out["verdict"] == "drop"


def test_address_list_membership():
    ev = _ev(
        filter=[{"chain": "forward", "action": "drop", "src-address-list": "blocked"}],
        address_lists=[{"list": "blocked", "address": "10.0.0.0/24"}],
    )
    assert simulate_packet(ev, {"src": "10.0.0.5", "dst": "8.8.8.8", "dst_port": 80})["verdict"] == "drop"
    assert simulate_packet(ev, {"src": "10.9.9.9", "dst": "8.8.8.8", "dst_port": 80})["verdict"] == "accept"


def test_negation_src_address():
    # drop semua KECUALI subnet tepercaya
    ev = _ev(filter=[{"chain": "forward", "action": "drop", "src-address": "!192.168.88.0/24"}])
    assert simulate_packet(ev, {"src": "192.168.88.10", "dst": "8.8.8.8", "dst_port": 80})["verdict"] == "accept"
    assert simulate_packet(ev, {"src": "172.16.0.5", "dst": "8.8.8.8", "dst_port": 80})["verdict"] == "drop"


def test_connection_state_new_vs_established():
    # aturan drop hanya untuk state 'invalid' tak kena paket 'new'
    ev = _ev(filter=[{"chain": "forward", "action": "drop", "connection-state": "invalid"}])
    out = simulate_packet(ev, {"src": "10.0.0.2", "dst": "8.8.8.8", "dst_port": 80, "state": "new"})
    assert out["verdict"] == "accept"


def test_jump_and_return():
    ev = _ev(filter=[
        {"chain": "forward", "action": "jump", "jump-target": "custom"},
        {"chain": "custom", "action": "drop", "src-address": "10.0.0.5"},
        {"chain": "custom", "action": "return"},
    ])
    dropped = simulate_packet(ev, {"src": "10.0.0.5", "dst": "8.8.8.8", "dst_port": 80})
    passed = simulate_packet(ev, {"src": "10.0.0.9", "dst": "8.8.8.8", "dst_port": 80})
    assert dropped["verdict"] == "drop"
    assert passed["verdict"] == "accept"  # return -> keluar custom -> default accept


def test_dstnat_rewrites_destination():
    ev = _ev(nat=[{"chain": "dstnat", "action": "dst-nat", "protocol": "tcp",
                   "dst-port": "8080", "to-addresses": "192.168.88.10", "to-ports": "80"}])
    out = simulate_packet(ev, {"src": "1.2.3.4", "dst": "203.0.113.1",
                               "protocol": "tcp", "dst_port": 8080})
    assert out["dst_efektif"] == "192.168.88.10"
    assert out["dst_nat"] is not None


def test_srcnat_masquerade_detected():
    ev = _ev(nat=[{"chain": "srcnat", "action": "masquerade", "out-interface": "ether1"}])
    out = simulate_packet(ev, {"src": "192.168.88.10", "dst": "8.8.8.8", "dst_port": 443})
    assert out["src_nat"] is not None
    assert out["src_nat"]["action"] == "masquerade"


def test_simulate_change_flips_verdict():
    ev = _ev()  # awalnya accept
    res = simulate_change(
        ev,
        {"src": "192.168.88.10", "dst": "8.8.8.8", "dst_port": 80},
        {"chain": "forward", "action": "drop", "src-address": "192.168.88.10"},
    )
    assert res["berubah"] is True
    assert "DITERUSKAN" in res["sebelum"]
    assert "DI-DROP" in res["sesudah"]


def test_simulate_change_no_effect():
    ev = _ev()
    res = simulate_change(
        ev,
        {"src": "192.168.88.10", "dst": "8.8.8.8", "dst_port": 80},
        {"chain": "forward", "action": "drop", "src-address": "10.10.10.10"},
    )
    assert res["berubah"] is False
