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
        default="minimal", alias="OPENAI_BOOK_LOOKUP_REASONING_EFFORT"
    )
    openai_book_lookup_max_output_tokens: int = Field(
        default=4000, alias="OPENAI_BOOK_LOOKUP_MAX_OUTPUT_TOKENS"
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
