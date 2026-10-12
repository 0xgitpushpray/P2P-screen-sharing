"""WebSocket signaling server.

Media never flows through this server. It only introduces peers to each other
by relaying the SDP offer/answer exchange; after that the sharer and each
viewer talk directly over an encrypted WebRTC connection.

Protocol (JSON text frames):

    client -> server  {"type": "join", "room": str, "role": "sharer"|"viewer", "password": str}
    server -> client  {"type": "joined", "id": str, "role": str, "waiting": bool}
    server -> sharer  {"type": "peer-joined", "peer": id} / {"type": "peer-left", "peer": id}
    server -> viewer  {"type": "sharer-left"}
    sharer -> server  {"type": "offer", "to": viewer_id, "sdp": str}
    viewer -> server  {"type": "answer", "sdp": str}
    server -> client  relayed offer/answer, with "from": sender_id added
    server -> client  {"type": "error", "message": str}
"""

from __future__ import annotations

import asyncio
import contextlib
import hmac
import json
import logging
import re
import secrets
import ssl
from dataclasses import dataclass, field
from http import HTTPStatus

from websockets.asyncio.server import ServerConnection, serve
from websockets.exceptions import ConnectionClosed

log = logging.getLogger(__name__)

ROOM_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
JOIN_TIMEOUT = 10
MAX_MESSAGE_BYTES = 64 * 1024
# Which message types each role may relay to the other side.
RELAYABLE = {"sharer": {"offer"}, "viewer": {"answer"}}


@dataclass(eq=False)
class Peer:
    id: str
    ws: ServerConnection
    room: str
    role: str
    password: str


@dataclass
class Room:
    sharer: Peer | None = None
    viewers: dict[str, Peer] = field(default_factory=dict)


def _parse(raw: str | bytes) -> dict | None:
    try:
        msg = json.loads(raw)
    except ValueError:
        return None
    return msg if isinstance(msg, dict) else None


def _password_ok(given: str, expected: str) -> bool:
    return hmac.compare_digest(given.encode(), expected.encode())


async def _send(ws: ServerConnection, msg: dict) -> None:
    with contextlib.suppress(ConnectionClosed):
        await ws.send(json.dumps(msg))


async def _reject(ws: ServerConnection, message: str) -> None:
    await _send(ws, {"type": "error", "message": message})
    await ws.close(1008, message[:100])


class SignalingServer:
    def __init__(self, max_viewers: int = 16) -> None:
        self.max_viewers = max_viewers
        self.rooms: dict[str, Room] = {}

    async def handler(self, ws: ServerConnection) -> None:
        peer = await self._join(ws)
        if peer is None:
            return
        try:
            async for raw in ws:
                await self._relay(peer, raw)
        except ConnectionClosed:
            pass
        finally:
            await self._leave(peer)

    async def _join(self, ws: ServerConnection) -> Peer | None:
        try:
            msg = _parse(await asyncio.wait_for(ws.recv(), JOIN_TIMEOUT))
        except (asyncio.TimeoutError, ConnectionClosed):
            msg = None
        if msg is None or msg.get("type") != "join":
            await _reject(ws, "expected a join message")
            return None

        room_name, role, password = msg.get("room"), msg.get("role"), msg.get("password") or ""
        if not isinstance(room_name, str) or not ROOM_RE.match(room_name):
            await _reject(ws, "room must be 1-64 characters of A-Z, a-z, 0-9, '_' or '-'")
            return None
        if role not in RELAYABLE:
            await _reject(ws, "role must be 'sharer' or 'viewer'")
            return None
        if not isinstance(password, str):
            await _reject(ws, "password must be a string")
            return None

        room = self.rooms.setdefault(room_name, Room())
        peer = Peer(secrets.token_hex(8), ws, room_name, role, password)

        if role == "sharer":
            if room.sharer is not None:
                await _reject(ws, f"someone is already sharing in room '{room_name}'")
                return None
            room.sharer = peer
            await _send(ws, {"type": "joined", "id": peer.id, "role": role, "waiting": False})
            # Viewers that arrived early could not be checked yet: drop wrong
            # passwords now and introduce the rest to the sharer.
            for viewer in list(room.viewers.values()):
                if _password_ok(viewer.password, password):
                    await _send(ws, {"type": "peer-joined", "peer": viewer.id})
                else:
                    room.viewers.pop(viewer.id, None)
                    await _reject(viewer.ws, "wrong room password")
        else:
            if len(room.viewers) >= self.max_viewers:
                await _reject(ws, f"room '{room_name}' is full")
                return None
            if room.sharer is not None and not _password_ok(password, room.sharer.password):
                await _reject(ws, "wrong room password")
                return None
            room.viewers[peer.id] = peer
            waiting = room.sharer is None
            await _send(ws, {"type": "joined", "id": peer.id, "role": role, "waiting": waiting})
            if room.sharer is not None:
                await _send(room.sharer.ws, {"type": "peer-joined", "peer": peer.id})

        log.info("%s %s joined room %s", role, peer.id, room_name)
        return peer

    async def _relay(self, peer: Peer, raw: str | bytes) -> None:
        msg = _parse(raw)
        if msg is None or msg.get("type") not in RELAYABLE[peer.role]:
            await _send(peer.ws, {"type": "error", "message": "unsupported message"})
            return
        room = self.rooms.get(peer.room)
        if room is None:
            return
        if peer.role == "sharer":
            target = room.viewers.get(msg.pop("to", None))
        else:
            msg.pop("to", None)
            target = room.sharer
        if target is None:
            return  # the other side left in the meantime
        msg["from"] = peer.id
        await _send(target.ws, msg)

    async def _leave(self, peer: Peer) -> None:
        room = self.rooms.get(peer.room)
        if room is None:
            return
        if room.sharer is peer:
            room.sharer = None
            for viewer in list(room.viewers.values()):
                await _send(viewer.ws, {"type": "sharer-left"})
        elif room.viewers.pop(peer.id, None) is not None and room.sharer is not None:
            await _send(room.sharer.ws, {"type": "peer-left", "peer": peer.id})
        if room.sharer is None and not room.viewers:
            self.rooms.pop(peer.room, None)
        log.info("%s %s left room %s", peer.role, peer.id, peer.room)


def _health_check(connection, request):
    # Lets load balancers / PaaS platforms probe the server over plain HTTP.
    if request.path == "/healthz":
        return connection.respond(HTTPStatus.OK, "OK\n")
    return None


async def run_server(
    host: str,
    port: int,
    *,
    max_viewers: int = 16,
    certfile: str | None = None,
    keyfile: str | None = None,
) -> None:
    ssl_context = None
    if certfile:
        ssl_context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        ssl_context.load_cert_chain(certfile, keyfile)

    server = SignalingServer(max_viewers=max_viewers)
    async with serve(
        server.handler,
        host,
        port,
        ssl=ssl_context,
        max_size=MAX_MESSAGE_BYTES,
        process_request=_health_check,
    ) as ws_server:
        scheme = "wss" if ssl_context else "ws"
        log.info("Signaling server listening on %s://%s:%d", scheme, host, port)
        await ws_server.serve_forever()
