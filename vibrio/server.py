import asyncio
import functools
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
        """Constructs the base URL for the API."""
        return f"http://localhost:{self.port}"

    def url(self, endpoint: str) -> str:
        """Constructs API URL for a given endpoint."""
        return f"{self.address()}/api/{endpoint}"


class Server(ServerBase):
    """
    Synchronous server implementation.

    WARNING: intended for internal use only. Does not guarantee variables are instantiated before use.
    """

    def __init__(self, port: int, use_logging: bool, session: requests.Session) -> None:
        super().__init__(port, use_logging)
        self.session = session
        self.process: subprocess.Popen[bytes]

        @functools.wraps(self.session.get)
        def get(endpoint: str, *args: Any, **kwargs: Any) -> requests.Response:
            return self.session.get(self.url(endpoint), *args, **kwargs)

        @functools.wraps(self.session.post)
        def post(endpoint: str, *args: Any, **kwargs: Any) -> requests.Response:
            return self.session.post(self.url(endpoint), *args, **kwargs)

        self.get = get
        self.post = post

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

    @classmethod
    def create(cls, port: int, use_logging: bool) -> Self:
        """Generates instance of server class and launches executable."""
        server = cls(port, use_logging, requests.Session())
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

    def __init__(
        self, port: int, use_logging: bool, session: aiohttp.ClientSession
    ) -> None:
        super().__init__(port, use_logging)
        self.session = session
        self.process: asyncio.subprocess.Process

        @functools.wraps(self.session.get)
        def get(
            endpoint: str, *args: Any, **kwargs: Any
        ) -> aiohttp.client._RequestContextManager:
            return self.session.get(self.url(endpoint), *args, **kwargs)

        @functools.wraps(self.session.post)
        def post(
            endpoint: str, *args: Any, **kwargs: Any
        ) -> aiohttp.client._RequestContextManager:
            return self.session.post(self.url(endpoint), *args, **kwargs)

        self.get = get
        self.post = post

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

    @classmethod
    async def create(cls, port: int, use_logging: bool) -> Self:
        """Generates instance of server class and launches executable."""
        server = cls(port, use_logging, aiohttp.ClientSession())
        await server.start()
        return server

    async def stop(self) -> None:
        """Cleans up server subprocess."""
        parent = psutil.Process(self.process.pid)
        for child in parent.children(recursive=True):
            child.terminate()
        parent.terminate()
        await self.process.wait()

        await self.session.close()

        if self.log is not None:
            print(f"Server output logged at {self.log.file.name}")
            self.log.close()
            self.log = None
