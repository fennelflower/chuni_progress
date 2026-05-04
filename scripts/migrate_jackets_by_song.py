from __future__ import annotations

from pathlib import Path

from common import clean_filename, load_config, project_path
from db import cursor, rows_to_dicts
from fetch_image import image_is_square_enough


def choose_existing_path(paths: list[Path], max_aspect_ratio: float) -> Path | None:
    for path in paths:
        if not path.exists():
            continue
        try:
            if image_is_square_enough(path, max_aspect_ratio):
                return path
        except Exception:
            continue
    for path in paths:
        if path.exists():
            return path
    return None


def unique_target_path(jacket_dir: Path, song_name: str, suffix: str) -> Path:
    base = clean_filename(song_name)
    target = jacket_dir / f"{base}{suffix}"
    if not target.exists():
        return target
    return target


def main() -> None:
    config = load_config()
    jacket_dir = project_path(config["jacket_dir"])
    max_aspect_ratio = float(config.get("jacket_max_aspect_ratio", 1.2))
    migrated = 0

    with cursor(commit=False) as cur:
        conn = cur.connection
        cur.execute(
            """
            SELECT song_name, array_agg(DISTINCT jacket_path) AS jacket_paths
            FROM songs
            WHERE jacket_path IS NOT NULL
            GROUP BY song_name
            ORDER BY song_name
            """
        )
        rows = rows_to_dicts(cur.fetchall())

        for row in rows:
            song_name = row["song_name"]
            paths = [Path(value) for value in row["jacket_paths"] if value]
            source = choose_existing_path(paths, max_aspect_ratio)
            if source is None:
                continue

            target = unique_target_path(jacket_dir, song_name, source.suffix)
            if source != target:
                if target.exists():
                    source.unlink(missing_ok=True)
                else:
                    source.rename(target)

            cur.execute(
                """
                UPDATE songs
                SET jacket_path = %s, updated_at = CURRENT_TIMESTAMP
                WHERE song_name = %s
                """,
                (str(target), song_name),
            )
            conn.commit()

            for old_path in paths:
                if old_path != target and old_path.exists():
                    old_path.unlink(missing_ok=True)
            migrated += 1

    print(f"migrated {migrated} songs to one jacket per song")


if __name__ == "__main__":
    main()
