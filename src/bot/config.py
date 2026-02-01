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
    openai_key: str = Field(alias="OPENAI_API_KEY")
    is_staging: bool = Field(alias="STAGING", default=False)
    max_election_appearances: int = Field(
        default=3, alias="BOOK_MAX_ELECTION_APPEARANCES"
    )
    discord_guild_id: int = Field(alias="DISCORD_GUILD_ID")
    google_calendar_id: str = Field(alias="GOOGLE_CALENDAR_ID")
    google_service_account_email: str = Field(alias="GOOGLE_SERVICE_ACCOUNT_EMAIL")
    google_service_account_private_key: str = Field(
        alias="GOOGLE_SERVICE_ACCOUNT_PRIVATE_KEY"
    )

    model_config = SettingsConfigDict(env_file=".env")


@lru_cache
def get_settings() -> Settings:
    return Settings()
