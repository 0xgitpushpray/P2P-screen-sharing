"""Client side of the signaling protocol (see signaling.py)."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator

from websockets.asyncio.client import ClientConnection, connect

from .signaling import MAX_MESSAGE_BYTES


class SignalingError(Exception):
    """The signaling server rejected us or reported an error."""


class SignalingClient:
    def __init__(self, url: str) -> None:
        self.url = url
        self._ws: ClientConnection | None = None

    async def __aenter__(self) -> SignalingClient:
        self._ws = await connect(self.url, max_size=MAX_MESSAGE_BYTES)
        return self

    async def __aexit__(self, *exc_info) -> None:
        await self._ws.close()

    async def join(self, room: str, role: str, password: str | None = None) -> dict:
        await self.send({"type": "join", "room": room, "role": role, "password": password or ""})
        return await self.recv()

    async def send(self, msg: dict) -> None:
        await self._ws.send(json.dumps(msg))

    async def recv(self) -> dict:
        return self._check(json.loads(await self._ws.recv()))

    async def __aiter__(self) -> AsyncIterator[dict]:
        async for raw in self._ws:
            yield self._check(json.loads(raw))

    @staticmethod
    def _check(msg: dict) -> dict:
        if msg.get("type") == "error":
            raise SignalingError(msg.get("message", "unknown error"))
        return msg
