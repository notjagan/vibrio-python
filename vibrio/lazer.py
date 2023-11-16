from __future__ import annotations

import asyncio
import atexit
import io
import socket
from abc import ABC
from typing import BinaryIO, Generic, Optional, TypeVar

from typing_extensions import Self

from vibrio.server import Server, ServerAsync, ServerBase


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


T = TypeVar("T", bound=ServerBase)


class LazerBase(ABC, Generic[T]):
    """Shared functionality for lazer wrappers."""

    def __init__(self, port: Optional[int] = None, use_logging: bool = True) -> None:
        if port is None:
            self.port = find_open_port()
        else:
            self.port = port
        self.use_logging = use_logging

        self.server: Optional[T] = None


class Lazer(LazerBase[Server]):
    """Synchronous implementation for interfacing with osu!lazer functionality."""

    def start(self) -> None:
        """Creates and starts up server."""
        if self.server is None:
            self.server = Server.create(self.port, self.use_logging)
            atexit.register(self.stop)
        else:
            raise ServerStateError("Server is already running")

    def stop(self) -> None:
        """Cleans up and removes server."""
        if self.server is not None:
            self.server.stop()
            self.server = None

    def __enter__(self) -> Self:
        self.start()
        return self

    def __exit__(self, *_) -> bool:
        self.stop()
        return False

    def has_beatmap(self, beatmap_id: int) -> bool:
        """Checks if given beatmap is cached/available locally."""
        if self.server is None:
            raise ServerStateError("Server is not currently active")

        with self.server.session.get(f"/api/beatmaps/{beatmap_id}/status") as response:
            if response.status_code == 200:
                return True
            elif response.status_code == 404:
                return False
            raise ServerError(
                f"Unexpected status code {response.status_code}; check server logs for error details"
            )

    def get_beatmap(self, beatmap_id: int) -> BinaryIO:
        """Returns a file stream for the given beatmap."""
        if self.server is None:
            raise ServerStateError("Server is not currently active")

        with self.server.session.get(f"/api/beatmaps/{beatmap_id}") as response:
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
        if self.server is None:
            raise ServerStateError("Server is not currently active")

        with self.server.session.delete("/api/beatmaps/cache") as response:
            if response.status_code != 200:
                raise ServerError(
                    f"Unexpected status code {response.status_code}; check server logs for error details"
                )


class LazerAsync(LazerBase[ServerAsync]):
    """Asynchronous implementation for interfacing with osu!lazer functionality."""

    async def start(self) -> None:
        """Creates and starts up server."""
        if self.server is None:
            self.server = await ServerAsync.create(self.port, self.use_logging)
            atexit.register(lambda: asyncio.run(self.stop()))
        else:
            raise ServerStateError("Server is already running")

    async def stop(self) -> None:
        """Cleans up and removes server."""
        if self.server is not None:
            await self.server.stop()
            self.server = None

    async def __aenter__(self) -> Self:
        await self.start()
        return self

    async def __aexit__(self, *_) -> bool:
        await self.stop()
        return False

    async def has_beatmap(self, beatmap_id: int) -> bool:
        """Checks if given beatmap is cached/available locally."""
        if self.server is None:
            raise ServerStateError("Server is not currently active")

        async with self.server.session.get(
            f"/api/beatmaps/{beatmap_id}/status"
        ) as response:
            if response.status == 200:
                return True
            elif response.status == 404:
                return False
            raise ServerError(
                f"Unexpected status code {response.status}; check server logs for error details"
            )

    async def get_beatmap(self, beatmap_id: int) -> BinaryIO:
        """Returns a file stream for the given beatmap."""
        if self.server is None:
            raise ServerStateError("Server is not currently active")

        async with self.server.session.get(f"/api/beatmaps/{beatmap_id}") as response:
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
        if self.server is None:
            raise ServerStateError("Server is not currently active")

        async with self.server.session.delete("/api/beatmaps/cache") as response:
            if response.status != 200:
                raise ServerError(
                    f"Unexpected status code {response.status}; check server logs for error details"
                )
