import asyncio

import pytest
from websockets.asyncio.server import serve

from p2pshare.client import SignalingClient, SignalingError
from p2pshare.signaling import SignalingServer


@pytest.fixture
async def server_url():
    server = SignalingServer(max_viewers=2)
    async with serve(server.handler, "127.0.0.1", 0) as ws_server:
        port = ws_server.sockets[0].getsockname()[1]
        yield f"ws://127.0.0.1:{port}"


async def recv(client: SignalingClient) -> dict:
    return await asyncio.wait_for(client.recv(), 2)


async def test_offer_and_answer_are_relayed(server_url):
    async with SignalingClient(server_url) as sharer, SignalingClient(server_url) as viewer:
        sharer_id = (await sharer.join("room1", "sharer"))["id"]
        joined = await viewer.join("room1", "viewer")
        assert joined["waiting"] is False

        assert await recv(sharer) == {"type": "peer-joined", "peer": joined["id"]}
        await sharer.send({"type": "offer", "to": joined["id"], "sdp": "offer-sdp"})
        assert await recv(viewer) == {"type": "offer", "sdp": "offer-sdp", "from": sharer_id}
        await viewer.send({"type": "answer", "sdp": "answer-sdp"})
        assert await recv(sharer) == {"type": "answer", "sdp": "answer-sdp", "from": joined["id"]}


async def test_early_viewer_is_introduced_when_sharer_arrives(server_url):
    async with SignalingClient(server_url) as viewer, SignalingClient(server_url) as sharer:
        joined = await viewer.join("room1", "viewer")
        assert joined["waiting"] is True
        await sharer.join("room1", "sharer")
        assert await recv(sharer) == {"type": "peer-joined", "peer": joined["id"]}


async def test_departures_are_announced(server_url):
    async with SignalingClient(server_url) as sharer:
        await sharer.join("room1", "sharer")
        async with SignalingClient(server_url) as viewer:
            viewer_id = (await viewer.join("room1", "viewer"))["id"]
            await recv(sharer)
        assert await recv(sharer) == {"type": "peer-left", "peer": viewer_id}

        async with SignalingClient(server_url) as viewer:
            await viewer.join("room1", "viewer")
            await recv(sharer)
            await sharer._ws.close()
            assert await recv(viewer) == {"type": "sharer-left"}


async def test_second_sharer_is_rejected(server_url):
    async with SignalingClient(server_url) as first, SignalingClient(server_url) as second:
        await first.join("room1", "sharer")
        with pytest.raises(SignalingError, match="already sharing"):
            await second.join("room1", "sharer")


async def test_wrong_password_is_rejected(server_url):
    async with SignalingClient(server_url) as sharer, SignalingClient(server_url) as viewer:
        await sharer.join("room1", "sharer", "secret")
        with pytest.raises(SignalingError, match="wrong room password"):
            await viewer.join("room1", "viewer", "guess")


async def test_early_viewer_with_wrong_password_is_dropped(server_url):
    async with SignalingClient(server_url) as viewer, SignalingClient(server_url) as sharer:
        await viewer.join("room1", "viewer", "guess")
        await sharer.join("room1", "sharer", "secret")
        with pytest.raises(SignalingError, match="wrong room password"):
            await recv(viewer)


async def test_room_full(server_url):
    async with (
        SignalingClient(server_url) as v1,
        SignalingClient(server_url) as v2,
        SignalingClient(server_url) as v3,
    ):
        await v1.join("room1", "viewer")
        await v2.join("room1", "viewer")
        with pytest.raises(SignalingError, match="full"):
            await v3.join("room1", "viewer")


async def test_viewer_cannot_send_offers(server_url):
    async with SignalingClient(server_url) as viewer:
        await viewer.join("room1", "viewer")
        await viewer.send({"type": "offer", "sdp": "x"})
        with pytest.raises(SignalingError, match="unsupported"):
            await recv(viewer)


@pytest.mark.parametrize("room", ["", "has space", "x" * 65, "../etc"])
async def test_invalid_room_names(server_url, room):
    async with SignalingClient(server_url) as client:
        with pytest.raises(SignalingError, match="room must be"):
            await client.join(room, "viewer")
