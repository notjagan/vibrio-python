from __future__ import annotations

import asyncio
import atexit
import io
import platform
import signal
import socket
import stat
import subprocess
import tempfile
import time
import urllib.parse
from abc import ABC
from pathlib import Path
from typing import Any, BinaryIO, Optional, TextIO

import aiohttp
import psutil
import requests
from typing_extensions import Self

from vibrio.types import OsuDifficultyAttributes, OsuMod

PACKAGE_DIR = Path(__file__).absolute().parent


class ServerStateError(Exception):
    """Exception due to attempting to induce an invalid server state transition."""


class ServerError(Exception):
    """Unknown/unexpected server-side error."""


class BeatmapNotFound(FileNotFoundError):
    """Exception caused by missing/unknown beatmap."""


def find_open_port() -> int:
    """Returns a port not currently in use on the system."""
    with socket.socket() as sock:
        sock.bind(("", 0))
        return sock.getsockname()[1]


def get_vibrio_path(platform: str) -> Path:
    """Determines path to server executable on a given platform."""
    if platform == "Windows":
        suffix = ".exe"
    else:
        suffix = ""

    return PACKAGE_DIR / "lib" / f"Vibrio{suffix}"


class LazerBase(ABC):
    """Shared functionality for lazer wrappers."""

    STARTUP_DELAY = 0.05  # Amount of time (seconds) between requests during startup

    def __init__(self, port: Optional[int] = None, use_logging: bool = True) -> None:
        if port is None:
            self.port = find_open_port()
        else:
            self.port = port
        self.use_logging = use_logging
        self.running = False

        self.server_path = get_vibrio_path(platform.system())
        if not self.server_path.exists():
            raise FileNotFoundError(f'No executable found at "{self.server_path}".')
        self.server_path.chmod(self.server_path.stat().st_mode | stat.S_IEXEC)

        self.log: Optional[tempfile._TemporaryFileWrapper[bytes]] = None

    def address(self) -> str:
        """Constructs the base URL for the web server."""
        return f"http://localhost:{self.port}"

    def _start(self) -> None:
        if self.running:
            raise ServerStateError("Server is already running")

        self.running = True

        if self.use_logging:
            self.log = tempfile.NamedTemporaryFile(delete=False)

    def _stop(self) -> None:
        if self.log is not None:
            print(f"Server output logged at {self.log.file.name}")
            self.log.close()
            self.log = None


class BaseUrlSession(requests.Session):
    def __init__(self, base_url: str) -> None:
        super().__init__()
        self.base_url = base_url

    def request(
        self, method: str | bytes, url: str | bytes, *args: Any, **kwargs: Any
    ) -> requests.Response:
        full_url = urllib.parse.urljoin(self.base_url, str(url))
        return super().request(method, full_url, *args, **kwargs)


class Lazer(LazerBase):
    """Synchronous implementation for interfacing with osu!lazer functionality."""

    def __init__(self, port: int | None = None, use_logging: bool = True) -> None:
        super().__init__(port, use_logging)

        self.session = None
        self.process = None

    @property
    def session(self) -> BaseUrlSession:
        if self._session is None:
            raise ServerStateError("Session has not been initialized")
        return self._session

    @session.setter
    def session(self, value: Optional[BaseUrlSession]) -> None:
        self._session = value

    @property
    def process(self) -> subprocess.Popen[bytes]:
        if self._process is None:
            raise ServerStateError("Process has not been initialized")
        return self._process

    @process.setter
    def process(self, value: Optional[subprocess.Popen[bytes]]) -> None:
        self._process = value

    def start(self) -> None:
        """Launches server executable."""
        self._start()

        if self.log is not None:
            out = self.log
        else:
            out = subprocess.DEVNULL

        self.process = subprocess.Popen(
            [self.server_path, "--urls", self.address()],
            stdout=out,
            stderr=out,
        )

        self.session = BaseUrlSession(self.address())

        # block until webserver has launched
        while True:
            try:
                with self.session.get("/api/status") as response:
                    if response.status_code == 200:
                        break
            except (ConnectionError, IOError):
                pass
            finally:
                time.sleep(self.STARTUP_DELAY)

        atexit.register(self.stop)

    def stop(self) -> None:
        """Cleans up server executable."""
        self._stop()

        try:
            parent = psutil.Process(self.process.pid)
            for child in parent.children(recursive=True):
                child.terminate()
            parent.terminate()
            status = self.process.wait()
            self.process = None

            if status != 0 and status != signal.SIGTERM:
                raise SystemError(
                    f"Could not cleanly shutdown server subprocess; received return code {status}"
                )

            self.session.close()
            self.session = None

        except ServerStateError:
            pass

    def __enter__(self) -> Self:
        self.start()
        return self

    def __exit__(self, *_) -> bool:
        self.stop()
        return False

    def has_beatmap(self, beatmap_id: int) -> bool:
        """Checks if given beatmap is cached/available locally."""
        with self.session.get(f"/api/beatmaps/{beatmap_id}/status") as response:
            if response.status_code == 200:
                return True
            elif response.status_code == 404:
                return False
            raise ServerError(
                f"Unexpected status code {response.status_code}; check server logs for error details"
            )

    def get_beatmap(self, beatmap_id: int) -> BinaryIO:
        """Returns a file stream for the given beatmap."""
        with self.session.get(f"/api/beatmaps/{beatmap_id}") as response:
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

    def clear_cache(self) -> None:
        """Clears beatmap cache (if applicable)."""
        with self.session.delete("/api/beatmaps/cache") as response:
            if response.status_code != 200:
                raise ServerError(
                    f"Unexpected status code {response.status_code}; check server logs for error details"
                )

    def calculate_difficulty(
        self,
        mods: list[OsuMod],
        beatmap_id: Optional[int] = None,
        beatmap: Optional[TextIO] = None,
    ) -> OsuDifficultyAttributes:
        params = {"mods": [mod.value for mod in mods]}

        if beatmap_id is not None:
            if beatmap is not None:
                raise ValueError(
                    "Exactly one of `beatmap_id` and `beatmap_data` should be set"
                )

            with self.session.get(
                f"/api/difficulty/{beatmap_id}", params=params
            ) as response:
                print(response.text)
                if response.status_code == 200:
                    return OsuDifficultyAttributes.from_json(response.json())
                elif response.status_code == 404:
                    raise BeatmapNotFound(f"No beatmap found for id {beatmap_id}")
                else:
                    raise ServerError(
                        f"Unexpected status code {response.status_code}; check server logs for error details"
                    )

        elif beatmap is not None:
            with self.session.post(
                "/api/difficulty", params=params, files={"beatmap": beatmap}
            ) as response:
                if response.status_code == 200:
                    return OsuDifficultyAttributes.from_json(response.json())
                else:
                    raise ServerError(
                        f"Unexpected status code {response.status_code}; check server logs for error details"
                    )

        else:
            raise ValueError(
                "Exactly one of `beatmap_id` and `beatmap_data` should be set"
            )


class LazerAsync(LazerBase):
    """Asynchronous implementation for interfacing with osu!lazer functionality."""

    def __init__(self, port: int | None = None, use_logging: bool = True) -> None:
        super().__init__(port, use_logging)

        self.session = None
        self.process = None

    @property
    def session(self) -> aiohttp.ClientSession:
        if self._session is None:
            raise ServerStateError("Session has not been initialized")
        return self._session

    @session.setter
    def session(self, value: Optional[aiohttp.ClientSession]) -> None:
        self._session = value

    @property
    def process(self) -> asyncio.subprocess.Process:
        if self._process is None:
            raise ServerStateError("Process has not been initialized")
        return self._process

    @process.setter
    def process(self, value: Optional[asyncio.subprocess.Process]) -> None:
        self._process = value

    async def start(self) -> None:
        """Launches server executable."""
        self._start()

        if self.log is not None:
            out = self.log
        else:
            out = subprocess.DEVNULL

        self.process = await asyncio.create_subprocess_shell(
            f"{self.server_path} --urls {self.address()}",
            stdout=out,
            stderr=out,
        )

        self.session = aiohttp.ClientSession(self.address())

        # block until webserver has launched
        while True:
            try:
                async with self.session.get("/api/status") as response:
                    if response.status == 200:
                        break
            except (ConnectionError, aiohttp.ClientConnectionError):
                pass
            finally:
                await asyncio.sleep(self.STARTUP_DELAY)

        atexit.register(lambda: asyncio.run(self.stop()))

    async def stop(self) -> None:
        """Cleans up server executable."""
        self._stop()

        try:
            parent = psutil.Process(self.process.pid)
            for child in parent.children(recursive=True):
                child.terminate()
            parent.terminate()
            status = await self.process.wait()
            self.process = None

            await self.session.close()
            self.session = None

            if status != 0 and status != signal.SIGTERM:
                raise SystemError(
                    f"Could not cleanly shutdown server subprocess; received return code {status}"
                )

        except ServerStateError:
            pass

    async def __aenter__(self) -> Self:
        await self.start()
        return self

    async def __aexit__(self, *_) -> bool:
        await self.stop()
        return False

    async def has_beatmap(self, beatmap_id: int) -> bool:
        """Checks if given beatmap is cached/available locally."""
        async with self.session.get(f"/api/beatmaps/{beatmap_id}/status") as response:
            if response.status == 200:
                return True
            elif response.status == 404:
                return False
            raise ServerError(
                f"Unexpected status code {response.status}; check server logs for error details"
            )

    async def get_beatmap(self, beatmap_id: int) -> BinaryIO:
        """Returns a file stream for the given beatmap."""
        async with self.session.get(f"/api/beatmaps/{beatmap_id}") as response:
            if response.status == 200:
                stream = io.BytesIO()
                stream.write(await response.read())
                stream.seek(0)
                return stream
            elif response.status == 404:
                raise BeatmapNotFound(f"No beatmap found for id {beatmap_id}")
            else:
                raise ServerError(
                    f"Unexpected status code {response.status}; check server logs for error details"
                )

    async def clear_cache(self) -> None:
        """Clears beatmap cache (if applicable)."""
        async with self.session.delete("/api/beatmaps/cache") as response:
            if response.status != 200:
                raise ServerError(
                    f"Unexpected status code {response.status}; check server logs for error details"
                )

    async def calculate_difficulty(
        self,
        mods: list[OsuMod],
        beatmap_id: Optional[int] = None,
        beatmap: Optional[TextIO] = None,
    ) -> OsuDifficultyAttributes:
        params = {"mods": [mod.value for mod in mods]}

        if beatmap_id is not None:
            if beatmap is not None:
                raise ValueError(
                    "Exactly one of `beatmap_id` and `beatmap_data` should be set"
                )

            async with self.session.get(
                f"/api/difficulty/{beatmap_id}", params=params
            ) as response:
                print(response.text)
                if response.status == 200:
                    return OsuDifficultyAttributes.from_json(await response.json())
                elif response.status == 404:
                    raise BeatmapNotFound(f"No beatmap found for id {beatmap_id}")
                else:
                    raise ServerError(
                        f"Unexpected status code {response.status}; check server logs for error details"
                    )

        elif beatmap is not None:
            data = aiohttp.FormData()
            data.add_field(
                "beatmap",
                beatmap.read(),
                content_type="multipart/form-data",
                filename="beatmap",
            )
            async with self.session.post(
                "/api/difficulty", params=params, data=data
            ) as response:
                if response.status == 200:
                    return OsuDifficultyAttributes.from_json(await response.json())
                else:
                    raise ServerError(
                        f"Unexpected status code {response.status}; check server logs for error details"
                    )

        else:
            raise ValueError(
                "Exactly one of `beatmap_id` and `beatmap_data` should be set"
            )
