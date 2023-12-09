from dataclasses import dataclass, fields
from enum import Enum
from typing import Any

from typing_extensions import Self


class OsuMod(Enum):
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
class OsuDifficultyAttributes:
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

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> Self:
        values: dict[str, Any] = {}
        data_lowercase = {k.lower(): v for k, v in data.items()}
        for field in fields(cls):
            name = field.name.replace("_", "")
            if field.type is list[OsuMod]:
                value = [OsuMod(acronym) for acronym in data_lowercase[name]]
            else:
                value = data_lowercase[name]

            values[field.name] = value

        return cls(**values)
