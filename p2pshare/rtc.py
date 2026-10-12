"""WebRTC configuration shared by sharer and viewer."""

from __future__ import annotations

from aioice import ice
from aiortc import RTCConfiguration, RTCIceServer
from aiortc.codecs import h264, vpx

# aiortc only uses the first STUN and the first TURN server it is given.
DEFAULT_STUN = "stun:stun.l.google.com:19302"


def build_rtc_config(
    stun: str | None = None,
    turn: str | None = None,
    turn_user: str | None = None,
    turn_password: str | None = None,
) -> RTCConfiguration:
    servers = [RTCIceServer(urls=stun or DEFAULT_STUN)]
    if turn:
        servers.append(RTCIceServer(urls=turn, username=turn_user, credential=turn_password))
    return RTCConfiguration(iceServers=servers)


def set_gathering_timeout(seconds: float) -> None:
    """Cap how long aioice waits for STUN/TURN replies (5 s by default).

    aioice queries STUN from every local adapter and waits for all of them, so
    virtual adapters (Hyper-V, WSL, VPNs, Docker) that can never get a reply
    stall each connection for the full timeout, on both sides in turn.
    """
    get_candidates = ice.Connection.get_component_candidates
    if get_candidates.__defaults__ and len(get_candidates.__defaults__) == 1:
        get_candidates.__defaults__ = (seconds,)


def set_max_bitrate(bps: int) -> None:
    """Raise aiortc's encoder bitrate ceiling (1.5 Mbps for VP8 by default).

    Screen content is mostly text, which turns to mush at low bitrates. The
    encoder still adapts downwards based on the viewer's bandwidth estimate.
    """
    for codec in (vpx, h264):
        codec.MAX_BITRATE = bps
        codec.DEFAULT_BITRATE = min(codec.DEFAULT_BITRATE, bps)
        codec.MIN_BITRATE = min(codec.MIN_BITRATE, bps)
