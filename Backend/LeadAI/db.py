"""
Database access for LeadAI.

There is deliberately no second engine, no second connection pool and no second
`Base`. LeadAI rides the exact same MySQL engine the outbound app configured in
database.py, and registers its tables on the same declarative Base from base.py.
Consequences that matter:

  * one transaction can span outbound tables and LeadAI tables (e.g. link a
    CallSid from `calllogs` to a `leadai_conversations` row atomically);
  * connection-pool tuning already done in database.py applies unchanged;
  * `db.connect_db()` in the outbound startup path creates LeadAI tables too,
    because it iterates Base.metadata.sorted_tables.

`ensure_tables()` exists for the case where LeadAI is registered after the
outbound startup hook has already run: it creates only the missing LeadAI
tables, one at a time with checkfirst, mirroring db.connect_db()'s
failure-tolerant strategy so one pre-existing table can't abort the rest.
"""
from __future__ import annotations

import logging
from collections.abc import Iterator

from sqlalchemy.orm import Session

from base import Base
from database import SessionLocalAdmin, engine_admin

logger = logging.getLogger(__name__)


def get_leadai_db() -> Iterator[Session]:
    """FastAPI dependency yielding a session on the shared engine."""
    db = SessionLocalAdmin()
    try:
        yield db
    finally:
        db.close()


def session() -> Session:
    """Imperative session for background tasks / the call bridge, where there is
    no request to hang a dependency off. Caller owns close()."""
    return SessionLocalAdmin()


def ensure_tables() -> dict:
    """Create any missing LeadAI table. Safe to call repeatedly."""
    from . import models  # noqa: F401  (registers mappers on Base.metadata)

    leadai_names = {t.__tablename__ for t in models.ALL_LEADAI_TABLES}
    created, skipped = [], []
    for table in Base.metadata.sorted_tables:
        if table.name not in leadai_names:
            continue
        try:
            table.create(bind=engine_admin, checkfirst=True)
            created.append(table.name)
        except Exception as exc:  # noqa: BLE001
            skipped.append(table.name)
            logger.warning(
                "[LeadAI] table '%s' create skipped (%s)", table.name, exc.__class__.__name__
            )
    logger.info("[LeadAI] tables ready (ok=%d, skipped=%d)", len(created), len(skipped))
    return {"created": created, "skipped": skipped}


def ensure_columns() -> dict:
    """Add any LeadAI column that exists in the model but not yet in MySQL.

    WHY THIS EXISTS
    ---------------
    `create_all` only ever CREATEs whole tables; it will not touch a table that
    already exists. When a release adds a column to a table that shipped in an
    earlier version (e.g. `LeadCompanySettings.LeadScoreThreshold`), an install
    that already has the table would keep the old shape and every query naming
    the new column would fail with "Unknown column".

    Deliberate constraints, so this can never destroy data:
      * ADD COLUMN only. Never DROP, never MODIFY, never RENAME.
      * Scoped to tables whose name starts with `leadai_`. Outbound tables are
        untouchable.
      * Each statement is independent — one failure is logged and skipped, the
        rest still apply.

    This is not a replacement for Alembic. If you later need destructive or
    ordered migrations, generate them with Alembic and turn this off with
    LEADAI_AUTO_MIGRATE=false; it is here so that additive feature releases
    deploy with no manual DBA step.
    """
    import os

    if os.getenv("LEADAI_AUTO_MIGRATE", "true").strip().lower() in ("0", "false", "no"):
        logger.info("[LeadAI] auto column migration disabled")
        return {"added": [], "skipped": []}

    from sqlalchemy import inspect as sa_inspect
    from sqlalchemy.schema import CreateColumn

    from . import models  # noqa: F401

    inspector = sa_inspect(engine_admin)
    try:
        existing_tables = set(inspector.get_table_names())
    except Exception as exc:  # noqa: BLE001
        logger.warning("[LeadAI] could not inspect schema: %s", exc)
        return {"added": [], "skipped": []}

    added, skipped = [], []
    for table in Base.metadata.sorted_tables:
        if not table.name.startswith("leadai_") or table.name not in existing_tables:
            continue
        try:
            present = {c["name"] for c in inspector.get_columns(table.name)}
        except Exception:  # noqa: BLE001
            continue

        for column in table.columns:
            if column.name in present:
                continue
            # A NOT NULL column cannot be added to a populated table without a
            # default, so give it one derived from the model.
            ddl = CreateColumn(column).compile(bind=engine_admin).string
            if not column.nullable and column.default is None and column.server_default is None:
                ddl = ddl.replace(" NOT NULL", "") + " NULL"
            statement = f"ALTER TABLE `{table.name}` ADD COLUMN {ddl}"
            try:
                with engine_admin.begin() as conn:
                    from sqlalchemy import text as sa_text

                    conn.execute(sa_text(statement))
                added.append(f"{table.name}.{column.name}")
            except Exception as exc:  # noqa: BLE001
                skipped.append(f"{table.name}.{column.name}")
                logger.warning(
                    "[LeadAI] column %s.%s not added (%s)",
                    table.name, column.name, exc.__class__.__name__,
                )

    if added:
        logger.info("[LeadAI] columns added: %s", ", ".join(added))
    return {"added": added, "skipped": skipped}
