# p2pshare

Lightweight peer-to-peer screen sharing in Python. Share your screen with anyone
over the internet from the command line; viewers watch it in a native window.
No browser involved.

- **WebRTC** ([aiortc](https://github.com/aiortc/aiortc)) carries the video directly between peers, encrypted (DTLS-SRTP) and bandwidth-adaptive.
- **WebSockets** ([websockets](https://github.com/python-websockets/websockets)) power a tiny signaling server that only introduces peers to each other.
- **mss** captures the screen and **OpenCV** displays it.

```
            1. join room / exchange SDP (tiny JSON messages)
  sharer  <──────────── signaling server ────────────>  viewer(s)
     │                                                     ▲
     └──────────── 2. screen video, direct P2P ────────────┘
                 (STUN for NAT traversal, TURN as fallback)
```

One sharer per room, with any number of viewers (16 by default). The screen is
captured once and fanned out to every viewer.

## Install

Requires Python 3.10+.

```bash
pip install -e .          # or: pip install -e ".[dev]" to run the tests
```

## Quick start

```bash
# 1. somewhere both sides can reach
p2pshare server                                   # listens on ws://0.0.0.0:8765

# 2. the person sharing
p2pshare share --server ws://<server-host>:8765
#   Sharing monitor 1 in room 'k3v9x0q2mz'. Viewers can join with:
#       p2pshare view k3v9x0q2mz --server ws://<server-host>:8765

# 3. each viewer
p2pshare view k3v9x0q2mz --server ws://<server-host>:8765
```

In the viewer window: **f** toggles fullscreen, **q** / **Esc** quits.
Set `P2PSHARE_SERVER` to skip typing `--server` every time.

## Sharing over the internet

Only the signaling server needs a public address. It handles a few kilobytes
per connection and no video, so the smallest VPS or free PaaS tier is enough.

- **Docker:** `docker build -t p2pshare-signal . && docker run -p 8765:8765 p2pshare-signal`
- **PaaS** (Render, Railway, Fly.io, ...): run `python -m p2pshare server`. It honours `$PORT` and answers `GET /healthz` for health checks.
- **TLS:** use `wss://` in production so nobody can tamper with the handshake. Either put the server behind a TLS-terminating proxy (Caddy, nginx, or your PaaS), or pass `--certfile` / `--keyfile` directly.

Protect a room with `--password` on both sides. Room names are random by default, so they also act as a shared secret.

### When peers cannot connect (NAT / firewalls)

A public STUN server (Google's by default) lets most home and office networks
connect directly. Symmetric NATs, some mobile carriers, and strict corporate
firewalls need a **TURN** relay, such as a self-hosted [coturn](https://github.com/coturn/coturn)
or a hosted service:

```bash
p2pshare share --turn turn:turn.example.com:3478 --turn-user alice --turn-password s3cret
```

Either side can supply TURN, but both sides supplying it is the most reliable. The same
values can come from `P2PSHARE_TURN`, `P2PSHARE_TURN_USER`, and `P2PSHARE_TURN_PASSWORD`.
aiortc uses only one STUN server and one TURN server.

## Options

| Command | Option | Default | |
|---|---|---|---|
| `share` | `--room` | random | room name |
| | `--monitor` | `1` | monitor index, `0` = all monitors |
| | `--list-monitors` | | list monitors and exit |
| | `--fps` | `15` | capture frame rate |
| | `--max-width` | `1920` | downscale wider screens (keeps aspect ratio) |
| | `--max-bitrate` | `1500` | encoder bitrate ceiling in kbps; raise it (e.g. `4000`) for sharper text |
| `share`, `view` | `--server`, `--password`, `--stun`, `--turn`, `--turn-user`, `--turn-password` | | |
| `server` | `--host`, `--port`, `--max-viewers`, `--certfile`, `--keyfile` | `0.0.0.0`, `8765`, `16` | |

Add `-v` before the command (`p2pshare -v share ...`) for debug logs.

## Platform notes

- **macOS:** grant your terminal *Screen Recording* permission (System Settings → Privacy & Security).
- **Linux:** capture needs an X11 session; pure Wayland sessions are not supported by mss.
- The mouse cursor is not included in the capture, and audio is not shared.

## Project layout

```
p2pshare/
  cli.py        command line entry point
  signaling.py  WebSocket signaling server (rooms, passwords, SDP relay)
  client.py     signaling client
  capture.py    screen capture as a WebRTC video track
  sharer.py     one peer connection per viewer, fed from a single capture
  viewer.py     receives the stream and renders it in an OpenCV window
  rtc.py        ICE servers and encoder tuning
tests/          signaling protocol tests and a full WebRTC end-to-end test
```

```bash
pytest
```
