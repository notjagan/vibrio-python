import asyncio
import platform
import stat
import subprocess
import tempfile
import time
from abc import ABC
from pathlib import Path
from typing import Any

import aiohttp
import psutil
import requests
from requests.models import Response
from typing_extensions import Self

PACKAGE_DIR = Path(__file__).absolute().parent


def get_vibrio_path(platform: str) -> Path:
    """Determines path to server executable on a given platform."""
    if platform == "Windows":
        suffix = ".exe"
    else:
        suffix = ""

    return PACKAGE_DIR / "lib" / f"Vibrio{suffix}"


class ServerBase(ABC):
    """Container class for server process and other relevant objects."""

    def __init__(self, port: int, use_logging: bool) -> None:
        self.port = port
        if use_logging:
            self.log = tempfile.NamedTemporaryFile(delete=False)
        else:
            self.log = None

        self.server_path = get_vibrio_path(platform.system())
        if not self.server_path.exists():
            raise FileNotFoundError(f'No executable found at "{self.server_path}".')
        self.server_path.chmod(self.server_path.stat().st_mode | stat.S_IEXEC)

    def address(self) -> str:
        """Constructs the base URL for the web server."""
        return f"http://localhost:{self.port}"


class BaseUrlSession(requests.Session):
    def __init__(self, base_url: str) -> None:
        super().__init__()
        self.base_url = base_url

    def request(
        self, method: str | bytes, url: str | bytes, *args: Any, **kwargs: Any
    ) -> Response:
        return super().request(method, f"{self.base_url}{url}", *args, **kwargs)


class Server(ServerBase):
    """
    Synchronous server implementation.

    WARNING: intended for internal use only. Does not guarantee variables are instantiated before use.
    """

    def __init__(self, port: int, use_logging: bool) -> None:
        super().__init__(port, use_logging)
        self.session: requests.Session
        self.process: subprocess.Popen[bytes]

    def start(self) -> None:
        """Spawns server executable as a subprocess."""
        print(f"Launching server on port {self.port}")

        if self.log is not None:
            self.process = subprocess.Popen(
                [self.server_path, "--urls", self.address()],
                stdout=self.log,
                stderr=self.log,
            )
            # block until first output
            while self.log.tell() == 0:
                time.sleep(0.1)
        else:
            self.process = subprocess.Popen(
                [self.server_path, "--urls", self.address()],
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
            )
            assert self.process.stdout is not None
            # block until first output
            self.process.stdout.readline()

        self.session = BaseUrlSession(self.address())

    @classmethod
    def create(cls, port: int, use_logging: bool) -> Self:
        """Generates instance of server class and launches executable."""
        server = cls(port, use_logging)
        server.start()
        return server

    def stop(self) -> None:
        """Cleans up server subprocess."""
        self.process.kill()
        self.session.close()
        if self.log is not None:
            print(f"Server output logged at {self.log.file.name}")
            self.log.close()
            self.log = None


class ServerAsync(ServerBase):
    """
    Asynchronous server implementation.

    WARNING: intended for internal use only. Does not guarantee variables are instantiated before use.
    """

    def __init__(self, port: int, use_logging: bool) -> None:
        super().__init__(port, use_logging)
        self.session: aiohttp.ClientSession
        self.process: asyncio.subprocess.Process

    async def start(self) -> None:
        """Spawns server executable as a subprocess."""
        print(f"Launching server on port {self.port}")

        if self.log is not None:
            self.process = await asyncio.create_subprocess_shell(
                f"{self.server_path} --urls {self.address()}",
                stdout=self.log,
                stderr=self.log,
            )
            # block until first output
            while self.log.tell() == 0:
                await asyncio.sleep(0.1)
        else:
            self.process = await asyncio.create_subprocess_shell(
                f"{self.server_path} --urls {self.address()}",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
            )
            assert self.process.stdout is not None
            # block until first output
            await self.process.stdout.readline()

        self.session = aiohttp.ClientSession(self.address())

    @classmethod
    async def create(cls, port: int, use_logging: bool) -> Self:
        """Generates instance of server class and launches executable."""
        server = cls(port, use_logging)
        await server.start()
        return server

    async def stop(self) -> None:
        """Cleans up server subprocess."""
        parent = psutil.Process(self.process.pid)
        for child in parent.children(recursive=True):
            child.terminate()
        parent.terminate()
        status = await self.process.wait()

        await self.session.close()

        if self.log is not None:
            print(f"Server output logged at {self.log.file.name}")
            self.log.close()
            self.log = None

        if status == 1:
            raise SystemError("Could not cleanly shutdown server subprocess")
