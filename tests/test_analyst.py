"""Uji lapis AI (Fase 2/3): config, ringkasan brief, parsing tool-use Anthropic."""

from __future__ import annotations

import httpx

from mikroclaw.web import analyst as A
from mikroclaw.web.analyst import Analyst, AnalystConfig


# ----------------------------------------------------------------- config
def test_as_float_int_helpers():
    assert A._as_float("1.5", 9.0) == 1.5
    assert A._as_float("", 9.0) == 9.0
    assert A._as_float("bad", 9.0) == 9.0
    assert A._as_int("3", 7) == 3
    assert A._as_int(None, 7) == 7


def test_config_from_env_defaults(monkeypatch):
    for k in ("ANTHROPIC_API_KEY", "MIKROCLAW_AI_MODEL",
              "MIKROCLAW_AI_INTERVAL", "MIKROCLAW_AI_MAX_TOKENS"):
        monkeypatch.delenv(k, raising=False)
    cfg = AnalystConfig.from_env()
    assert cfg.api_key == ""
    assert cfg.model == "claude-sonnet-4-6"
    assert cfg.interval == 60.0
    assert cfg.max_tokens == 2048


def test_config_from_env_overrides(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    monkeypatch.setenv("MIKROCLAW_AI_MODEL", "claude-opus-4-8")
    monkeypatch.setenv("MIKROCLAW_AI_INTERVAL", "30")
    cfg = AnalystConfig.from_env()
    assert cfg.api_key == "sk-test"
    assert cfg.model == "claude-opus-4-8"
    assert cfg.interval == 30.0


# ----------------------------------------------------------------- brief
def test_brief_trims_logs_and_passes_forecast():
    snap = {
        "connected": True,
        "system": {"identity": "r1", "version": "7.15", "cpu_load": 5},
        "counters": {"clients_total": 3},
        "forecast": {"disk": {"trend": "naik"}},
        "wan": {"iface": "ether1"},
        "interfaces": [{"name": "ether1", "running": True, "rx_bps": 100,
                        "tx_bps": 0, "disabled": False}],
        "clients": [{"ip": "192.168.88.2"}],
        "services": [{"name": "www"}],
        "logs": [{"time": str(i), "message": f"m{i}", "topics": "system",
                  "severity": "info"} for i in range(40)],
    }
    brief = A._brief(snap)
    assert brief["forecast"] == {"disk": {"trend": "naik"}}
    assert len(brief["recent_logs"]) == 25          # hanya 25 terakhir
    assert brief["recent_logs"][-1]["msg"] == "m39"
    assert brief["clients_total"] == 3


# ----------------------------------------------------------------- analyze
def _mk_analyst(api_key="sk-test"):
    cfg = AnalystConfig(api_key=api_key, model="claude-sonnet-4-6",
                        interval=60.0, max_tokens=512)
    return Analyst(poller=None, cfg=cfg)


def _patch_client(analyst, handler):
    analyst._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))


async def test_analyze_parses_tool_use():
    a = _mk_analyst()
    payload = {
        "model": "claude-sonnet-4-6",
        "stop_reason": "tool_use",
        "usage": {"input_tokens": 100, "output_tokens": 20},
        "content": [{"type": "tool_use", "name": A.TOOL_NAME, "input": {
            "status": "perhatian", "ringkasan": "ada anomali",
            "anomali": [{"severity": "warning", "judul": "x", "detail": "y"}],
            "rekomendasi": ["cek kabel"],
            "prediksi": [{"metrik": "disk", "arah": "naik", "horizon": "12 jam", "detail": "z"}],
            "remediasi": [{"tipe": "blokir_ip", "judul": "blok", "alasan": "spam",
                           "parameter": {"address": "1.2.3.4", "list": "", "service": ""}}],
            "log_penting": [],
        }}],
    }
    _patch_client(a, lambda req: httpx.Response(200, json=payload))
    res = await a.analyze({"system": {}, "counters": {}})
    assert res["ok"] is True
    assert res["status"] == "perhatian"
    assert res["remediasi"][0]["tipe"] == "blokir_ip"
    assert res["prediksi"][0]["metrik"] == "disk"
    assert res["usage"]["in"] == 100


async def test_analyze_disabled_without_key():
    a = _mk_analyst(api_key="")
    res = await a.analyze({"system": {}})
    assert res["enabled"] is False and res["ok"] is False


async def test_analyze_http_error_status():
    a = _mk_analyst()
    _patch_client(a, lambda req: httpx.Response(401, json={"error": {"message": "bad key"}}))
    res = await a.analyze({"system": {}, "counters": {}})
    assert res["ok"] is False
    assert "401" in res["error"]


async def test_analyze_no_tool_use_block():
    a = _mk_analyst()
    payload = {"model": "m", "stop_reason": "end_turn",
               "content": [{"type": "text", "text": "halo"}]}
    _patch_client(a, lambda req: httpx.Response(200, json=payload))
    res = await a.analyze({"system": {}, "counters": {}})
    assert res["ok"] is False and "tool_use" in res["error"]
