import atexit
import io
import platform
import socket
import stat
import subprocess
import time
from pathlib import Path
from tempfile import TemporaryFile, _TemporaryFileWrapper
from typing import BinaryIO, Optional

import requests
from typing_extensions import Self

PACKAGE_DIR = Path(__file__).absolute().parent


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

    return PACKAGE_DIR / "lib" / f"Vibrio{suffix}"


def find_open_port() -> int:
    """Returns a port not currently in use on the system."""
    with socket.socket() as sock:
        sock.bind(("", 0))
        return sock.getsockname()[1]


class LazerBase:
    def __init__(self, port: Optional[int] = None, use_logging: bool = True) -> None:
        if port is None:
            self.port = find_open_port()
        else:
            self.port = port
        self.use_logging = use_logging

        self.server_path = get_vibrio_path(platform.system())
        if not self.server_path.exists():
            raise FileNotFoundError(f'No executable found at "{self.server_path}".')
        self.server_path.chmod(self.server_path.stat().st_mode | stat.S_IEXEC)

    def address(self) -> str:
        """Constructs the base URL for the API."""
        return f"http://localhost:{self.port}"

    def url(self, endpoint: str) -> str:
        """Constructs API URL for a given endpoint."""
        return f"{self.address()}/api/{endpoint}"


class Lazer(LazerBase):
    def __init__(self, port: Optional[int] = None, use_logging: bool = True) -> None:
        super().__init__(port, use_logging)

        self.process: Optional[subprocess.Popen] = None
        self.log: Optional[_TemporaryFileWrapper[bytes]] = None

    def start(self) -> None:
        if self.process is None:
            print(f"Launching server on port {self.port}")

            if self.use_logging:
                self.log = TemporaryFile(delete=False)
                self.process = subprocess.Popen(
                    [self.server_path, "--urls", self.address()],
                    stdout=self.log,
                    stderr=self.log,
                )
                # block until first output
                while self.log.tell() == 0:
                    time.sleep(0.5)
            else:
                self.process = subprocess.Popen(
                    [self.server_path, "--urls", self.address()],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                )
                assert self.process.stdout is not None
                # block until first output
                self.process.stdout.readline()

            atexit.register(self.stop)
        else:
            raise ServerStateException("Server is already running")

    def stop(self) -> None:
        if self.process is not None:
            self.process.kill()
            self.process = None
            if self.log is not None:
                print(f"Server output logged at {self.log.file.name}")
                self.log.close()
                self.log = None

    def __enter__(self) -> Self:
        self.start()
        return self

    def __exit__(self, *_) -> bool:
        self.stop()
        return False

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
