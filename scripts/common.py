from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Iterable


PROJECT_ROOT = Path(__file__).resolve().parents[1]
CONFIG_PATH = PROJECT_ROOT / "config" / "config.json"

DIFFICULTIES = ("basic", "advanced", "expert", "master", "ultima")
GRADE_ORDER = {
    "D": 0,
    "C": 1,
    "B": 2,
    "BB": 3,
    "BBB": 4,
    "A": 5,
    "AA": 6,
    "AAA": 7,
    "S": 8,
    "S+": 9,
    "SS": 10,
    "SS+": 11,
    "SSS": 12,
    "SSS+": 13,
}


def load_config() -> dict[str, Any]:
    with CONFIG_PATH.open("r", encoding="utf-8") as f:
        return json.load(f)


def project_path(value: str | Path) -> Path:
    path = Path(value)
    if path.is_absolute():
        return path
    return PROJECT_ROOT / path


def normalize_difficulty(value: str) -> str:
    normalized = str(value).strip().lower()
    aliases = {
        "bas": "basic",
        "basic": "basic",
        "adv": "advanced",
        "advanced": "advanced",
        "exp": "expert",
        "expert": "expert",
        "mas": "master",
        "master": "master",
        "ult": "ultima",
        "ultima": "ultima",
    }
    if normalized not in aliases:
        raise ValueError(f"unknown difficulty: {value}")
    return aliases[normalized]


def normalize_grade(value: str | None) -> str | None:
    if value is None:
        return None
    grade = str(value).strip().upper()
    if not grade:
        return None
    return grade if grade in GRADE_ORDER else None


def grade_from_score(score: int) -> str:
    if score >= 1_009_000:
        return "SSS+"
    if score >= 1_007_500:
        return "SSS"
    if score >= 1_005_000:
        return "SS+"
    if score >= 1_000_000:
        return "SS"
    if score >= 990_000:
        return "S+"
    if score >= 975_000:
        return "S"
    if score >= 950_000:
        return "AAA"
    if score >= 925_000:
        return "AA"
    if score >= 900_000:
        return "A"
    if score >= 800_000:
        return "BBB"
    if score >= 700_000:
        return "BB"
    if score >= 600_000:
        return "B"
    if score >= 500_000:
        return "C"
    return "D"


def grade_at_least(actual: str | None, minimum: str) -> bool:
    actual_grade = normalize_grade(actual)
    minimum_grade = normalize_grade(minimum)
    if actual_grade is None or minimum_grade is None:
        return False
    return GRADE_ORDER[actual_grade] >= GRADE_ORDER[minimum_grade]


def level_to_min_constant(level: str | float | int) -> float:
    text = str(level).strip()
    if text.endswith("+"):
        return float(text[:-1]) + 0.5
    return float(text)


def level_matches_min(level_str: str | None, constant: float | None, min_level: float) -> bool:
    if constant is not None:
        return float(constant) >= float(min_level)
    if level_str:
        return level_to_min_constant(level_str) >= float(min_level)
    return True


def first_present(row: dict[str, str], names: Iterable[str]) -> str | None:
    lowered = {key.strip().lower(): value for key, value in row.items()}
    for name in names:
        value = lowered.get(name.lower())
        if value not in (None, ""):
            return value
    return None


def clean_filename(name: str) -> str:
    return re.sub(r'[<>:"/\\|?*\x00-\x1f]+', "_", name).strip(". ")
