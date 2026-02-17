from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from bot.calendar_sync import DiscordGoogleCalendarSync, SyncPlan


class _Resp:
    def __init__(self, payload, status_code=200):
        self._payload = payload
        self.status_code = status_code

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"http error {self.status_code}")

    def json(self):
        return self._payload


class _Client:
    def __init__(self, routes):
        self.routes = routes

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def get(self, url, headers=None, params=None):
        key = ("GET", url, frozenset((params or {}).items()))
        return self.routes[key].pop(0)

    def post(self, url, headers=None, data=None, json=None):
        key = ("POST", url)
        return self.routes[key].pop(0)

    def patch(self, url, headers=None, json=None):
        key = ("PATCH", url)
        return self.routes[key].pop(0)


def _settings(**kwargs):
    base = dict(
        discord_guild_id=42,
        discord_token="token",
        bookclub_channel_id=777,
        google_calendar_id="cal",
        google_service_account_email="svc@example.com",
        google_service_account_private_key="pk",
        is_staging=False,
    )
    base.update(kwargs)
    return SimpleNamespace(**base)


def test_run_staging_is_dry_run(monkeypatch):
    sync = DiscordGoogleCalendarSync(_settings(is_staging=True))
    plan = SyncPlan(
        to_create=[{"a": 1}], to_update=[{"google_event_id": "x", "body": {}}]
    )

    monkeypatch.setattr(sync, "_load_discord_events", lambda: [{}])
    monkeypatch.setattr(sync, "_load_google_events", lambda: [{}])
    monkeypatch.setattr(sync, "_build_sync_plan", lambda *_: plan)

    called = []
    monkeypatch.setattr(
        sync, "_create_google_event", lambda *_: called.append("create")
    )
    monkeypatch.setattr(
        sync, "_update_google_event", lambda *_: called.append("update")
    )
    monkeypatch.setattr(
        sync, "_cancel_google_event", lambda *_: called.append("cancel")
    )

    summary = sync.run()
    assert summary["mode"] == "dry-run"
    assert called == []


def test_run_apply_calls_create_update_cancel(monkeypatch):
    sync = DiscordGoogleCalendarSync(_settings(is_staging=False))
    plan = SyncPlan(
        to_create=[{"a": 1}],
        to_update=[{"google_event_id": "x", "body": {}}],
        to_cancel=[{"google_event_id": "y"}],
    )

    monkeypatch.setattr(sync, "_load_discord_events", lambda: [{}])
    monkeypatch.setattr(sync, "_load_google_events", lambda: [{}])
    monkeypatch.setattr(sync, "_build_sync_plan", lambda *_: plan)

    called = []
    monkeypatch.setattr(
        sync, "_create_google_event", lambda *_: called.append("create")
    )
    monkeypatch.setattr(
        sync, "_update_google_event", lambda *_: called.append("update")
    )
    monkeypatch.setattr(
        sync, "_cancel_google_event", lambda *_: called.append("cancel")
    )

    summary = sync.run()
    assert summary["mode"] == "apply"
    assert called == ["create", "update", "cancel"]


def test_load_discord_events(monkeypatch):
    sync = DiscordGoogleCalendarSync(_settings(discord_guild_id=123))
    url = "https://discord.com/api/v10/guilds/123/scheduled-events"
    routes = {
        ("GET", url, frozenset({("with_user_count", "false")})): [_Resp([{"id": "1"}])]
    }
    monkeypatch.setattr(
        "bot.calendar_sync.httpx.Client", lambda timeout: _Client(routes)
    )

    events = sync._load_discord_events()
    assert events == [{"id": "1"}]


def test_load_google_events_filters_and_paginates(monkeypatch):
    sync = DiscordGoogleCalendarSync(_settings(google_calendar_id="abc"))
    url = "https://www.googleapis.com/calendar/v3/calendars/abc/events"
    params1 = frozenset(
        {
            ("singleEvents", "true"),
            ("showDeleted", "false"),
            ("maxResults", 2500),
            ("timeMin", "2026-01-01T00:00:00Z"),
        }
    )
    params2 = frozenset(
        {
            ("singleEvents", "true"),
            ("showDeleted", "false"),
            ("maxResults", 2500),
            ("timeMin", "2026-01-01T00:00:00Z"),
            ("pageToken", "next"),
        }
    )
    routes = {
        ("GET", url, params1): [
            _Resp(
                {
                    "items": [
                        {
                            "id": "keep-1",
                            "extendedProperties": {
                                "private": {
                                    "sync_source": "discord_scheduled_event",
                                    "discord_event_id": "d1",
                                }
                            },
                        },
                        {"id": "drop-1", "extendedProperties": {"private": {}}},
                    ],
                    "nextPageToken": "next",
                }
            )
        ],
        ("GET", url, params2): [
            _Resp(
                {
                    "items": [
                        {
                            "id": "keep-2",
                            "extendedProperties": {
                                "private": {
                                    "sync_source": "discord_scheduled_event",
                                    "discord_event_id": "d2",
                                }
                            },
                        }
                    ]
                }
            )
        ],
    }
    monkeypatch.setattr(
        "bot.calendar_sync.httpx.Client", lambda timeout: _Client(routes)
    )
    monkeypatch.setattr(sync, "_google_headers", lambda: {"Authorization": "Bearer x"})

    events = sync._load_google_events()
    assert [e["id"] for e in events] == ["keep-1", "keep-2"]


def test_build_google_event_body_and_cutoff():
    sync = DiscordGoogleCalendarSync(_settings(discord_guild_id=99))

    before, skipped = sync._build_google_event_body(
        {"id": "1", "name": "old", "scheduled_start_time": "2025-12-31T00:00:00Z"}
    )
    assert before is None
    assert skipped is True

    body, skipped = sync._build_google_event_body(
        {
            "id": "2",
            "name": "new",
            "description": "desc",
            "scheduled_start_time": "2026-01-02T10:00:00Z",
            "scheduled_end_time": "2026-01-02T11:00:00Z",
            "status": 1,
            "entity_metadata": {"location": "room"},
        }
    )
    assert skipped is False
    assert body["summary"] == "new"
    assert body["description"] == "desc"
    assert body["location"] == "room"
    assert body["extendedProperties"]["private"]["discord_guild_id"] == "99"


def test_build_sync_plan_create_and_update():
    sync = DiscordGoogleCalendarSync(_settings(discord_guild_id=42))
    discord_events = [
        {
            "id": "a",
            "name": "create",
            "scheduled_start_time": "2026-01-02T00:00:00Z",
            "scheduled_end_time": "2026-01-02T01:00:00Z",
            "status": 1,
        },
        {
            "id": "b",
            "name": "update",
            "description": "new",
            "scheduled_start_time": "2026-01-03T00:00:00Z",
            "scheduled_end_time": "2026-01-03T01:00:00Z",
            "status": 1,
        },
    ]
    google_events = [
        {
            "id": "g-b",
            "extendedProperties": {
                "private": {
                    "discord_event_id": "b",
                    "sync_hash": "old-hash",
                }
            },
        }
    ]

    plan = sync._build_sync_plan(discord_events, google_events)
    assert len(plan.to_create) == 1
    assert len(plan.to_update) == 1


def test_resolve_discord_guild_id_from_channel(monkeypatch):
    sync = DiscordGoogleCalendarSync(
        _settings(discord_guild_id=None, bookclub_channel_id=555)
    )
    url = "https://discord.com/api/v10/channels/555"
    routes = {("GET", url, frozenset()): [_Resp({"guild_id": "987"})]}
    monkeypatch.setattr(
        "bot.calendar_sync.httpx.Client", lambda timeout: _Client(routes)
    )

    assert sync._discord_guild_id() == 987
    assert sync._discord_guild_id() == 987


def test_google_access_token_cached(monkeypatch):
    sync = DiscordGoogleCalendarSync(_settings())
    monkeypatch.setattr(sync, "_build_service_account_jwt", lambda *_: "assert")

    token_payload = {"access_token": "tok", "expires_in": 3600}
    routes = {("POST", "https://oauth2.googleapis.com/token"): [_Resp(token_payload)]}
    monkeypatch.setattr(
        "bot.calendar_sync.httpx.Client", lambda timeout: _Client(routes)
    )

    a = sync._google_access_token()
    b = sync._google_access_token()
    assert a == "tok"
    assert b == "tok"


def test_build_service_account_jwt_sign(monkeypatch):
    sync = DiscordGoogleCalendarSync(_settings(google_service_account_private_key="k"))

    class _Proc:
        returncode = 0
        stdout = b"sig"

    monkeypatch.setattr("bot.calendar_sync.subprocess.run", lambda *a, **k: _Proc())
    token = sync._build_service_account_jwt(1700000000)
    assert token.count(".") == 2


def test_parse_datetime_helpers():
    assert DiscordGoogleCalendarSync._parse_datetime(None) is None
    parsed = DiscordGoogleCalendarSync._parse_datetime("2026-01-01T00:00:00Z")
    assert parsed is not None
    assert parsed.tzinfo is UTC
    assert DiscordGoogleCalendarSync._datetime_to_rfc3339(parsed).endswith("Z")
