from __future__ import annotations

from contextlib import contextmanager
from functools import lru_cache

from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from fastsocial.config import settings


class Base(DeclarativeBase):
    pass


@lru_cache(maxsize=1)
def engine() -> Engine:
    url = settings().database_url
    kwargs: dict = {"pool_pre_ping": True}
    if url.startswith("sqlite"):
        kwargs["connect_args"] = {"check_same_thread": False}
    value = create_engine(url, **kwargs)
    if url.startswith("sqlite"):
        event.listen(value, "connect", _sqlite_foreign_keys)
    return value


def _sqlite_foreign_keys(dbapi_connection, _connection_record) -> None:
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.close()


@lru_cache(maxsize=1)
def session_factory() -> sessionmaker[Session]:
    return sessionmaker(bind=engine(), expire_on_commit=False)


@contextmanager
def session_scope():
    session = session_factory()()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def init_db() -> None:
    from fastsocial import models  # noqa: F401

    Base.metadata.create_all(engine())


def reset_db_caches() -> None:
    session_factory.cache_clear()
    engine.cache_clear()
