import os
import shutil
import stat
import subprocess
from pathlib import Path
from typing import Generator, Optional
from zipfile import ZipFile

import toml
from git import Repo
from setuptools import Command, Extension, setup
from setuptools.command.build_ext import build_ext
from setuptools.dist import Distribution

VENDOR_DIR = Path(__file__).parent.absolute() / "vibrio" / "lib"


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
    """Describes the build process for a package with only precompiled extensions."""

    def run(self):
        """Directly copies relevant executable extension(s)."""
        for ext in self.extensions:
            if isinstance(ext, PrecompiledExtension):
                dest = Path(self.build_lib) / ext.path.relative_to(
                    Path(__file__).parent.absolute()
                )
                dest.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy(ext.path, dest.parent)


def find_extensions(directory: Path) -> list[PrecompiledExtension]:
    """Creates extension modules from all files in a directory."""
    return [PrecompiledExtension(path) for path in directory.glob("*")]


class MSBuild(Command):
    """Command to build server executables from GitHub repository."""

    user_options = [
        ("repo=", "R", "Server repository URL"),
        ("ref=", "r", "Git version reference (commit ID, tag, etc.)")
    ]

    def initialize_options(self) -> None:
        self.url: Optional[str] = None
        self.ref: Optional[str] = None

    def finalize_options(self) -> None:
        pyproject_path = Path(__file__).parent.absolute() / "pyproject.toml"
        with pyproject_path.open() as pyproject:
            config = toml.load(pyproject)
        if self.url is None:
            self.url = config["tool"]["vendor"]["repository"]
        if self.ref is None:
            self.ref = config["tool"]["vendor"]["reference"]

    def run(self) -> None:
        def onerror(func, path, ex_info):
            ex, *_ = ex_info
            # resolve any permission issues
            if issubclass(ex, PermissionError) and not os.access(path, os.W_OK):
                os.chmod(path, stat.S_IWUSR)
                func(path)
            else:
                raise

        shutil.rmtree(VENDOR_DIR, onerror=onerror)
        VENDOR_DIR.mkdir(parents=True, exist_ok=True)

        assert self.url is not None
        server_path = VENDOR_DIR / "server"
        repo = Repo.clone_from(self.url, server_path, no_checkout=True)
        repo.git.checkout(self.ref)
        subprocess.call(["dotnet", "msbuild", "/m", "/t:FullClean;Publish", "/Restore"], cwd=server_path / "Vibrio")
        
        publish_dir = server_path / "publish"
        for file in publish_dir.glob("*"):
            file.rename(VENDOR_DIR / file.name)
        shutil.rmtree(server_path, onerror=onerror)

        for zip_path in VENDOR_DIR.glob("*.zip"):
            with ZipFile(zip_path, "r") as zip_file:
                zip_file.extractall(VENDOR_DIR)
            zip_path.unlink()


setup(
    ext_modules=find_extensions(VENDOR_DIR),
    cmdclass={"build_ext": BuildPrecompiledExtensions, "msbuild": MSBuild},
    distclass=PrecompiledDistribution,
)
