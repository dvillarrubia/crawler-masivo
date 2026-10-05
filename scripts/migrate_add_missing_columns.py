"""
Add columns present in SQLAlchemy models but missing from the live DB.

Use case: schema evolved without alembic; init_db.py only runs create_all,
which creates new tables but never adds columns to existing tables.

This script inspects every Table in shared.models metadata, compares to
the live DB schema, and issues ALTER TABLE ... ADD COLUMN for each gap.
Read-only safe: never drops or alters existing columns.
"""
from __future__ import annotations

import os
import sys

_PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

from sqlalchemy import inspect, text
from sqlalchemy.schema import CreateColumn

from shared.database import engine
from shared import models  # noqa: F401  -- ensure all models register on Base.metadata
from shared.models import Base


def main() -> None:
    insp = inspect(engine)
    existing_tables = set(insp.get_table_names())

    added_total = 0
    missing_tables: list[str] = []

    with engine.begin() as conn:
        for table in Base.metadata.sorted_tables:
            if table.name not in existing_tables:
                missing_tables.append(table.name)
                continue

            db_cols = {c["name"] for c in insp.get_columns(table.name)}
            for col in table.columns:
                if col.name in db_cols:
                    continue
                # Render: column_name TYPE [NULL/NOT NULL] [DEFAULT ...]
                ddl = str(CreateColumn(col).compile(engine))
                stmt = f'ALTER TABLE "{table.name}" ADD COLUMN {ddl}'
                print(f"  + {table.name}.{col.name}  ::  {ddl}")
                conn.execute(text(stmt))
                added_total += 1

    print(f"\nDone. {added_total} column(s) added.")
    if missing_tables:
        print(f"Tables missing entirely (not handled here): {missing_tables}")


if __name__ == "__main__":
    main()
