from __future__ import annotations

import atexit
import threading
from contextlib import contextmanager
from functools import lru_cache

from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from fastsocial.config import settings


class Base(DeclarativeBase):
    pass


_engines: dict[str, Engine] = {}
_engine_lock = threading.Lock()


def engine() -> Engine:
    """Return one bounded SQLAlchemy engine per database URL."""
    configured = settings()
    url = configured.database_url
    value = _engines.get(url)
    if value is not None:
        return value
    with _engine_lock:
        value = _engines.get(url)
        if value is None:
            kwargs: dict = {"pool_pre_ping": True}
            if url.startswith("sqlite"):
                kwargs["connect_args"] = {"check_same_thread": False}
            else:
                kwargs.update(
                    pool_size=configured.db_pool_size,
                    max_overflow=configured.db_max_overflow,
                    pool_timeout=configured.db_pool_timeout,
                    pool_recycle=configured.db_pool_recycle,
                    connect_args={
                        "application_name": configured.db_application_name
                    },
                )
            value = create_engine(url, **kwargs)
            if url.startswith("sqlite"):
                event.listen(value, "connect", _sqlite_foreign_keys)
            _engines[url] = value
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
    with _engine_lock:
        engines = list(_engines.values())
        _engines.clear()
    session_factory.cache_clear()
    for value in engines:
        value.dispose()


atexit.register(reset_db_caches)
