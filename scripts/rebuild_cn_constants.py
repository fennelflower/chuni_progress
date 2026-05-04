from __future__ import annotations

from db import cursor
from upload_score import import_scores
from common import load_config, project_path


def main() -> None:
    config = load_config()
    with cursor(commit=True) as cur:
        cur.execute("UPDATE songs SET cn_constant = NULL")

    result = import_scores(project_path(config["score_csv_path"]))
    print(
        "rebuilt cn constants from scores: "
        f"{result.score_records} score records, "
        f"{result.accepted_rows} accepted rows, "
        f"{result.skipped_rows} skipped rows"
    )


if __name__ == "__main__":
    main()
