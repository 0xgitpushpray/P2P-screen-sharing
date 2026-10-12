"""Command line entry point: `p2pshare server | share | view`."""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import secrets
import string
import sys

from websockets.exceptions import ConnectionClosed, InvalidHandshake, InvalidURI

from .client import SignalingError

DEFAULT_SERVER = os.environ.get("P2PSHARE_SERVER", "ws://localhost:8765")


def _random_room() -> str:
    alphabet = string.ascii_lowercase + string.digits
    return "".join(secrets.choice(alphabet) for _ in range(10))


def _rtc_config(args: argparse.Namespace):
    from .rtc import build_rtc_config, set_gathering_timeout

    if not args.turn:
        # A public STUN server answers in well under a second; TURN allocation
        # (possibly over TLS) keeps aioice's more generous default.
        set_gathering_timeout(2)
    return build_rtc_config(args.stun, args.turn, args.turn_user, args.turn_password)


async def _serve(args: argparse.Namespace) -> None:
    from .signaling import run_server

    await run_server(
        args.host, args.port, max_viewers=args.max_viewers, certfile=args.certfile, keyfile=args.keyfile
    )


async def _share(args: argparse.Namespace) -> None:
    from .capture import ScreenTrack, list_monitors
    from .rtc import set_max_bitrate
    from .sharer import Sharer

    if args.list_monitors:
        for index, mon in enumerate(list_monitors()):
            label = "all monitors combined" if index == 0 else f"monitor {index}"
            print(f"{index}: {mon['width']}x{mon['height']} at ({mon['left']}, {mon['top']})  {label}")
        return

    if args.max_bitrate:
        set_max_bitrate(args.max_bitrate * 1000)
    room = args.room or _random_room()
    source = ScreenTrack(monitor=args.monitor, fps=args.fps, max_width=args.max_width)

    command = f"p2pshare view {room} --server {args.server}"
    if args.password:
        command += " --password <password>"
    print(f"Sharing monitor {args.monitor} in room '{room}'. Viewers can join with:\n\n    {command}\n")
    print("Press Ctrl+C to stop sharing.", flush=True)

    try:
        await Sharer(args.server, room, source, _rtc_config(args), args.password).run()
    finally:
        source.stop()


async def _view(args: argparse.Namespace) -> None:
    from .viewer import Viewer

    await Viewer(args.server, args.room, _rtc_config(args), args.password).run()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="p2pshare", description="Peer-to-peer screen sharing over WebRTC.")
    parser.add_argument("-v", "--verbose", action="store_true", help="show debug logging")
    commands = parser.add_subparsers(dest="command", required=True)

    server = commands.add_parser("server", help="run the signaling server")
    server.add_argument("--host", default="0.0.0.0")
    server.add_argument("--port", type=int, default=int(os.environ.get("PORT", 8765)))
    server.add_argument("--max-viewers", type=int, default=16, help="viewers allowed per room")
    server.add_argument("--certfile", help="TLS certificate, to serve wss:// directly")
    server.add_argument("--keyfile", help="TLS private key for --certfile")
    server.set_defaults(func=_serve)

    peer = argparse.ArgumentParser(add_help=False)
    peer.add_argument("--server", default=DEFAULT_SERVER, help=f"signaling server URL (default: {DEFAULT_SERVER})")
    peer.add_argument("--password", help="room password")
    peer.add_argument("--stun", help="STUN server URL (default: Google's public STUN)")
    peer.add_argument("--turn", default=os.environ.get("P2PSHARE_TURN"), help="TURN server URL, e.g. turn:host:3478")
    peer.add_argument("--turn-user", default=os.environ.get("P2PSHARE_TURN_USER"))
    peer.add_argument("--turn-password", default=os.environ.get("P2PSHARE_TURN_PASSWORD"))

    share = commands.add_parser("share", parents=[peer], help="share your screen")
    share.add_argument("--room", help="room name (default: random)")
    share.add_argument("--monitor", type=int, default=1, help="monitor to share, 0 = all (default: 1)")
    share.add_argument("--list-monitors", action="store_true", help="list monitors and exit")
    share.add_argument("--fps", type=float, default=15, help="frames per second (default: 15)")
    share.add_argument("--max-width", type=int, default=1920, help="downscale wider screens (default: 1920)")
    share.add_argument("--max-bitrate", type=int, help="encoder bitrate ceiling in kbps (aiortc default: 1500)")
    share.set_defaults(func=_share)

    view = commands.add_parser("view", parents=[peer], help="watch a shared screen")
    view.add_argument("room", help="room name printed by the sharer")
    view.set_defaults(func=_view)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)-7s %(message)s",
        datefmt="%H:%M:%S",
    )
    if not args.verbose:
        # These libraries log every ICE check / socket event at INFO level.
        for name in ("aioice", "aiortc", "websockets"):
            logging.getLogger(name).setLevel(logging.WARNING)

    try:
        asyncio.run(args.func(args))
    except KeyboardInterrupt:
        pass
    except SignalingError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    except (OSError, InvalidHandshake, InvalidURI) as exc:
        print(f"error: cannot reach signaling server {args.server}: {exc}", file=sys.stderr)
        return 1
    except ConnectionClosed:
        print("error: lost connection to the signaling server", file=sys.stderr)
        return 1
    return 0
