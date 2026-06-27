"""Uji RouterOSClient: pemetaan verb REST, normalisasi path, penanganan error."""

from __future__ import annotations

import httpx
import pytest

from mikroclaw.client import RouterOSError


async def test_get_maps_to_get_and_normalizes_path(mock_client_factory):
    seen = {}

    def handler(req: httpx.Request) -> httpx.Response:
        seen["method"] = req.method
        seen["path"] = req.url.path
        return httpx.Response(200, json=[{"a": 1}])

    client = mock_client_factory(handler)
    out = await client.get("interface")  # tanpa leading slash
    assert out == [{"a": 1}]
    assert seen["method"] == "GET"
    assert seen["path"].endswith("/rest/interface")


@pytest.mark.parametrize(
    "verb,call,expect_method",
    [
        ("put", lambda c: c.put("ip/firewall/filter", {"x": 1}), "PUT"),
        ("patch", lambda c: c.patch("ip/service/1", {"disabled": "yes"}), "PATCH"),
        ("delete", lambda c: c.delete("ip/firewall/filter/1"), "DELETE"),
        ("post", lambda c: c.post("ping", {"address": "8.8.8.8"}), "POST"),
    ],
)
async def test_verb_mapping(mock_client_factory, verb, call, expect_method):
    captured = {}

    def handler(req: httpx.Request) -> httpx.Response:
        captured["method"] = req.method
        captured["body"] = req.content.decode() or ""
        return httpx.Response(200, json={"ok": True})

    client = mock_client_factory(handler)
    out = await call(client)
    assert out == {"ok": True}
    assert captured["method"] == expect_method


async def test_put_sends_json_body(mock_client_factory):
    captured = {}

    def handler(req: httpx.Request) -> httpx.Response:
        captured["body"] = req.content.decode()
        return httpx.Response(201, json={})

    client = mock_client_factory(handler)
    await client.put("ip/firewall/filter", {"chain": "forward", "action": "drop"})
    assert '"chain"' in captured["body"] and "forward" in captured["body"]


async def test_4xx_raises_routeroserror_with_detail(mock_client_factory):
    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"error": "unauthorized"})

    client = mock_client_factory(handler)
    with pytest.raises(RouterOSError) as ei:
        await client.get("system/resource")
    assert "401" in str(ei.value)


async def test_connection_error_wrapped(mock_client_factory):
    def handler(req: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("boom")

    client = mock_client_factory(handler)
    with pytest.raises(RouterOSError) as ei:
        await client.get("system/resource")
    assert "Gagal menghubungi RouterOS" in str(ei.value)


async def test_empty_body_returns_none(mock_client_factory):
    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"")

    client = mock_client_factory(handler)
    assert await client.get("system/reboot") is None


async def test_non_json_returns_text(mock_client_factory):
    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"plain-text")

    client = mock_client_factory(handler)
    assert await client.get("x") == "plain-text"
