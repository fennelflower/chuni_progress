from __future__ import annotations

from contextlib import contextmanager
from collections.abc import Iterable, Mapping
from typing import Any, Iterator, cast

import psycopg2
import psycopg2.extras

from common import load_config


def connect():
    config = load_config()
    return psycopg2.connect(
        host=config["db_host"],
        port=config["db_port"],
        dbname=config["db_name"],
        user=config["db_user"],
        password=config["db_password"],
    )


def row_to_dict(row: object) -> dict[str, Any]:
    return dict(cast(Mapping[str, Any], row))


def rows_to_dicts(rows: Iterable[object]) -> list[dict[str, Any]]:
    return [row_to_dict(row) for row in rows]


@contextmanager
def cursor(commit: bool = False) -> Iterator[psycopg2.extensions.cursor]:
    conn = connect()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            yield cur
        if commit:
            conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
