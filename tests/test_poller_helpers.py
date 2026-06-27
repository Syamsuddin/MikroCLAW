"""Uji helper murni poller: konversi, severity log, RTT, sinyal, pair, tanggal, vendor."""

from __future__ import annotations

import datetime

import pytest

from mikroclaw.web import poller as P


@pytest.mark.parametrize("v,exp", [("5", 5), (3.9, 3), ("x", 0), (None, 0), ("12.0", 12)])
def test_to_int(v, exp):
    assert P._to_int(v) == exp


@pytest.mark.parametrize("v,exp", [("5.5", 5.5), (2, 2.0), ("bad", 0.0), (None, 0.0)])
def test_to_float(v, exp):
    assert P._to_float(v) == exp


@pytest.mark.parametrize("v,exp", [("true", True), ("yes", True), ("1", True),
                                   ("false", False), ("", False), (None, False)])
def test_is_true(v, exp):
    assert P._is_true(v) is exp


@pytest.mark.parametrize("topics,exp", [
    ("system,error", "critical"),
    ("firewall,critical", "critical"),
    ("dhcp,warning", "warning"),
    ("system,info", "info"),
    ("", "info"),
    (None, "info"),
])
def test_log_severity(topics, exp):
    assert P._log_severity(topics) == exp


@pytest.mark.parametrize("v,exp", [
    ("12ms", 12.0),
    ("1.2ms", 1.2),
    ("8ms764us", 8.0),   # ms diutamakan
    ("500us", 0.5),
    ("15", 15.0),        # angka polos -> ms
    ("timeout", None),
    (None, None),
])
def test_parse_rtt_ms(v, exp):
    assert P._parse_rtt_ms(v) == exp


@pytest.mark.parametrize("v,exp", [("-67", -67), ("-67dBm", -67), ("bad", None), (None, None)])
def test_signal_dbm(v, exp):
    assert P._signal_dbm(v) == exp


@pytest.mark.parametrize("v,exp", [("123/456", (123, 456)), ("", (0, 0)),
                                   (None, (0, 0)), ("nopair", (0, 0))])
def test_split_pair(v, exp):
    assert P._split_pair(v) == exp


def test_vendor_known_and_unknown():
    assert P._vendor("AC:DE:48:11:22:33") == "Apple"
    assert P._vendor("ac-de-48-11-22-33") == "Apple"  # normalisasi '-' & case
    assert P._vendor("00:00:00:00:00:00") == ""
    assert P._vendor(None) == ""


def test_days_until():
    today = datetime.date.today()
    future = today + datetime.timedelta(days=10)
    s = f"{future.isoformat()} 12:00:00"
    assert P._days_until(s) == 10
    assert P._days_until(None) is None
    assert P._days_until("tanpa-tanggal") is None
