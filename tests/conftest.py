"""Fixtures bersama untuk test MikroCLAW — tanpa router/jaringan nyata."""

from __future__ import annotations

from typing import Any, Callable

import httpx
import pytest

from mikroclaw.client import RouterOSClient
from mikroclaw.config import Config


def make_config(**over: Any) -> Config:
    base = dict(
        host="192.0.2.10", user="u", password="p", port=443,
        use_tls=True, verify_tls=False, allow_write=False, timeout=5.0,
    )
    base.update(over)
    return Config(**base)


@pytest.fixture
def mock_client_factory() -> Callable[[Callable[[httpx.Request], httpx.Response]], RouterOSClient]:
    """Bangun RouterOSClient yang transport-nya di-mock oleh sebuah handler.

    Handler menerima httpx.Request dan mengembalikan httpx.Response — tak ada
    soket nyata yang dibuka.
    """
    created: list[RouterOSClient] = []

    def factory(handler: Callable[[httpx.Request], httpx.Response]) -> RouterOSClient:
        cfg = make_config()
        client = RouterOSClient(cfg)
        # ganti transport bawaan dengan MockTransport
        client._client = httpx.AsyncClient(
            base_url=cfg.base_url,
            transport=httpx.MockTransport(handler),
        )
        created.append(client)
        return client

    return factory


class FakeRos:
    """Stub RouterOSClient untuk menguji actions/poller tanpa httpx.

    Merekam tiap panggilan ke dalam ``calls`` dan mengembalikan nilai dari
    ``responses`` (per metode) atau ``None``. Set ``raise_on`` untuk memicu error.
    """

    def __init__(self, responses: dict[str, Any] | None = None) -> None:
        self.calls: list[tuple[str, str, Any]] = []
        self.responses = responses or {}

    async def get(self, path: str) -> Any:
        self.calls.append(("GET", path, None))
        return self.responses.get(("GET", path), self.responses.get("GET"))

    async def put(self, path: str, data: Any) -> Any:
        self.calls.append(("PUT", path, data))
        return self.responses.get("PUT")

    async def patch(self, path: str, data: Any) -> Any:
        self.calls.append(("PATCH", path, data))
        return self.responses.get("PATCH")

    async def post(self, path: str, data: Any = None) -> Any:
        self.calls.append(("POST", path, data))
        return self.responses.get("POST")

    async def delete(self, path: str) -> Any:
        self.calls.append(("DELETE", path, None))
        return self.responses.get("DELETE")
