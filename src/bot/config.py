from functools import lru_cache
from pydantic import Field
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    discord_token: str = Field(alias="DISCORD_BOT_TOKEN")
    bookclub_channel_id: int = Field(alias="BOOKCLUB_CHANNEL_ID")
    nom_channel_id: int = Field(alias="BOOKCLUB_NOM_CHANNEL_ID")
    results_channel_id: int = Field(alias="BOOKCLUB_RESULTS_CHANNEL_ID")
    predictions_channel_id: int = Field(alias="PREDICTIONS_CHANNEL_ID")
    role_highweight_id: int = Field(alias="BOOKCLUB_ROLE_ID")
    weight_inner: int = Field(alias="VOTE_WEIGHT_INNER")
    weight_outer: int = Field(alias="VOTE_WEIGHT_OUTER")
    ballot_size: int = Field(alias="BALLOT_SIZE")
    database_url: str = Field(alias="DATABASE_URL")
    redis_url: str = Field(alias="REDIS_URL")
    openai_key: str = Field(alias="OPENAI_API_KEY")

    class Config:
        env_file = ".env"


@lru_cache
def get_settings() -> Settings:
    return Settings()
