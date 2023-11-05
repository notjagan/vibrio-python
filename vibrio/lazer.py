import atexit
import platform
import socket
import subprocess
from pathlib import Path
from typing import Optional

import requests

try:
    from typing import Self
except ImportError:
    from typing_extensions import Self


class UnsupportedPlatformError(ValueError):
    pass


class ServerStateException(Exception):
    pass


class ServerError(Exception):
    pass


def get_vibrio_path(plat: str, arch: str) -> Path:
    """Determines path to server executable on a given platform and architecture."""
    suffix = ""
    if plat == "Windows":
        if arch == "x86_64" or arch == "AMD64":
            suffix = "win-x64.exe"
        elif arch == "i386":
            suffix = "win-x86.exe"
    elif plat == "Linux":
        if arch == "x86_64":
            suffix = "linux-x64"
        elif arch == "arm64":
            suffix = "linux-arm64"
    elif plat == "Darwin":
        if arch == "x86_64":
            suffix = "osx-x64"
        elif arch == "arm64":
            suffix = "osx-arm64"
    else:
        raise UnsupportedPlatformError(
            f'Platform "{plat}" with architecture "{arch}" is not supported'
        )

    return Path(__file__).parent.absolute() / "lib" / f"vibrio.{suffix}"


class Server:
    def __init__(self, port: int) -> None:
        self.port = port
        self.vibrio_path = get_vibrio_path(platform.system(), platform.machine())
        if not self.vibrio_path.exists():
            raise FileNotFoundError(f'No executable found at "{self.vibrio_path}".')
        self.process: Optional[subprocess.Popen] = None

    def address(self) -> str:
        return f"http://localhost:{self.port}"

    def start(self) -> None:
        """Spawns `vibrio` server executable as a subprocess."""
        if self.process is None:
            self.process = subprocess.Popen(
                [self.vibrio_path, "--urls", self.address()],
                stdout=subprocess.DEVNULL,
            )
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
        sock.bind(('', 0))
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
        response = requests.get(self.url(f"beatmaps/{beatmap_id}"))
        if response.status_code == 200:
            return True
        elif response.status_code == 404:
            return False
        raise ServerError(
            f"Unexpected status code {response.status_code}; check server logs for error details"
        )
