from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import discord
import pytest

from bot.commands import summarize as summarize_module


BASE_TIME = datetime(2026, 9, 4, 12, 0, tzinfo=timezone.utc)


def message(
    minutes: int,
    content: str,
    *,
    author_id: int = 1,
    nickname: str | None = None,
    message_id: int | None = None,
    reply_to: int | None = None,
):
    return SimpleNamespace(
        id=message_id if message_id is not None else minutes,
        created_at=BASE_TIME + timedelta(minutes=minutes),
        content=content,
        clean_content=content,
        author=SimpleNamespace(
            id=author_id, display_name=f"User {author_id}", nick=nickname
        ),
        reference=(
            SimpleNamespace(message_id=reply_to) if reply_to is not None else None
        ),
        attachments=[],
        embeds=[],
    )


def test_format_transcript_keeps_newest_messages_under_cutoff():
    messages = [message(0, "a" * 100), message(1, "newest")]

    transcript, omitted = summarize_module.format_transcript(messages, max_chars=120)

    assert omitted == 1
    assert "newest" in transcript
    assert "a" * 100 not in transcript


def test_format_transcript_omits_empty_messages():
    transcript, omitted = summarize_module.format_transcript([message(0, "")], 1000)

    assert transcript == ""
    assert omitted == 0


def test_format_transcript_uses_names_and_inline_reply_quotes():
    original = message(0, "bar baz", author_id=2, message_id=10)
    reply = message(1, "foo bar", author_id=1, message_id=11, reply_to=10)
    transcript, _ = summarize_module.format_transcript([original, reply], 1000)

    assert transcript == (
        "User 2:\n"
        "bar baz\n\n"
        "User 1 in response to User 2:\n"
        "> bar baz\n"
        "foo bar"
    )


def test_format_transcript_prefers_server_nicknames():
    original = message(0, "Hi", author_id=2, nickname="Bookworm")
    reply = message(1, "Hello", author_id=1, nickname="Reader")

    transcript, _ = summarize_module.format_transcript([original, reply], 1000)

    assert transcript == "Bookworm:\nHi\n\nReader:\nHello"


@pytest.mark.asyncio
async def test_fetch_history_falls_back_to_last_20_messages():
    older = message(0, "Older")
    newer = message(1, "Newer")
    calls = []

    async def history(**kwargs):
        calls.append(kwargs)
        items = [newer] if "after" in kwargs else [newer, older]
        for item in items:
            yield item

    messages, used_fallback = await summarize_module.fetch_summary_history(
        history, BASE_TIME + timedelta(minutes=2)
    )

    assert messages == [older, newer]
    assert used_fallback is True
    assert calls[0]["limit"] == 100
    assert calls[0]["after"] == BASE_TIME + timedelta(minutes=2) - timedelta(hours=48)
    assert calls[1] == {
        "limit": 20,
        "before": BASE_TIME + timedelta(minutes=2),
        "oldest_first": False,
    }


@pytest.mark.asyncio
async def test_fetch_history_keeps_48_hour_window_when_it_has_20_messages():
    newest_first = [message(index, str(index)) for index in range(20, 0, -1)]
    calls = []

    async def history(**kwargs):
        calls.append(kwargs)
        for item in newest_first:
            yield item

    messages, used_fallback = await summarize_module.fetch_summary_history(
        history, BASE_TIME + timedelta(minutes=21)
    )

    assert messages == list(reversed(newest_first))
    assert used_fallback is False
    assert len(calls) == 1


def test_split_discord_message_respects_limit_and_preserves_text():
    text = "first line\n" + ("word " * 20)

    chunks = summarize_module.split_discord_message(text, limit=40)

    assert all(len(chunk) <= 40 for chunk in chunks)
    assert " ".join(" ".join(chunks).split()) == " ".join(text.split())


@pytest.mark.asyncio
async def test_summarize_messages_uses_configured_luna_model(monkeypatch):
    create = AsyncMock(
        return_value=SimpleNamespace(
            output_text="A useful summary", id="response-1", status="completed"
        )
    )
    monkeypatch.setattr(
        summarize_module,
        "AsyncOpenAI",
        lambda **kwargs: SimpleNamespace(responses=SimpleNamespace(create=create)),
    )

    result = await summarize_module.summarize_messages([message(0, "Hello")])

    assert result == "A useful summary"
    kwargs = create.await_args.kwargs
    assert kwargs["model"] == "gpt-5.6-luna"
    assert kwargs["reasoning"] == {"effort": "low"}
    assert kwargs["text"] == {"verbosity": "low"}
    assert kwargs["store"] is False
    assert "untrusted data" in kwargs["instructions"]
    assert "only the most recent coherent topic" in kwargs["instructions"]
    assert "180 words or fewer" in kwargs["instructions"]
    assert "only the most recent topic" in kwargs["input"]
    assert "# Transcript" in kwargs["input"]
    assert "——\nUser 1:\nHello\n——" in kwargs["input"]


class FakeInteraction(discord.Interaction):
    pass


@pytest.mark.asyncio
async def test_command_posts_ephemeral_summary(monkeypatch):
    older = message(0, "Earlier topic")
    newer = message(1, "Latest topic", author_id=2)

    interaction = FakeInteraction()
    interaction.created_at = BASE_TIME + timedelta(minutes=2)
    history = Mock()
    interaction.channel = SimpleNamespace(id=123, history=history)
    interaction.response = SimpleNamespace(defer=AsyncMock())
    interaction.followup = SimpleNamespace(send=AsyncMock())
    monkeypatch.setattr(
        summarize_module, "summarize_messages", AsyncMock(return_value="Summary text")
    )
    fetch = AsyncMock(return_value=([older, newer], True))
    monkeypatch.setattr(summarize_module, "fetch_summary_history", fetch)

    cog = summarize_module.Summarize(SimpleNamespace())
    await cog.summarize(interaction)

    interaction.response.defer.assert_awaited_once_with(ephemeral=True)
    fetch.assert_awaited_once_with(history, interaction.created_at)
    summarize_module.summarize_messages.assert_awaited_once_with([older, newer])
    sent_text = interaction.followup.send.await_args.args[0]
    assert "latest topic from 2 recent messages" in sent_text
    assert "Summary text" in sent_text
    assert interaction.followup.send.await_args.kwargs["ephemeral"] is True
