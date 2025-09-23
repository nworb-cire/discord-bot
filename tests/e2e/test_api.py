from __future__ import annotations

import requests
import pytest


pytestmark = pytest.mark.e2e


def _create_book(api_base_url: str, **overrides) -> dict:
    payload = {
        "title": "Default Title",
        "description": "Default description",
        "summary": "Default summary",
        "isbn": "0000000000000",
        "length": 100,
    }
    payload.update(overrides)
    response = requests.post(f"{api_base_url}/books", json=payload, timeout=5)
    assert response.status_code == 201, response.text
    return response.json()


def _create_nomination(api_base_url: str, **overrides) -> dict:
    assert "book_id" in overrides, "book_id is required for a nomination"
    payload = {
        "book_id": overrides["book_id"],
        "nominator_discord_id": overrides.get("nominator_discord_id", 111),
        "message_id": overrides.get("message_id", 222),
        "reactions": overrides.get("reactions", 0),
    }
    response = requests.post(f"{api_base_url}/nominations", json=payload, timeout=5)
    assert response.status_code == 201, response.text
    return response.json()


def _update_nomination(api_base_url: str, nomination_id: int, **fields) -> dict:
    response = requests.patch(
        f"{api_base_url}/nominations/{nomination_id}",
        json=fields,
        timeout=5,
    )
    assert response.status_code == 200, response.text
    return response.json()


def test_create_book_and_nomination_flow(api_base_url: str, db_connection) -> None:
    book_payload = {
        "title": "The Rust Programming Language",
        "description": "Comprehensive guide to Rust.",
        "summary": "Rust book",
        "isbn": "9781593278281",
        "length": 552,
    }
    created_book = _create_book(api_base_url, **book_payload)

    response = requests.get(f"{api_base_url}/books/{created_book['id']}", timeout=5)
    assert response.status_code == 200
    fetched_book = response.json()
    assert fetched_book["title"] == book_payload["title"]

    nomination_payload = {
        "book_id": created_book["id"],
        "nominator_discord_id": 12345,
        "message_id": 67890,
        "reactions": 7,
    }
    created_nomination = _create_nomination(api_base_url, **nomination_payload)
    assert created_nomination["book_id"] == created_book["id"]

    with db_connection.cursor() as cursor:
        cursor.execute("SELECT title, description, summary, isbn, length FROM books WHERE id = %s", (created_book["id"],))
        stored_book = cursor.fetchone()
        assert stored_book == (
            book_payload["title"],
            book_payload["description"],
            book_payload["summary"],
            book_payload["isbn"],
            book_payload["length"],
        )

        cursor.execute(
            "SELECT nominator_discord_id, message_id, reactions FROM nominations WHERE book_id = %s",
            (created_book["id"],),
        )
        stored_nomination = cursor.fetchone()
        assert stored_nomination == (
            nomination_payload["nominator_discord_id"],
            nomination_payload["message_id"],
            nomination_payload["reactions"],
        )


def test_full_election_flow(api_base_url: str, db_connection) -> None:
    book_alpha = _create_book(
        api_base_url,
        title="Project Hail Mary",
        isbn="9780593135204",
        summary="Ryland Grace saves humanity.",
    )
    book_beta = _create_book(
        api_base_url,
        title="Children of Time",
        isbn="9781529030834",
        summary="Spiders in space evolve.",
    )
    book_gamma = _create_book(
        api_base_url,
        title="The Dispossessed",
        isbn="9780060512750",
        summary="Anarres physicist seeks unity.",
    )

    nom_alpha = _create_nomination(
        api_base_url,
        book_id=book_alpha["id"],
        nominator_discord_id=101,
        message_id=5001,
    )
    nom_beta = _create_nomination(
        api_base_url,
        book_id=book_beta["id"],
        nominator_discord_id=202,
        message_id=5002,
    )
    nom_gamma = _create_nomination(
        api_base_url,
        book_id=book_gamma["id"],
        nominator_discord_id=303,
        message_id=5003,
    )

    _update_nomination(api_base_url, nom_alpha["id"], reactions=5)
    _update_nomination(api_base_url, nom_beta["id"], reactions=3)
    _update_nomination(api_base_url, nom_gamma["id"], reactions=1)

    open_response = requests.post(
        f"{api_base_url}/elections",
        json={"opener_discord_id": 999, "hours": 24},
        timeout=5,
    )
    assert open_response.status_code == 201, open_response.text
    election = open_response.json()
    assert election["ballot"] == [book_alpha["id"], book_beta["id"], book_gamma["id"]]

    vote_response = requests.post(
        f"{api_base_url}/votes",
        json={
            "election_id": election["id"],
            "voter_discord_id": 7001,
            "is_bookclub_member": True,
            "entries": [
                {"book_id": book_alpha["id"], "weight": 3},
                {"book_id": book_beta["id"], "weight": 1},
            ],
        },
        timeout=5,
    )
    assert vote_response.status_code == 204, vote_response.text

    vote_response = requests.post(
        f"{api_base_url}/votes",
        json={
            "election_id": election["id"],
            "voter_discord_id": 8002,
            "is_bookclub_member": False,
            "entries": [
                {"book_id": book_beta["id"], "weight": 2},
                {"book_id": book_alpha["id"], "weight": 1},
            ],
        },
        timeout=5,
    )
    assert vote_response.status_code == 204, vote_response.text

    late_nomination = {
        "book_id": book_alpha["id"],
        "nominator_discord_id": 404,
        "message_id": 6000,
        "reactions": 2,
    }
    late_response = requests.post(f"{api_base_url}/nominations", json=late_nomination, timeout=5)
    assert late_response.status_code == 409

    close_response = requests.post(
        f"{api_base_url}/elections/{election['id']}/close",
        json={"closed_by": 1234},
        timeout=5,
    )
    assert close_response.status_code == 200, close_response.text
    closed = close_response.json()
    assert closed["winner"]["book_id"] == book_alpha["id"]
    assert len(closed["results"]) == 3
    assert closed["results"][0]["total_votes"] == pytest.approx(4)
    assert closed["results"][1]["total_votes"] == pytest.approx(3)
    assert closed["results"][2]["total_votes"] == pytest.approx(0)

    with db_connection.cursor() as cursor:
        cursor.execute("SELECT closed_at, winner FROM elections WHERE id = %s", (election["id"],))
        closed_at, winner = cursor.fetchone()
        assert closed_at is not None
        assert winner == book_alpha["id"]

        cursor.execute(
            "SELECT voter_discord_id, book_id, weight FROM votes WHERE election_id = %s ORDER BY voter_discord_id, book_id",
            (election["id"],),
        )
        stored_votes = cursor.fetchall()
        assert stored_votes == [
            (7001, book_alpha["id"], 3),
            (7001, book_beta["id"], 1),
            (8002, book_alpha["id"], 1),
            (8002, book_beta["id"], 2),
        ]

        cursor.execute("SELECT COUNT(*) FROM nominations")
        assert cursor.fetchone()[0] == 3


def test_database_isolation(api_base_url: str, db_connection) -> None:
    with db_connection.cursor() as cursor:
        cursor.execute("SELECT COUNT(*) FROM books")
        assert cursor.fetchone()[0] == 0
        cursor.execute("SELECT COUNT(*) FROM nominations")
        assert cursor.fetchone()[0] == 0
