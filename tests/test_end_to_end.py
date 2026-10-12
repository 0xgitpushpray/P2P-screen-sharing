"""Sharer -> signaling server -> viewer over real WebRTC on localhost."""

import asyncio

import numpy as np
import pytest
from aiortc import RTCConfiguration, VideoStreamTrack
from av import VideoFrame
from websockets.asyncio.server import serve

from p2pshare.sharer import Sharer
from p2pshare.signaling import SignalingServer
from p2pshare.viewer import Viewer

# No STUN: host candidates are enough on localhost and the test stays offline.
LOCAL_ONLY = RTCConfiguration(iceServers=[])


class ColorTrack(VideoStreamTrack):
    async def recv(self):
        pts, time_base = await self.next_timestamp()
        frame = VideoFrame.from_ndarray(np.full((240, 320, 3), (0, 0, 255), np.uint8), format="bgr24")
        frame.pts, frame.time_base = pts, time_base
        return frame


class FakeWindow:
    def __init__(self):
        self.frames = []
        self.got_frames = asyncio.Event()
        self.open = True

    def show_frame(self, frame):
        self.frames.append(frame.to_ndarray(format="bgr24"))
        if len(self.frames) >= 3:
            self.got_frames.set()

    def show_message(self, text):
        pass

    def poll(self):
        return self.open

    def close(self):
        self.open = False


@pytest.mark.parametrize("viewers", [1, 2])
async def test_viewers_receive_the_shared_video(viewers):
    async with serve(SignalingServer().handler, "127.0.0.1", 0) as ws_server:
        url = f"ws://127.0.0.1:{ws_server.sockets[0].getsockname()[1]}"
        source = ColorTrack()
        sharer = Sharer(url, "room1", source, LOCAL_ONLY, password="pw")
        windows = [FakeWindow() for _ in range(viewers)]
        tasks = [asyncio.create_task(sharer.run())]
        tasks += [
            asyncio.create_task(Viewer(url, "room1", LOCAL_ONLY, password="pw", window=w).run())
            for w in windows
        ]
        try:
            await asyncio.wait_for(asyncio.gather(*(w.got_frames.wait() for w in windows)), 20)
        finally:
            for window in windows:
                window.open = False
            await asyncio.wait_for(asyncio.gather(*tasks[1:]), 10)
            tasks[0].cancel()
            await asyncio.gather(tasks[0], return_exceptions=True)
            source.stop()

    for window in windows:
        frame = window.frames[-1]
        assert frame.shape == (240, 320, 3)
        # Mostly red after lossy VP8 round trip (BGR order).
        b, g, r = frame.reshape(-1, 3).mean(axis=0)
        assert r > 200 and g < 50 and b < 50
