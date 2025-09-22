from types import SimpleNamespace

import pytest

from bot.utils import get_open_election, utcnow
from tests.utils import DummyResult, DummySession


def test_utcnow_returns_timezone_aware():
    now = utcnow()
    assert now.tzinfo is not None
    assert now.tzinfo.utcoffset(now).total_seconds() == 0


@pytest.mark.asyncio
async def test_get_open_election_returns_latest():
    election = SimpleNamespace(id=1)
    session = DummySession(execute_results=[DummyResult(scalar=election)])

    result = await get_open_election(session)

    assert result is election


@pytest.mark.asyncio
async def test_get_open_election_handles_absence():
    session = DummySession(execute_results=[DummyResult(scalar=None)])

    result = await get_open_election(session)

    assert result is None
