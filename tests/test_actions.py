"""Uji remediasi 1-klik (Fase 3): validasi allowlist & eksekusi tervalidasi."""

from __future__ import annotations

from conftest import FakeRos

from mikroclaw.client import RouterOSError
from mikroclaw.web import actions


# ----------------------------------------------------------------- validate
def test_validate_ok():
    assert actions.validate_action(
        {"tipe": "blokir_ip", "parameter": {"address": "1.2.3.4", "list": "", "service": ""}}
    ) is None


def test_validate_rejects_unknown_type():
    err = actions.validate_action({"tipe": "reboot", "parameter": {}})
    assert err and "tidak diizinkan" in err


def test_validate_requires_params():
    err = actions.validate_action({"tipe": "blokir_ip", "parameter": {"address": "  "}})
    assert err and "address" in err

    err2 = actions.validate_action({"tipe": "tambah_address_list",
                                    "parameter": {"address": "1.2.3.4", "list": ""}})
    assert err2 and "list" in err2


def test_validate_rejects_non_dict():
    assert actions.validate_action("x") is not None
    assert actions.validate_action({"tipe": "blokir_ip", "parameter": "x"}) is not None


# ----------------------------------------------------------------- execute
async def test_execute_blokir_ip_puts_drop_rule():
    ros = FakeRos()
    res = await actions.execute_action(
        ros, {"tipe": "blokir_ip", "parameter": {"address": "10.0.0.5"}})
    assert res["ok"] is True
    method, path, body = ros.calls[0]
    assert method == "PUT" and path == "/ip/firewall/filter"
    assert body["action"] == "drop" and body["src-address"] == "10.0.0.5"
    assert body["comment"] == actions.AUDIT


async def test_execute_address_list():
    ros = FakeRos()
    res = await actions.execute_action(
        ros, {"tipe": "tambah_address_list",
              "parameter": {"address": "1.2.3.4", "list": "blokir"}})
    assert res["ok"] is True
    _, path, body = ros.calls[0]
    assert path == "/ip/firewall/address-list"
    assert body["list"] == "blokir"


async def test_execute_disable_service_resolves_id():
    ros = FakeRos(responses={("GET", "/ip/service"): [
        {".id": "*7", "name": "telnet"}, {".id": "*8", "name": "www"}]})
    res = await actions.execute_action(
        ros, {"tipe": "nonaktifkan_service", "parameter": {"service": "telnet"}})
    assert res["ok"] is True
    # panggilan kedua: PATCH ke id yang benar
    assert ros.calls[-1][0] == "PATCH"
    assert ros.calls[-1][1] == "/ip/service/*7"
    assert ros.calls[-1][2] == {"disabled": "yes"}


async def test_execute_disable_service_not_found():
    ros = FakeRos(responses={("GET", "/ip/service"): [{".id": "*1", "name": "ssh"}]})
    res = await actions.execute_action(
        ros, {"tipe": "nonaktifkan_service", "parameter": {"service": "telnet"}})
    assert res["ok"] is False and "tidak ditemukan" in res["pesan"]


async def test_execute_wraps_routeros_error():
    class Boom(FakeRos):
        async def put(self, path, data):
            raise RouterOSError("gagal konek")

    res = await actions.execute_action(
        Boom(), {"tipe": "blokir_ip", "parameter": {"address": "9.9.9.9"}})
    assert res["ok"] is False and "gagal" in res["pesan"]
