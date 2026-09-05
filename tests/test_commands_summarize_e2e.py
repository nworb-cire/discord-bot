from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from bot.commands.summarize import summarize_messages


@pytest.mark.e2e
@pytest.mark.asyncio
async def test_live_conversation_summarization():
    started_at = datetime(2026, 9, 4, 18, 0, tzinfo=timezone.utc)
    messages = [
        SimpleNamespace(
            id=index,
            created_at=started_at + timedelta(minutes=index),
            content=content,
            clean_content=content,
            author=SimpleNamespace(id=index, display_name=author),
            reference=None,
            attachments=[],
            embeds=[],
        )
        for index, (author, content) in enumerate(
            [
                ("Avery", "Let's hold the autumn book meetup on October 12."),
                ("Blake", "Agreed. I'll reserve the library room by Friday."),
                ("Casey", "Should we start at 6 PM or 7 PM?"),
            ],
            start=1,
        )
    ]

    summary = await summarize_messages(messages)

    assert summary.strip()
    assert "October 12" in summary
    assert "Blake" in summary
