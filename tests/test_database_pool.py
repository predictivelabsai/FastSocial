"""Process-wide database pool ownership."""

from types import SimpleNamespace

import fastsocial.db as database


def test_engine_is_singleton_per_url(monkeypatch):
    created = []

    class FakeEngine:
        def dispose(self):
            pass

    def fake_create_engine(url, **kwargs):
        created.append((url, kwargs))
        return FakeEngine()

    configured = SimpleNamespace(
        database_url="postgresql+psycopg://unused/fastsocial",
        db_pool_size=3,
        db_max_overflow=2,
        db_pool_timeout=10,
        db_pool_recycle=1800,
        db_application_name="fastsocial",
    )
    database.reset_db_caches()
    monkeypatch.setattr(database, "settings", lambda: configured)
    monkeypatch.setattr(database, "create_engine", fake_create_engine)

    assert database.engine() is database.engine()
    assert len(created) == 1
    assert created[0][1]["pool_size"] == 3
    assert created[0][1]["max_overflow"] == 2
    database.reset_db_caches()
