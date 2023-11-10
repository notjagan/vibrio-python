import pytest

from vibrio import Lazer, LazerAsync

pytest_plugins = ("pytest_asyncio",)


@pytest.mark.parametrize("beatmap_id", [1001682])
def test_get_beatmap(beatmap_id):
    beatmap = None
    with Lazer() as lazer:
        beatmap = lazer.get_beatmap(beatmap_id)

    assert beatmap is not None
    for line in beatmap.readlines():
        if line.startswith(b"BeatmapID"):
            _, found_id = line.split(b":")
            assert beatmap_id == int(found_id)
            break


@pytest.mark.asyncio
@pytest.mark.parametrize("beatmap_id", [1001682])
async def test_get_beatmap_async(beatmap_id):
    beatmap = None
    async with LazerAsync() as lazer:
        beatmap = await lazer.get_beatmap(beatmap_id)

    assert beatmap is not None
    for line in beatmap.readlines():
        if line.startswith(b"BeatmapID"):
            _, found_id = line.split(b":")
            assert beatmap_id == int(found_id)
            break
