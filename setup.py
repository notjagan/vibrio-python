import os
import shutil
import stat
import subprocess
from pathlib import Path
from typing import Any, Callable, Generator
from zipfile import ZipFile

from setuptools import Command, Extension, setup
from setuptools.command.build import build
from setuptools.command.build_ext import build_ext
from setuptools.dist import Distribution

PROJECT_DIR = Path(__file__).absolute().parent
PACKAGE_DIR = PROJECT_DIR / "vibrio"
EXTENSION_DIR = PACKAGE_DIR / "lib"
VENDOR_DIR = PACKAGE_DIR / "vendor"


class PrecompiledDistribution(Distribution):
    """Represents a distribution with solely precompiled extensions."""

    def iter_distribution_names(self) -> Generator[str, None, None]:
        """Override base method to ignore extension modules."""
        for pkg in self.packages or ():
            yield pkg

        for module in self.py_modules or ():
            yield module


class PrecompiledExtension(Extension):
    """Represents an extension module with an existing executable file."""

    def __init__(self, path: Path):
        self.path = path
        super().__init__(self.path.name, [])


class BuildPrecompiledExtensions(build_ext):
    """Command to copy executables for precompiled extensions."""

    def run(self):
        """Directly copies relevant executable extension(s)."""
        for ext in self.extensions:
            if isinstance(ext, PrecompiledExtension):
                for path in ext.path.glob("*"):
                    dest = Path(self.build_lib) / path.relative_to(
                        Path(__file__).parent.absolute()
                    )
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy(path, dest.parent)


class BuildVendoredDependencies(Command):
    """Command to build executables from vendored server library."""

    user_options = []

    def initialize_options(self) -> None:
        pass

    def finalize_options(self) -> None:
        pass

    def run(self):
        def onerror(
            func: Callable[..., Any], path: str, ex_info: tuple[Exception, ...]
        ):
            ex, *_ = ex_info
            # resolve any permission issues
            if ex is PermissionError and not os.access(path, os.W_OK):
                os.chmod(path, stat.S_IWUSR)
                func(path)
            elif ex is FileNotFoundError:
                pass
            else:
                raise

        shutil.rmtree(EXTENSION_DIR, onerror=onerror)
        EXTENSION_DIR.mkdir(parents=True, exist_ok=True)

        server_dir = VENDOR_DIR / "vibrio"
        code = subprocess.call(
            [
                "dotnet",
                "msbuild",
                "/m",
                "/t:FullClean;Publish",
                "/Restore",
                '/p:"UseCurrentRuntimeIdentifier=True"',
            ],
            cwd=server_dir / "Vibrio",
        )
        if code != 0:
            raise Exception("MSBuild exited with non-zero code")

        publish_dir = server_dir / "publish"
        for path in publish_dir.glob("*.zip"):
            with ZipFile(path, "r") as zip_file:
                zip_file.extractall(EXTENSION_DIR)


class CustomBuild(build):
    sub_commands = [("build_vendor", None)] + build.sub_commands


setup(
    ext_modules=[PrecompiledExtension(EXTENSION_DIR)],
    cmdclass={
        "build_ext": BuildPrecompiledExtensions,
        "build_vendor": BuildVendoredDependencies,
        "build": CustomBuild,
    },
    distclass=PrecompiledDistribution,
)
