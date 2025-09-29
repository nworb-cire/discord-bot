from sqlalchemy.ext.asyncio import (
    create_async_engine,
    async_sessionmaker,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from sqlalchemy import (
    Integer,
    String,
    Numeric,
    BigInteger,
    Text,
    Boolean,
    ForeignKey,
    DateTime,
    TIMESTAMP,
    JSON,
)
from datetime import datetime
from bot.config import get_settings

settings = get_settings()
engine = create_async_engine(settings.database_url, echo=False, future=True)
async_session = async_sessionmaker(engine, expire_on_commit=False)


class Base(DeclarativeBase):
    pass


class Book(Base):
    __tablename__ = "books"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    title: Mapped[str] = mapped_column(Text)
    description: Mapped[str] = mapped_column(Text, nullable=True)
    summary: Mapped[str] = mapped_column(Text, nullable=True)
    isbn: Mapped[str] = mapped_column(String(13), unique=True, nullable=True)
    length: Mapped[int] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), default=datetime.utcnow
    )


class Nomination(Base):
    __tablename__ = "nominations"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    book_id: Mapped[int] = mapped_column(ForeignKey("books.id", ondelete="CASCADE"))
    nominator_discord_id: Mapped[int] = mapped_column(BigInteger)
    message_id: Mapped[int] = mapped_column(BigInteger)
    reactions: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), default=datetime.utcnow
    )


class Election(Base):
    __tablename__ = "elections"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    opener_discord_id: Mapped[int] = mapped_column(BigInteger)
    opened_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True))
    closes_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True))
    closed_by: Mapped[int] = mapped_column(BigInteger, nullable=True)
    closed_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), nullable=True)
    ballot: Mapped[list[int]] = mapped_column(JSON)
    ballot_message_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    vote_reaction_frozen: Mapped[bool] = mapped_column(Boolean, default=False)
    winner: Mapped[int] = mapped_column(
        ForeignKey("books.id", ondelete="SET NULL"), nullable=True
    )


class Vote(Base):
    __tablename__ = "votes"

    election_id: Mapped[int] = mapped_column(
        ForeignKey("elections.id", ondelete="CASCADE"), primary_key=True
    )
    voter_discord_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    book_id: Mapped[int] = mapped_column(
        ForeignKey("books.id", ondelete="CASCADE"), primary_key=True
    )
    weight: Mapped[float] = mapped_column(Numeric)


class Prediction(Base):
    __tablename__ = "predictions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    predictor_discord_id: Mapped[int] = mapped_column(BigInteger)
    text: Mapped[str] = mapped_column(Text)
    odds: Mapped[float] = mapped_column(Numeric(4, 1))
    due_at: Mapped[datetime] = mapped_column(DateTime(timezone=False))
    message_id: Mapped[int] = mapped_column(BigInteger)
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), default=datetime.utcnow
    )
    reminded: Mapped[bool] = mapped_column(Boolean, default=False)
