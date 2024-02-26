"""
Module for interacting with osu!lazer functionality (see :class:`Lazer`,
:class:`LazerAsync`).
"""

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


class StateError(Exception):
    """
    Exception due to attempting to induce an invalid state transition e.g. attempting to
    launch the server when an instance is already tied to the current object.
    """


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
    """Abstract base class for `Lazer` and `LazerAsync`."""

    STARTUP_DELAY = 0.05
    """Amount of time (seconds) between requests during startup."""

    def __init__(
        self,
        *,
        port: int | None = None,
        self_hosted: bool = False,
        log_level: logging._Level = logging.NOTSET,
    ) -> None:
        self.self_hosted = self_hosted
        if self.self_hosted and port is None:
            raise ValueError("`port` must be provided if self-hosting")

        if port is None:
            self.port = find_open_port()
        else:
            self.port = port

        self.connected = False
        self._server_path = get_vibrio_path(platform.system())
        if not self._server_path.exists():
            raise FileNotFoundError(f'No executable found at "{self._server_path}"')

        self._logger = logging.getLogger(str(id(self)))
        self._logger.setLevel(log_level)

        self._info_pipe: LogPipe | None
        self._error_pipe: LogPipe | None

    def args(self) -> list[str]:
        """Produces the command line arguments for the server executable."""
        return [str(self._server_path), "--urls", self.address()]

    def address(self) -> str:
        """Constructs the base URL for the web server."""
        return f"http://localhost:{self.port}"

    def _start(self) -> None:
        if self.connected:
            raise StateError("Already connected to server")

        self._info_pipe = LogPipe(self._logger.info)
        self._error_pipe = LogPipe(self._logger.error)

        if not self.self_hosted:
            self._logger.info(f"Hosting server on port {self.port}.")

    def _stop(self) -> None:
        self._logger.info("Shutting down...")
        if self._info_pipe is not None:
            self._info_pipe.close()
        if self._error_pipe is not None:
            self._error_pipe.close()

    @staticmethod
    def _not_found_error(beatmap_id: int) -> BeatmapNotFound:
        return BeatmapNotFound(f"No beatmap found for id {beatmap_id}")


class BaseUrlSession(requests.Session):
    """Request session with a base URL as used internally in `Lazer`."""

    def __init__(self, base_url: str) -> None:
        super().__init__()
        self.base_url = base_url

    def request(
        self, method: str | bytes, url: str | bytes, *args: Any, **kwargs: Any
    ) -> requests.Response:
        """
        Makes a request using the currently stored base URL.

        See Also
        --------
        requests.Session.request
        """
        full_url = urllib.parse.urljoin(self.base_url, str(url))
        return super().request(method, full_url, *args, **kwargs)


class Lazer(LazerBase):
    """
    Context manager for interfacing with osu!lazer functionality (synchronously).

    Attributes
    ----------
    connected : bool
        Whether the class instance is currently connected to a server.

    Examples
    --------
    >>> from vibrio import HitStatistics, Lazer, OsuMod
    >>> with Lazer() as lazer:
    ...     attributes = lazer.calculate_performance(
    ...         beatmap_id=1001682,
    ...         mods=[OsuMod.HIDDEN, OsuMod.DOUBLE_TIME],
    ...         hitstats=HitStatistics(
    ...             count_300=2019, count_100=104, count_50=0, count_miss=3, combo=3141
    ...         ),
    ...     )
    ...     attributes.total
    1304.35

    Notes
    -----
    This class can be used traditionally instead of as a context manager, in which case
    the use of `start()` and `stop()` are left up to the user. Only do this if you know
    what you are doing; failing to call `stop()` appropriately may leave an instance of
    the server dangling. `start()` will attempt to create a callback that culls any
    server instances on program shutdown, but proceed with caution and call `stop()` as
    necessary to avoid any possible memory leaks.

    See Also
    --------
    LazerAsync : asynchronous implementation of the same functionality
    """

    def __init__(
        self,
        *,
        port: int | None = None,
        self_hosted: bool = False,
        log_level: logging._Level = logging.NOTSET,
    ) -> None:
        """
        Constructs a `Lazer` instance.

        This *does not* launch/connect to a server instance. If you are using this class
        as a context manager, feel free to ignore this as the `__enter__()` and
        `__exit__()` methods will handle this for you. Otherwise, see `start()` and
        `stop()` and the notes in the class docstring.

        Parameters
        ----------
        port : int, optional
            Port to run/connect to the server on. Automatically generates an unused
            port if left unset.
        self_hosted : bool, default False
            Whether the user is hosting their own server instance. Requires
            specification of a port if set to `True`.
        log_level : logging level, default `logging.NOTSET`
            Mininum severity level for logging, as found in the `logging` standard
            library.

        Returns
        -------
        Lazer
        """
        super().__init__(port=port, self_hosted=self_hosted, log_level=log_level)

        self.session = None
        self.process = None

    @property
    def session(self) -> BaseUrlSession:
        """Request session; errors if unset."""
        if self._session is None:
            raise StateError("Session has not been initialized")
        return self._session

    @session.setter
    def session(self, value: BaseUrlSession | None) -> None:
        self._session = value

    @property
    def process(self) -> subprocess.Popen[bytes]:
        """Executable process; errors if unset."""
        if self._process is None:
            raise StateError("Process has not been initialized")
        return self._process

    @process.setter
    def process(self, value: subprocess.Popen[bytes] | None) -> None:
        self._process = value

    def start(self) -> None:
        """Launches and connects to `vibrio` server executable."""
        self._start()

        if not self.self_hosted:
            self.process = subprocess.Popen(
                self.args(),
                stdout=self._info_pipe,
                stderr=self._error_pipe,
            )

        self.session = BaseUrlSession(self.address())
        while True:  # block until webserver has launched
            try:
                with self.session.get("/api/status") as response:
                    if response.status_code == 200:
                        break
            except (ConnectionError, IOError):
                time.sleep(self.STARTUP_DELAY)

        self.connected = True
        atexit.register(self.stop)

    def stop(self) -> None:
        """Cleans up server executable and related periphery."""
        if not self.connected:
            return
        self._stop()

        if not self.self_hosted:
            parent = psutil.Process(self.process.pid)
            for child in parent.children(recursive=True):
                child.terminate()
            parent.terminate()
            status = self.process.wait()
            self.process = None

            if status != 0 and status != signal.SIGTERM:
                self._logger.error(
                    "Could not cleanly shutdown server subprocess; received return code"
                    f" {status}"
                )

        self.session.close()
        self.session = None

        self.connected = False
        self._logger.info("Connection closed.")

    def __enter__(self) -> Self:
        self.start()
        return self

    def __exit__(self, *_) -> bool:
        self.stop()
        return False

    @staticmethod
    def _status_error(response: requests.Response) -> ServerError:
        """Emits an error based on the status of the provided request."""
        if response.text:
            return ServerError(
                f"Unexpected status code {response.status_code}: {response.text}"
            )
        else:
            return ServerError(f"Unexpected status code {response.status_code}")

    def has_beatmap(self, beatmap_id: int) -> bool:
        """Returns true if the given beatmap is currently stored locally."""
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
        """
        Calculates the difficulty parameters for a beatmap and optional mod combination.

        `beatmap_id` and `beatmap` specify the beatmap to be queried; exactly one of
        the two must be set during difficulty calculation.

        Parameters
        ----------
        beatmap_id : int, optional
        beatmap : binary file stream, optional
        mods : list of OsuMod enums, optional

        Returns
        -------
        OsuDifficultyAttributes
            Dataclass encoding the difficulty attributes of the requested map.
        """
        params: dict[str, Any] = {}
        if mods is not None:
            params["mods"] = [mod.value for mod in mods]

        if beatmap_id is not None:
            if beatmap is not None:
                raise ValueError(
                    "Exactly one of `beatmap_id` and `beatmap` must be populated"
                )
            response = self.session.get(f"/api/difficulty/{beatmap_id}", params=params)
        elif beatmap is not None:
            response = self.session.post(
                "/api/difficulty", params=params, files={"beatmap": beatmap}
            )
        else:
            raise ValueError(
                "Exactly one of `beatmap_id` and `beatmap` must be populated"
            )

        with response:
            if response.status_code == 200:
                return OsuDifficultyAttributes.from_dict(response.json())
            elif response.status_code == 404 and beatmap_id is not None:
                raise self._not_found_error(beatmap_id)
            else:
                raise self._status_error(response)

    def _request_performance_beatmap_id(
        self,
        beatmap_id: int,
        mods: list[OsuMod] | None,
        hit_stats: HitStatistics | None = None,
        replay: BinaryIO | None = None,
    ) -> requests.Response:
        """Queries for the performance of a play given a beatmap ID."""
        if hit_stats is not None:
            params = hit_stats.to_dict()
            if mods is not None:
                params["mods"] = [mod.value for mod in mods]
            return self.session.get(f"/api/performance/{beatmap_id}", params=params)
        elif replay is not None:
            return self.session.post(
                f"/api/performance/replay/{beatmap_id}", files={"replay": replay}
            )
        else:
            raise ValueError(
                "Exactly one of `hit_stats` and `replay` must be populated when"
                " calculating performance with a beatmap ID"
            )

    def _request_performance_beatmap(
        self,
        beatmap: BinaryIO,
        mods: list[OsuMod] | None,
        hit_stats: HitStatistics | None = None,
        replay: BinaryIO | None = None,
    ) -> requests.Response:
        """Queries for the performance of a play given a beatmap ID."""
        if hit_stats is not None:
            params = hit_stats.to_dict()
            if mods is not None:
                params["mods"] = [mod.value for mod in mods]
            return self.session.post(
                "/api/performance", params=params, files={"beatmap": beatmap}
            )
        elif replay is not None:
            return self.session.post(
                "/api/performance/replay",
                files={"beatmap": beatmap, "replay": replay},
            )
        else:
            raise ValueError(
                "Exactly one of `hit_stats` and `replay` must be populated when"
                " calculating performance with a beatmap"
            )

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
        """
        Calculates the performance values for a given play on a provided beatmap.

        Each query essentially requires a method of specifying the beatmap the play was
        made on (through exactly one of `beatmap_id`, `beatmap` or `difficulty`) and
        a method of describing the play itself (through exactly one of `hit_stats` and
        `replay`). However, processing a replay is not possible with difficulty
        attributes alone, so using `difficulty` requires the use of `hit_stats`.

        Parameters
        ----------
        beatmap_id : int, optional
        beatmap : binary file stream, optional
        mods : list of OsuMod enums, optional
            For use with either `beatmap` or `beatmap_id`.
        difficulty : OsuDifficultyAttributes, optional
            Difficulty attribute instance, as returned by `calculate_difficulty()`.
        hit_stats : HitStatistics, optional
        replay : binary file stream, optional

        Returns
        -------
        OsuPerformanceAttributes
            Dataclass encoding the performance values of the requested play.
        """
        if beatmap_id is not None:
            response = self._request_performance_beatmap_id(
                beatmap_id, mods, hit_stats, replay
            )
        elif beatmap is not None:
            response = self._request_performance_beatmap(
                beatmap, mods, hit_stats, replay
            )
        elif difficulty is not None:
            if hit_stats is not None:
                response = self.session.get(
                    "/api/performance",
                    params=difficulty.to_dict() | hit_stats.to_dict(),
                )
            else:
                raise ValueError(
                    "`hit_stats` must be populated when querying with `difficulty`"
                )
        else:
            raise ValueError(
                "Exactly one of `beatmap_id`, `beatmap`, and `difficulty` must be"
                " populated"
            )

        with response:
            if response.status_code == 200:
                return OsuPerformanceAttributes.from_dict(response.json())
            elif response.status_code == 404 and beatmap_id is not None:
                raise self._not_found_error(beatmap_id)
            else:
                raise self._status_error(response)


class LazerAsync(LazerBase):
    """
    Context manager for interfacing with osu!lazer functionality asynchronously.

    Attributes
    ----------
    connected : bool
        Whether the class instance is currently connected to a server.

    Examples
    --------
    Note: the following example would not execute in a REPL environment as async
    statements must occur within async functions, but the principle still holds.

    >>> from vibrio import HitStatistics, Lazer, OsuMod
    >>> async with Lazer() as lazer:
    ...     attributes = await lazer.calculate_performance(
    ...         beatmap_id=1001682,
    ...         mods=[OsuMod.HIDDEN, OsuMod.DOUBLE_TIME],
    ...         hitstats=HitStatistics(
    ...             count_300=2019, count_100=104, count_50=0, count_miss=3, combo=3141
    ...         ),
    ...     )
    ...     attributes.total
    1304.35

    Notes
    -----
    This class can be used traditionally instead of as a context manager, in which case
    the use of `start()` and `stop()` are left up to the user. Only do this if you know
    what you are doing; failing to call `stop()` appropriately may leave an instance of
    the server dangling. `start()` will attempt to create a callback that culls any
    server instances on program shutdown, but proceed with caution and call `stop()` as
    necessary to avoid any possible memory leaks.

    See Also
    --------
    Lazer : synchronous implementation of the same functionality
    """

    def __init__(
        self,
        *,
        port: int | None = None,
        self_hosted: bool = False,
        log_level: logging._Level = logging.NOTSET,
    ) -> None:
        """
        Constructs a `LazerAsync` instance.

        This *does not* launch/connect to a server instance. If you are using this class
        as a context manager, feel free to ignore this as the `__aenter__()` and
        `__aexit__()` methods will handle this for you. Otherwise, see `start()` and
        `stop()` and the notes in the class docstring.

        Parameters
        ----------
        port : int, optional
            Port to run/connect to the server on. Automatically generates an unused
            port if left unset.
        self_hosted : bool, default False
            Whether the user is hosting their own server instance. Requires
            specification of a port if set to `True`.
        log_level : logging level, default `logging.NOTSET`
            Mininum severity level for logging, as found in the `logging` standard
            library.

        Returns
        -------
        LazerAsync
        """
        super().__init__(port=port, self_hosted=self_hosted, log_level=log_level)

        self.session = None
        self.process = None

    @property
    def session(self) -> aiohttp.ClientSession:
        """Request session; errors if unset."""
        if self._session is None:
            raise StateError("Session has not been initialized")
        return self._session

    @session.setter
    def session(self, value: aiohttp.ClientSession | None) -> None:
        self._session = value

    @property
    def process(self) -> asyncio.subprocess.Process:
        """Executable process; errors if unset."""
        if self._process is None:
            raise StateError("Process has not been initialized")
        return self._process

    @process.setter
    def process(self, value: asyncio.subprocess.Process | None) -> None:
        self._process = value

    async def start(self) -> None:
        """Launches and connects to `vibrio` server executable."""
        self._start()

        if not self.self_hosted:
            self.process = await asyncio.create_subprocess_shell(
                " ".join(self.args()),
                stdout=self._info_pipe,
                stderr=self._error_pipe,
            )

        self.session = aiohttp.ClientSession(self.address())
        while True:  # block until webserver has launched
            try:
                async with self.session.get("/api/status") as response:
                    if response.status == 200:
                        break
            except (ConnectionError, aiohttp.ClientConnectionError):
                await asyncio.sleep(self.STARTUP_DELAY)

        self.connected = True
        atexit.register(lambda: asyncio.run(self.stop()))

    async def stop(self) -> None:
        """Cleans up server executable and related periphery."""
        if not self.connected:
            return
        self._stop()

        if not self.self_hosted:
            parent = psutil.Process(self.process.pid)
            for child in parent.children(recursive=True):
                child.terminate()
            parent.terminate()
            status = await self.process.wait()
            self.process = None

            if status != 0 and status != signal.SIGTERM:
                self._logger.error(
                    "Could not cleanly shutdown server subprocess; received return code"
                    f" {status}"
                )

        await self.session.close()
        self.session = None

        self.connected = False
        self._logger.info("Connection closed.")

    async def __aenter__(self) -> Self:
        await self.start()
        return self

    async def __aexit__(self, *_) -> bool:
        await self.stop()
        return False

    @staticmethod
    async def _status_error(response: aiohttp.ClientResponse) -> ServerError:
        """Emits an error based on the status of the provided request."""
        text = await response.text()
        if text:
            return ServerError(
                f"Unexpected status code {response.status}: {response.text}"
            )
        else:
            return ServerError(f"Unexpected status code {response.status}")

    async def has_beatmap(self, beatmap_id: int) -> bool:
        """Returns true if the given beatmap is currently stored locally."""
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
        """
        Calculates the difficulty parameters for a beatmap and optional mod combination.

        `beatmap_id` and `beatmap` specify the beatmap to be queried; exactly one of
        the two must be set during difficulty calculation.

        Parameters
        ----------
        beatmap_id : int, optional
        beatmap : binary file stream, optional
        mods : list of OsuMod enums, optional

        Returns
        -------
        OsuDifficultyAttributes
            Dataclass encoding the difficulty attributes of the requested map.
        """
        params = {}
        if mods is not None:
            params["mods"] = [mod.value for mod in mods]

        if beatmap_id is not None:
            if beatmap is not None:
                raise ValueError(
                    "Exactly one of `beatmap_id` and `beatmap` must be populated"
                )
            response = await self.session.get(
                f"/api/difficulty/{beatmap_id}", params=params
            )
        elif beatmap is not None:
            response = await self.session.post(
                "/api/difficulty", params=params, data={"beatmap": beatmap}
            )
        else:
            raise ValueError(
                "Exactly one of `beatmap_id` and `beatmap` must be populated"
            )

        async with response:
            if response.status == 200:
                return OsuDifficultyAttributes.from_dict(await response.json())
            elif response.status == 404 and beatmap_id is not None:
                raise self._not_found_error(beatmap_id)
            else:
                raise await self._status_error(response)

    async def _request_performance_beatmap_id(
        self,
        beatmap_id: int,
        mods: list[OsuMod] | None,
        hit_stats: HitStatistics | None = None,
        replay: BinaryIO | None = None,
    ) -> aiohttp.ClientResponse:
        """Queries for the performance of a play given a beatmap ID."""
        if hit_stats is not None:
            params = hit_stats.to_dict()
            if mods is not None:
                params["mods"] = [mod.value for mod in mods]
            return await self.session.get(
                f"/api/performance/{beatmap_id}", params=params
            )
        elif replay is not None:
            return await self.session.post(
                f"/api/performance/replay/{beatmap_id}", data={"replay": replay}
            )
        else:
            raise ValueError(
                "Exactly one of `hit_stats` and `replay` must be populated when"
                " calculating performance with a beatmap ID"
            )

    async def _request_performance_beatmap(
        self,
        beatmap: BinaryIO,
        mods: list[OsuMod] | None,
        hit_stats: HitStatistics | None = None,
        replay: BinaryIO | None = None,
    ) -> aiohttp.ClientResponse:
        """Queries for the performance of a play given a beatmap ID."""
        if hit_stats is not None:
            params = hit_stats.to_dict()
            if mods is not None:
                params["mods"] = [mod.value for mod in mods]
            return await self.session.post(
                "/api/performance", params=params, data={"beatmap": beatmap}
            )
        elif replay is not None:
            return await self.session.post(
                "/api/performance/replay",
                data={"beatmap": beatmap, "replay": replay},
            )
        else:
            raise ValueError(
                "Exactly one of `hit_stats` and `replay` must be populated when"
                " calculating performance with a beatmap"
            )

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
        """
        Calculates the performance values for a given play on a provided beatmap.

        Each query essentially requires a method of specifying the beatmap the play was
        made on (through exactly one of `beatmap_id`, `beatmap` or `difficulty`) and
        a method of describing the play itself (through exactly one of `hit_stats` and
        `replay`). However, processing a replay is not possible with difficulty
        attributes alone, so using `difficulty` requires the use of `hit_stats`.

        Parameters
        ----------
        beatmap_id : int, optional
        beatmap : binary file stream, optional
        mods : list of OsuMod enums, optional
            For use with either `beatmap` or `beatmap_id`.
        difficulty : OsuDifficultyAttributes, optional
            Difficulty attribute instance, as returned by `calculate_difficulty()`.
        hit_stats : HitStatistics, optional
        replay : binary file stream, optional

        Returns
        -------
        OsuPerformanceAttributes
            Dataclass encoding the performance values of the requested play.
        """
        if beatmap_id is not None:
            response = await self._request_performance_beatmap_id(
                beatmap_id, mods, hit_stats, replay
            )
        elif beatmap is not None:
            response = await self._request_performance_beatmap(
                beatmap, mods, hit_stats, replay
            )
        elif difficulty is not None:
            if hit_stats is not None:
                response = await self.session.get(
                    "/api/performance",
                    params=difficulty.to_dict() | hit_stats.to_dict(),
                )
            else:
                raise ValueError(
                    "`hit_stats` must be populated when querying with `difficulty`"
                )
        else:
            raise ValueError(
                "Exactly one of `beatmap_id`, `beatmap`, and `difficulty` must be"
                " populated"
            )

        async with response:
            if response.status == 200:
                return OsuPerformanceAttributes.from_dict(await response.json())
            elif response.status == 404 and beatmap_id is not None:
                raise self._not_found_error(beatmap_id)
            else:
                raise await self._status_error(response)
