from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from bot import calendar_sync


class _ListRequest:
    def __init__(self, pages, token):
        self._pages = pages
        self._token = token or "0"

    def execute(self):
        return self._pages[self._token]


class _ExecRequest:
    def execute(self):
        return {}


class _EventsApi:
    def __init__(self, pages, tracker):
        self._pages = pages
        self._tracker = tracker

    def list(self, **kwargs):
        token = kwargs.get("pageToken")
        return _ListRequest(self._pages, token)

    def delete(self, calendarId, eventId):
        self._tracker["deleted"].append(eventId)
        return _ExecRequest()

    def update(self, calendarId, eventId, body):
        self._tracker["updated"].append((eventId, body))
        return _ExecRequest()

    def insert(self, calendarId, body):
        self._tracker["inserted"].append(body)
        return _ExecRequest()


class _Service:
    def __init__(self, pages, tracker):
        self._events = _EventsApi(pages, tracker)

    def events(self):
        return self._events


def test_google_snapshot_matches_desired_snapshot():
    start_time = datetime(2026, 2, 10, 18, 0, tzinfo=timezone.utc)
    end_time = datetime(2026, 2, 10, 19, 0, tzinfo=timezone.utc)
    google_event = {
        "summary": "  Book Club  ",
        "description": "Discuss the pick ",
        "location": " Library ",
        "start": {"dateTime": "2026-02-10T18:00:00Z"},
        "end": {"dateTime": "2026-02-10T19:00:00Z"},
        "extendedProperties": {"private": {"discord_event_id": "42"}},
    }
    discord_snapshot = calendar_sync.DiscordEventSnapshot(
        event_id=42,
        name="Book Club",
        description="Discuss the pick",
        location="Library",
        start_time=start_time,
        end_time=end_time,
    )

    existing_snapshot = calendar_sync._google_event_snapshot(google_event)
    desired_snapshot = calendar_sync._desired_google_snapshot(discord_snapshot)

    assert existing_snapshot == desired_snapshot


def test_event_snapshot_defaults_end_time_to_one_hour():
    start_time = datetime(2026, 2, 12, 20, 0, tzinfo=timezone.utc)
    event = SimpleNamespace(
        id=7,
        name="Chat",
        description="",
        location="Online",
        scheduled_start_time=start_time,
        scheduled_end_time=None,
    )

    snapshot = calendar_sync._event_snapshot(event)

    assert snapshot is not None
    assert snapshot.end_time == start_time + timedelta(hours=1)


def test_snapshot_is_syncable_requires_cutoff_and_future(monkeypatch):
    now = datetime(2026, 1, 15, tzinfo=timezone.utc)
    future_before_cutoff = calendar_sync.DiscordEventSnapshot(
        event_id=1,
        name="Old",
        description="",
        location="",
        start_time=datetime(2026, 1, 30, tzinfo=timezone.utc),
        end_time=datetime(2026, 1, 30, 1, tzinfo=timezone.utc),
    )
    future_after_cutoff = calendar_sync.DiscordEventSnapshot(
        event_id=2,
        name="New",
        description="",
        location="",
        start_time=datetime(2026, 2, 1, tzinfo=timezone.utc),
        end_time=datetime(2026, 2, 1, 1, tzinfo=timezone.utc),
    )

    monkeypatch.setattr(calendar_sync, "utcnow", lambda: now)

    assert calendar_sync._snapshot_is_syncable(future_before_cutoff) is False
    assert calendar_sync._snapshot_is_syncable(future_after_cutoff) is True


def test_normalize_private_key_and_rfc3339():
    assert calendar_sync._normalize_private_key("a\\nb\\n") == "a\nb\n"
    assert (
        calendar_sync._rfc3339(datetime(2026, 2, 2, 12, 0, tzinfo=timezone.utc))
        == "2026-02-02T12:00:00Z"
    )


def test_sync_google_events_updates_deletes_and_inserts(monkeypatch):
    start_time = datetime(2026, 2, 5, 12, 0, tzinfo=timezone.utc)
    end_time = datetime(2026, 2, 5, 13, 0, tzinfo=timezone.utc)
    snapshots = {
        "42": calendar_sync.DiscordEventSnapshot(
            event_id=42,
            name="Event",
            description="Desc",
            location="Room",
            start_time=start_time,
            end_time=end_time,
        ),
        "99": calendar_sync.DiscordEventSnapshot(
            event_id=99,
            name="New Event",
            description="",
            location="",
            start_time=start_time,
            end_time=end_time,
        ),
    }
    tracker = {"deleted": [], "updated": [], "inserted": []}
    pages = {
        "0": {
            "items": [
                {
                    "id": "no-discord",
                    "summary": "Manual",
                    "start": {"dateTime": "2026-02-05T12:00:00Z"},
                    "end": {"dateTime": "2026-02-05T13:00:00Z"},
                },
                {
                    "id": "old-42",
                    "summary": "Old",
                    "description": "Old",
                    "location": "Old",
                    "start": {"dateTime": "2026-02-05T12:00:00Z"},
                    "end": {"dateTime": "2026-02-05T13:00:00Z"},
                    "extendedProperties": {"private": {"discord_event_id": "42"}},
                },
                {
                    "id": "old-77",
                    "summary": "Orphan",
                    "start": {"dateTime": "2026-02-05T12:00:00Z"},
                    "end": {"dateTime": "2026-02-05T13:00:00Z"},
                    "extendedProperties": {"private": {"discord_event_id": "77"}},
                },
            ],
            "nextPageToken": None,
        }
    }

    monkeypatch.setattr(
        calendar_sync, "_build_calendar_service", lambda: _Service(pages, tracker)
    )
    monkeypatch.setattr(calendar_sync, "utcnow", lambda: start_time)

    settings = SimpleNamespace(google_calendar_id="calendar-id")
    calendar_sync._sync_google_events(settings, snapshots)

    assert tracker["deleted"] == ["no-discord", "old-77"]
    assert tracker["inserted"]
    assert any(item[0] == "old-42" for item in tracker["updated"])
