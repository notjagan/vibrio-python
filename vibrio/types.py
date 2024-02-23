"""
Custom types used with :class:`~vibrio.Lazer` and :class:`~vibrio.LazerAsync`.
"""

from abc import ABC
from dataclasses import asdict, dataclass, fields
from enum import Enum
from typing import Any

from typing_extensions import Self


class OsuMod(Enum):
    """Enum representing the osu!standard mods as two-letter string codes."""

    NO_FAIL = "NF"
    EASY = "EZ"
    TOUCH_DEVICE = "TD"
    HIDDEN = "HD"
    HARD_ROCK = "HR"
    SUDDEN_DEATH = "SD"
    DOUBLE_TIME = "DT"
    RELAX = "RX"
    HALF_TIME = "HT"
    NIGHTCORE = "NC"
    FLASHLIGHT = "FL"
    AUTOPLAY = "AT"
    SPUN_OUT = "SO"
    AUTOPILOT = "AP"
    PERFECT = "PF"


@dataclass
class SerializableDataclass(ABC):
    """
    Abstract base for dataclasses supporting serialization to and deserialization
    from a dictionary.

    This base class is intended for cross-compatible use with return types from C# code,
    so key names are expected in camel case during deserialization and are normalized to
    remove underscores during serialization.
    """

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Self:
        """Instantiates a dataclass from the provided dictionary."""
        values: dict[str, Any] = {}
        data_lowercase = {k.lower(): v for k, v in data.items()}
        for field in fields(cls):
            name = field.name.replace("_", "")
            value = data_lowercase[name]
            if field.type is list[OsuMod]:
                value = [OsuMod(acronym) for acronym in value]

            values[field.name] = value

        return cls(**values)

    @staticmethod
    def _factory(items: list[tuple[str, Any]]) -> dict[str, Any]:
        data: dict[str, Any] = {}
        for k, v in items:
            if type(v) is list[OsuMod]:
                v = [mod.value for mod in v]
            data[k.replace("_", "")] = v
        return data

    def to_dict(self) -> dict[str, Any]:
        """Serializes dataclass values to a dictionary."""
        return asdict(self, dict_factory=self._factory)


@dataclass
class HitStatistics(SerializableDataclass):
    """Dataclass representing an osu! play in terms of individual hit statistics."""

    count_300: int
    count_100: int
    count_50: int
    count_miss: int
    combo: int


@dataclass
class OsuDifficultyAttributes(SerializableDataclass):
    """
    osu!standard difficulty attributes, as produced by osu!lazer's internal difficulty
    calculation.

    See Also
    --------
    Lazer.calculate_difficulty
    LazerAsync.calculate_difficulty
    """

    mods: list[OsuMod]
    star_rating: float
    max_combo: int
    aim_difficulty: float
    speed_difficulty: float
    speed_note_count: float
    flashlight_difficulty: float
    slider_factor: float
    approach_rate: float
    overall_difficulty: float
    drain_rate: float
    hit_circle_count: int
    slider_count: int
    spinner_count: int


@dataclass
class OsuPerformanceAttributes(SerializableDataclass):
    """
    osu!standard performance attributes, as produced by osu!lazer's internal performance
    calculation.

    See Also
    --------
    Lazer.calculate_difficulty
    LazerAsync.calculate_difficulty
    """

    total: float
    """The play's total pp amount."""
    aim: float
    speed: float
    accuracy: float
    flashlight: float
    effective_miss_count: float
