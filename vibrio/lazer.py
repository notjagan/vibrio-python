import atexit
import platform
import subprocess
from pathlib import Path
from typing import Optional

try:
    from typing import Self
except ImportError:
    from typing_extensions import Self


class UnsupportedPlatformError(ValueError):
    pass


class ServerStateException(Exception):
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
    def __init__(self) -> None:
        self.vibrio_path = get_vibrio_path(platform.system(), platform.machine())
        if not self.vibrio_path.exists():
            raise FileNotFoundError(f'No executable found at "{self.vibrio_path}".')
        self.process: Optional[subprocess.Popen] = None

    def start(self) -> None:
        """Spawns `vibrio` server executable as a subprocess."""
        if self.process is None:
            self.process = subprocess.Popen(self.vibrio_path, stdout=subprocess.DEVNULL)
            atexit.register(self.stop)
        else:
            raise ServerStateException("Server is already running")

    def stop(self) -> None:
        """Kills server subprocess."""
        if self.process is not None:
            self.process.kill()
            self.process = None


class Lazer:
    def __init__(self) -> None:
        self.server = Server()

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
