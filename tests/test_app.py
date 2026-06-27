"""Uji endpoint Pulse: gerbang remediasi, analyze, snapshot, _action_in_proposal."""

from __future__ import annotations

import json

from conftest import FakeRos

from mikroclaw.web import app as APP


# ----------------------------------------------------------------- helpers
class FakeRequest:
    def __init__(self, data):
        self._data = data

    async def json(self):
        if self._data is _BAD:
            raise ValueError("bad json")
        return self._data


_BAD = object()


class FakePoller:
    def __init__(self, allow_write=False, ai=None, ros=None):
        self.ros = ros or FakeRos()
        self.state = {"allow_write": allow_write, "ai": ai}
        self.notified = 0

    async def _notify(self):
        self.notified += 1

    def snapshot(self):
        return {"hello": "world"}


def _body(resp):
    return json.loads(bytes(resp.body))


PROPOSAL = {"remediasi": [
    {"tipe": "blokir_ip", "parameter": {"address": "1.2.3.4", "list": "", "service": ""}}]}
ACTION = {"tipe": "blokir_ip", "parameter": {"address": "1.2.3.4", "list": "", "service": ""}}


# ----------------------------------------------------------------- proposal match
def test_action_in_proposal():
    assert APP._action_in_proposal(ACTION, PROPOSAL) is True
    assert APP._action_in_proposal(
        {"tipe": "blokir_ip", "parameter": {"address": "9.9.9.9"}}, PROPOSAL) is False
    assert APP._action_in_proposal(ACTION, None) is False
    assert APP._action_in_proposal(ACTION, {"remediasi": []}) is False


# ----------------------------------------------------------------- remediate gates
async def test_remediate_503_without_poller(monkeypatch):
    monkeypatch.setattr(APP, "_poller", None)
    resp = await APP.remediate(FakeRequest({"action": ACTION}))
    assert resp.status_code == 503


async def test_remediate_403_when_write_disabled(monkeypatch):
    monkeypatch.setattr(APP, "_poller", FakePoller(allow_write=False, ai=PROPOSAL))
    resp = await APP.remediate(FakeRequest({"action": ACTION}))
    assert resp.status_code == 403
    assert "ALLOW_WRITE" in _body(resp)["error"]


async def test_remediate_400_invalid_action(monkeypatch):
    monkeypatch.setattr(APP, "_poller", FakePoller(allow_write=True, ai=PROPOSAL))
    resp = await APP.remediate(FakeRequest({"action": {"tipe": "reboot", "parameter": {}}}))
    assert resp.status_code == 400


async def test_remediate_400_bad_json(monkeypatch):
    monkeypatch.setattr(APP, "_poller", FakePoller(allow_write=True, ai=PROPOSAL))
    resp = await APP.remediate(FakeRequest(_BAD))
    assert resp.status_code == 400


async def test_remediate_409_when_not_proposed(monkeypatch):
    monkeypatch.setattr(APP, "_poller", FakePoller(allow_write=True, ai={"remediasi": []}))
    resp = await APP.remediate(FakeRequest({"action": ACTION}))
    assert resp.status_code == 409


async def test_remediate_success_executes_and_notifies(monkeypatch):
    pol = FakePoller(allow_write=True, ai=PROPOSAL)
    monkeypatch.setattr(APP, "_poller", pol)
    resp = await APP.remediate(FakeRequest({"action": ACTION}))
    assert resp.status_code == 200
    assert _body(resp)["ok"] is True
    # benar-benar memanggil ros.put utk aturan drop + memberi tahu SSE
    assert pol.ros.calls[0][0] == "PUT"
    assert pol.notified == 1


# ----------------------------------------------------------------- analyze / snapshot
async def test_analyze_503_without_analyst(monkeypatch):
    monkeypatch.setattr(APP, "_analyst", None)
    resp = await APP.analyze(FakeRequest(None))
    assert resp.status_code == 503


async def test_analyze_triggers_request_now(monkeypatch):
    class FakeAnalyst:
        def __init__(self):
            self.triggered = 0

        def request_now(self):
            self.triggered += 1

    fa = FakeAnalyst()
    monkeypatch.setattr(APP, "_analyst", fa)
    resp = await APP.analyze(FakeRequest(None))
    assert resp.status_code == 200
    assert fa.triggered == 1


async def test_snapshot_returns_state(monkeypatch):
    monkeypatch.setattr(APP, "_poller", FakePoller())
    resp = await APP.snapshot(FakeRequest(None))
    assert _body(resp) == {"hello": "world"}
