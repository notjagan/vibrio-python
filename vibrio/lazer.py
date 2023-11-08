import atexit
import io
import platform
import socket
import stat
import subprocess
from pathlib import Path
from typing import BinaryIO, Optional

import requests
from typing_extensions import Self


class ServerStateException(Exception):
    """Exception due to attempting to induce an invalid server state transition."""


class ServerError(Exception):
    """Unknown/unexpected server-side error."""


class BeatmapNotFound(FileNotFoundError):
    """Exception caused by missing/unknown beatmap."""


def get_vibrio_path(platform: str) -> Path:
    """Determines path to server executable on a given platform."""
    if platform == "Windows":
        suffix = ".exe"
    else:
        suffix = ""

    return Path(__file__).parent.absolute() / "lib" / f"Vibrio{suffix}"


class Server:
    def __init__(self, port: int) -> None:
        self.port = port
        self.vibrio_path = get_vibrio_path(platform.system())
        if not self.vibrio_path.exists():
            raise FileNotFoundError(f'No executable found at "{self.vibrio_path}".')
        self.vibrio_path.chmod(self.vibrio_path.stat().st_mode | stat.S_IEXEC)
        self.process: Optional[subprocess.Popen] = None

    def address(self) -> str:
        return f"http://localhost:{self.port}"

    def start(self) -> None:
        """Spawns `vibrio` server executable as a subprocess."""
        if self.process is None:
            self.process = subprocess.Popen(
                [self.vibrio_path, "--urls", self.address()],
                stdout=subprocess.PIPE,
            )
            assert self.process.stdout is not None
            # block until webserver launches
            self.process.stdout.readline()
            atexit.register(self.stop)
        else:
            raise ServerStateException("Server is already running")

    def stop(self) -> None:
        """Kills server subprocess."""
        if self.process is not None:
            self.process.kill()
            self.process = None


def find_open_port() -> int:
    """Returns a port not currently in use on the system."""
    with socket.socket() as sock:
        sock.bind(("", 0))
        return sock.getsockname()[1]


class Lazer:
    def __init__(self) -> None:
        self.server = Server(find_open_port())

    def start(self) -> None:
        self.server.start()

    def stop(self) -> None:
        self.server.stop()

    def __enter__(self) -> Self:
        self.start()
        return self

    def __exit__(self, *_) -> bool:
        self.stop()
        return False

    def url(self, endpoint: str) -> str:
        """Constructs API URL for a given endpoint."""
        return f"{self.server.address()}/api/{endpoint}"

    def has_beatmap(self, beatmap_id: int) -> bool:
        """Checks if given beatmap is cached/available locally."""
        response = requests.get(self.url(f"beatmaps/{beatmap_id}/status"))
        if response.status_code == 200:
            return True
        elif response.status_code == 404:
            return False
        raise ServerError(
            f"Unexpected status code {response.status_code}; check server logs for error details"
        )

    def get_beatmap(self, beatmap_id: int) -> BinaryIO:
        """Returns a file stream for the given beatmap."""
        response = requests.get(self.url(f"beatmaps/{beatmap_id}"))
        if response.status_code == 200:
            stream = io.BytesIO()
            stream.write(response.content)
            stream.seek(0)
            return stream
        elif response.status_code == 404:
            raise BeatmapNotFound(f"No beatmap found for id {beatmap_id}")
        else:
            raise ServerError(
                f"Unexpected status code {response.status_code}; check server logs for error details"
            )
