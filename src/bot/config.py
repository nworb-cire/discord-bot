from functools import lru_cache
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    discord_token: str = Field(alias="DISCORD_BOT_TOKEN")
    bookclub_channel_id: int = Field(alias="BOOKCLUB_CHANNEL_ID")
    nom_channel_id: int = Field(alias="BOOKCLUB_NOM_CHANNEL_ID")
    results_channel_id: int = Field(alias="BOOKCLUB_RESULTS_CHANNEL_ID")
    predictions_channel_id: int = Field(alias="PREDICTIONS_CHANNEL_ID")
    role_highweight_id: int = Field(alias="BOOKCLUB_ROLE_ID")
    weight_inner: int = Field(alias="VOTE_WEIGHT_INNER")
    weight_outer: int = Field(alias="VOTE_WEIGHT_OUTER")
    database_url: str = Field(alias="DATABASE_URL")
    redis_url: str = Field(alias="REDIS_URL")
    openai_api_key: str = Field(alias="OPENAI_API_KEY")
    openai_book_lookup_model: str = Field(
        default="gpt-5-mini", alias="OPENAI_BOOK_LOOKUP_MODEL"
    )
    openai_book_lookup_reasoning_effort: str = Field(
        default="low", alias="OPENAI_BOOK_LOOKUP_REASONING_EFFORT"
    )
    openai_book_lookup_max_output_tokens: int = Field(
        default=4000, alias="OPENAI_BOOK_LOOKUP_MAX_OUTPUT_TOKENS"
    )
    goodreads_lookup_timeout_seconds: float = Field(
        default=10.0, alias="GOODREADS_LOOKUP_TIMEOUT_SECONDS"
    )
    goodreads_backfill_interval_seconds: float = Field(
        default=60.0, alias="GOODREADS_BACKFILL_INTERVAL_SECONDS"
    )
    openai_prediction_evidence_model: str = Field(
        default="gpt-5.6-luna", alias="OPENAI_PREDICTION_EVIDENCE_MODEL"
    )
    openai_prediction_evidence_reasoning_effort: str = Field(
        default="high", alias="OPENAI_PREDICTION_EVIDENCE_REASONING_EFFORT"
    )
    openai_prediction_evidence_max_output_tokens: int = Field(
        default=2000, alias="OPENAI_PREDICTION_EVIDENCE_MAX_OUTPUT_TOKENS"
    )
    openai_summarization_model: str = Field(
        default="gpt-5.6-luna", alias="OPENAI_SUMMARIZATION_MODEL"
    )
    openai_summarization_reasoning_effort: str = Field(
        default="low", alias="OPENAI_SUMMARIZATION_REASONING_EFFORT"
    )
    openai_summarization_max_output_tokens: int = Field(
        default=3000, alias="OPENAI_SUMMARIZATION_MAX_OUTPUT_TOKENS"
    )
    summarization_history_limit: int = Field(
        default=500, alias="SUMMARIZATION_HISTORY_LIMIT", ge=1, le=1000
    )
    summarization_max_input_chars: int = Field(
        default=600_000, alias="SUMMARIZATION_MAX_INPUT_CHARS", ge=1000
    )
    summarization_hard_gap_hours: float = Field(
        default=6.0, alias="SUMMARIZATION_HARD_GAP_HOURS", gt=0
    )
    summarization_soft_gap_minutes: float = Field(
        default=90.0, alias="SUMMARIZATION_SOFT_GAP_MINUTES", gt=0
    )
    google_service_account_email: str | None = Field(
        default=None, alias="GOOGLE_SERVICE_ACCOUNT_EMAIL"
    )
    google_service_account_private_key: str | None = Field(
        default=None, alias="GOOGLE_SERVICE_ACCOUNT_PRIVATE_KEY"
    )
    google_calendar_id: str | None = Field(default=None, alias="GOOGLE_CALENDAR_ID")
    discord_guild_id: int | None = Field(alias="DISCORD_GUILD_ID", default=None)
    is_staging: bool = Field(alias="STAGING", default=False)
    max_election_appearances: int = Field(
        default=3, alias="BOOK_MAX_ELECTION_APPEARANCES"
    )
    nomination_reaction_refresh_debounce_seconds: float = Field(
        default=1.0, alias="NOMINATION_REACTION_REFRESH_DEBOUNCE_SECONDS"
    )

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")


@lru_cache
def get_settings() -> Settings:
    return Settings()
