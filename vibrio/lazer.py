from __future__ import annotations

import asyncio
import atexit
import io
import logging
import os
import platform
import signal
import socket
import subprocess
import threading
import time
import urllib.parse
from abc import ABC
from pathlib import Path
from typing import IO, Any, BinaryIO, Callable

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
        _, port = sock.getsockname()
        return port


def get_vibrio_path(platform: str) -> Path:
    """Determines path to server executable on a given platform."""
    if platform == "Windows":
        suffix = ".exe"
    else:
        suffix = ""

    return PACKAGE_DIR / "lib" / f"Vibrio{suffix}"


class LogPipe(IO[str]):
    """IO wrapper around a thread for piping output to log function."""

    def __init__(self, log_func: Callable[[str], None]) -> None:
        self.log_func = log_func
        self.fd_read, self.fd_write = os.pipe()

        class LogThread(threading.Thread):
            def run(_self) -> None:
                with os.fdopen(self.fd_read) as pipe_reader:
                    for line in iter(pipe_reader.readline, ""):
                        self.log_func(line.strip("\n"))

        self.thread = LogThread()
        self.thread.daemon = True
        self.thread.start()

    def fileno(self) -> int:
        return self.fd_write

    def close(self) -> None:
        os.close(self.fd_write)


class LazerBase(ABC):
    """Shared functionality for lazer wrappers."""

    STARTUP_DELAY = 0.05  # Amount of time (seconds) between requests during startup

    def __init__(
        self, *, port: int | None = None, log_level: logging._Level = logging.NOTSET
    ) -> None:
        if port is None:
            self.port = find_open_port()
        else:
            self.port = port

        self.running = False
        self.server_path = get_vibrio_path(platform.system())
        if not self.server_path.exists():
            raise FileNotFoundError(f'No executable found at "{self.server_path}"')

        self.logger = logging.getLogger(str(id(self)))
        self.logger.setLevel(log_level)

        self.info_pipe: LogPipe | None
        self.error_pipe: LogPipe | None

    def args(self) -> list[str]:
        return [str(self.server_path), "--urls", self.address()]

    def address(self) -> str:
        """Constructs the base URL for the web server."""
        return f"http://localhost:{self.port}"

    def _start(self) -> None:
        if self.running:
            raise ServerStateError("Server is already running")
        self.running = True

        self.info_pipe = LogPipe(self.logger.info)
        self.error_pipe = LogPipe(self.logger.error)

        self.logger.info(f"Hosting server on port {self.port}.")

    def _stop(self) -> None:
        self.logger.info(f"Shutting down server...")
        if self.info_pipe is not None:
            self.info_pipe.close()
        if self.error_pipe is not None:
            self.error_pipe.close()

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

    def __init__(
        self, *, port: int | None = None, log_level: logging._Level = logging.NOTSET
    ) -> None:
        super().__init__(port=port, log_level=log_level)

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

        self.process = subprocess.Popen(
            self.args(),
            stdout=self.info_pipe,
            stderr=self.error_pipe,
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
        if not self.running:
            return
        self._stop()

        parent = psutil.Process(self.process.pid)
        for child in parent.children(recursive=True):
            child.terminate()
        parent.terminate()
        status = self.process.wait()
        self.process = None

        if status != 0 and status != signal.SIGTERM:
            self.logger.error(
                f"Could not cleanly shutdown server subprocess; received return code {status}"
            )

        self.session.close()
        self.session = None

        self.running = False
        self.logger.info("Server closed.")

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
        params: dict[str, Any] = {}
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

    def __init__(
        self, *, port: int | None = None, log_level: logging._Level = logging.NOTSET
    ) -> None:
        super().__init__(port=port, log_level=log_level)

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

        self.process = await asyncio.create_subprocess_shell(
            " ".join(self.args()),
            stdout=self.info_pipe,
            stderr=self.error_pipe,
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
        if not self.running:
            return
        self._stop()

        parent = psutil.Process(self.process.pid)
        for child in parent.children(recursive=True):
            child.terminate()
        parent.terminate()
        status = await self.process.wait()
        self.process = None

        if status != 0 and status != signal.SIGTERM:
            self.logger.error(
                f"Could not cleanly shutdown server subprocess; received return code {status}"
            )

        await self.session.close()
        self.session = None

        self.running = False
        self.logger.info("Server closed.")

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
