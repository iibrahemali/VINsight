from pathlib import Path
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict

REPO_ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=REPO_ROOT / ".env", extra="ignore")

    vinsight_provider: Literal["gemini", "claude", "groq"] = "gemini"
    # Takes over for the rest of a conversation when the primary fails. "none" disables it.
    vinsight_backup_provider: Literal["gemini", "groq", "claude", "none"] = "groq"
    vinsight_max_turns: int = 12

    # Gemini: models are tried in order; later ones are fallbacks when earlier ones are overloaded.
    gemini_api_key: str | None = None
    # 3.5 Flash first: on the free tier the newer Flash models are often overloaded or out of quota.
    gemini_models: list[str] = ["gemini-3.5-flash", "gemini-3.8-flash"]
    gemini_timeout_s: float = 30.0  # max wait for the first streamed chunk before trying the next model
    gemini_stream_timeout_s: float = 180.0

    # Groq. gpt-oss-120b is the strongest tool-use model on Groq's free tier; qwen3.8-27b is faster to
    # get under the rate limit but made factual mistakes in testing, so it is not a default fallback.
    groq_api_key: str | None = None
    groq_models: list[str] = ["openai/gpt-oss-120b"]
    groq_max_retries: int = 3  # free tier is ~8K tokens/min; waiting out a 429 beats a weaker model
    groq_timeout_s: float = 30.0
    groq_max_completion_tokens: int = 2048
    groq_notetaker_model: str = "openai/gpt-oss-20b"  # separate rate-limit budget from the advisor
    groq_stt_model: str = "whisper-large-v3-turbo"
    groq_tts_model: str = "canopylabs/orpheus-v1-english"
    groq_tts_voice: str = "tara"
    groq_max_text_chars: int = 110  # long prose in tool results is cut to this for Groq's small budget

    # Claude
    anthropic_api_key: str | None = None
    vinsight_model: str = "claude-opus-5-5"
    vinsight_effort: str = "medium"

    nhtsa_vpic_url: str = "https://vpic.nhtsa.dot.gov/api/vehicles"
    nhtsa_api_url: str = "https://api.nhtsa.gov"
    http_timeout_s: float = 15.0


settings = Settings()
