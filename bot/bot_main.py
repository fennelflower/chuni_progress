from __future__ import annotations

import sys
from pathlib import Path

from flask import Flask, jsonify, request

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_DIR = PROJECT_ROOT / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from common import load_config
from handler import handle_event


app = Flask(__name__)


@app.post("/")
def webhook():
    event = request.get_json(force=True, silent=True) or {}
    try:
        result = handle_event(event)
    except Exception as exc:
        app.logger.exception("webhook failed")
        result = f"error: {exc}"
    app.logger.info("event handled: %s", result)
    return jsonify({"status": result}), 200


@app.get("/health")
def health():
    return jsonify({"ok": True})


def main() -> None:
    config = load_config()
    app.run(host=config.get("bot_host", "127.0.0.1"), port=int(config.get("bot_port", 8088)))


if __name__ == "__main__":
    main()
