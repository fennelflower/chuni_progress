from __future__ import annotations

import sys
from pathlib import Path

import requests
from PIL import Image

from common import clean_filename, load_config, project_path
from db import cursor
from fetch_image import (
    download_first_square_image,
    get_with_retries,
    find_detail_image_urls,
)


def parse_name_from_jacket(path: Path) -> tuple[str, str | None]:
    stem = path.stem
    for difficulty in ("basic", "advanced", "expert", "master", "ultima"):
        suffix = f"_{difficulty}"
        if stem.endswith(suffix):
            return stem[: -len(suffix)], difficulty
    return stem, None


def aspect_ratio(path: Path) -> float | None:
    try:
        with Image.open(path) as image:
            width, height = image.size
    except Exception:
        return None
    if width <= 0 or height <= 0:
        return None
    return max(width, height) / min(width, height)


def main() -> None:
    config = load_config()
    jacket_dir = project_path(config["jacket_dir"])
    timeout = int(config.get("request_timeout_seconds", 30))
    delay = float(config.get("request_delay_seconds", 0.8))
    max_retries = int(config.get("request_max_retries", 3))
    max_aspect_ratio = float(config.get("jacket_max_aspect_ratio", 1.15))
    session = requests.Session()

    targets = []
    with cursor() as cur:
        cur.execute(
            """
            SELECT song_name, difficulty, jacket_path, source_url
            FROM songs
            WHERE jacket_path IS NOT NULL AND source_url IS NOT NULL
            ORDER BY song_name, difficulty
            """
        )
        for row in cur.fetchall():
            path = Path(row["jacket_path"])
            ratio = aspect_ratio(path)
            if ratio is None or ratio > max_aspect_ratio:
                targets.append(row)

        known = {(row["song_name"], row["difficulty"]) for row in targets}
        for path in jacket_dir.glob("*"):
            parsed = parse_name_from_jacket(path)
            if parsed is None:
                continue
            ratio = aspect_ratio(path)
            if ratio is not None and ratio <= max_aspect_ratio:
                continue
            song_name, difficulty = parsed
            key = (song_name, difficulty or "")
            if key in known:
                continue
            if difficulty is None:
                cur.execute(
                    """
                    SELECT song_name, difficulty, jacket_path, source_url
                    FROM songs
                    WHERE song_name = %s AND source_url IS NOT NULL
                    ORDER BY updated_at DESC
                    LIMIT 1
                    """,
                    (song_name,),
                )
            else:
                cur.execute(
                    """
                    SELECT song_name, difficulty, jacket_path, source_url
                    FROM songs
                    WHERE song_name = %s AND difficulty = %s AND source_url IS NOT NULL
                    """,
                    (song_name, difficulty),
                )
            row = cur.fetchone()
            if row:
                targets.append(row)
                known.add(key)

    if not targets:
        print("no bad jacket images found")
        return

    print(f"found {len(targets)} bad jacket images")
    fixed = 0
    with cursor(commit=False) as cur:
        conn = cur.connection
        for row in targets:
            response = get_with_retries(session, row["source_url"], timeout, delay, max_retries)
            if response is None:
                print(f"skip: {row['song_name']} ({row['difficulty']})")
                continue

            image_urls = find_detail_image_urls(response.text, row["source_url"])
            stem = clean_filename(row["song_name"])
            path = download_first_square_image(
                session,
                image_urls,
                jacket_dir,
                stem,
                timeout,
                delay,
                max_retries,
                max_aspect_ratio,
            )
            if path is None:
                print(f"not fixed: {row['song_name']} ({row['difficulty']})")
                continue

            old_path = Path(row["jacket_path"])
            if old_path.exists() and old_path != path:
                old_path.unlink(missing_ok=True)

            cur.execute(
                """
                UPDATE songs
                SET jacket_path = %s, updated_at = CURRENT_TIMESTAMP
                WHERE song_name = %s
                """,
                (str(path), row["song_name"]),
            )
            conn.commit()
            fixed += 1
            print(f"fixed: {row['song_name']} ({row['difficulty']}) -> {path}")

    if fixed != len(targets):
        print(f"fixed {fixed}/{len(targets)} images", file=sys.stderr)
    else:
        print(f"fixed {fixed} images")


if __name__ == "__main__":
    main()
