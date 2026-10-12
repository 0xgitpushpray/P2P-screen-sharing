import pytest

from p2pshare import __version__
from p2pshare.cli import _random_room, build_parser, main
from p2pshare.signaling import ROOM_RE


def test_random_rooms_are_valid_and_unique():
    rooms = {_random_room() for _ in range(100)}
    assert len(rooms) == 100
    # Must also be safe to paste as a CLI argument (never starts with '-').
    assert all(ROOM_RE.match(room) and room[0].isalnum() for room in rooms)


def test_version(capsys):
    with pytest.raises(SystemExit) as exit_info:
        main(["--version"])
    assert exit_info.value.code == 0
    assert __version__ in capsys.readouterr().out


@pytest.mark.parametrize(
    "argv",
    [
        ["share", "--fps", "0"],
        ["share", "--max-width", "-5"],
        ["share", "--max-bitrate", "abc"],
        ["server", "--max-viewers", "0"],
        ["view"],
    ],
)
def test_invalid_arguments_are_rejected(argv):
    with pytest.raises(SystemExit) as exit_info:
        build_parser().parse_args(argv)
    assert exit_info.value.code == 2


def test_password_from_environment(monkeypatch):
    monkeypatch.setenv("P2PSHARE_PASSWORD", "from-env")
    assert build_parser().parse_args(["view", "room1"]).password == "from-env"
