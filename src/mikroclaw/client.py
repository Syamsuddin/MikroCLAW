"""Client REST API RouterOS v7 (async, httpx)."""

from __future__ import annotations

from typing import Any

import httpx

from .config import Config


class RouterOSError(RuntimeError):
    """Error koneksi atau respons error dari RouterOS."""


class RouterOSClient:
    """Pembungkus tipis di atas REST API RouterOS (/rest/...).

    RouterOS REST memetakan path konsol ke URL, mis:
      /interface/print   -> GET  /rest/interface
      /ip/address        -> GET  /rest/ip/address
      tambah item        -> PUT  /rest/<path>      (body JSON)
      ubah item by id    -> PATCH /rest/<path>/<id>
      hapus item by id   -> DELETE /rest/<path>/<id>
      command (ping dll) -> POST /rest/<path>
    """

    def __init__(self, cfg: Config) -> None:
        self.cfg = cfg
        self._client = httpx.AsyncClient(
            base_url=cfg.base_url,
            auth=(cfg.user, cfg.password),
            verify=cfg.verify_tls,
            timeout=cfg.timeout,
            headers={"Content-Type": "application/json"},
        )

    async def aclose(self) -> None:
        await self._client.aclose()

    async def request(
        self, method: str, path: str, json: Any | None = None
    ) -> Any:
        norm = "/" + path.strip("/")
        try:
            resp = await self._client.request(method, norm, json=json)
        except httpx.HTTPError as exc:  # koneksi/timeout/TLS
            raise RouterOSError(
                f"Gagal menghubungi RouterOS di {self.cfg.base_url}{norm}: {exc}"
            ) from exc

        if resp.status_code >= 400:
            detail: Any = resp.text
            try:
                detail = resp.json()
            except ValueError:
                pass
            raise RouterOSError(
                f"RouterOS membalas {resp.status_code} untuk {method} {norm}: {detail}"
            )

        if not resp.content:
            return None
        try:
            return resp.json()
        except ValueError:
            return resp.text

    async def get(self, path: str) -> Any:
        return await self.request("GET", path)

    async def post(self, path: str, data: dict[str, Any] | None = None) -> Any:
        return await self.request("POST", path, json=data or {})

    async def put(self, path: str, data: dict[str, Any]) -> Any:
        return await self.request("PUT", path, json=data)

    async def patch(self, path: str, data: dict[str, Any]) -> Any:
        return await self.request("PATCH", path, json=data)

    async def delete(self, path: str) -> Any:
        return await self.request("DELETE", path)
