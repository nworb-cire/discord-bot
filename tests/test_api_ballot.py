from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace

import pytest

from bot import api
from tests.utils import DummyResult, DummySession


def _candidate_row(
    *,
    book_id: int,
    title: str,
    reactions: int,
    previous_votes: float,
    score: float,
    created_at: datetime,
    has_prior: bool = False,
):
    return SimpleNamespace(
        book_id=book_id,
        title=title,
        reactions=reactions,
        previous_votes=previous_votes,
        score=score,
        created_at=created_at,
        has_prior_appearance=has_prior,
    )


@pytest.mark.asyncio
async def test_calculate_ballot_prioritizes_fresh_entries():
    created = datetime(2024, 1, 1)
    session = DummySession(
        execute_results=[
            DummyResult(
                rows=[
                    _candidate_row(
                        book_id=1,
                        title="Returning Favorite",
                        reactions=4,
                        previous_votes=2.0,
                        score=6.0,
                        created_at=created,
                        has_prior=True,
                    ),
                    _candidate_row(
                        book_id=2,
                        title="Another Returning",
                        reactions=3,
                        previous_votes=4.0,
                        score=7.0,
                        created_at=created,
                        has_prior=True,
                    ),
                    _candidate_row(
                        book_id=3,
                        title="Fresh A",
                        reactions=5,
                        previous_votes=0.0,
                        score=5.0,
                        created_at=created,
                        has_prior=False,
                    ),
                    _candidate_row(
                        book_id=4,
                        title="Fresh B",
                        reactions=2,
                        previous_votes=1.0,
                        score=3.0,
                        created_at=created,
                        has_prior=False,
                    ),
                ]
            )
        ]
    )

    ballot = await api._calculate_ballot(session, limit=4)

    assert [entry["book_id"] for entry in ballot] == [3, 4, 2, 1]
    assert [entry["has_prior_appearance"] for entry in ballot] == [
        False,
        False,
        True,
        True,
    ]


@pytest.mark.asyncio
async def test_calculate_ballot_applies_limit_after_reordering():
    created = datetime(2024, 2, 1)
    session = DummySession(
        execute_results=[
            DummyResult(
                rows=[
                    _candidate_row(
                        book_id=1,
                        title="Returning High Score",
                        reactions=8,
                        previous_votes=2.0,
                        score=10.0,
                        created_at=created,
                        has_prior=True,
                    ),
                    _candidate_row(
                        book_id=2,
                        title="Fresh A",
                        reactions=4,
                        previous_votes=0.0,
                        score=4.0,
                        created_at=created,
                        has_prior=False,
                    ),
                    _candidate_row(
                        book_id=3,
                        title="Fresh B",
                        reactions=3,
                        previous_votes=0.0,
                        score=3.0,
                        created_at=created,
                        has_prior=False,
                    ),
                ]
            )
        ]
    )

    ballot = await api._calculate_ballot(session, limit=2)

    assert [entry["book_id"] for entry in ballot] == [2, 3]


@pytest.mark.asyncio
async def test_calculate_ballot_returns_empty_when_no_rows():
    session = DummySession(execute_results=[DummyResult(rows=[])])

    ballot = await api._calculate_ballot(session, limit=3)

    assert ballot == []
