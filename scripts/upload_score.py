from __future__ import annotations

import argparse
import csv
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from common import (
    first_present,
    grade_from_score,
    level_matches_min,
    load_config,
    normalize_difficulty,
    normalize_grade,
    project_path,
)
from db import cursor, row_to_dict, rows_to_dicts


SONG_COLUMNS = ("song_name", "song", "title", "name", "\u4e50\u66f2\u540d")
DIFFICULTY_COLUMNS = ("difficulty", "diff")
DIFFICULTY_INDEX_COLUMNS = ("level_index", "difficulty_index", "diff_index")
SCORE_COLUMNS = ("score", "\u5206\u6570")
GRADE_COLUMNS = ("grade", "grade_label", "rank")
LEVEL_COLUMNS = ("level", "level_str", "\u96be\u5ea6")
CONSTANT_COLUMNS = ("constant", "const", "\u5b9a\u6570")
RATING_COLUMNS = ("rating",)
LUOXUE_REQUIRED_COLUMNS = {"id", "song_name", "level", "level_index", "score", "rating"}
SHUIYU_REQUIRED_COLUMNS = {"\u6392\u540d", "\u4e50\u66f2\u540d", "\u96be\u5ea6", "\u5b9a\u6570", "\u5206\u6570", "rating"}

DIFFICULTY_INDEX_MAP = {
    "0": "basic",
    "1": "advanced",
    "2": "expert",
    "3": "master",
    "4": "ultima",
}
RANK_MAP = {
    "d": "D",
    "c": "C",
    "b": "B",
    "bb": "BB",
    "bbb": "BBB",
    "a": "A",
    "aa": "AA",
    "aaa": "AAA",
    "s": "S",
    "sp": "S+",
    "ss": "SS",
    "ssp": "SS+",
    "sss": "SSS",
    "sssp": "SSS+",
}


@dataclass
class ImportResult:
    total_rows: int = 0
    accepted_rows: int = 0
    score_records: int = 0
    skipped_rows: int = 0
    source_format: str = "unknown"

    def __iter__(self):
        yield self.accepted_rows
        yield self.skipped_rows


def parse_score(value: str) -> int:
    return int(str(value).replace(",", "").strip())


def parse_optional_float(value: str | None) -> float | None:
    if value is None or str(value).strip() == "":
        return None
    return float(str(value).strip())


def estimate_constant_from_rating(score: int, rating: float | None) -> float | None:
    if rating is None or score < 1_000_000:
        return None
    if score >= 1_009_000:
        constant = rating - 2.15
    elif score >= 1_007_500:
        constant = rating - 2.0 - (score - 1_007_500) / 1_500 * 0.15
    elif score >= 1_005_000:
        constant = rating - 1.5 - (score - 1_005_000) / 2_500 * 0.5
    elif score >= 1_000_000:
        constant = rating - 1.0 - (score - 1_000_000) / 5_000 * 0.5
    else:
        return None
    return round(constant, 1)


def detect_csv_format(fieldnames: Sequence[str] | None) -> str:
    normalized = {str(name).strip().lower() for name in (fieldnames or [])}
    if LUOXUE_REQUIRED_COLUMNS <= normalized:
        return "luoxue"
    if SHUIYU_REQUIRED_COLUMNS <= normalized:
        return "shuiyu"
    return "generic"


def infer_difficulty_from_song_table(cur, song_name: str, constant: float | None, level_str: str | None) -> str | None:
    if constant is not None:
        cur.execute(
            """
            SELECT difficulty
            FROM songs
            WHERE song_name = %s
              AND (
                    ABS(COALESCE(cn_constant, jp_constant, constant) - %s) < 0.051
                 OR ABS(COALESCE(jp_constant, constant, cn_constant) - %s) < 0.051
              )
            ORDER BY
                CASE difficulty
                    WHEN 'ultima' THEN 0
                    WHEN 'master' THEN 1
                    WHEN 'expert' THEN 2
                    WHEN 'advanced' THEN 3
                    WHEN 'basic' THEN 4
                    ELSE 5
                END
            LIMIT 1
            """,
            (song_name, constant, constant),
        )
        row = cur.fetchone()
        if row:
            return str(row_to_dict(row)["difficulty"])

    if level_str:
        cur.execute(
            """
            SELECT difficulty
            FROM songs
            WHERE song_name = %s
              AND level_str = %s
            ORDER BY
                CASE difficulty
                    WHEN 'ultima' THEN 0
                    WHEN 'master' THEN 1
                    WHEN 'expert' THEN 2
                    WHEN 'advanced' THEN 3
                    WHEN 'basic' THEN 4
                    ELSE 5
                END
            LIMIT 1
            """,
            (song_name, level_str),
        )
        row = cur.fetchone()
        if row:
            return str(row_to_dict(row)["difficulty"])

    if constant is not None:
        cur.execute(
            """
            SELECT
                difficulty,
                ABS(COALESCE(cn_constant, jp_constant, constant) - %s) AS distance
            FROM songs
            WHERE song_name = %s
              AND COALESCE(cn_constant, jp_constant, constant) IS NOT NULL
            ORDER BY distance ASC
            LIMIT 2
            """,
            (constant, song_name),
        )
        candidates = rows_to_dicts(cur.fetchall())
        if candidates:
            best_distance = float(candidates[0]["distance"])
            second_distance = float(candidates[1]["distance"]) if len(candidates) > 1 else None
            if best_distance <= 0.201 and (second_distance is None or second_distance - best_distance > 0.051):
                return str(candidates[0]["difficulty"])

    cur.execute(
        """
        SELECT difficulty
        FROM songs
        WHERE song_name = %s
        ORDER BY
            CASE difficulty
                WHEN 'ultima' THEN 0
                WHEN 'master' THEN 1
                WHEN 'expert' THEN 2
                WHEN 'advanced' THEN 3
                WHEN 'basic' THEN 4
                ELSE 5
            END
        """,
        (song_name,),
    )
    candidates = rows_to_dicts(cur.fetchall())
    if len(candidates) == 1:
        return str(candidates[0]["difficulty"])

    return None


def parse_difficulty(
    row: dict[str, str],
    *,
    source_format: str = "generic",
    cur=None,
    song_name: str | None = None,
    constant: float | None = None,
    level_str: str | None = None,
) -> str | None:
    difficulty_raw = first_present(row, DIFFICULTY_COLUMNS)
    if difficulty_raw:
        return normalize_difficulty(difficulty_raw)

    difficulty_index = first_present(row, DIFFICULTY_INDEX_COLUMNS)
    if difficulty_index is not None:
        return DIFFICULTY_INDEX_MAP.get(str(difficulty_index).strip())

    if source_format == "shuiyu" and cur is not None and song_name:
        return infer_difficulty_from_song_table(cur, song_name.strip(), constant, level_str)

    return None


def parse_grade(row: dict[str, str], score: int) -> str:
    raw_grade = first_present(row, GRADE_COLUMNS)
    if raw_grade:
        normalized = normalize_grade(raw_grade)
        if normalized:
            return normalized
        mapped = RANK_MAP.get(str(raw_grade).strip().lower())
        if mapped:
            return mapped
    return grade_from_score(score)


def read_csv(path: Path) -> tuple[list[dict[str, str]], str]:
    encodings = ("utf-8-sig", "utf-8", "gbk")
    last_error: Exception | None = None
    for encoding in encodings:
        try:
            with path.open("r", encoding=encoding, newline="") as f:
                reader = csv.DictReader(f)
                rows = list(reader)
                return rows, detect_csv_format(reader.fieldnames)
        except UnicodeDecodeError as exc:
            last_error = exc
    raise RuntimeError(f"failed to read csv with supported encodings: {path}") from last_error


def import_scores(csv_path: Path, user_id: int = 1) -> ImportResult:
    config = load_config()
    allowed_difficulties = {
        normalize_difficulty(value) for value in config.get("import_difficulties", [])
    }
    min_level = float(config.get("min_level", 0))

    rows, source_format = read_csv(csv_path)
    result = ImportResult(total_rows=len(rows), source_format=source_format)
    touched_scores: set[tuple[str, str]] = set()

    with cursor(commit=True) as cur:
        for index, row in enumerate(rows, start=2):
            song_name = first_present(row, SONG_COLUMNS)
            score_raw = first_present(row, SCORE_COLUMNS)
            if not song_name or not score_raw:
                result.skipped_rows += 1
                print(f"skip row {index}: missing song/score")
                continue

            try:
                score = parse_score(score_raw)
            except ValueError as exc:
                result.skipped_rows += 1
                print(f"skip row {index}: {exc}")
                continue

            level_str = first_present(row, LEVEL_COLUMNS)
            constant = parse_optional_float(first_present(row, CONSTANT_COLUMNS))
            if not level_matches_min(level_str, constant, min_level):
                result.skipped_rows += 1
                continue

            normalized_song_name = song_name.strip()
            try:
                difficulty = parse_difficulty(
                    row,
                    source_format=source_format,
                    cur=cur,
                    song_name=normalized_song_name,
                    constant=constant,
                    level_str=level_str,
                )
                if difficulty is None:
                    raise ValueError("missing or unknown difficulty")
            except ValueError as exc:
                result.skipped_rows += 1
                if source_format != "shuiyu":
                    print(f"skip row {index}: {exc}")
                continue

            if allowed_difficulties and difficulty not in allowed_difficulties:
                result.skipped_rows += 1
                continue

            rating = parse_optional_float(first_present(row, RATING_COLUMNS))
            cn_constant = constant if constant is not None else estimate_constant_from_rating(score, rating)
            grade = parse_grade(row, score)

            cur.execute(
                """
                INSERT INTO songs (song_name, difficulty, level_str, cn_constant, updated_at)
                VALUES (%s, %s, %s, %s, CURRENT_TIMESTAMP)
                ON CONFLICT (song_name, difficulty)
                DO UPDATE SET
                    level_str = COALESCE(songs.level_str, EXCLUDED.level_str),
                    cn_constant = COALESCE(EXCLUDED.cn_constant, songs.cn_constant),
                    updated_at = CURRENT_TIMESTAMP
                """,
                (normalized_song_name, difficulty, level_str, cn_constant),
            )

            cur.execute(
                """
                INSERT INTO scores (user_id, song_name, difficulty, score, grade_label, updated_at)
                VALUES (%s, %s, %s, %s, %s, CURRENT_TIMESTAMP)
                ON CONFLICT (user_id, song_name, difficulty)
                DO UPDATE SET
                    score = GREATEST(scores.score, EXCLUDED.score),
                    grade_label = CASE
                        WHEN EXCLUDED.score >= scores.score THEN EXCLUDED.grade_label
                        ELSE scores.grade_label
                    END,
                    updated_at = CURRENT_TIMESTAMP
                WHERE EXCLUDED.score >= scores.score
                """,
                (user_id, normalized_song_name, difficulty, score, grade),
            )
            result.accepted_rows += 1
            touched_scores.add((normalized_song_name, difficulty))

    result.score_records = len(touched_scores)
    return result


def main() -> None:
    config = load_config()
    parser = argparse.ArgumentParser(description="Import CHUNITHM scores from csv.")
    parser.add_argument(
        "csv_path",
        nargs="?",
        default=config["score_csv_path"],
        help="CSV file path. Defaults to config.score_csv_path.",
    )
    args = parser.parse_args()

    csv_path = project_path(args.csv_path)
    if not csv_path.exists():
        raise FileNotFoundError(f"score csv not found: {csv_path}")

    result = import_scores(csv_path)
    print(
        "imported "
        f"{result.score_records} score records from {result.accepted_rows} accepted rows, "
        f"skipped {result.skipped_rows}/{result.total_rows} rows, "
        f"source={result.source_format}"
    )


if __name__ == "__main__":
    main()
