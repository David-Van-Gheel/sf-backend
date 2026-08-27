from collections.abc import Generator

from sqlalchemy import create_engine, event, inspect, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.config import get_settings


class Base(DeclarativeBase):
    """Declarative base for all ORM models."""


def _engine_kwargs(database_url: str) -> dict:
    if not database_url.startswith("sqlite"):
        return {}

    kwargs: dict = {"connect_args": {"check_same_thread": False}}
    if ":memory:" in database_url or "mode=memory" in database_url:
        # A plain in-memory SQLite database lives and dies with its connection.
        # StaticPool keeps a single connection alive so every request — and every
        # thread FastAPI hands work to — sees the same data for the process's lifetime.
        kwargs["poolclass"] = StaticPool
    return kwargs


settings = get_settings()

engine = create_engine(
    settings.database_url,
    echo=settings.sql_echo,
    **_engine_kwargs(settings.database_url),
)

SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


@event.listens_for(Engine, "connect")
def _enable_sqlite_foreign_keys(dbapi_connection, _connection_record) -> None:
    if engine.dialect.name != "sqlite":
        return
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.close()


def init_db() -> None:
    """Create tables and apply serialized, versioned schema migrations."""
    from app import models  # noqa: F401  (register models on Base.metadata)

    with engine.connect() as connection:
        if engine.dialect.name == "sqlite":
            connection.exec_driver_sql("BEGIN IMMEDIATE")
        elif engine.dialect.name == "postgresql":
            connection.execute(text("SELECT pg_advisory_xact_lock(847291)"))

        try:
            Base.metadata.create_all(bind=connection)
            connection.execute(
                text(
                    "CREATE TABLE IF NOT EXISTS schema_migrations "
                    "(version INTEGER PRIMARY KEY)"
                )
            )
            version = connection.execute(
                text("SELECT version FROM schema_migrations ORDER BY version DESC LIMIT 1")
            ).scalar_one_or_none(            )
            if version is None:
                columns = {column["name"] for column in inspect(connection).get_columns("contacts")}
                if "photo_url" not in columns:
                    connection.execute(text("ALTER TABLE contacts ADD COLUMN photo_url TEXT"))
                connection.execute(text("INSERT INTO schema_migrations (version) VALUES (1)"))
            if (version or 1) < 2:
                if engine.dialect.name == "postgresql":
                    connection.execute(text(
                        "CREATE TABLE IF NOT EXISTS addresses ("
                        "id SERIAL PRIMARY KEY, contact_id INTEGER NOT NULL REFERENCES contacts(id) ON DELETE CASCADE, "
                        "type VARCHAR(20) NOT NULL, address VARCHAR(300), city VARCHAR(120), "
                        "state VARCHAR(120), postal_code VARCHAR(20), country VARCHAR(120))"
                    ))
                else:
                    connection.execute(text(
                        "CREATE TABLE IF NOT EXISTS addresses ("
                        "id INTEGER PRIMARY KEY, contact_id INTEGER NOT NULL REFERENCES contacts(id) ON DELETE CASCADE, "
                        "type VARCHAR(20) NOT NULL, address VARCHAR(300), city VARCHAR(120), "
                        "state VARCHAR(120), postal_code VARCHAR(20), country VARCHAR(120))"
                    ))
                connection.execute(text(
                    "INSERT INTO addresses (contact_id, type, address, city, state, postal_code, country) "
                    "SELECT id, 'Other', address, city, state, postal_code, country FROM contacts "
                    "WHERE address IS NOT NULL OR city IS NOT NULL OR state IS NOT NULL "
                    "OR postal_code IS NOT NULL OR country IS NOT NULL"
                ))
                connection.execute(text("INSERT INTO schema_migrations (version) VALUES (2)"))
            connection.commit()
        except BaseException:
            connection.rollback()
            raise


def get_db() -> Generator[Session, None, None]:
    """FastAPI dependency yielding a session that is always closed."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
