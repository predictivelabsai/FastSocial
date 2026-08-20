from __future__ import annotations

import os
import tempfile
from pathlib import Path

import pytest

TEST_ROOT = Path(tempfile.mkdtemp(prefix="fastsocial-tests-"))
os.environ["DATABASE_URL"] = f"sqlite:///{TEST_ROOT / 'test.db'}"
os.environ["MEDIA_LOCAL_DIR"] = str(TEST_ROOT / "media")
os.environ["MEDIA_STORAGE"] = "local"
os.environ["SCHEDULER_ENABLED"] = "0"
os.environ["AUTO_CREATE_SCHEMA"] = "1"
os.environ["APP_ENV"] = "development"
os.environ["APP_SECRET"] = "test-secret-that-is-not-used-in-production"
os.environ["SERVICE_URL"] = "http://testserver"
os.environ["X_CLIENT_ID"] = ""
os.environ["X_CLIENT_SECRET"] = ""
os.environ["LINKEDIN_CLIENT_ID"] = ""
os.environ["LINKEDIN_CLIENT_SECRET"] = ""
os.environ["META_APP_ID"] = ""
os.environ["META_APP_SECRET"] = ""


@pytest.fixture(scope="session", autouse=True)
def test_environment():
    yield TEST_ROOT
