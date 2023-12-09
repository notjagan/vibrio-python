from pathlib import Path

import pytest
from pytest import approx  # type: ignore

from vibrio import Lazer, LazerAsync
from vibrio.types import OsuMod

RESOURCES_DIR = Path(__file__).absolute().parent / "resources"

pytest_plugins = ("pytest_asyncio",)


@pytest.mark.parametrize("beatmap_id", [1001682])
def test_get_beatmap(beatmap_id: int):
    beatmap = None
    with Lazer() as lazer:
        beatmap = lazer.get_beatmap(beatmap_id)

    assert beatmap is not None
    for line in beatmap.readlines():
        if line.startswith(b"BeatmapID"):
            _, found_id = line.split(b":")
            assert beatmap_id == int(found_id)
            break


@pytest.mark.parametrize("beatmap_id", [1001682])
def test_cache_status(beatmap_id: int):
    with Lazer() as lazer:
        assert not lazer.has_beatmap(beatmap_id)
        lazer.get_beatmap(beatmap_id)
        assert lazer.has_beatmap(beatmap_id)
        lazer.clear_cache()
        assert not lazer.has_beatmap(beatmap_id)


@pytest.mark.parametrize("beatmap_id", [1001682])
@pytest.mark.parametrize("mods", [[OsuMod.DOUBLE_TIME]])
@pytest.mark.parametrize("star_rating", [9.7])
@pytest.mark.parametrize("max_combo", [3220])
def test_calculate_difficulty_id(
    beatmap_id: int, mods: list[OsuMod], star_rating: float, max_combo: int
):
    with Lazer() as lazer:
        attributes = lazer.calculate_difficulty(mods, beatmap_id=beatmap_id)
        assert attributes.star_rating == approx(star_rating, 0.03)
        assert attributes.max_combo == max_combo


@pytest.mark.parametrize("beatmap_filename", ["1001682.osu"])
@pytest.mark.parametrize("mods", [[OsuMod.DOUBLE_TIME]])
@pytest.mark.parametrize("star_rating", [9.7])
@pytest.mark.parametrize("max_combo", [3220])
def test_calculate_difficulty_file(
    beatmap_filename: str, mods: list[OsuMod], star_rating: float, max_combo: int
):
    with Lazer() as lazer, open(RESOURCES_DIR / beatmap_filename) as beatmap:
        attributes = lazer.calculate_difficulty(mods, beatmap=beatmap)
        assert attributes.star_rating == approx(star_rating, 0.03)
        assert attributes.max_combo == max_combo


@pytest.mark.asyncio
@pytest.mark.parametrize("beatmap_id", [1001682])
async def test_get_beatmap_async(beatmap_id: int):
    beatmap = None
    async with LazerAsync() as lazer:
        beatmap = await lazer.get_beatmap(beatmap_id)

    assert beatmap is not None
    for line in beatmap.readlines():
        if line.startswith(b"BeatmapID"):
            _, found_id = line.split(b":")
            assert beatmap_id == int(found_id)
            break


@pytest.mark.asyncio
@pytest.mark.parametrize("beatmap_id", [1001682])
async def test_cache_status_async(beatmap_id: int):
    async with LazerAsync() as lazer:
        assert not await lazer.has_beatmap(beatmap_id)
        await lazer.get_beatmap(beatmap_id)
        assert await lazer.has_beatmap(beatmap_id)
        await lazer.clear_cache()
        assert not await lazer.has_beatmap(beatmap_id)


@pytest.mark.asyncio
@pytest.mark.parametrize("beatmap_id", [1001682])
@pytest.mark.parametrize("mods", [[OsuMod.DOUBLE_TIME]])
@pytest.mark.parametrize("star_rating", [9.7])
@pytest.mark.parametrize("max_combo", [3220])
async def test_calculate_difficulty_id_async(
    beatmap_id: int, mods: list[OsuMod], star_rating: float, max_combo: int
):
    async with LazerAsync() as lazer:
        attributes = await lazer.calculate_difficulty(mods, beatmap_id=beatmap_id)
        assert attributes.star_rating == approx(star_rating, 0.03)
        assert attributes.max_combo == max_combo


@pytest.mark.asyncio
@pytest.mark.parametrize("beatmap_filename", ["1001682.osu"])
@pytest.mark.parametrize("mods", [[OsuMod.DOUBLE_TIME]])
@pytest.mark.parametrize("star_rating", [9.7])
@pytest.mark.parametrize("max_combo", [3220])
async def test_calculate_difficulty_file_async(
    beatmap_filename: str, mods: list[OsuMod], star_rating: float, max_combo: int
):
    async with LazerAsync() as lazer:
        with open(RESOURCES_DIR / beatmap_filename) as beatmap:
            attributes = await lazer.calculate_difficulty(mods, beatmap=beatmap)
        assert attributes.star_rating == approx(star_rating, 0.03)
        assert attributes.max_combo == max_combo
