from __future__ import annotations

from common import PROJECT_ROOT
from auth import ensure_default_admin
from db import connect


def main() -> None:
    sql_path = PROJECT_ROOT / "sql" / "init_tables.sql"
    with sql_path.open("r", encoding="utf-8") as f:
        sql = f.read()

    conn = connect()
    try:
        with conn.cursor() as cur:
            cur.execute(sql)
            cur.execute("ALTER TABLE songs ADD COLUMN IF NOT EXISTS jp_constant NUMERIC(3, 1)")
            cur.execute("ALTER TABLE songs ADD COLUMN IF NOT EXISTS cn_constant NUMERIC(3, 1)")
            cur.execute("UPDATE songs SET jp_constant = constant WHERE jp_constant IS NULL AND constant IS NOT NULL")
        conn.commit()
    finally:
        conn.close()

    ensure_default_admin()
    print(f"initialized tables from {sql_path}")


if __name__ == "__main__":
    main()
