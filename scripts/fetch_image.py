from __future__ import annotations

import argparse
import re
import time
from pathlib import Path
from typing import TypedDict
from urllib.parse import unquote, urljoin, urlparse

import requests
from bs4 import BeautifulSoup

from common import clean_filename, level_matches_min, load_config, normalize_difficulty, project_path
from db import cursor, row_to_dict


HEADERS = {
    "User-Agent": "chunithm-progress/0.1 (+local personal database builder)",
}
DIFFICULTY_ALIASES = {
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
IMAGE_SKIP_MARKERS = (
    "logo",
    "icon",
    "button",
    "edit",
    "search",
    "blank",
    "spacer",
    "common",
)
IMAGE_EXTENSIONS = (".jpg", ".jpeg", ".png", ".webp", ".jpg.webp", ".png.webp")


class SongRow(TypedDict):
    song_name: str
    difficulty: str
    level_str: str | None
    constant: float | None
    image_url: str | None
    detail_url: str
    source_url: str


def attr_to_str(value: object) -> str | None:
    if value is None:
        return None
    if isinstance(value, str):
        return value
    if isinstance(value, list | tuple):
        return " ".join(str(item) for item in value)
    return str(value)


def cell_text(cell) -> str:
    return " ".join(cell.get_text(" ", strip=True).split())


def get_with_retries(
    session: requests.Session,
    url: str,
    timeout: int,
    delay: float,
    max_retries: int,
) -> requests.Response | None:
    for attempt in range(max_retries + 1):
        if delay > 0:
            time.sleep(delay)

        try:
            response = session.get(url, headers=HEADERS, timeout=(10, timeout))
        except requests.RequestException as exc:
            wait_seconds = max(3.0, delay * (attempt + 2) * 3)
            print(f"request failed: wait {wait_seconds}s then retry {attempt + 1}/{max_retries}: {url} ({exc})")
            time.sleep(wait_seconds)
            continue

        if response.status_code == 429:
            retry_after = response.headers.get("Retry-After")
            wait_seconds = int(retry_after) if retry_after and retry_after.isdigit() else max(5.0, delay * (attempt + 2) * 4)
            print(f"rate limited: wait {wait_seconds}s then retry {attempt + 1}/{max_retries}: {url}")
            time.sleep(wait_seconds)
            continue

        try:
            response.raise_for_status()
            return response
        except requests.HTTPError as exc:
            status = response.status_code
            if status < 500 and status != 408:
                print(f"skip after HTTP {status}: {url}")
                return None
            wait_seconds = max(3.0, delay * (attempt + 2) * 3)
            print(f"server error: wait {wait_seconds}s then retry {attempt + 1}/{max_retries}: {url} ({exc})")
            time.sleep(wait_seconds)

    print(f"skip after repeated failures: {url}")
    return None


def normalize_url(url: str, base_url: str) -> str:
    return urljoin(base_url, url)


def extract_level(text: str) -> str | None:
    match = re.search(r"Lv\s*([0-9]+(?:\+)?)", text, flags=re.IGNORECASE)
    return match.group(1) if match else None


def nearest_level_for_table(table) -> str | None:
    for sibling in table.find_previous_siblings():
        if sibling.name in {"h1", "h2", "h3", "h4"}:
            level = extract_level(cell_text(sibling))
            if level:
                return level
    heading = table.find_previous(["h1", "h2", "h3", "h4"])
    return extract_level(cell_text(heading)) if heading else None


def difficulty_from_text(text: str) -> str | None:
    return DIFFICULTY_ALIASES.get(text.strip().lower())


def find_song_link(row, base_url: str) -> tuple[str, str] | None:
    candidates = []
    for link in row.find_all("a", href=True):
        title = cell_text(link)
        href = attr_to_str(link.get("href")) or ""
        if not title or href.startswith("#"):
            continue
        lower_title = title.lower()
        lower_href = href.lower()
        if lower_title in {"edit", "編集", "添付", "新規"}:
            continue
        if any(marker in lower_href for marker in ("cmd=edit", "cmd=attach", "cmd=backup", "cmd=diff")):
            continue
        candidates.append((title, normalize_url(href, base_url)))

    if not candidates:
        return None
    return max(candidates, key=lambda item: len(item[0]))


def header_indexes(table) -> dict[str, int]:
    first_row = table.find("tr")
    if first_row is None:
        return {}
    cells = first_row.find_all(["td", "th"], recursive=False)
    indexes: dict[str, int] = {}
    for index, cell in enumerate(cells):
        text = cell_text(cell).lower()
        if "title" in text or "タイトル" in text:
            indexes["title"] = index
        elif "const" in text or "定数" in text:
            indexes["constant"] = index
    return indexes


def find_song_link_in_cell(cells, title_index: int | None, base_url: str) -> tuple[str, str] | None:
    if title_index is not None and title_index < len(cells):
        song_link = find_song_link(cells[title_index], base_url)
        if song_link:
            return song_link
    return find_song_link(cells[-3] if len(cells) >= 3 else cells[-1], base_url)


def parse_constant(values: list[str]) -> float | None:
    for value in reversed(values):
        compact = value.replace(" ", "")
        if re.fullmatch(r"\d{1,2}\.\d", compact):
            return float(compact)
    return None


def find_inline_image_url(row, base_url: str) -> str | None:
    image = row.find("img")
    if image is None:
        return None
    for attr in ("data-src", "src"):
        value = attr_to_str(image.get(attr))
        if value:
            return normalize_url(value, base_url)
    return None


def is_image_url(url: str) -> bool:
    path = unquote(urlparse(url).path).lower()
    return path.endswith(IMAGE_EXTENSIONS)


def candidate_image_urls(soup: BeautifulSoup, base_url: str) -> list[tuple[str, str]]:
    candidates: list[tuple[str, str]] = []

    for image in soup.find_all("img"):
        label = " ".join(
            value
            for value in (
                attr_to_str(image.get("alt")),
                attr_to_str(image.get("title")),
                attr_to_str(image.get("class")),
            )
            if value
        )
        for attr in ("data-src", "src"):
            value = attr_to_str(image.get(attr))
            if value and is_image_url(value):
                candidates.append((normalize_url(value, base_url), label))

    for link in soup.find_all("a", href=True):
        href = attr_to_str(link.get("href"))
        label = cell_text(link)
        if href and (is_image_url(href) or "image:" in label.lower()):
            candidates.append((normalize_url(href, base_url), label))

    return candidates


def score_image_candidate(url: str, label: str, page_name: str) -> int:
    lower_url = unquote(url).lower()
    lower_label = label.lower()
    lower_page = page_name.lower()
    filename = Path(urlparse(lower_url).path).name

    if any(marker in lower_url for marker in IMAGE_SKIP_MARKERS):
        return -100

    score = 0
    if "%3a%3aref" in lower_url or "::ref" in lower_url:
        score += 100
    if lower_page and lower_page in lower_url:
        score += 60
    if lower_page and lower_page in lower_label:
        score += 40
    if "image:" in lower_label:
        score += 20
    if filename.endswith(IMAGE_EXTENSIONS):
        score += 10
    return score


def find_detail_image_urls(html: str, base_url: str) -> list[str]:
    soup = BeautifulSoup(html, "html.parser")
    page_name = unquote(urlparse(base_url).path.rstrip("/").split("/")[-1])
    candidates = candidate_image_urls(soup, base_url)
    if not candidates:
        return []

    scored = [
        (score_image_candidate(url, label, page_name), url)
        for url, label in candidates
    ]
    scored = [(score, url) for score, url in scored if score >= 0]
    if not scored:
        return []
    scored.sort(reverse=True)
    urls = []
    seen = set()
    for _, url in scored:
        if url not in seen:
            urls.append(url)
            seen.add(url)
    return urls


def parse_song_rows(html: str, base_url: str) -> list[SongRow]:
    soup = BeautifulSoup(html, "html.parser")
    songs: list[SongRow] = []

    for table in soup.find_all("table"):
        table_level = nearest_level_for_table(table)
        indexes = header_indexes(table)
        title_index = indexes.get("title")
        current_difficulty = None
        parsed_table_rows = 0

        for row in table.find_all("tr"):
            cells = row.find_all(["td", "th"])
            if len(cells) < 3:
                continue

            values = [cell_text(cell) for cell in cells]
            for value in values:
                maybe_difficulty = difficulty_from_text(value)
                if maybe_difficulty:
                    current_difficulty = maybe_difficulty
                    break
            if current_difficulty is None:
                continue

            song_link = find_song_link_in_cell(cells, title_index, base_url)
            if song_link is None:
                continue

            song_name, detail_url = song_link
            if difficulty_from_text(song_name) is not None:
                continue

            constant = parse_constant(values)
            level_str = table_level
            if level_str is None and constant is not None:
                level_str = str(int(constant))

            songs.append(
                {
                    "song_name": song_name,
                    "difficulty": current_difficulty,
                    "level_str": level_str,
                    "constant": constant,
                    "image_url": find_inline_image_url(row, base_url),
                    "detail_url": detail_url,
                    "source_url": detail_url,
                }
            )
            parsed_table_rows += 1

        if parsed_table_rows:
            print(f"parsed {parsed_table_rows} rows from table level {table_level or '-'}")

    return songs


def resolve_song_image_urls(
    session: requests.Session,
    item: SongRow,
    timeout: int,
    delay: float,
    max_retries: int,
) -> list[str]:
    image_url = item.get("image_url")
    if image_url:
        return [str(image_url)]

    detail_url = item.get("detail_url")
    if not detail_url:
        return []

    response = get_with_retries(session, str(detail_url), timeout, delay, max_retries)
    if response is None:
        return []
    return find_detail_image_urls(response.text, str(detail_url))


def image_extension(url: str, content_type: str | None) -> str:
    suffix = Path(urlparse(url).path).suffix.lower()
    if suffix in {".jpg", ".jpeg", ".png", ".webp"}:
        return suffix
    if content_type and "png" in content_type:
        return ".png"
    if content_type and "webp" in content_type:
        return ".webp"
    return ".jpg"


def image_is_square_enough(path: Path, max_aspect_ratio: float) -> bool:
    from PIL import Image

    with Image.open(path) as image:
        width, height = image.size
    if width <= 0 or height <= 0:
        return False
    return max(width, height) / min(width, height) <= max_aspect_ratio


def download_image(
    session: requests.Session,
    url: str,
    jacket_dir: Path,
    file_stem: str,
    timeout: int,
    delay: float,
    max_retries: int,
) -> Path | None:
    response = get_with_retries(session, url, timeout, delay, max_retries)
    if response is None:
        return None
    ext = image_extension(url, response.headers.get("content-type"))
    path = jacket_dir / f"{file_stem}{ext}"
    path.write_bytes(response.content)
    return path


def download_first_square_image(
    session: requests.Session,
    urls: list[str],
    jacket_dir: Path,
    file_stem: str,
    timeout: int,
    delay: float,
    max_retries: int,
    max_aspect_ratio: float,
) -> Path | None:
    for index, url in enumerate(urls, start=1):
        path = download_image(session, url, jacket_dir, f"{file_stem}_{index}", timeout, delay, max_retries)
        if path is None:
            continue
        try:
            if image_is_square_enough(path, max_aspect_ratio):
                final_path = path.with_name(f"{file_stem}{path.suffix}")
                if final_path != path:
                    if final_path.exists():
                        final_path.unlink()
                    path.rename(final_path)
                return final_path
            print(f"skip non-square image: {path}")
            path.unlink(missing_ok=True)
        except Exception as exc:
            print(f"skip invalid image: {path} ({exc})")
            path.unlink(missing_ok=True)
    return None


def existing_valid_jacket_path(cur, song_name: str, max_aspect_ratio: float) -> str | None:
    cur.execute(
        """
        SELECT jacket_path
        FROM songs
        WHERE song_name = %s AND jacket_path IS NOT NULL
        ORDER BY updated_at DESC
        LIMIT 1
        """,
        (song_name,),
    )
    row = cur.fetchone()
    if not row:
        return None
    song = row_to_dict(row)
    if not song.get("jacket_path"):
        return None

    path = Path(song["jacket_path"])
    if not path.exists():
        return None

    try:
        if image_is_square_enough(path, max_aspect_ratio):
            return str(path)
    except Exception:
        return None
    return None


def fetch_from_urls(urls: list[str], download: bool) -> int:
    config = load_config()
    jacket_dir = project_path(config["jacket_dir"])
    jacket_dir.mkdir(parents=True, exist_ok=True)
    timeout = int(config.get("request_timeout_seconds", 20))
    delay = float(config.get("request_delay_seconds", 0.8))
    max_retries = int(config.get("request_max_retries", 3))
    min_level = float(config.get("fetch_min_level", 14.0))
    max_aspect_ratio = float(config.get("jacket_max_aspect_ratio", 1.15))
    skip_existing = bool(config.get("skip_existing_jackets", True))
    session = requests.Session()
    total = 0

    with cursor(commit=False) as cur:
        conn = cur.connection
        for url in urls:
            response = get_with_retries(session, url, timeout, 0, max_retries)
            if response is None:
                continue
            rows = parse_song_rows(response.text, url)
            original_count = len(rows)
            rows = [
                row
                for row in rows
                if level_matches_min(row.get("level_str"), row.get("constant"), min_level)
            ]
            print(f"parsed {original_count} song rows from {url}")
            print(f"filtered to {len(rows)} rows with level >= {min_level}")

            for item in rows:
                difficulty = normalize_difficulty(str(item["difficulty"]))
                song_name = str(item["song_name"])
                jacket_path = None
                if download and skip_existing:
                    jacket_path = existing_valid_jacket_path(cur, song_name, max_aspect_ratio)
                    if jacket_path:
                        print(f"skip existing jacket: {song_name}")

                if download and not jacket_path:
                    image_urls = resolve_song_image_urls(session, item, timeout, delay, max_retries)
                    if image_urls:
                        stem = clean_filename(song_name)
                        downloaded = download_first_square_image(
                            session,
                            image_urls,
                            jacket_dir,
                            stem,
                            timeout,
                            delay,
                            max_retries,
                            max_aspect_ratio,
                        )
                        jacket_path = str(downloaded) if downloaded else None
                    else:
                        print(f"no jacket found: {song_name} ({difficulty})")

                cur.execute(
                    """
                    INSERT INTO songs
                        (song_name, difficulty, level_str, constant, jp_constant, jacket_path, source_url, updated_at)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, CURRENT_TIMESTAMP)
                    ON CONFLICT (song_name, difficulty)
                    DO UPDATE SET
                        level_str = COALESCE(EXCLUDED.level_str, songs.level_str),
                        constant = COALESCE(EXCLUDED.constant, songs.constant),
                        jp_constant = COALESCE(EXCLUDED.jp_constant, songs.jp_constant),
                        jacket_path = COALESCE(EXCLUDED.jacket_path, songs.jacket_path),
                        source_url = EXCLUDED.source_url,
                        updated_at = CURRENT_TIMESTAMP
                    """,
                    (
                        item["song_name"],
                        difficulty,
                        item["level_str"],
                        item["constant"],
                        item["constant"],
                        jacket_path,
                        item["source_url"],
                    ),
                )
                conn.commit()
                total += 1

    return total


def main() -> None:
    config = load_config()
    parser = argparse.ArgumentParser(description="Fetch CHUNITHM song metadata and jackets from wiki pages.")
    parser.add_argument("urls", nargs="*", help="Wiki song list page URLs.")
    parser.add_argument("--no-download", action="store_true", help="Only import metadata, do not download jackets.")
    args = parser.parse_args()

    urls = args.urls or config.get("wiki_song_list_urls", [])
    if not urls:
        raise SystemExit("no wiki urls provided. Add config.wiki_song_list_urls or pass URLs on command line.")

    count = fetch_from_urls(urls, download=not args.no_download)
    print(f"upserted {count} song rows")


if __name__ == "__main__":
    main()
