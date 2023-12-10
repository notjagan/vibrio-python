from dataclasses import dataclass
from pathlib import Path

import pytest
from pytest import approx  # type: ignore

from vibrio import Lazer, LazerAsync
from vibrio.types import HitStatistics, OsuMod

RESOURCES_DIR = Path(__file__).absolute().parent / "resources"

pytest_plugins = ("pytest_asyncio",)


@pytest.mark.parametrize("beatmap_id", [1001682])
class TestBeatmap:
    def test_get_beatmap(self, beatmap_id: int):
        beatmap = None
        with Lazer() as lazer:
            beatmap = lazer.get_beatmap(beatmap_id)

        assert beatmap is not None
        for line in beatmap.readlines():
            if line.startswith(b"BeatmapID"):
                _, found_id = line.split(b":")
                assert beatmap_id == int(found_id)
                break

    def test_cache_status(self, beatmap_id: int):
        with Lazer() as lazer:
            assert not lazer.has_beatmap(beatmap_id)
            lazer.get_beatmap(beatmap_id)
            assert lazer.has_beatmap(beatmap_id)
            lazer.clear_cache()
            assert not lazer.has_beatmap(beatmap_id)

    @pytest.mark.asyncio
    async def test_get_beatmap_async(self, beatmap_id: int):
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
    async def test_cache_status_async(self, beatmap_id: int):
        async with LazerAsync() as lazer:
            assert not await lazer.has_beatmap(beatmap_id)
            await lazer.get_beatmap(beatmap_id)
            assert await lazer.has_beatmap(beatmap_id)
            await lazer.clear_cache()
            assert not await lazer.has_beatmap(beatmap_id)


@dataclass
class DifficultyTestCase:
    beatmap_id: int
    beatmap_filename: str
    mods: list[OsuMod]
    star_rating: float
    max_combo: int


@pytest.mark.parametrize(
    "test_case",
    [DifficultyTestCase(1001682, "1001682.osu", [OsuMod.DOUBLE_TIME], 9.7, 3220)],
)
class TestDifficulty:
    def test_calculate_difficulty_id(self, test_case: DifficultyTestCase):
        with Lazer() as lazer:
            attributes = lazer.calculate_difficulty(
                mods=test_case.mods, beatmap_id=test_case.beatmap_id
            )
            assert attributes.star_rating == approx(test_case.star_rating, 0.03)
            assert attributes.max_combo == test_case.max_combo

    def test_calculate_difficulty_file(self, test_case: DifficultyTestCase):
        with Lazer() as lazer, open(
            RESOURCES_DIR / test_case.beatmap_filename
        ) as beatmap:
            attributes = lazer.calculate_difficulty(
                mods=test_case.mods, beatmap=beatmap
            )
            assert attributes.star_rating == approx(test_case.star_rating, 0.03)
            assert attributes.max_combo == test_case.max_combo

    @pytest.mark.asyncio
    async def test_calculate_difficulty_id_async(self, test_case: DifficultyTestCase):
        async with LazerAsync() as lazer:
            attributes = await lazer.calculate_difficulty(
                mods=test_case.mods, beatmap_id=test_case.beatmap_id
            )
            assert attributes.star_rating == approx(test_case.star_rating, 0.03)
            assert attributes.max_combo == test_case.max_combo

    @pytest.mark.asyncio
    async def test_calculate_difficulty_file_async(self, test_case: DifficultyTestCase):
        async with LazerAsync() as lazer:
            with open(RESOURCES_DIR / test_case.beatmap_filename) as beatmap:
                attributes = await lazer.calculate_difficulty(
                    mods=test_case.mods, beatmap=beatmap
                )
            assert attributes.star_rating == approx(test_case.star_rating, 0.03)
            assert attributes.max_combo == test_case.max_combo


@dataclass
class PerformanceTestCase:
    beatmap_id: int
    mods: list[OsuMod]
    hit_stats: HitStatistics
    pp: float


@pytest.mark.parametrize(
    "test_case",
    [
        PerformanceTestCase(
            1001682,
            [OsuMod.HIDDEN, OsuMod.DOUBLE_TIME],
            HitStatistics(2019, 104, 0, 3, 3141),
            1304.35,
        )
    ],
)
class TestPerformance:
    def test_calculate_performance_hitstat(self, test_case: PerformanceTestCase):
        with Lazer() as lazer:
            attributes = lazer.calculate_performance(
                beatmap_id=test_case.beatmap_id,
                mods=test_case.mods,
                hit_stats=test_case.hit_stats,
            )
            assert attributes.total == approx(test_case.pp, 0.05)

    def test_calculate_performance_difficulty(self, test_case: PerformanceTestCase):
        with Lazer() as lazer:
            attributes = lazer.calculate_performance(
                difficulty=lazer.calculate_difficulty(
                    mods=test_case.mods, beatmap_id=test_case.beatmap_id
                ),
                hit_stats=test_case.hit_stats,
            )
            assert attributes.total == approx(test_case.pp, 0.05)
