from __future__ import annotations

import asyncio
import atexit
import io
import logging
import platform
import signal
import socket
import subprocess
import tempfile
import time
import urllib.parse
from abc import ABC
from pathlib import Path
from typing import Any, BinaryIO

import aiohttp
import psutil
import requests
from typing_extensions import Self

from vibrio.types import (
    HitStatistics,
    OsuDifficultyAttributes,
    OsuMod,
    OsuPerformanceAttributes,
)

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

    def __init__(self, port: int | None = None, use_logging: bool = True) -> None:
        if port is None:
            self.port = find_open_port()
        else:
            self.port = port
        self.use_logging = use_logging
        self.running = False

        self.server_path = get_vibrio_path(platform.system())
        if not self.server_path.exists():
            raise FileNotFoundError(f'No executable found at "{self.server_path}".')

        self.log: tempfile._TemporaryFileWrapper[bytes] | None = None

    def address(self) -> str:
        """Constructs the base URL for the web server."""
        return f"http://localhost:{self.port}"

    def _start(self) -> None:
        if self.running:
            raise ServerStateError("Server is already running")
        self.running = True

        if self.use_logging:
            logging.basicConfig(level=logging.INFO)
            logging.info(f"Launching server on port {self.port}")
            self.log = tempfile.NamedTemporaryFile(delete=False)

    def _stop(self) -> None:
        if self.log is not None:
            logging.info(f"Server output logged at {self.log.file.name}")
            self.log.close()
            self.log = None

        self.running = False

    @staticmethod
    def _not_found_error(beatmap_id: int) -> BeatmapNotFound:
        return BeatmapNotFound(f"No beatmap found for id {beatmap_id}")


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

    def __init__(self, *, port: int | None = None, use_logging: bool = True) -> None:
        super().__init__(port, use_logging)

        self.session = None
        self.process = None

    @property
    def session(self) -> BaseUrlSession:
        if self._session is None:
            raise ServerStateError("Session has not been initialized")
        return self._session

    @session.setter
    def session(self, value: BaseUrlSession | None) -> None:
        self._session = value

    @property
    def process(self) -> subprocess.Popen[bytes]:
        if self._process is None:
            raise ServerStateError("Process has not been initialized")
        return self._process

    @process.setter
    def process(self, value: subprocess.Popen[bytes] | None) -> None:
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
                logging.error(
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

    @staticmethod
    def _status_error(response: requests.Response) -> ServerError:
        if response.text:
            return ServerError(
                f"Unexpected status code {response.status_code}: {response.text}"
            )
        else:
            return ServerError(f"Unexpected status code {response.status_code}")

    def has_beatmap(self, beatmap_id: int) -> bool:
        """Checks if given beatmap is cached/available locally."""
        with self.session.get(f"/api/beatmaps/{beatmap_id}/status") as response:
            if response.status_code == 200:
                return True
            elif response.status_code == 404:
                return False
            raise self._status_error(response)

    def get_beatmap(self, beatmap_id: int) -> BinaryIO:
        """Returns a file stream for the given beatmap."""
        with self.session.get(f"/api/beatmaps/{beatmap_id}") as response:
            if response.status_code == 200:
                stream = io.BytesIO()
                stream.write(response.content)
                stream.seek(0)
                return stream
            elif response.status_code == 404:
                raise self._not_found_error(beatmap_id)
            else:
                raise self._status_error(response)

    def clear_cache(self) -> None:
        """Clears beatmap cache (if applicable)."""
        with self.session.delete("/api/beatmaps/cache") as response:
            if response.status_code != 200:
                raise self._status_error(response)

    def calculate_difficulty(
        self,
        *,
        beatmap_id: int | None = None,
        beatmap: BinaryIO | None = None,
        mods: list[OsuMod] | None = None,
    ) -> OsuDifficultyAttributes:
        params = {}
        if mods is not None:
            params["mods"] = [mod.value for mod in mods]

        if beatmap_id is not None:
            if beatmap is not None:
                raise ValueError(
                    "Exactly one of `beatmap_id` and `beatmap` should be set"
                )
            response = self.session.get(f"/api/difficulty/{beatmap_id}", params=params)
        elif beatmap is not None:
            response = self.session.post(
                "/api/difficulty", params=params, files={"beatmap": beatmap}
            )
        else:
            raise ValueError("Exactly one of `beatmap_id` and `beatmap` should be set")

        with response:
            if response.status_code == 200:
                return OsuDifficultyAttributes.from_dict(response.json())
            elif response.status_code == 404 and beatmap_id is not None:
                raise self._not_found_error(beatmap_id)
            else:
                raise self._status_error(response)

    def calculate_performance(
        self,
        *,
        beatmap_id: int | None = None,
        beatmap: BinaryIO | None = None,
        mods: list[OsuMod] | None = None,
        difficulty: OsuDifficultyAttributes | None = None,
        hit_stats: HitStatistics | None = None,
        replay: BinaryIO | None = None,
    ) -> OsuPerformanceAttributes:
        if beatmap_id is not None:
            if hit_stats is not None:
                params = hit_stats.to_dict()
                if mods is not None:
                    params["mods"] = [mod.value for mod in mods]
                response = self.session.get(
                    f"/api/performance/{beatmap_id}", params=params
                )
            elif replay is not None:
                response = self.session.post(
                    f"/api/performance/replay/{beatmap_id}", files={"replay": replay}
                )
            else:
                raise ValueError

        elif beatmap is not None:
            if hit_stats is not None:
                params = hit_stats.to_dict()
                if mods is not None:
                    params["mods"] = [mod.value for mod in mods]
                response = self.session.post(
                    "/api/performance", params=params, files={"beatmap": beatmap}
                )
            elif replay is not None:
                response = self.session.post(
                    "/api/performance/replay",
                    files={"beatmap": beatmap, "replay": replay},
                )
            else:
                raise ValueError

        elif difficulty is not None and hit_stats is not None:
            params = difficulty.to_dict() | hit_stats.to_dict()
            response = self.session.get("/api/performance", params=params)

        else:
            raise ValueError

        with response:
            if response.status_code == 200:
                return OsuPerformanceAttributes.from_dict(response.json())
            elif response.status_code == 404 and beatmap_id is not None:
                raise self._not_found_error(beatmap_id)
            else:
                raise self._status_error(response)


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
    def session(self, value: aiohttp.ClientSession | None) -> None:
        self._session = value

    @property
    def process(self) -> asyncio.subprocess.Process:
        if self._process is None:
            raise ServerStateError("Process has not been initialized")
        return self._process

    @process.setter
    def process(self, value: asyncio.subprocess.Process | None) -> None:
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

    @staticmethod
    async def _status_error(response: aiohttp.ClientResponse) -> ServerError:
        text = await response.text()
        if text:
            return ServerError(
                f"Unexpected status code {response.status}: {response.text}"
            )
        else:
            return ServerError(f"Unexpected status code {response.status}")

    async def has_beatmap(self, beatmap_id: int) -> bool:
        """Checks if given beatmap is cached/available locally."""
        async with self.session.get(f"/api/beatmaps/{beatmap_id}/status") as response:
            if response.status == 200:
                return True
            elif response.status == 404:
                return False
            raise await self._status_error(response)

    async def get_beatmap(self, beatmap_id: int) -> BinaryIO:
        """Returns a file stream for the given beatmap."""
        async with self.session.get(f"/api/beatmaps/{beatmap_id}") as response:
            if response.status == 200:
                stream = io.BytesIO()
                stream.write(await response.read())
                stream.seek(0)
                return stream
            elif response.status == 404:
                raise self._not_found_error(beatmap_id)
            else:
                raise await self._status_error(response)

    async def clear_cache(self) -> None:
        """Clears beatmap cache (if applicable)."""
        async with self.session.delete("/api/beatmaps/cache") as response:
            if response.status != 200:
                raise await self._status_error(response)

    async def calculate_difficulty(
        self,
        *,
        beatmap_id: int | None = None,
        beatmap: BinaryIO | None = None,
        mods: list[OsuMod] | None = None,
    ) -> OsuDifficultyAttributes:
        params = {}
        if mods is not None:
            params["mods"] = [mod.value for mod in mods]

        if beatmap_id is not None:
            if beatmap is not None:
                raise ValueError(
                    "Exactly one of `beatmap_id` and `beatmap` should be set"
                )
            response = await self.session.get(
                f"/api/difficulty/{beatmap_id}", params=params
            )
        elif beatmap is not None:
            response = await self.session.post(
                "/api/difficulty", params=params, data={"beatmap": beatmap}
            )
        else:
            raise ValueError("Exactly one of `beatmap_id` and `beatmap` should be set")

        async with response:
            if response.status == 200:
                return OsuDifficultyAttributes.from_dict(await response.json())
            elif response.status == 404 and beatmap_id is not None:
                raise self._not_found_error(beatmap_id)
            else:
                raise await self._status_error(response)

    async def calculate_performance(
        self,
        *,
        beatmap_id: int | None = None,
        beatmap: BinaryIO | None = None,
        mods: list[OsuMod] | None = None,
        difficulty: OsuDifficultyAttributes | None = None,
        hit_stats: HitStatistics | None = None,
        replay: BinaryIO | None = None,
    ) -> OsuPerformanceAttributes:
        if beatmap_id is not None:
            if hit_stats is not None:
                params = hit_stats.to_dict()
                if mods is not None:
                    params["mods"] = [mod.value for mod in mods]
                response = await self.session.get(
                    f"/api/performance/{beatmap_id}", params=params
                )
            elif replay is not None:
                response = await self.session.post(
                    f"/api/performance/replay/{beatmap_id}", data={"replay": replay}
                )
            else:
                raise ValueError

        elif beatmap is not None:
            if hit_stats is not None:
                params = hit_stats.to_dict()
                if mods is not None:
                    params["mods"] = [mod.value for mod in mods]
                response = await self.session.post(
                    "/api/performance", params=params, data={"beatmap": beatmap}
                )
            elif replay is not None:
                response = await self.session.post(
                    "/api/performance/replay",
                    data={"beatmap": beatmap, "replay": replay},
                )
            else:
                raise ValueError

        elif difficulty is not None and hit_stats is not None:
            params = difficulty.to_dict() | hit_stats.to_dict()
            response = await self.session.get("/api/performance", params=params)

        else:
            raise ValueError

        async with response:
            if response.status == 200:
                return OsuPerformanceAttributes.from_dict(await response.json())
            elif response.status == 404 and beatmap_id is not None:
                raise self._not_found_error(beatmap_id)
            else:
                raise await self._status_error(response)
