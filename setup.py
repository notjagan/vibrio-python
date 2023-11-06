import shutil
from pathlib import Path
from typing import Generator, Optional
from zipfile import ZipFile

import requests
import toml
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


class UnsupportedPlatformError(ValueError):
    """Error caused by attempting to build on an unsupported platform/architecture."""


class BuildPrecompiledExtensions(build_ext):
    """Describes the build process for a package with only precompiled extensions."""

    def suffix(self) -> str:
        """Determines executable file suffix for the current build platform."""
        if self.plat_name == "win-amd64":
            return ".win-x64.exe"
        elif self.plat_name == "win32":
            return ".win-x86.exe"
        elif self.plat_name.startswith("macosx"):
            if self.plat_name.endswith("x86_64"):
                return ".osx-x64"
            elif self.plat_name.endswith("arm64"):
                return ".osx-arm64"
        elif self.plat_name == "manylinux1_x86_64":
            return ".linux-x64"
        elif self.plat_name == "manylinux1_arm64":
            return ".linux-arm64"
        raise UnsupportedPlatformError(f'Platform "{self.plat_name}" is not supported')

    def run(self):
        """Directly copies relevant executable extension(s)."""
        for ext in self.extensions:
            if isinstance(ext, PrecompiledExtension) and ext.path.name.endswith(
                self.suffix()
            ):
                dest = Path(self.build_lib) / ext.path.parent
                dest.mkdir(parents=True, exist_ok=True)
                shutil.copy(ext.path, dest)


def find_extensions(directory: Path) -> list[PrecompiledExtension]:
    """Creates extension modules from all files in a directory."""
    return [PrecompiledExtension(path) for path in directory.glob("*")]


class VendorBinaries(Command):
    """Command to download server executables from GitHub release."""

    user_options = [("tag=", "t", "Tag associated with release version of server")]

    def initialize_options(self) -> None:
        self.tag: Optional[str] = None

    def finalize_options(self) -> None:
        with open(Path(__file__).parent.absolute() / "pyproject.toml") as file:
            config = toml.load(file)
        if self.tag is None:
            self.tag = config["tool"]["vendor"]["release"]

    def run(self) -> None:
        shutil.rmtree(VENDOR_DIR, ignore_errors=True)
        VENDOR_DIR.mkdir(parents=True, exist_ok=True)

        response = requests.get(
            f"https://api.github.com/repos/notjagan/vibrio/releases/tags/{self.tag}"
        )
        release = response.json()

        for asset in release["assets"]:
            name: str = asset["name"]
            zip_path = (VENDOR_DIR / name).with_suffix(".zip")
            with open(zip_path, "wb") as file:
                file.write(requests.get(asset["browser_download_url"]).content)

            with ZipFile(zip_path, "r") as zipfile:
                zipfile.extractall(VENDOR_DIR)
            zip_path.unlink()


setup(
    ext_modules=find_extensions(VENDOR_DIR),
    cmdclass={"build_ext": BuildPrecompiledExtensions, "vendor": VendorBinaries},
    distclass=PrecompiledDistribution,
)
