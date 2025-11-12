from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timedelta, timezone

from aiohttp import web
from pydantic import BaseModel, ValidationError
from sqlalchemy import Integer, cast, func, select
from sqlalchemy.dialects.postgresql import insert

from bot.config import get_settings
from bot.db import Book, Election, Nomination, Vote, async_session
from bot.reactions import update_election_vote_reaction

settings = get_settings()


class BookPayload(BaseModel):
    title: str
    description: str | None = None
    summary: str | None = None
    isbn: str | None = None
    length: int | None = None


class NominationPayload(BaseModel):
    book_id: int
    nominator_discord_id: int
    message_id: int
    reactions: int = 0


class NominationUpdatePayload(BaseModel):
    reactions: int | None = None


class OpenElectionPayload(BaseModel):
    opener_discord_id: int
    hours: int = 72
    ballot_size: int = 5


class VoteEntryPayload(BaseModel):
    book_id: int
    weight: float


class VotePayload(BaseModel):
    election_id: int
    voter_discord_id: int
    entries: list[VoteEntryPayload]
    is_bookclub_member: bool = False


class CloseElectionPayload(BaseModel):
    closed_by: int | None = None


async def health(_: web.Request) -> web.Response:
    return web.json_response({"status": "ok"})


async def create_book(request: web.Request) -> web.Response:
    payload = await _parse_request(request, BookPayload)
    async with async_session() as session:
        book = Book(
            title=payload.title,
            description=payload.description,
            summary=payload.summary,
            isbn=payload.isbn,
            length=payload.length,
        )
        session.add(book)
        await session.commit()
        await session.refresh(book)

    return web.json_response(
        {
            "id": book.id,
            "title": book.title,
            "description": book.description,
            "summary": book.summary,
            "isbn": book.isbn,
            "length": book.length,
        },
        status=201,
    )


async def get_book(request: web.Request) -> web.Response:
    book_id = int(request.match_info["book_id"])
    async with async_session() as session:
        book = await session.get(Book, book_id)
        if book is None:
            raise web.HTTPNotFound(
                text=json.dumps({"detail": "book not found"}),
                content_type="application/json",
            )

    return web.json_response(
        {
            "id": book.id,
            "title": book.title,
            "description": book.description,
            "summary": book.summary,
            "isbn": book.isbn,
            "length": book.length,
        }
    )


async def create_nomination(request: web.Request) -> web.Response:
    payload = await _parse_request(request, NominationPayload)
    async with async_session() as session:
        open_election = await session.execute(
            select(Election).where(Election.closed_at.is_(None)).limit(1)
        )
        if open_election.scalar_one_or_none() is not None:
            raise web.HTTPConflict(
                text=json.dumps(
                    {"detail": "Nominations are closed while an election is active."}
                ),
                content_type="application/json",
            )

        book = await session.get(Book, payload.book_id)
        if book is None:
            raise web.HTTPNotFound(
                text=json.dumps({"detail": "book not found"}),
                content_type="application/json",
            )

        nomination = Nomination(
            book_id=payload.book_id,
            nominator_discord_id=payload.nominator_discord_id,
            message_id=payload.message_id,
            reactions=payload.reactions,
        )
        session.add(nomination)
        await session.commit()
        await session.refresh(nomination)

    return web.json_response(
        {
            "id": nomination.id,
            "book_id": nomination.book_id,
            "nominator_discord_id": nomination.nominator_discord_id,
            "message_id": nomination.message_id,
            "reactions": int(nomination.reactions),
        },
        status=201,
    )


async def update_nomination(request: web.Request) -> web.Response:
    nomination_id = int(request.match_info["nomination_id"])
    payload = await _parse_request(request, NominationUpdatePayload)
    async with async_session() as session:
        nomination = await session.get(Nomination, nomination_id)
        if nomination is None:
            raise web.HTTPNotFound(
                text=json.dumps({"detail": "nomination not found"}),
                content_type="application/json",
            )

        if payload.reactions is not None:
            nomination.reactions = payload.reactions

        await session.commit()
        await session.refresh(nomination)

    return web.json_response(
        {
            "id": nomination.id,
            "book_id": nomination.book_id,
            "nominator_discord_id": nomination.nominator_discord_id,
            "message_id": nomination.message_id,
            "reactions": int(nomination.reactions),
        }
    )


async def open_election(request: web.Request) -> web.Response:
    payload = await _parse_request(request, OpenElectionPayload)
    async with async_session() as session:
        existing = await session.execute(
            select(Election).where(Election.closed_at.is_(None)).limit(1)
        )
        if existing.scalar_one_or_none() is not None:
            raise web.HTTPConflict(
                text=json.dumps({"detail": "An election is already open."}),
                content_type="application/json",
            )

        ballot_rows = await _calculate_ballot(session, limit=payload.ballot_size)
        if not ballot_rows:
            raise web.HTTPBadRequest(
                text=json.dumps({"detail": "No nominations available for voting."}),
                content_type="application/json",
            )

        opened_at = datetime.now(timezone.utc)
        closes_at = opened_at + timedelta(hours=payload.hours)
        election = Election(
            opener_discord_id=payload.opener_discord_id,
            opened_at=opened_at,
            closes_at=closes_at,
            ballot=[row["book_id"] for row in ballot_rows],
        )
        session.add(election)
        await session.commit()
        await session.refresh(election)

    return web.json_response(
        {
            "id": election.id,
            "ballot": election.ballot,
            "ballot_details": ballot_rows,
            "opened_at": election.opened_at.isoformat(),
            "closes_at": election.closes_at.isoformat(),
        },
        status=201,
    )


async def cast_vote(request: web.Request) -> web.Response:
    payload = await _parse_request(request, VotePayload)
    async with async_session() as session:
        election = await session.get(Election, payload.election_id)
        if election is None:
            raise web.HTTPNotFound(
                text=json.dumps({"detail": "election not found"}),
                content_type="application/json",
            )
        if election.closed_at is not None:
            raise web.HTTPBadRequest(
                text=json.dumps({"detail": "Election is already closed."}),
                content_type="application/json",
            )

        ballot_book_ids = set(election.ballot or [])
        for entry in payload.entries:
            if entry.book_id not in ballot_book_ids:
                raise web.HTTPBadRequest(
                    text=json.dumps(
                        {"detail": "Vote must reference books on the ballot."}
                    ),
                    content_type="application/json",
                )

        max_score = (
            settings.weight_inner
            if payload.is_bookclub_member
            else settings.weight_outer
        )
        total = sum(entry.weight**2 for entry in payload.entries)
        if total > max_score:
            raise web.HTTPBadRequest(
                text=json.dumps(
                    {"detail": "Total vote weight exceeds the allowed maximum."}
                ),
                content_type="application/json",
            )

        for entry in payload.entries:
            stmt = (
                insert(Vote)
                .values(
                    election_id=election.id,
                    voter_discord_id=payload.voter_discord_id,
                    book_id=entry.book_id,
                    weight=entry.weight,
                )
                .on_conflict_do_update(
                    index_elements=[
                        Vote.election_id,
                        Vote.voter_discord_id,
                        Vote.book_id,
                    ],
                    set_={"weight": entry.weight},
                )
            )
            await session.execute(stmt)

        await session.commit()

    try:
        client = request.app["discord_client"]
    except KeyError:
        client = None

    if client is not None:
        try:
            await update_election_vote_reaction(client, payload.election_id)
        except Exception:  # pragma: no cover - defensive logging
            log = logging.getLogger(__name__)
            log.exception(
                "Failed to update vote reaction for election %s", payload.election_id
            )

    return web.Response(status=204)


async def close_election(request: web.Request) -> web.Response:
    election_id = int(request.match_info["election_id"])
    payload = await _parse_request(request, CloseElectionPayload)
    async with async_session() as session:
        election = await session.get(Election, election_id)
        if election is None:
            raise web.HTTPNotFound(
                text=json.dumps({"detail": "election not found"}),
                content_type="application/json",
            )
        if election.closed_at is not None:
            raise web.HTTPBadRequest(
                text=json.dumps({"detail": "Election already closed."}),
                content_type="application/json",
            )

        vote_totals = await session.execute(
            select(
                Vote.book_id,
                func.coalesce(func.sum(Vote.weight), 0).label("total_votes"),
            )
            .where(Vote.election_id == election.id)
            .group_by(Vote.book_id)
        )
        totals_map = {
            int(row.book_id): float(row.total_votes or 0.0) for row in vote_totals
        }

        results: list[dict[str, object]] = []
        for book_id in election.ballot or []:
            book = await session.get(Book, book_id)
            if book is None:
                continue
            total_votes = totals_map.get(book_id, 0.0)
            results.append(
                {
                    "book_id": book_id,
                    "title": book.title,
                    "total_votes": total_votes,
                }
            )

        results.sort(key=lambda item: item["total_votes"], reverse=True)
        winner = results[0] if results else None

        election.closed_at = datetime.now(timezone.utc)
        election.closed_by = payload.closed_by
        election.winner = winner["book_id"] if winner else None
        await session.commit()
        await session.refresh(election)

    response_body = {
        "id": election.id,
        "closed_at": election.closed_at.isoformat() if election.closed_at else None,
        "winner": winner,
        "results": results,
    }
    return web.json_response(response_body)


async def _calculate_ballot(session, limit: int) -> list[dict[str, object]]:
    nominations_table = Nomination.__table__
    ballot_entries = (
        select(
            cast(func.json_array_elements_text(Election.ballot), Integer).label(
                "book_id"
            )
        )
        .select_from(Election)
        .cte("ballot_entries")
    )
    ballot_appearances = (
        select(
            ballot_entries.c.book_id,
            func.count().label("appearance_count"),
        )
        .group_by(ballot_entries.c.book_id)
        .cte("ballot_appearances")
    )
    appearance_count_expr = func.coalesce(ballot_appearances.c.appearance_count, 0)
    has_prior_expr = (appearance_count_expr > 0).label("has_prior_appearance")
    vote_totals = (
        select(
            Vote.book_id.label("book_id"),
            func.sum(Vote.weight).label("vote_sum"),
        )
        .group_by(Vote.book_id)
        .subquery()
    )

    score_expr = (
        func.coalesce(nominations_table.c.reactions, 0)
        + func.coalesce(vote_totals.c.vote_sum, 0)
    ).label("score")
    stmt = (
        select(
            Book.id.label("book_id"),
            Book.title,
            Book.created_at.label("created_at"),
            func.coalesce(nominations_table.c.reactions, 0).label("reactions"),
            func.coalesce(vote_totals.c.vote_sum, 0).label("previous_votes"),
            score_expr,
            has_prior_expr,
        )
        .select_from(Book)
        .join(nominations_table, nominations_table.c.book_id == Book.id)
        .outerjoin(vote_totals, vote_totals.c.book_id == Book.id)
        .outerjoin(ballot_appearances, ballot_appearances.c.book_id == Book.id)
    )
    if not settings.is_staging:
        stmt = stmt.where(func.coalesce(nominations_table.c.reactions, 0) > 0)

    result = await session.execute(stmt)
    rows = result.all()
    if not rows:
        return []

    ballot: list[dict[str, object]] = []
    for row in rows:
        previous_votes = row.previous_votes or 0
        score = row.score or 0
        ballot.append(
            {
                "book_id": int(row.book_id),
                "title": row.title,
                "reactions": int(row.reactions or 0),
                "previous_votes": float(previous_votes),
                "score": float(score),
                "has_prior_appearance": bool(
                    getattr(row, "has_prior_appearance", False)
                ),
                "_created_at": getattr(row, "created_at", None),
            }
        )

    ordered_ballot = sorted(
        ballot,
        key=lambda item: (
            item["has_prior_appearance"],
            -item["score"],
            item["_created_at"] or datetime.min,
        ),
    )
    if limit:
        ordered_ballot = ordered_ballot[:limit]

    return ordered_ballot


async def _parse_request(request: web.Request, model: type[BaseModel]) -> BaseModel:
    try:
        data = await request.json()
    except Exception as exc:  # pragma: no cover - aiohttp normalises JSON errors
        raise web.HTTPBadRequest(
            text=json.dumps({"detail": "invalid JSON"}), content_type="application/json"
        ) from exc

    try:
        return model.model_validate(data)
    except ValidationError as exc:
        raise web.HTTPBadRequest(
            text=exc.json(), content_type="application/json"
        ) from exc


def create_app() -> web.Application:
    get_settings()  # ensure environment is validated
    app = web.Application()
    app.router.add_get("/health", health)
    app.router.add_post("/books", create_book)
    app.router.add_get("/books/{book_id}", get_book)
    app.router.add_post("/nominations", create_nomination)
    app.router.add_patch("/nominations/{nomination_id}", update_nomination)
    app.router.add_post("/elections", open_election)
    app.router.add_post("/votes", cast_vote)
    app.router.add_post("/elections/{election_id}/close", close_election)
    return app


def run() -> None:
    port = int(os.environ.get("PORT", "8080"))
    web.run_app(create_app(), port=port)


if __name__ == "__main__":
    run()
