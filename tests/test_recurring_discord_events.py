from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from bot.recurring_discord_events import (
    CreationPlan,
    MonthlyRecurrenceRule,
    RecurringDiscordEventCreator,
)


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
        self.routes.setdefault("posted", []).append(json)
        return self.routes[key].pop(0)


def _settings(**kwargs):
    base = dict(
        discord_guild_id=42,
        discord_token="token",
        bookclub_channel_id=777,
        is_staging=False,
    )
    base.update(kwargs)
    return SimpleNamespace(**base)


def test_monthly_recurrence_rule_second_weekday_occurrence():
    rule = MonthlyRecurrenceRule(byweekday=5, bysetpos=2, at_time=datetime.min.time())
    result = rule.occurrence(2026, 2, timezone.utc)
    assert result.year == 2026
    assert result.month == 2
    assert result.day == 14


def test_build_creation_plan_respects_existing_regex_match():
    creator = RecurringDiscordEventCreator(_settings())
    month_starts = [datetime(2026, 1, 1, tzinfo=timezone.utc).date()]
    existing = [
        {
            "name": "Book club: The Left Hand of Darkness",
            "scheduled_start_time": "2026-01-20T02:00:00Z",
            "status": 1,
        }
    ]

    plan = creator._build_creation_plan(month_starts, existing)
    assert len(plan.to_create) == 0


def test_build_creation_plan_adds_meetup_with_correct_location():
    creator = RecurringDiscordEventCreator(_settings())
    month_starts = [datetime(2026, 4, 1, tzinfo=timezone.utc).date()]

    plan = creator._build_creation_plan(month_starts, [])

    assert len(plan.to_create) == 1
    event = plan.to_create[0]
    assert event.name == "April Meetup"
    assert event.location == "Liberty Park"


def test_run_staging_dry_run_does_not_create(monkeypatch):
    creator = RecurringDiscordEventCreator(_settings(is_staging=True))

    monkeypatch.setattr(
        creator,
        "_target_months",
        lambda _: [datetime(2026, 2, 1, tzinfo=timezone.utc).date()],
    )
    monkeypatch.setattr(creator, "_load_discord_events", lambda: [])
    monkeypatch.setattr(
        creator,
        "_build_creation_plan",
        lambda *_: CreationPlan(to_create=[]),
    )

    created = []
    monkeypatch.setattr(
        creator, "_create_discord_event", lambda event: created.append(event)
    )

    summary = creator.run()
    assert summary["mode"] == "dry-run"
    assert created == []


def test_run_apply_posts_new_events(monkeypatch):
    creator = RecurringDiscordEventCreator(_settings(discord_guild_id=99))
    monkeypatch.setattr(
        creator,
        "_target_months",
        lambda _: [datetime(2026, 2, 1, tzinfo=timezone.utc).date()],
    )

    url = "https://discord.com/api/v10/guilds/99/scheduled-events"
    routes = {
        ("GET", url, frozenset({("with_user_count", "false")})): [_Resp([])],
        ("POST", url): [_Resp({"id": "new"})],
    }
    monkeypatch.setattr(
        "bot.recurring_discord_events.httpx.Client", lambda timeout: _Client(routes)
    )

    summary = creator.run()
    assert summary["mode"] == "apply"
    assert summary["to_create"] == 1
    assert len(routes["posted"]) == 1
    assert routes["posted"][0]["name"] == "February Meetup"


def test_may_event_uses_dst_offset_when_planned_in_february():
    creator = RecurringDiscordEventCreator(_settings())
    may = datetime(2026, 5, 1, tzinfo=timezone.utc).date()
    plan = creator._build_creation_plan([may], [])

    assert len(plan.to_create) == 1
    event = plan.to_create[0]
    assert event.name == "Book Club"
    assert event.start_at.utcoffset() == timezone(timedelta(hours=-6)).utcoffset(None)
    assert creator._datetime_to_rfc3339(event.start_at) == "2026-05-13T01:00:00Z"
