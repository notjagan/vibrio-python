# vibrio-python ([documentation](https://vibrio-python.readthedocs.io/en/latest/))

[![PyPI](https://img.shields.io/pypi/v/vibrio.svg)](https://pypi.org/project/vibrio/)
[![Build](https://github.com/notjagan/vibrio-python/actions/workflows/build.yml/badge.svg)](https://github.com/notjagan/vibrio-python/actions/workflows/build.yml)
[![CodeFactor](https://www.codefactor.io/repository/github/notjagan/vibrio-python/badge)](https://www.codefactor.io/repository/github/notjagan/vibrio-python)

Python library for interfacing with osu!lazer functionality. Acts as bindings for https://github.com/notjagan/vibrio under the hood.

# Installation

`pip install vibrio`

Supports Python 3.9+.

Tested (through `cibuildwheel` deployment) and published on `pip` on the following platforms:
- Ubuntu (via manylinux and musl) (x86)
- macOS (x86, arm64)
    - arm is currently untested due to unavailability through GitHub actions hosting
- Windows (x86 and AMD64)

If you do not have one of the supported platforms (or otherwise want to build from source), simply clone the repository and use the `build` subcommand (and/or any superset of `build` like `sdist`) in `setup.py` to produce an installable package, then use `pip install` on the result. This will require having the `dotnet` C# SDK on the system path to compile the server solution.
