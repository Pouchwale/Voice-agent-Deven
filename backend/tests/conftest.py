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
os.environ["INBOUND_CDR_SYNC"] = "false"  # never poll Vobiz call records from tests

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest  # noqa: E402

from app.db import Base, engine  # noqa: E402
from app import models  # noqa: E402,F401


@pytest.fixture(autouse=True, scope="session")
def _schema():
    Base.metadata.create_all(engine)
    yield


@pytest.fixture(autouse=True)
def _restore_agent_settings():
    """Every test starts and ends with the same saved agent settings: a test that switches inbound or
    outbound calling off (or edits a greeting) can never leak that into the next test, even if it fails."""
    import copy

    from app.db import SessionLocal
    from app.models import AgentSettings
    from app.services import agent_config

    with SessionLocal() as db:
        row = db.get(AgentSettings, 1)
        saved = copy.deepcopy(row.data) if row is not None else None
    yield
    with SessionLocal() as db:
        row = db.get(AgentSettings, 1)
        if saved is None:
            if row is not None:
                db.delete(row)
        elif row is None:
            db.add(AgentSettings(id=1, data=saved))
        else:
            row.data = saved
        db.commit()
    agent_config._cache = None


@pytest.fixture(autouse=True)
def _offline(monkeypatch):
    """Tests must never reach real providers (the OpenAI warm-up call would otherwise go out)."""
    import httpx

    from app.services import inbound_audio, vobiz
    from app.services import session as session_mod

    async def no_warmup() -> None:
        return None

    async def fake_clip_tts(text, cfg, sample_rate):  # the inbound clip pre-warm runs at every app startup
        return b"\x00\x00" * (sample_rate // 5)

    monkeypatch.setattr(session_mod, "warmup", no_warmup)
    monkeypatch.setattr(inbound_audio, "tts_rest", fake_clip_tts)
    # Vobiz account API (setup checks, call-record back-fill): answered locally, never the network.
    # Tests that need real-looking answers install tests.fakes.FakeVobizAPI on top of this.
    monkeypatch.setattr(vobiz, "_client", lambda timeout: httpx.AsyncClient(
        transport=httpx.MockTransport(lambda r: httpx.Response(599)), timeout=timeout))
    yield
