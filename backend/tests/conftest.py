"""Test setup: isolated SQLite DB + fake provider keys, set BEFORE the app is imported."""
import os
import sys
import tempfile
from pathlib import Path

_tmp = Path(tempfile.mkdtemp(prefix="va_test_"))
os.environ["DATABASE_URL"] = f"sqlite:///{(_tmp / 'test.db').as_posix()}"
os.environ["RECORDINGS_DIR"] = str(_tmp / "recordings")
os.environ["OPENAI_API_KEY"] = "test-openai"
os.environ["SARVAM_API_KEY"] = "test-sarvam"
os.environ["VOBIZ_AUTH_ID"] = "MA_TEST"
os.environ["VOBIZ_AUTH_TOKEN"] = "test-token"
os.environ["VOBIZ_FROM_NUMBER"] = "+918000000000"
os.environ["PUBLIC_BASE_URL"] = "https://agent.example.com"

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest  # noqa: E402

from app.db import Base, engine  # noqa: E402
from app import models  # noqa: E402,F401


@pytest.fixture(autouse=True, scope="session")
def _schema():
    Base.metadata.create_all(engine)
    yield


@pytest.fixture(autouse=True)
def _offline(monkeypatch):
    """Tests must never reach real providers (the OpenAI warm-up call would otherwise go out)."""
    from app.services import session as session_mod

    async def no_warmup() -> None:
        return None

    monkeypatch.setattr(session_mod, "warmup", no_warmup)
    yield
