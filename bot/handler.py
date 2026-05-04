from __future__ import annotations

import sys
import time
import shlex
import re
import hashlib
from urllib.parse import unquote, urlparse
from pathlib import Path
from typing import Any

import requests

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_DIR = PROJECT_ROOT / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from common import GRADE_ORDER, load_config, project_path
from auth import GROUP_ADMIN, GROUP_HONORED, create_user, bind_login, get_user_by_qq, logout_by_qq, set_user_group
from common import grade_from_score, normalize_difficulty
from db import cursor
from generate_board import render_board
from init_db import main as init_db
from upload_score import import_scores


ZH_HELP = "\u4e2d\u4e8c\u5e2e\u52a9"
ZH_UPLOAD = "\u4e2d\u4e8c\u4e0a\u4f20"
ZH_BOARD = "\u4e2d\u4e8c\u7b49\u7ea7\u5b8c\u6210\u8868"
ZH_UNFINISHED_BOARD_NAMES = {
    "\u4e2d\u4e8c\u672a\u5b8c\u6210\u8868",
    "\u4e2d\u4e8c\u7b49\u7ea7\u672a\u5b8c\u6210\u8868",
    "\u4e2d\u4e8c\u672a\u5b8c\u6210\u8fdb\u5ea6\u8868",
}
ZH_REGISTER = "\u6ce8\u518c"
ZH_LOGIN = "\u767b\u5f55"
ZH_LOGOUT = "\u767b\u51fa"
ZH_WHOAMI = "\u6211\u662f\u8c01"
ZH_GRANT = "\u6388\u6743"

PENDING_UPLOADS: dict[tuple[str, int, int], float] = {}
UPLOAD_TTL_SECONDS = 300


def plain_text(message: Any) -> str:
    if isinstance(message, str):
        return message.strip()
    if isinstance(message, list):
        parts = []
        for segment in message:
            if segment.get("type") == "text":
                parts.append(segment.get("data", {}).get("text", ""))
        return "".join(parts).strip()
    return ""


def strip_bot_at(message: Any, self_id: Any) -> tuple[str, bool]:
    if not isinstance(message, list):
        text = plain_text(message)
        self_id_text = str(self_id or "")
        cq_at = re.compile(r"\[CQ:at,qq=([^\]]+)\]\s*")
        match = cq_at.match(text)
        if match:
            target = match.group(1)
            if not self_id_text or target == self_id_text or target.lower() == "all":
                return text[match.end():].strip(), True
        return text, False

    parts = []
    mentioned = False
    self_id_text = str(self_id or "")
    for segment in message:
        segment_type = segment.get("type")
        data = segment.get("data", {})
        if segment_type == "at":
            target = str(data.get("qq") or data.get("user_id") or "")
            if target == self_id_text or target.lower() == "all":
                mentioned = True
            continue
        if segment_type == "text":
            parts.append(data.get("text", ""))
    return "".join(parts).strip(), mentioned


def is_group_event(event: dict[str, Any]) -> bool:
    return event.get("message_type") == "group" or bool(event.get("group_id"))


def split_command(text: str) -> tuple[str, list[str]]:
    try:
        pieces = shlex.split(text.strip())
    except ValueError:
        pieces = text.strip().split()
    if not pieces:
        return "", []
    return pieces[0], pieces[1:]


def command_matches(command: str, ascii_names: tuple[str, ...], chinese_name: str) -> bool:
    normalized = command.lstrip("/\\")
    return command in ascii_names or normalized == chinese_name


def command_matches_any(command: str, ascii_names: tuple[str, ...], chinese_names: set[str]) -> bool:
    normalized = command.lstrip("/\\")
    return command in ascii_names or normalized in chinese_names


def require_user(event: dict[str, Any]) -> dict[str, Any] | None:
    return get_user_by_qq(int(event.get("user_id") or 0))


def require_groups(event: dict[str, Any], groups: set[str]) -> dict[str, Any] | None:
    user = require_user(event)
    if not user:
        reply_text(event, "Please register or login first: /register account password")
        return None
    if user["user_group"] not in groups:
        reply_text(event, f"Permission denied. Required group: {', '.join(sorted(groups))}")
        return None
    return user


def session_key(event: dict[str, Any]) -> tuple[str, int, int]:
    message_type = event.get("message_type") or ("group" if event.get("group_id") else "private")
    group_id = int(event.get("group_id") or 0)
    user_id = int(event.get("user_id") or 0)
    return str(message_type), group_id, user_id


def mark_pending_upload(event: dict[str, Any]) -> None:
    PENDING_UPLOADS[session_key(event)] = time.time()


def pop_pending_upload(event: dict[str, Any]) -> bool:
    key = session_key(event)
    requested_at = PENDING_UPLOADS.get(key)
    if requested_at is None and event.get("group_id"):
        group_id = int(event.get("group_id") or 0)
        candidates = [
            candidate
            for candidate in PENDING_UPLOADS
            if candidate[0] == "group" and candidate[1] == group_id
        ]
        if candidates:
            key = max(candidates, key=lambda candidate: PENDING_UPLOADS[candidate])
            requested_at = PENDING_UPLOADS.get(key)

    if requested_at is None:
        return False
    if time.time() - requested_at > UPLOAD_TTL_SECONDS:
        PENDING_UPLOADS.pop(key, None)
        return False
    PENDING_UPLOADS.pop(key, None)
    return True


def find_file_info(event: dict[str, Any]) -> dict[str, Any] | None:
    message = event.get("message")
    if isinstance(message, list):
        for segment in message:
            if segment.get("type") == "file":
                return segment.get("data", {})

    if event.get("post_type") == "notice" and event.get("notice_type") == "group_upload":
        return event.get("file", {})

    return None


def napcat_headers(config: dict[str, Any]) -> dict[str, str]:
    token = config.get("napcat_access_token", "")
    return {"Authorization": f"Bearer {token}"} if token else {}


def call_napcat(action: str, payload: dict[str, Any]) -> dict[str, Any]:
    config = load_config()
    url = f"{config['napcat_api_url'].rstrip('/')}/{action}"
    response = requests.post(url, json=payload, headers=napcat_headers(config), timeout=20)
    if response.status_code >= 400:
        raise RuntimeError(f"NapCat API {action} failed: HTTP {response.status_code} {response.text}")
    try:
        return response.json()
    except ValueError:
        return {}


def reply_text(event: dict[str, Any], text: str) -> None:
    if event.get("message_type") == "group" or event.get("group_id"):
        call_napcat("send_group_msg", {"group_id": event["group_id"], "message": text})
    elif event.get("user_id"):
        call_napcat("send_private_msg", {"user_id": event["user_id"], "message": text})


def reply_image(event: dict[str, Any], image_path: Path) -> None:
    path = image_path.resolve().as_posix()
    message = [{"type": "image", "data": {"file": f"file:///{path}"}}]
    if event.get("message_type") == "group" or event.get("group_id"):
        call_napcat("send_group_msg", {"group_id": event["group_id"], "message": message})
    elif event.get("user_id"):
        call_napcat("send_private_msg", {"user_id": event["user_id"], "message": message})


def resolve_file_info(file_info: dict[str, Any]) -> dict[str, Any]:
    if file_info.get("url") or file_info.get("path"):
        return file_info

    file_id = (
        file_info.get("file_id")
        or file_info.get("id")
        or file_info.get("fid")
        or file_info.get("file")
    )
    if not file_id:
        return file_info

    response = call_napcat("get_file", {"file_id": file_id})
    data = response.get("data") if isinstance(response, dict) else None
    if isinstance(data, dict):
        merged = dict(file_info)
        merged.update(data)
        return merged
    return file_info


def download_csv(file_info: dict[str, Any]) -> Path:
    file_info = resolve_file_info(file_info)
    raw_dir = project_path("data/raw/bot_uploads")
    raw_dir.mkdir(parents=True, exist_ok=True)

    name = file_info.get("name") or file_info.get("file_name") or f"upload_{int(time.time())}.csv"
    if not str(name).lower().endswith(".csv"):
        name = f"{name}.csv"
    target = raw_dir / Path(str(name)).name

    url = file_info.get("url")
    local_path = file_info.get("path") or file_info.get("file")

    if url:
        url_text = str(url)
        if len(url_text) >= 3 and url_text[1] == ":" and url_text[2] in {"\\", "/"}:
            source_path = Path(url_text)
            if source_path.exists():
                target.write_bytes(source_path.read_bytes())
                return target

        parsed = urlparse(url_text)
        if parsed.scheme in {"", "file"}:
            candidate = unquote(parsed.path if parsed.scheme == "file" else url_text)
            if candidate.startswith("/") and len(candidate) > 3 and candidate[2] == ":":
                candidate = candidate[1:]
            source_path = Path(candidate)
            if source_path.exists():
                target.write_bytes(source_path.read_bytes())
                return target
        response = requests.get(url, timeout=60)
        response.raise_for_status()
        target.write_bytes(response.content)
        return target

    if local_path and Path(local_path).exists():
        target.write_bytes(Path(local_path).read_bytes())
        return target

    raise RuntimeError(f"CSV file url/path was not found in the NapCat event. keys={sorted(file_info.keys())}")


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def get_user_cache(user_id: int) -> dict[str, Any] | None:
    with cursor() as cur:
        cur.execute(
            """
            SELECT user_id, csv_hash, board_cache_key, board_image_path
            FROM user_cache
            WHERE user_id = %s
            """,
            (user_id,),
        )
        row = cur.fetchone()
        return dict(row) if row else None


def upsert_user_cache(user_id: int, csv_hash: str | None = None, board_cache_key: str | None = None, board_image_path: str | None = None) -> None:
    with cursor(commit=True) as cur:
        cur.execute(
            """
            INSERT INTO user_cache (user_id, csv_hash, board_cache_key, board_image_path, updated_at)
            VALUES (%s, %s, %s, %s, CURRENT_TIMESTAMP)
            ON CONFLICT (user_id)
            DO UPDATE SET
                csv_hash = COALESCE(EXCLUDED.csv_hash, user_cache.csv_hash),
                board_cache_key = EXCLUDED.board_cache_key,
                board_image_path = EXCLUDED.board_image_path,
                updated_at = CURRENT_TIMESTAMP
            """,
            (user_id, csv_hash, board_cache_key, board_image_path),
        )


def invalidate_board_cache(user_id: int) -> None:
    cache = get_user_cache(user_id)
    old_path = Path(cache["board_image_path"]) if cache and cache.get("board_image_path") else None
    if old_path and old_path.exists():
        old_path.unlink(missing_ok=True)
    upsert_user_cache(user_id, board_cache_key=None, board_image_path=None)


def handle_upload(event: dict[str, Any]) -> str:
    user = require_groups(event, {GROUP_HONORED, GROUP_ADMIN})
    if not user:
        return "permission_denied"

    file_info = find_file_info(event)
    if not file_info:
        return "Upload mode enabled. Please send the CSV file within 5 minutes."

    csv_path = download_csv(file_info)
    csv_hash = file_sha256(csv_path)
    cache = get_user_cache(user["id"])
    if cache and cache.get("csv_hash") == csv_hash:
        return "CSV is unchanged. Database import skipped; existing scores will be used."

    reply_text(event, f"CSV received: {csv_path.name}. Initializing tables and importing scores...")
    init_db()
    result = import_scores(csv_path, user_id=user["id"])
    invalidate_board_cache(user["id"])
    upsert_user_cache(user["id"], csv_hash=csv_hash, board_cache_key=None, board_image_path=None)
    return (
        "Import completed: "
        f"source={result.source_format}, "
        f"{result.score_records} score records, "
        f"{result.accepted_rows} accepted rows, "
        f"{result.skipped_rows} skipped rows, "
        f"{result.total_rows} total rows."
    )


def handle_board(event: dict[str, Any], args: list[str], unfinished_only: bool = False) -> None:
    user = require_user(event)
    if not user:
        reply_text(event, "Please register or login first: /register account password")
        return

    config = load_config()
    level = args[0] if len(args) >= 1 else str(config.get("default_board_level", "15"))
    min_grade = (args[1] if len(args) >= 2 else config.get("default_min_grade", "SSS")).upper()
    constant_source = (args[2] if len(args) >= 3 else config.get("default_constant_source", "cn")).lower()

    if min_grade not in GRADE_ORDER:
        reply_text(event, f"Unknown grade: {min_grade}")
        return
    if constant_source not in {"cn", "jp"}:
        reply_text(event, "Constant source must be cn or jp.")
        return

    output_dir = project_path(config["output_dir"]) / "bot"
    csv_hash = (get_user_cache(user["id"]) or {}).get("csv_hash") or "manual"
    mode = "unfinished" if unfinished_only else "full"
    board_cache_key = f"{mode}:{csv_hash}:{level}:{min_grade}:{constant_source}"
    cache = get_user_cache(user["id"])
    if cache and cache.get("board_cache_key") == board_cache_key and cache.get("board_image_path"):
        cached_path = Path(cache["board_image_path"])
        if cached_path.exists():
            reply_image(event, cached_path)
            return

    if cache and cache.get("board_image_path"):
        old_path = Path(cache["board_image_path"])
        if old_path.exists():
            old_path.unlink(missing_ok=True)

    output_path = output_dir / f"user_{user['id']}_board.png"
    path = render_board(
        level,
        min_grade,
        output_path,
        constant_source,
        user_id=user["id"],
        player_name=user["account"],
        unfinished_only=unfinished_only,
    )
    upsert_user_cache(user["id"], board_cache_key=board_cache_key, board_image_path=str(path))
    reply_image(event, path)


def handle_register(event: dict[str, Any], args: list[str]) -> str:
    if len(args) < 2:
        return "Usage: /register account password"
    user = create_user(args[0], args[1], int(event.get("user_id") or 0))
    return f"Registered account {user['account']} as {user['user_group']} with user_id={user['id']}."


def handle_login(event: dict[str, Any], args: list[str]) -> str:
    if len(args) < 2:
        return "Usage: /login account password"
    user = bind_login(args[0], args[1], int(event.get("user_id") or 0))
    if not user:
        return "Login failed: bad account or password."
    return f"Logged in as {user['account']} ({user['user_group']}), user_id={user['id']}."


def handle_logout(event: dict[str, Any]) -> str:
    user = logout_by_qq(int(event.get("user_id") or 0))
    if not user:
        return "No account is currently bound to this QQ."
    return f"Logged out from {user['account']}."


def handle_whoami(event: dict[str, Any]) -> str:
    user = require_user(event)
    if not user:
        return "No account is currently bound to this QQ. Use /register account password or /login account password."
    return (
        "Current account:\n"
        f"account: {user['account']}\n"
        f"user_id: {user['id']}\n"
        f"group: {user['user_group']}\n"
        f"qq_user_id: {user.get('qq_user_id') or ''}"
    )


def handle_grant(event: dict[str, Any], args: list[str]) -> str:
    admin = require_groups(event, {GROUP_ADMIN})
    if not admin:
        return "permission_denied"
    if len(args) < 2:
        return "Usage: /grant account honored_users|normal_users"
    target_group = args[1]
    if target_group not in {"honored_users", "normal_users"}:
        return "Admin can only set honored_users or normal_users."
    user = set_user_group(args[0], target_group)
    if not user:
        return f"Account not found: {args[0]}"
    return f"Updated {user['account']} to {user['user_group']}."


def handle_upsert(event: dict[str, Any], args: list[str]) -> str:
    user = require_user(event)
    if not user:
        return "Please register or login first: /register account password"
    if len(args) < 3:
        return 'Usage: /upsert "song name" master 1009000'

    score = int(args[-1])
    difficulty = normalize_difficulty(args[-2])
    song_name = " ".join(args[:-2]).strip()
    if not song_name:
        return 'Usage: /upsert "song name" master 1009000'
    grade = grade_from_score(score)

    with cursor(commit=True) as cur:
        cur.execute(
            """
            INSERT INTO scores (user_id, song_name, difficulty, score, grade_label, updated_at)
            VALUES (%s, %s, %s, %s, %s, CURRENT_TIMESTAMP)
            ON CONFLICT (user_id, song_name, difficulty)
            DO UPDATE SET
                score = EXCLUDED.score,
                grade_label = EXCLUDED.grade_label,
                updated_at = CURRENT_TIMESTAMP
            """,
            (user["id"], song_name, difficulty, score, grade),
        )
    invalidate_board_cache(user["id"])
    return f"Updated {song_name} [{difficulty}] = {score} ({grade})."


def help_text(event: dict[str, Any]) -> str:
    user = require_user(event)
    lines = [
        "Available commands:",
        "/help - show commands available to your account",
        "/register account password - create a normal_users account and bind it to this QQ",
        "/login account password - bind this QQ to an existing account",
        "/logout - unbind this QQ from the current account",
        "/whoami - show the account currently bound to this QQ",
    ]

    if not user:
        lines.append("Register or login first to use score commands.")
        return "\n".join(lines)

    lines.append(f"Current account: {user['account']} ({user['user_group']}), user_id={user['id']}")
    lines.extend(
        [
            '/upsert "song name" master 1009000 - update one score manually',
            "/chuni_board 15 SSS cn - generate your progress image using CN constants",
            "/chuni_board 15+ SSS+ jp - generate your progress image using JP constants",
            "/chuni_unfinished 15 SSS cn - generate only songs below the target grade",
            "/\u4e2d\u4e8c\u7b49\u7ea7\u5b8c\u6210\u8868 15 SSS cn",
            "/\u4e2d\u4e8c\u672a\u5b8c\u6210\u8868 15 SSS cn",
            "/\u4e2d\u4e8c\u7b49\u7ea7\u672a\u5b8c\u6210\u8868 15 SSS cn",
        ]
    )

    if user["user_group"] in {GROUP_HONORED, GROUP_ADMIN}:
        lines.extend(
            [
                "/chuni_upload - enable CSV upload mode for 5 minutes",
                "/\u4e2d\u4e8c\u4e0a\u4f20",
            ]
        )

    if user["user_group"] == GROUP_ADMIN:
        lines.append("/grant account honored_users|normal_users - change another account group")

    return "\n".join(lines)


def handle_event(event: dict[str, Any]) -> str:
    if is_group_event(event):
        text, mentioned = strip_bot_at(event.get("message"), event.get("self_id"))
        if not mentioned and not find_file_info(event):
            return "ignored_group_without_at"
    else:
        text = plain_text(event.get("message"))
    command, args = split_command(text)
    file_info = find_file_info(event)

    try:
        if command_matches(command, ("/help", "\\help", "/chuni_help", "\\chuni_help"), ZH_HELP):
            reply_text(event, help_text(event))
            return "help"

        if command_matches(command, ("/register", "\\register"), ZH_REGISTER):
            result = handle_register(event, args)
            reply_text(event, result)
            return result

        if command_matches(command, ("/login", "\\login"), ZH_LOGIN):
            result = handle_login(event, args)
            reply_text(event, result)
            return result

        if command_matches(command, ("/logout", "\\logout"), ZH_LOGOUT):
            result = handle_logout(event)
            reply_text(event, result)
            return result

        if command_matches(command, ("/whoami", "\\whoami"), ZH_WHOAMI):
            result = handle_whoami(event)
            reply_text(event, result)
            return result

        if command_matches(command, ("/grant", "\\grant"), ZH_GRANT):
            result = handle_grant(event, args)
            reply_text(event, result)
            return result

        if command in {"/upsert", "\\upsert"}:
            result = handle_upsert(event, args)
            reply_text(event, result)
            return result

        if command_matches(command, ("/chuni_upload", "\\chuni_upload"), ZH_UPLOAD):
            if not require_groups(event, {GROUP_HONORED, GROUP_ADMIN}):
                return "permission_denied"
            if file_info:
                result = handle_upload(event)
            else:
                mark_pending_upload(event)
                result = handle_upload(event)
            reply_text(event, result)
            return result

        if file_info:
            if not pop_pending_upload(event):
                return "ignored_file_without_upload_command"
            result = handle_upload(event)
            reply_text(event, result)
            return result

        if command_matches(command, ("/chuni_board", "\\chuni_board"), ZH_BOARD):
            handle_board(event, args)
            return "ok"

        if command_matches_any(command, ("/chuni_unfinished", "\\chuni_unfinished"), ZH_UNFINISHED_BOARD_NAMES):
            handle_board(event, args, unfinished_only=True)
            return "ok"

        return "ignored"
    except Exception as exc:
        print(f"bot handler error: {exc}", file=sys.stderr)
        try:
            reply_text(event, f"Command failed: {exc}")
        except Exception as reply_exc:
            print(f"bot reply error: {reply_exc}", file=sys.stderr)
        return f"error: {exc}"
