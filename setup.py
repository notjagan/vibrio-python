from __future__ import annotations

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

    def __init__(self, path: Path) -> None:
        self.path = path
        super().__init__(self.path.name, [])


class BuildPrecompiledExtensions(build_ext):
    """Command to copy executables for precompiled extensions."""

    def run(self) -> None:
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

    def run(self) -> None:
        def onerror(
            func: Callable[[str], Any], path: str, ex_info: tuple[BaseException, ...]
        ) -> None:
            ex_type, *_ = ex_info
            # resolve any permission issues
            if ex_type is PermissionError and not os.access(path, os.W_OK):
                os.chmod(path, os.stat(path).st_mode | stat.S_IWUSR)
                func(path)
            # ignore missing file
            elif ex_type is FileNotFoundError:
                pass
            else:
                raise ex_type

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
                for filename in zip_file.filelist:
                    executable = Path(zip_file.extract(filename, EXTENSION_DIR))
                    executable.chmod(executable.stat().st_mode | stat.S_IEXEC)


class CustomBuild(build):
    """Build process including compiling server executable."""

    sub_commands = [("build_vendor", None)] + build.sub_commands  # type: ignore


setup(
    ext_modules=[PrecompiledExtension(EXTENSION_DIR)],
    cmdclass={
        "build_ext": BuildPrecompiledExtensions,
        "build_vendor": BuildVendoredDependencies,
        "build": CustomBuild,
    },
    distclass=PrecompiledDistribution,
)
