"""Uji Chronicle: snapshot/diff/penilaian risiko konfigurasi (murni + persistensi)."""

from __future__ import annotations

from mikroclaw.chronicle import (
    assess_change,
    diff_snapshots,
    list_snapshots,
    load_latest,
    narrate_diff,
    save_snapshot,
    snapshot_config,
)


def _ev(**over):
    base = {
        "identity": "rtr", "version": "7.15",
        "filter": [], "nat": [], "services": [], "users": [], "groups": [],
        "schedulers": [], "scripts": [], "dns": {}, "address_lists": [],
    }
    base.update(over)
    return base


def test_snapshot_is_stable_and_hashable():
    a = snapshot_config(_ev(filter=[{"chain": "input", "action": "drop", "comment": "x"}]))
    b = snapshot_config(_ev(filter=[{"chain": "input", "action": "drop", "comment": "x"}]))
    assert a["hash"] == b["hash"]
    assert "firewall_filter" in a["sections"]


def test_snapshot_drops_volatile_fields():
    snap = snapshot_config(_ev(filter=[{
        "chain": "input", "action": "drop", "comment": "x",
        "bytes": "999", "packets": "12", ".id": "*7",
    }]))
    rule = snap["sections"]["firewall_filter"]["#x"]
    assert "bytes" not in rule and "packets" not in rule and ".id" not in rule


def test_rule_reorder_does_not_diff():
    old = snapshot_config(_ev(filter=[
        {"chain": "input", "action": "accept", "comment": "a"},
        {"chain": "input", "action": "drop", "comment": "b"},
    ]))
    new = snapshot_config(_ev(filter=[
        {"chain": "input", "action": "drop", "comment": "b"},
        {"chain": "input", "action": "accept", "comment": "a"},
    ]))
    d = diff_snapshots(old, new)
    assert d["identik"] is True
    assert d["jumlah_perubahan"] == 0


def test_diff_detects_add_remove_modify():
    old = snapshot_config(_ev(services=[{"name": "ssh", "disabled": "false", "port": "22"}]))
    new = snapshot_config(_ev(services=[{"name": "ssh", "disabled": "false", "port": "2222"}]))
    d = diff_snapshots(old, new)
    assert d["jumlah_perubahan"] == 1
    ch = d["perubahan"][0]
    assert ch["jenis"] == "diubah"
    assert ch["delta"]["port"]["dari"] == "22" and ch["delta"]["port"]["ke"] == "2222"


def test_risk_new_user_is_critical():
    ch = {"bagian": "user", "jenis": "ditambah", "nilai": {"name": "hacker", "group": "full"}}
    assert assess_change(ch)["severity"] == "critical"


def test_risk_open_mgmt_port_is_critical():
    ch = {"bagian": "firewall_filter", "jenis": "ditambah",
          "nilai": {"chain": "input", "action": "accept", "dst-port": "8291", "src-address": ""}}
    assert assess_change(ch)["severity"] == "critical"


def test_risk_new_scheduler_warning():
    ch = {"bagian": "scheduler", "jenis": "ditambah", "nilai": {"name": "persist"}}
    assert assess_change(ch)["severity"] == "warning"


def test_risk_open_resolver_warning():
    ch = {"bagian": "ip_dns", "jenis": "diubah",
          "delta": {"allow-remote-requests": {"dari": "false", "ke": "true"}}, "nilai": {}}
    assert assess_change(ch)["severity"] == "warning"


def test_risk_disabled_drop_rule_warning():
    ch = {"bagian": "firewall_filter", "jenis": "diubah",
          "nilai": {"chain": "forward", "action": "drop"},
          "delta": {"disabled": {"dari": "false", "ke": "true"}}}
    assert assess_change(ch)["severity"] == "warning"


def test_narrate_overall_severity_and_sort():
    old = snapshot_config(_ev())
    new = snapshot_config(_ev(
        users=[{"name": "x", "group": "full"}],                       # critical
        schedulers=[{"name": "s"}],                                    # warning
        nat=[{"chain": "dstnat", "action": "dst-nat", "to-addresses": "10.0.0.2"}],  # info
    ))
    narrated = narrate_diff(diff_snapshots(old, new))
    assert narrated["keparahan_tertinggi"] == "critical"
    assert narrated["perubahan"][0]["risiko"]["severity"] == "critical"  # terurut


def test_persistence_roundtrip(tmp_path):
    snap = snapshot_config(_ev(identity="rtr"))
    p = save_snapshot(snap, tmp_path, label="uji")
    assert p.exists()
    assert len(list_snapshots(tmp_path)) == 1
    loaded = load_latest(tmp_path)
    assert loaded["hash"] == snap["hash"]


def test_load_latest_empty(tmp_path):
    assert load_latest(tmp_path) is None
