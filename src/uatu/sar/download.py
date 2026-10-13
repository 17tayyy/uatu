"""Authenticated product downloads from the Copernicus Data Space Ecosystem."""

import asyncio
import time
from pathlib import Path

import httpx
import structlog

from uatu.sar.catalog import Scene

log = structlog.get_logger(component="sar")

TOKEN_URL = "https://identity.dataspace.copernicus.eu/auth/realms/CDSE/protocol/openid-connect/token"
DOWNLOAD_URL = "https://download.dataspace.copernicus.eu/odata/v1/Products({id})/$value"
_CLIENT_ID = "cdse-public"


class CdseAuth:
    """OAuth2 tokens for CDSE: a password grant once, then refreshes (access tokens last ~10 minutes)."""

    def __init__(self, client: httpx.AsyncClient, username: str, password: str) -> None:
        self._client = client
        self._username = username
        self._password = password
        self._access_token: str | None = None
        self._refresh_token: str | None = None
        self._access_expires = 0.0
        self._refresh_expires = 0.0

    async def token(self) -> str:
        now = time.monotonic()
        if self._access_token is not None and now < self._access_expires - 60:
            return self._access_token
        if self._refresh_token is not None and now < self._refresh_expires - 60:
            try:
                return await self._grant({"grant_type": "refresh_token", "refresh_token": self._refresh_token})
            except httpx.HTTPStatusError:
                log.info("cdse_refresh_rejected")  # e.g. session ended server-side; log in again
        return await self._grant({"grant_type": "password", "username": self._username, "password": self._password})

    async def _grant(self, data: dict[str, str]) -> str:
        now = time.monotonic()
        response = await self._client.post(TOKEN_URL, data={**data, "client_id": _CLIENT_ID})
        response.raise_for_status()
        tokens = response.json()
        self._access_token = tokens["access_token"]
        self._refresh_token = tokens.get("refresh_token")
        self._access_expires = now + tokens["expires_in"]
        self._refresh_expires = now + tokens.get("refresh_expires_in", 0)
        return self._access_token


async def download(client: httpx.AsyncClient, auth: CdseAuth, scene: Scene, dest_dir: Path) -> Path:
    """Download the scene's zipped SAFE into dest_dir, unless it is already there. Returns its path."""
    path = dest_dir / f"{scene.name.removesuffix('.SAFE')}.zip"
    if path.exists():
        return path
    await asyncio.to_thread(dest_dir.mkdir, parents=True, exist_ok=True)
    partial = path.with_name(path.name + ".part")

    # The download service redirects to another host, and httpx drops the Authorization
    # header on cross-host redirects, so redirects are followed by hand.
    url = DOWNLOAD_URL.format(id=scene.id)
    for _ in range(5):
        headers = {"Authorization": f"Bearer {await auth.token()}"}
        async with client.stream("GET", url, headers=headers, follow_redirects=False) as response:
            if response.is_redirect:
                url = response.headers["location"]
                continue
            response.raise_for_status()
            size = int(response.headers.get("content-length") or scene.size_bytes)
            log.info("download_started", scene=scene.name, size_mb=size // 2**20)
            written = 0
            next_report = 256 * 2**20
            with partial.open("wb") as f:
                async for chunk in response.aiter_bytes(2**20):
                    f.write(chunk)
                    written += len(chunk)
                    if written >= next_report:
                        log.info("download_progress", scene=scene.name, mb=written // 2**20)
                        next_report += 256 * 2**20
        partial.replace(path)
        log.info("download_finished", scene=scene.name, path=str(path), mb=written // 2**20)
        return path
    raise RuntimeError(f"too many redirects downloading {scene.name}")
