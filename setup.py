import os
import shutil
from pathlib import Path
from typing import Generator

from setuptools import Extension, setup
from setuptools.command.build_ext import build_ext
from setuptools.dist import Distribution


class PrecompiledDistribution(Distribution):
    def iter_distribution_names(self) -> Generator[str, None, None]:
        """Override base method to ignore extension modules."""
        for pkg in self.packages or ():
            yield pkg

        for module in self.py_modules or ():
            yield module


class PrecompiledExtension(Extension):
    def __init__(self, path: Path):
        self.path = path
        super().__init__(self.path.name, [])


class UnsupportedPlatformError(ValueError):
    pass


class BuildPrecompiledExtensions(build_ext):
    def suffix(self) -> str:
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
        for ext in self.extensions:
            if isinstance(ext, PrecompiledExtension) and ext.path.name.endswith(
                self.suffix()
            ):
                os.makedirs(f"{self.build_lib}/{ext.path.parent}", exist_ok=True)
                shutil.copy(f"{ext.path}", f"{self.build_lib}/{ext.path.parent}")


def find_extensions(directory: Path) -> list[PrecompiledExtension]:
    return [PrecompiledExtension(path) for path in directory.glob("*")]


setup(
    ext_modules=find_extensions(Path("vibrio") / "lib"),
    cmdclass={"build_ext": BuildPrecompiledExtensions},
    distclass=PrecompiledDistribution,
)
