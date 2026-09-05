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


def find_boundary(messages):
    return summarize_module.find_conversation_boundary(
        messages,
        hard_gap=timedelta(hours=6),
        soft_gap=timedelta(minutes=90),
    )


def test_boundary_uses_most_recent_hard_inactivity_gap():
    messages = [
        message(0, "old"),
        message(500, "middle", author_id=2),
        message(1000, "current", author_id=3),
        message(1001, "follow-up", author_id=2),
    ]

    boundary = find_boundary(messages)

    assert boundary.index == 2
    assert boundary.reason == "long inactivity gap"


def test_boundary_combines_soft_gap_with_author_change():
    messages = [message(0, "Earlier"), message(100, "Now", author_id=2)]

    boundary = find_boundary(messages)

    assert boundary.index == 1
    assert "author change" in boundary.reason


def test_boundary_does_not_use_soft_gap_without_another_signal():
    messages = [message(0, "Part one"), message(100, "Part two")]

    assert find_boundary(messages).index == 0


def test_boundary_recognizes_explicit_topic_transition_after_short_pause():
    messages = [message(0, "First topic"), message(20, "Switching gears: books")]

    boundary = find_boundary(messages)

    assert boundary.index == 1
    assert "explicit topic transition" in boundary.reason


def test_reply_keeps_referenced_message_across_a_large_gap():
    messages = [
        message(0, "Question", message_id=10),
        message(500, "Answer", author_id=2, reply_to=10),
    ]

    assert find_boundary(messages).index == 0


def test_reply_to_nonadjacent_message_keeps_context_across_gap():
    messages = [
        message(0, "Question", message_id=10),
        message(1, "Another detail", author_id=2, message_id=11),
        message(500, "Answer to question", author_id=3, reply_to=10),
    ]

    assert find_boundary(messages).index == 0


def test_serialize_transcript_keeps_newest_records_under_cutoff():
    messages = [message(0, "a" * 100), message(1, "newest")]

    transcript, omitted = summarize_module.serialize_transcript(messages, max_chars=130)

    assert omitted == 1
    assert "newest" in transcript
    assert "a" * 100 not in transcript


def test_serialize_transcript_omits_empty_messages():
    transcript, omitted = summarize_module.serialize_transcript([message(0, "")], 1000)

    assert transcript == "[]"
    assert omitted == 0


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
    assert '"content":"Hello"' in kwargs["input"]


class FakeInteraction(discord.Interaction):
    pass


@pytest.mark.asyncio
async def test_command_posts_non_ephemeral_summary(monkeypatch):
    messages = [message(0, "Old topic"), message(500, "Current topic", author_id=2)]

    async def history(**kwargs):
        assert kwargs["oldest_first"] is True
        for item in messages:
            yield item

    interaction = FakeInteraction()
    interaction.created_at = BASE_TIME + timedelta(minutes=501)
    interaction.channel = SimpleNamespace(id=123, history=history)
    interaction.response = SimpleNamespace(defer=AsyncMock())
    interaction.followup = SimpleNamespace(send=AsyncMock())
    monkeypatch.setattr(
        summarize_module, "summarize_messages", AsyncMock(return_value="Summary text")
    )

    cog = summarize_module.Summarize(SimpleNamespace())
    await cog.summarize(interaction)

    interaction.response.defer.assert_awaited_once_with(ephemeral=False)
    summarize_module.summarize_messages.assert_awaited_once_with([messages[1]])
    sent_text = interaction.followup.send.await_args.args[0]
    assert "1 message since" in sent_text
    assert "Summary text" in sent_text
    assert interaction.followup.send.await_args.kwargs["ephemeral"] is False
