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
os.environ["INBOUND_CDR_SYNC"] = "false"  # never poll provider call records from tests
# The shared phone tests drive Vobiz (the fake account API speaks Vobiz); tests/test_plivo.py switches
# the provider to Plivo per test.
os.environ["TELEPHONY_PROVIDER"] = "vobiz"
# Simulated calls deliver transcripts without real audio, so the loudness-based background-voice filter
# would ignore every line: off by default here, tests/test_background_voice.py switches it on with real audio.
os.environ["BACKGROUND_VOICE_FILTER"] = "false"
os.environ["PLIVO_AUTH_ID"] = "MAPLIVOTEST"
os.environ["PLIVO_AUTH_TOKEN"] = "plivo-test-token"
os.environ["PLIVO_FROM_NUMBER"] = "+918000000001"
# Settings also read backend/.env: pin the model so the tests don't change when the live calls' model does.
os.environ["OPENAI_MODEL"] = "gpt-4o-mini"

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

    from app.services import inbound_audio, plivo, vobiz
    from app.services import session as session_mod

    async def no_warmup() -> None:
        return None

    async def fake_clip_tts(text, cfg, sample_rate):  # the inbound clip pre-warm runs at every app startup
        return b"\x00\x00" * (sample_rate // 5)

    monkeypatch.setattr(session_mod, "warmup", no_warmup)
    # Scripted conversations sync on "the LLM was called N times"; an early-started reply (fast_replies)
    # would satisfy that a moment before the real reply. Off here; tests/test_humanize_live.py covers it.
    monkeypatch.setattr(session_mod.CallSession, "_maybe_prefetch", lambda self: None)
    # The call-review agent must never reach OpenAI from tests: no automatic reviews, and any review a test
    # starts fails fast unless the test installs a fake model (tests/test_review_agent.py).
    from app.services.review import agent as review_agent

    async def no_auto_review() -> None:
        return None

    async def offline_model(model, messages, max_out):
        raise review_agent.ReviewError("no real OpenAI from tests")

    monkeypatch.setattr(review_agent, "maybe_auto_run", no_auto_review)
    monkeypatch.setattr(review_agent, "_ask_json", offline_model)
    monkeypatch.setattr(inbound_audio, "tts_rest", fake_clip_tts)
    # The checks made before a call is dialed (services/preflight.py) never reach the voice provider or the
    # public link from tests: both answer "fine" here, and nothing learnt in one test carries into the next.
    # tests/test_call_preflight.py installs its own answers.
    from app.services import preflight

    async def voice_fine() -> int:
        return 200

    async def link_fine(url: str) -> tuple[int, str]:
        return 200, ""

    preflight.reset()
    monkeypatch.setattr(preflight, "_voice_status", voice_fine)
    monkeypatch.setattr(preflight, "_link_answer", link_fine)
    # Vobiz account API (setup checks, call-record back-fill): answered locally, never the network.
    # Tests that need real-looking answers install tests.fakes.FakeVobizAPI on top of this.
    monkeypatch.setattr(vobiz, "_client", lambda timeout: httpx.AsyncClient(
        transport=httpx.MockTransport(lambda r: httpx.Response(599)), timeout=timeout))
    monkeypatch.setattr(plivo, "_client", lambda timeout: httpx.AsyncClient(
        transport=httpx.MockTransport(lambda r: httpx.Response(599)), timeout=timeout))
    yield
