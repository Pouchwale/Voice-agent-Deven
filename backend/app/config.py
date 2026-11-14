"""App configuration loaded from environment variables / backend/.env."""
from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

BASE_DIR = Path(__file__).resolve().parent.parent  # backend/
DATA_DIR = BASE_DIR / "data"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=str(BASE_DIR / ".env"), env_file_encoding="utf-8", extra="ignore")

    # --- App ---
    app_name: str = "Voice Agent"
    database_url: str = f"sqlite:///{(DATA_DIR / 'voice_agent.db').as_posix()}"
    recordings_dir: Path = DATA_DIR / "recordings"
    cors_origins: str = "http://localhost:3100,http://127.0.0.1:3100"
    # Public HTTPS URL of THIS backend (ngrok / cloudflared / your domain). Vobiz calls it.
    public_base_url: str = "http://localhost:8100"
    timezone: str = "Asia/Kolkata"

    # --- Security ---
    # Password for dashboard access from anywhere other than this computer (tunnel, LAN, server).
    # Empty = remote dashboard access is refused; local use (localhost) needs no login.
    dashboard_password: str = ""
    require_login_local: bool = False   # True = ask for the password even on localhost
    allow_international: bool = False   # False = only +91 numbers can be stored/dialed (toll-fraud guard)

    # --- LLM brain (OpenAI) ---
    openai_api_key: str = ""
    openai_model: str = "gpt-4o-mini"
    llm_max_tokens: int = 120
    llm_temperature: float = 0.6
    # Post-call summary/scoring needs a bigger budget than a spoken reply (JSON output).
    analysis_max_tokens: int = 900

    # --- Sarvam (STT + TTS) ---
    sarvam_api_key: str = ""
    sarvam_base_url: str = "https://api.sarvam.ai"
    sarvam_ws_url: str = "wss://api.sarvam.ai"
    # Saaras v3 realtime streaming (true partial transcripts + server VAD), mode "codemix" =
    # English words in English letters, Hindi in Devanagari — ideal for Hinglish.
    sarvam_stt_model: str = "saaras:v3-realtime"
    sarvam_stt_mode: str = "codemix"
    sarvam_stt_stream_type: str = "fast"
    # End-of-turn pause for the server VAD. 350 ms = fast replies (client's choice); measured with real
    # Sarvam: final transcript ~0.4 s after the customer stops. 250 ms split sentences mid-way.
    sarvam_stt_silence_ms: int = 350
    sarvam_tts_model: str = "bulbul:v3"

    # --- Vobiz (telephony) ---
    vobiz_auth_id: str = ""
    vobiz_auth_token: str = ""
    vobiz_base_url: str = "https://api.vobiz.ai/api/v1"
    vobiz_from_number: str = ""
    vobiz_record_calls: bool = True
    # Inbound webhook URLs carry a secret derived from VOBIZ_AUTH_TOKEN and this salt. Change the salt
    # (e.g. inbound-v2) to revoke a leaked inbound URL, then click "Connect inbound on Vobiz" again.
    inbound_token_salt: str = "inbound-v1"
    # Back-fill inbound calls that rang while this backend/tunnel was offline (from Vobiz call records).
    inbound_cdr_sync: bool = True

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    @property
    def public_ws_base(self) -> str:
        base = self.public_base_url.rstrip("/")
        if base.startswith("https://"):
            return "wss://" + base[len("https://"):]
        if base.startswith("http://"):
            return "ws://" + base[len("http://"):]
        return base

    @property
    def openai_ready(self) -> bool:
        return bool(self.openai_api_key)

    @property
    def sarvam_ready(self) -> bool:
        return bool(self.sarvam_api_key)

    @property
    def vobiz_ready(self) -> bool:
        return bool(self.vobiz_auth_id and self.vobiz_auth_token and self.vobiz_from_number)


@lru_cache
def get_settings() -> Settings:
    s = Settings()
    s.recordings_dir.mkdir(parents=True, exist_ok=True)
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    return s
