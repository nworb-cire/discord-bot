from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import discord
import pytest

from bot.commands import summarize as summarize_module


BASE_TIME = datetime(2026, 9, 4, 12, 0, tzinfo=timezone.utc)


def message(
    minutes: int,
    content: str,
    *,
    author_id: int = 1,
    message_id: int | None = None,
    reply_to: int | None = None,
):
    return SimpleNamespace(
        id=message_id if message_id is not None else minutes,
        created_at=BASE_TIME + timedelta(minutes=minutes),
        content=content,
        clean_content=content,
        author=SimpleNamespace(id=author_id, display_name=f"User {author_id}"),
        reference=(
            SimpleNamespace(message_id=reply_to) if reply_to is not None else None
        ),
        attachments=[],
        embeds=[],
    )


def test_serialize_transcript_keeps_newest_records_under_cutoff():
    messages = [message(0, "a" * 100), message(1, "newest")]

    transcript, omitted = summarize_module.serialize_transcript(messages, max_chars=250)

    assert omitted == 1
    assert "newest" in transcript
    assert "a" * 100 not in transcript


def test_serialize_transcript_omits_empty_messages():
    transcript, omitted = summarize_module.serialize_transcript([message(0, "")], 1000)

    assert transcript == "[]"
    assert omitted == 0


def test_serialize_transcript_includes_reply_relationships():
    transcript, _ = summarize_module.serialize_transcript(
        [message(1, "A reply", message_id=11, reply_to=10)], 1000
    )

    assert '"message_id":"11"' in transcript
    assert '"reply_to_message_id":"10"' in transcript


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
    assert kwargs["store"] is False
    assert "untrusted data" in kwargs["instructions"]
    assert "only the most recent coherent topic" in kwargs["instructions"]
    assert "only the most recent topic" in kwargs["input"]
    assert '"content":"Hello"' in kwargs["input"]


class FakeInteraction(discord.Interaction):
    pass


@pytest.mark.asyncio
async def test_command_posts_non_ephemeral_summary(monkeypatch):
    older = message(0, "Earlier topic")
    newer = message(1, "Latest topic", author_id=2)

    async def history(**kwargs):
        assert kwargs == {
            "limit": 100,
            "before": interaction.created_at,
            "after": interaction.created_at - timedelta(hours=48),
            "oldest_first": False,
        }
        for item in [newer, older]:
            yield item

    interaction = FakeInteraction()
    interaction.created_at = BASE_TIME + timedelta(minutes=2)
    interaction.channel = SimpleNamespace(id=123, history=history)
    interaction.response = SimpleNamespace(defer=AsyncMock())
    interaction.followup = SimpleNamespace(send=AsyncMock())
    monkeypatch.setattr(
        summarize_module, "summarize_messages", AsyncMock(return_value="Summary text")
    )

    cog = summarize_module.Summarize(SimpleNamespace())
    await cog.summarize(interaction)

    interaction.response.defer.assert_awaited_once_with(ephemeral=False)
    summarize_module.summarize_messages.assert_awaited_once_with([older, newer])
    sent_text = interaction.followup.send.await_args.args[0]
    assert "latest topic from 2 recent messages" in sent_text
    assert "Summary text" in sent_text
    assert interaction.followup.send.await_args.kwargs["ephemeral"] is False
