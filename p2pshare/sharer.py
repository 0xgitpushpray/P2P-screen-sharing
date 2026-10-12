"""The sharing side: one WebRTC connection per viewer, all fed from one source."""

from __future__ import annotations

import asyncio
import logging

from aiortc import MediaStreamTrack, RTCConfiguration, RTCPeerConnection, RTCSessionDescription
from aiortc.contrib.media import MediaRelay

from .client import SignalingClient

log = logging.getLogger(__name__)


class Sharer:
    def __init__(
        self,
        signaling_url: str,
        room: str,
        source: MediaStreamTrack,
        rtc_config: RTCConfiguration,
        password: str | None = None,
    ) -> None:
        self.signaling_url = signaling_url
        self.room = room
        self.source = source
        self.rtc_config = rtc_config
        self.password = password
        # The relay lets every viewer consume the same capture without each
        # connection triggering its own screen grab.
        self._relay = MediaRelay()
        self._pcs: dict[str, RTCPeerConnection] = {}
        self._tasks: set[asyncio.Task] = set()

    async def run(self) -> None:
        async with SignalingClient(self.signaling_url) as sig:
            await sig.join(self.room, "sharer", self.password)
            log.info("Joined room '%s', waiting for viewers", self.room)
            try:
                async for msg in sig:
                    await self._handle(sig, msg)
            finally:
                await self.close()

    async def _handle(self, sig: SignalingClient, msg: dict) -> None:
        kind = msg.get("type")
        if kind == "peer-joined":
            # Creating an offer waits for ICE gathering; don't stall other messages.
            task = asyncio.create_task(self._offer(sig, msg["peer"]))
            self._tasks.add(task)
            task.add_done_callback(self._tasks.discard)
        elif kind == "answer":
            pc = self._pcs.get(msg["from"])
            if pc is None or pc.signalingState != "have-local-offer":
                return
            try:
                await pc.setRemoteDescription(RTCSessionDescription(sdp=msg["sdp"], type="answer"))
            except Exception:
                # A broken or malicious viewer must not take the whole share down.
                log.warning("Invalid answer from viewer %s", msg["from"], exc_info=True)
                await self._drop(msg["from"])
        elif kind == "peer-left":
            await self._drop(msg["peer"])

    async def _offer(self, sig: SignalingClient, peer_id: str) -> None:
        pc = RTCPeerConnection(self.rtc_config)
        self._pcs[peer_id] = pc

        @pc.on("connectionstatechange")
        async def on_state_change() -> None:
            log.info("Viewer %s: %s", peer_id, pc.connectionState)
            if pc.connectionState == "connected":
                log.info("%d viewer(s) connected", self.viewer_count)
            elif pc.connectionState == "failed":
                await self._drop(peer_id)

        try:
            pc.addTrack(self._relay.subscribe(self.source, buffered=False))
            await pc.setLocalDescription(await pc.createOffer())
            if self._pcs.get(peer_id) is pc:  # the viewer may have left meanwhile
                await sig.send({"type": "offer", "to": peer_id, "sdp": pc.localDescription.sdp})
        except Exception:
            log.exception("Could not create offer for viewer %s", peer_id)
            await self._drop(peer_id)

    async def _drop(self, peer_id: str) -> None:
        pc = self._pcs.pop(peer_id, None)
        if pc is not None:
            await pc.close()
            log.info("Viewer %s disconnected, %d remaining", peer_id, self.viewer_count)

    @property
    def viewer_count(self) -> int:
        return sum(pc.connectionState == "connected" for pc in self._pcs.values())

    async def close(self) -> None:
        for task in list(self._tasks):
            task.cancel()
        pcs, self._pcs = list(self._pcs.values()), {}
        await asyncio.gather(*(pc.close() for pc in pcs), return_exceptions=True)
