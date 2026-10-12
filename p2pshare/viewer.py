"""The viewing side: receives the shared screen and shows it in a native window."""

from __future__ import annotations

import asyncio
import logging

import cv2
import numpy as np
from aiortc import RTCConfiguration, RTCPeerConnection, RTCSessionDescription
from aiortc.mediastreams import MediaStreamError, MediaStreamTrack
from av import VideoFrame

from .client import SignalingClient

log = logging.getLogger(__name__)

# Initial window size limit; the window can be resized or made fullscreen.
MAX_WINDOW_WIDTH, MAX_WINDOW_HEIGHT = 1600, 900


class Window:
    """An OpenCV window. Must be used from the main thread, where asyncio runs."""

    def __init__(self, title: str) -> None:
        self.title = title
        self._size: tuple[int, int] | None = None
        self._fullscreen = False
        cv2.namedWindow(title, cv2.WINDOW_NORMAL | cv2.WINDOW_KEEPRATIO)
        self.show_message("Waiting for the sharer...")

    def show_frame(self, frame: VideoFrame) -> None:
        if self._size != (frame.width, frame.height):
            self._size = (frame.width, frame.height)
            scale = min(1.0, MAX_WINDOW_WIDTH / frame.width, MAX_WINDOW_HEIGHT / frame.height)
            cv2.resizeWindow(self.title, int(frame.width * scale), int(frame.height * scale))
        cv2.imshow(self.title, frame.to_ndarray(format="bgr24"))

    def show_message(self, text: str) -> None:
        self._size = None
        image = np.full((360, 640, 3), 32, np.uint8)
        cv2.putText(image, text, (24, 190), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (230, 230, 230), 2, cv2.LINE_AA)
        cv2.resizeWindow(self.title, 640, 360)
        cv2.imshow(self.title, image)

    def poll(self) -> bool:
        """Process window events. Returns False once the user wants to quit."""
        key = cv2.waitKey(1) & 0xFF
        if key in (ord("q"), 27):  # q or Esc
            return False
        if key == ord("f"):
            self._fullscreen = not self._fullscreen
            mode = cv2.WINDOW_FULLSCREEN if self._fullscreen else cv2.WINDOW_NORMAL
            cv2.setWindowProperty(self.title, cv2.WND_PROP_FULLSCREEN, mode)
        return cv2.getWindowProperty(self.title, cv2.WND_PROP_VISIBLE) >= 1

    def close(self) -> None:
        cv2.destroyAllWindows()


class Viewer:
    def __init__(
        self,
        signaling_url: str,
        room: str,
        rtc_config: RTCConfiguration,
        password: str | None = None,
        window: Window | None = None,
    ) -> None:
        self.signaling_url = signaling_url
        self.room = room
        self.rtc_config = rtc_config
        self.password = password
        self.window = window
        self._pc: RTCPeerConnection | None = None
        self._render_task: asyncio.Task | None = None

    async def run(self) -> None:
        async with SignalingClient(self.signaling_url) as sig:
            joined = await sig.join(self.room, "viewer", self.password)
            if joined.get("waiting"):
                log.info("Joined room '%s'; nobody is sharing yet, waiting...", self.room)
            else:
                log.info("Joined room '%s', connecting to the sharer...", self.room)
            if self.window is None:
                self.window = Window(f"p2pshare - {self.room}  (f: fullscreen, q: quit)")

            tasks = {asyncio.create_task(self._signaling_loop(sig)), asyncio.create_task(self._ui_loop())}
            try:
                done, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
                for task in done:
                    task.result()  # re-raise errors such as a lost signaling connection
            finally:
                for task in tasks:
                    task.cancel()
                await asyncio.gather(*tasks, return_exceptions=True)
                await self._close_connection()
                self.window.close()

    async def _ui_loop(self) -> None:
        while self.window.poll():
            await asyncio.sleep(0.01)

    async def _signaling_loop(self, sig: SignalingClient) -> None:
        async for msg in sig:
            kind = msg.get("type")
            if kind == "offer":
                await self._answer(sig, msg)
            elif kind == "sharer-left":
                log.info("The sharer left; waiting for them to come back...")
                await self._close_connection()
                self.window.show_message("Sharer left. Waiting...")

    async def _answer(self, sig: SignalingClient, offer: dict) -> None:
        await self._close_connection()
        pc = self._pc = RTCPeerConnection(self.rtc_config)

        @pc.on("track")
        def on_track(track: MediaStreamTrack) -> None:
            if track.kind == "video":
                self._render_task = asyncio.create_task(self._render(track))

        @pc.on("connectionstatechange")
        async def on_state_change() -> None:
            log.info("Connection to sharer: %s", pc.connectionState)
            if pc.connectionState == "failed" and pc is self._pc:
                self.window.show_message("Connection failed (try a TURN server)")

        await pc.setRemoteDescription(RTCSessionDescription(sdp=offer["sdp"], type="offer"))
        await pc.setLocalDescription(await pc.createAnswer())
        await sig.send({"type": "answer", "to": offer["from"], "sdp": pc.localDescription.sdp})

    async def _render(self, track: MediaStreamTrack) -> None:
        try:
            while True:
                self.window.show_frame(await track.recv())
        except MediaStreamError:
            pass

    async def _close_connection(self) -> None:
        if self._render_task is not None:
            self._render_task.cancel()
            self._render_task = None
        if self._pc is not None:
            pc, self._pc = self._pc, None
            await pc.close()
