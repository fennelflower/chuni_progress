from __future__ import annotations

import tkinter as tk
from pathlib import Path
from tkinter import ttk
from typing import Any, TypeAlias, TypedDict

from PIL import Image, ImageDraw, ImageFont, ImageOps, ImageTk

from common import GRADE_ORDER, grade_at_least, load_config, project_path
from db import cursor, rows_to_dicts


CANVAS_W = 1500
LEFT_GUTTER = 138
TOP_H = 170
SIDE_PAD = 34
SECTION_GAP = 58
ROW_GAP = 18
CARD_W = 94
CARD_H = 124
JACKET_SIZE = 88
CARD_GAP = 18
TEXT = (31, 26, 48)
MUTED = (102, 95, 124)
WHITE = (255, 255, 255)
PURPLE = (132, 39, 214)
ULTIMA_RED = (232, 42, 62)
PillowFont: TypeAlias = ImageFont.FreeTypeFont | ImageFont.ImageFont


class BoardRow(TypedDict):
    song_name: str
    difficulty: str
    level_str: str | None
    constant: float | None
    jp_constant: float | None
    cn_constant: float | None
    display_constant: float | None
    jacket_path: str | None
    score: int | None
    grade_label: str | None


def board_row(row: dict[str, Any]) -> BoardRow:
    return {
        "song_name": str(row["song_name"]),
        "difficulty": str(row["difficulty"]),
        "level_str": str(row["level_str"]) if row.get("level_str") is not None else None,
        "constant": float(row["constant"]) if row.get("constant") is not None else None,
        "jp_constant": float(row["jp_constant"]) if row.get("jp_constant") is not None else None,
        "cn_constant": float(row["cn_constant"]) if row.get("cn_constant") is not None else None,
        "display_constant": float(row["display_constant"]) if row.get("display_constant") is not None else None,
        "jacket_path": str(row["jacket_path"]) if row.get("jacket_path") is not None else None,
        "score": int(row["score"]) if row.get("score") is not None else None,
        "grade_label": str(row["grade_label"]) if row.get("grade_label") is not None else None,
    }


def load_font(size: int, bold: bool = False) -> PillowFont:
    candidates = [
        "C:/Windows/Fonts/msyhbd.ttc" if bold else "C:/Windows/Fonts/msyh.ttc",
        "C:/Windows/Fonts/arialbd.ttf" if bold else "C:/Windows/Fonts/arial.ttf",
    ]
    for candidate in candidates:
        if Path(candidate).exists():
            return ImageFont.truetype(candidate, size)
    return ImageFont.load_default()


def fetch_levels() -> list[str]:
    with cursor() as cur:
        cur.execute(
            """
            SELECT DISTINCT level_str
            FROM songs
            WHERE level_str IS NOT NULL AND level_str <> ''
            ORDER BY level_str
            """
        )
        levels = [row["level_str"] for row in rows_to_dicts(cur.fetchall())]
        cur.execute(
            """
            SELECT DISTINCT FLOOR(COALESCE(cn_constant, jp_constant, constant))::int AS base_level
            FROM songs
            WHERE COALESCE(cn_constant, jp_constant, constant) IS NOT NULL
              AND COALESCE(cn_constant, jp_constant, constant) - FLOOR(COALESCE(cn_constant, jp_constant, constant)) >= 0.5
            ORDER BY base_level
            """
        )
        plus_levels = [f"{row['base_level']}+" for row in rows_to_dicts(cur.fetchall())]
    for level in plus_levels:
        if level not in levels:
            levels.append(level)
    levels.sort(key=lambda value: (float(str(value).rstrip("+")), 1 if str(value).endswith("+") else 0))
    return levels


def level_bounds(level: str) -> tuple[float, float]:
    text = str(level).strip()
    if text.endswith("+"):
        base = float(text[:-1])
        return base + 0.5, base + 1.0
    base = float(text)
    return base, base + 0.5


def fetch_board_rows(level: str, constant_source: str, user_id: int = 1) -> list[BoardRow]:
    constant_expr = "songs.cn_constant" if constant_source == "cn" else "COALESCE(songs.jp_constant, songs.constant)"
    min_constant, max_constant = level_bounds(level)
    with cursor() as cur:
        cur.execute(
            f"""
            SELECT
                songs.song_name,
                songs.difficulty,
                songs.level_str,
                songs.constant,
                songs.jp_constant,
                songs.cn_constant,
                {constant_expr} AS display_constant,
                songs.jacket_path,
                scores.score,
                scores.grade_label
            FROM songs
            LEFT JOIN scores
                ON scores.song_name = songs.song_name
               AND scores.difficulty = songs.difficulty
               AND scores.user_id = %s
            WHERE {constant_expr} >= %s
              AND {constant_expr} < %s
            ORDER BY display_constant NULLS LAST, songs.song_name, songs.difficulty
            """,
            (user_id, min_constant, max_constant),
        )
        return [board_row(row) for row in rows_to_dicts(cur.fetchall())]


def constant_key(row: BoardRow) -> float:
    value = row.get("display_constant")
    if value is None:
        try:
            level_str = row.get("level_str")
            return float(level_str) if level_str is not None else 0.0
        except (TypeError, ValueError):
            return 0.0
    return float(value)


def group_by_constant(rows: list[BoardRow]) -> list[tuple[float, list[BoardRow]]]:
    groups: dict[float, list[BoardRow]] = {}
    for row in rows:
        groups.setdefault(round(constant_key(row), 1), []).append(row)
    return [(constant, groups[constant]) for constant in sorted(groups)]


def fit_text(draw: ImageDraw.ImageDraw, text: str, font, max_width: int) -> str:
    if draw.textlength(text, font=font) <= max_width:
        return text
    result = text
    while result and draw.textlength(result + "...", font=font) > max_width:
        result = result[:-1]
    return result + "..." if result else ""


def load_jacket(path: str | None, dimmed: bool, size: int = JACKET_SIZE) -> Image.Image:
    if path and Path(path).exists():
        image = Image.open(path).convert("RGB")
    else:
        image = Image.new("RGB", (size, size), (210, 214, 220))
        draw = ImageDraw.Draw(image)
        draw.rectangle((0, 0, size - 1, size - 1), outline=(170, 176, 186), width=2)

    image = ImageOps.fit(image, (size, size), method=Image.Resampling.LANCZOS)
    if dimmed:
        overlay = Image.new("RGB", image.size, (42, 42, 48))
        image = Image.blend(image, overlay, 0.62)
    return image


def draw_background(image: Image.Image) -> None:
    draw = ImageDraw.Draw(image)
    width, height = image.size
    for y in range(height):
        t = y / max(1, height - 1)
        if t < 0.45:
            p = t / 0.45
            left = (int(236 - 70 * p), int(126 + 95 * p), int(248 - 20 * p))
            right = (int(116 - 25 * p), int(221 + 20 * p), int(236 - 52 * p))
        else:
            p = (t - 0.45) / 0.55
            left = (int(166 + 72 * p), int(221 + 15 * p), int(228 - 60 * p))
            right = (int(91 + 40 * p), int(241 - 24 * p), int(184 + 20 * p))
        row = Image.new("RGB", (width, 1))
        row_pixels = []
        for x in range(width):
            q = x / max(1, width - 1)
            row_pixels.append(tuple(int(left[i] * (1 - q) + right[i] * q) for i in range(3)))
        row.putdata(row_pixels)
        image.paste(row, (0, y))

    for x in range(0, width, 92):
        draw.line((x, 0, x, height), fill=(255, 255, 255, 42), width=1)
    for y in range(TOP_H, height, 92):
        draw.line((0, y, width, y), fill=(255, 255, 255, 35), width=1)
    draw.rectangle((0, 0, width, TOP_H - 1), fill=(255, 255, 255, 48))


def draw_text_with_stroke(
    draw: ImageDraw.ImageDraw,
    xy: tuple[int, int],
    text: str,
    font,
    fill,
    stroke_fill=(255, 255, 255),
    stroke_width: int = 3,
) -> None:
    draw.text(xy, text, font=font, fill=fill, stroke_width=stroke_width, stroke_fill=stroke_fill)


def fit_font_to_width(draw: ImageDraw.ImageDraw, text: str, max_width: int, start_size: int, min_size: int, bold: bool = True) -> PillowFont:
    for size in range(start_size, min_size - 1, -2):
        font = load_font(size, bold=bold)
        if draw.textlength(text, font=font) <= max_width:
            return font
    return load_font(min_size, bold=bold)


def draw_header(
    image: Image.Image,
    level: str,
    min_grade: str,
    constant_source: str,
    achieved_count: int,
    total: int,
    player_name: str | None = None,
    unfinished_only: bool = False,
) -> None:
    draw = ImageDraw.Draw(image)
    title_font = load_font(62, bold=True)
    lv_font = load_font(52, bold=True)
    sub_font = load_font(24, bold=True)
    small_font = load_font(20)

    draw_text_with_stroke(draw, (92, 33), "CHUNITHM", load_font(36, bold=True), (67, 98, 178), stroke_width=2)
    if player_name:
        player_text = f"player: {player_name}"
        player_font = fit_font_to_width(draw, player_text, 300, 30, 16, bold=True)
        draw.text((94, 82), player_text, fill=TEXT, font=player_font)

    draw.rounded_rectangle((430, 55, 620, 118), radius=10, fill=WHITE)
    draw_text_with_stroke(draw, (448, 36), "LV", lv_font, (235, 48, 75), stroke_width=4)
    draw_text_with_stroke(draw, (530, 36), level, lv_font, (235, 48, 75), stroke_width=4)
    title_text = f"\u672a{min_grade}\u8fdb\u5ea6\u8868" if unfinished_only else f"{min_grade}\u8fdb\u5ea6\u8868"
    draw_text_with_stroke(draw, (690, 33), title_text, title_font, (117, 62, 198), stroke_width=5)
    draw.rounded_rectangle((1095, 66, 1460, 125), radius=8, fill=(255, 255, 255))
    source_label = "CN constants" if constant_source == "cn" else "JP constants"
    if unfinished_only:
        stat_text = f"{total - achieved_count}/{total} < {min_grade}"
    else:
        stat_text = f"{achieved_count}/{total} >= {min_grade}"
    draw.text((1130, 79), stat_text, fill=(207, 45, 64), font=sub_font)
    draw.text((1130, 30), f"CHUNITHM PROGRESS / {source_label}", fill=TEXT, font=small_font)


def draw_level_badge(image: Image.Image, x: int, y: int, constant: float) -> None:
    draw = ImageDraw.Draw(image)
    draw.ellipse((x, y, x + 94, y + 94), fill=(255, 255, 255), outline=(231, 65, 75), width=5)
    draw.arc((x + 7, y + 7, x + 87, y + 87), start=210, end=340, fill=(238, 178, 24), width=8)
    draw.text((x + 22, y + 12), "LV.", fill=(215, 32, 45), font=load_font(22, bold=True))
    text = f"{constant:.1f}"
    font = load_font(34, bold=True)
    bbox = draw.textbbox((0, 0), text, font=font)
    draw.text((x + (94 - (bbox[2] - bbox[0])) // 2, y + 42), text, fill=TEXT, font=font)


def difficulty_color(difficulty: str) -> tuple[int, int, int]:
    return ULTIMA_RED if difficulty == "ultima" else PURPLE


def focus_score_text(score: int | None) -> str | None:
    if score is None or int(score) < 900_000:
        return None
    score_int = int(score)
    if score_int >= 1_000_000:
        return f"{score_int % 10_000:04d}"
    return f"{score_int % 100_000:05d}"


def draw_centered_text(draw: ImageDraw.ImageDraw, box: tuple[int, int, int, int], text: str, font, fill, stroke_width: int = 0) -> None:
    left, top, right, bottom = box
    bbox = draw.textbbox((0, 0), text, font=font, stroke_width=stroke_width)
    width = bbox[2] - bbox[0]
    height = bbox[3] - bbox[1]
    draw.text(
        (left + (right - left - width) // 2, top + (bottom - top - height) // 2 - bbox[1]),
        text,
        fill=fill,
        font=font,
        stroke_width=stroke_width,
        stroke_fill=(18, 18, 24),
    )


def largest_font_for_box(
    draw: ImageDraw.ImageDraw,
    text: str,
    box: tuple[int, int, int, int],
    max_size: int,
    min_size: int,
    stroke_width: int = 0,
) -> PillowFont:
    max_width = box[2] - box[0]
    max_height = box[3] - box[1]
    for size in range(max_size, min_size - 1, -2):
        font = load_font(size, bold=True)
        bbox = draw.textbbox((0, 0), text, font=font, stroke_width=stroke_width)
        if bbox[2] - bbox[0] <= max_width and bbox[3] - bbox[1] <= max_height:
            return font
    return load_font(min_size, bold=True)


def wrap_title_lines(draw: ImageDraw.ImageDraw, text: str, font, max_width: int, max_lines: int = 2) -> list[str]:
    chars = list(str(text))
    lines: list[str] = []
    current = ""
    for char in chars:
        candidate = current + char
        if current and draw.textlength(candidate, font=font) > max_width:
            lines.append(current)
            current = char
            if len(lines) == max_lines - 1:
                break
        else:
            current = candidate

    remaining = "".join(chars[sum(len(line) for line in lines) + len(current):])
    if remaining:
        current += remaining
    if current:
        lines.append(current)

    if len(lines) > max_lines:
        lines = lines[:max_lines]

    if lines and draw.textlength(lines[-1], font=font) > max_width:
        line = lines[-1]
        while line and draw.textlength(line + "...", font=font) > max_width:
            line = line[:-1]
        lines[-1] = line + "..." if line else ""
    return lines


def choose_title_layout(draw: ImageDraw.ImageDraw, title: str, max_width: int, max_height: int) -> tuple[PillowFont, list[str]]:
    for size in (12, 11, 10, 9):
        font = load_font(size)
        single = [title]
        bbox = draw.textbbox((0, 0), title, font=font)
        if bbox[2] - bbox[0] <= max_width and bbox[3] - bbox[1] <= max_height:
            return font, single

        lines = wrap_title_lines(draw, title, font, max_width, 2)
        line_heights = [draw.textbbox((0, 0), line, font=font)[3] - draw.textbbox((0, 0), line, font=font)[1] for line in lines]
        if lines and max(line_heights, default=0) * len(lines) + 2 * (len(lines) - 1) <= max_height:
            return font, lines
    font = load_font(9)
    return font, wrap_title_lines(draw, title, font, max_width, 2)


def draw_title_box(draw: ImageDraw.ImageDraw, x: int, y: int, title: str) -> None:
    box = (x + 3, y + JACKET_SIZE, x + 3 + JACKET_SIZE, y + CARD_H - 9)
    draw.rectangle(box, fill=(13, 13, 19))
    max_width = JACKET_SIZE - 8
    max_height = box[3] - box[1] - 5
    font, lines = choose_title_layout(draw, title, max_width, max_height)
    line_boxes = [draw.textbbox((0, 0), line, font=font) for line in lines]
    line_heights = [bbox[3] - bbox[1] for bbox in line_boxes]
    total_height = sum(line_heights) + 2 * max(0, len(lines) - 1)
    cursor_y = box[1] + max(2, (box[3] - box[1] - total_height) // 2)
    for line, bbox, line_height in zip(lines, line_boxes, line_heights):
        line_width = bbox[2] - bbox[0]
        draw.text((box[0] + (JACKET_SIZE - line_width) // 2, cursor_y - bbox[1]), line, fill=WHITE, font=font)
        cursor_y += line_height + 2


def translucent_grade_layer(grade: str) -> Image.Image:
    layer = Image.new("RGBA", (JACKET_SIZE, JACKET_SIZE), (0, 0, 0, 0))
    draw = ImageDraw.Draw(layer)
    for size in range(50, 18, -2):
        font = load_font(size, bold=True)
        bbox = draw.textbbox((0, 0), grade, font=font, stroke_width=1)
        if bbox[2] - bbox[0] <= JACKET_SIZE * 0.94 and bbox[3] - bbox[1] <= JACKET_SIZE * 0.75:
            draw_centered_text(
                draw,
                (0, 3, JACKET_SIZE, JACKET_SIZE - 8),
                grade,
                font,
                fill=(255, 255, 255, 105),
                stroke_width=1,
            )
            return layer
    draw_centered_text(draw, (0, 3, JACKET_SIZE, JACKET_SIZE - 8), grade, load_font(22, bold=True), fill=(255, 255, 255, 105), stroke_width=1)
    return layer


def draw_small_card(canvas: Image.Image, row: BoardRow, x: int, y: int, min_grade: str) -> None:
    draw = ImageDraw.Draw(canvas)
    achieved = grade_at_least(row.get("grade_label"), min_grade)
    jacket = load_jacket(row.get("jacket_path"), dimmed=achieved, size=JACKET_SIZE)
    canvas.paste(jacket, (x + 3, y))

    if achieved and row.get("grade_label"):
        canvas.alpha_composite(translucent_grade_layer(str(row["grade_label"])), (x + 3, y))
    elif not achieved:
        focus_text = focus_score_text(row.get("score"))
        if focus_text:
            score_box = (x + 5, y + 3, x + 3 + JACKET_SIZE - 2, y + JACKET_SIZE - 21)
            score_font = largest_font_for_box(draw, focus_text, score_box, 39 if len(focus_text) <= 4 else 34, 22, stroke_width=2)
            draw_centered_text(
                draw,
                score_box,
                focus_text,
                score_font,
                fill=(255, 236, 89),
                stroke_width=2,
            )

    color = difficulty_color(row["difficulty"])
    draw.rectangle((x + 3, y + JACKET_SIZE - 18, x + 3 + JACKET_SIZE, y + JACKET_SIZE), fill=color)
    diff_text = row["difficulty"].upper()
    diff_font = load_font(11, bold=True)
    diff_bbox = draw.textbbox((0, 0), diff_text, font=diff_font)
    draw.text(
        (x + 3 + (JACKET_SIZE - (diff_bbox[2] - diff_bbox[0])) // 2, y + JACKET_SIZE - 18),
        diff_text,
        fill=WHITE,
        font=diff_font,
    )

    draw_title_box(draw, x, y, row["song_name"])


def render_board(
    level: str,
    min_grade: str,
    output_path: Path,
    constant_source: str = "cn",
    user_id: int = 1,
    player_name: str | None = None,
    unfinished_only: bool = False,
) -> Path:
    constant_source = constant_source.lower()
    if constant_source not in {"cn", "jp"}:
        raise ValueError("constant_source must be 'cn' or 'jp'")
    all_rows = fetch_board_rows(level, constant_source, user_id)
    if not all_rows:
        raise RuntimeError(f"no songs found for level {level}")
    achieved_count = sum(1 for row in all_rows if grade_at_least(row.get("grade_label"), min_grade))
    rows = [
        row
        for row in all_rows
        if not unfinished_only or not grade_at_least(row.get("grade_label"), min_grade)
    ]
    if not rows:
        raise RuntimeError(f"no unfinished songs found for level {level} below {min_grade}")

    groups = group_by_constant(rows)
    cols = max(1, (CANVAS_W - LEFT_GUTTER - SIDE_PAD * 2) // (CARD_W + CARD_GAP))
    group_heights = []
    for _, group_rows in groups:
        row_count = (len(group_rows) + cols - 1) // cols
        group_heights.append(max(118, row_count * CARD_H + (row_count - 1) * ROW_GAP))
    width = CANVAS_W
    height = TOP_H + SIDE_PAD + sum(group_heights) + SECTION_GAP * (len(groups) - 1) + SIDE_PAD
    image = Image.new("RGBA", (width, height), (245, 246, 248, 255))
    background = Image.new("RGB", (width, height), (245, 246, 248))
    draw_background(background)
    image.paste(background.convert("RGBA"), (0, 0))

    draw_header(
        image,
        level,
        min_grade,
        constant_source,
        achieved_count,
        len(all_rows),
        player_name,
        unfinished_only=unfinished_only,
    )

    y = TOP_H + SIDE_PAD
    for (constant, group_rows), group_height in zip(groups, group_heights):
        draw_level_badge(image, 25, y + 8, constant)
        for index, row in enumerate(group_rows):
            col = index % cols
            line = index // cols
            x = LEFT_GUTTER + col * (CARD_W + CARD_GAP)
            card_y = y + line * (CARD_H + ROW_GAP)
            draw_small_card(image, row, x, card_y, min_grade)
        y += group_height + SECTION_GAP

    output_path.parent.mkdir(parents=True, exist_ok=True)
    image.convert("RGB").save(output_path)
    return output_path


class BoardApp(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        config = load_config()
        self.title("CHUNITHM Progress Board")
        self.geometry("1120x860")
        self.output_dir = project_path(config["output_dir"])
        self.level_var = tk.StringVar(value=str(config.get("default_board_level", "")))
        self.grade_var = tk.StringVar(value=config.get("default_min_grade", "SSS"))
        self.constant_source_var = tk.StringVar(value=config.get("default_constant_source", "cn"))
        self.unfinished_only_var = tk.BooleanVar(value=False)
        self.preview: ImageTk.PhotoImage | None = None

        controls = ttk.Frame(self, padding=10)
        controls.pack(fill=tk.X)

        levels = fetch_levels()
        if levels and self.level_var.get() not in levels:
            self.level_var.set(levels[0])

        ttk.Label(controls, text="Level").pack(side=tk.LEFT)
        ttk.Combobox(controls, textvariable=self.level_var, values=levels, width=10, state="readonly").pack(
            side=tk.LEFT, padx=(6, 14)
        )
        ttk.Label(controls, text="Min grade").pack(side=tk.LEFT)
        ttk.Combobox(
            controls,
            textvariable=self.grade_var,
            values=list(GRADE_ORDER.keys()),
            width=10,
            state="readonly",
        ).pack(side=tk.LEFT, padx=(6, 14))
        ttk.Label(controls, text="Constants").pack(side=tk.LEFT)
        ttk.Combobox(
            controls,
            textvariable=self.constant_source_var,
            values=("cn", "jp"),
            width=8,
            state="readonly",
        ).pack(side=tk.LEFT, padx=(6, 14))
        ttk.Button(controls, text="Generate", command=self.generate).pack(side=tk.LEFT)
        ttk.Checkbutton(controls, text="Unfinished only", variable=self.unfinished_only_var).pack(side=tk.LEFT, padx=(14, 0))

        self.status = ttk.Label(self, text="")
        self.status.pack(fill=tk.X, padx=10)
        self.canvas = ttk.Label(self, anchor=tk.CENTER)
        self.canvas.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)

    def generate(self) -> None:
        level = self.level_var.get()
        min_grade = self.grade_var.get()
        constant_source = self.constant_source_var.get()
        suffix = "_unfinished" if self.unfinished_only_var.get() else ""
        output_path = self.output_dir / f"level_{level}_{constant_source}_{min_grade}{suffix}.png"
        try:
            path = render_board(
                level,
                min_grade,
                output_path,
                constant_source,
                unfinished_only=self.unfinished_only_var.get(),
            )
        except Exception as exc:
            self.status.configure(text=str(exc))
            return

        image = Image.open(path)
        image.thumbnail((1060, 730), Image.Resampling.LANCZOS)
        self.preview = ImageTk.PhotoImage(image)
        self.canvas.configure(image=self.preview)
        self.status.configure(text=f"saved: {path}")


def main() -> None:
    BoardApp().mainloop()


if __name__ == "__main__":
    main()
