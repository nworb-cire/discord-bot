from __future__ import annotations

import calendar
import json
import re
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, time, timedelta
from typing import Any, Callable, Pattern

import httpx
from loguru import logger

from bot.config import Settings, get_settings
from bot.utils import MOUNTAIN, utcnow

DISCORD_API_BASE_URL = "https://discord.com/api/v10"
DISCORD_STATUS_CANCELED = 4
DISCORD_PRIVACY_LEVEL_GUILD_ONLY = 2
DISCORD_ENTITY_TYPE_EXTERNAL = 3
REGISTRY: list["MonthlyEventSeries"] = []


class RecurringEventError(RuntimeError):
    """Raised when recurring event configuration or API interactions fail."""


@dataclass(frozen=True)
class MonthlyRecurrenceRule:
    """ICAL-style monthly recurrence fields for nth weekday rules."""

    byweekday: int
    bysetpos: int
    at_time: time

    def occurrence(self, year: int, month: int, tz) -> datetime:
        month_weeks = calendar.monthcalendar(year, month)
        weekday_dates = [
            week[self.byweekday] for week in month_weeks if week[self.byweekday]
        ]
        if not weekday_dates:
            raise RecurringEventError(
                f"Could not resolve weekday={self.byweekday} for {year}-{month:02d}."
            )

        index = self.bysetpos - 1
        if index < 0 or index >= len(weekday_dates):
            raise RecurringEventError(
                f"BYSETPOS={self.bysetpos} is out of range for {year}-{month:02d}."
            )

        day_of_month = weekday_dates[index]
        return datetime(
            year,
            month,
            day_of_month,
            self.at_time.hour,
            self.at_time.minute,
            tzinfo=tz,
        )


@dataclass(frozen=True)
class PlannedDiscordEvent:
    series_id: str
    month_key: str
    name: str
    start_at: datetime
    end_at: datetime
    location: str
    description: str | None = None


@dataclass(frozen=True)
class MonthlyEventSeries:
    series_id: str
    recurrence: MonthlyRecurrenceRule
    duration: timedelta
    include_month: Callable[[int], bool]
    title_factory: Callable[[date], str]
    location_factory: Callable[[date], str]
    name_matcher_factory: Callable[[date], Pattern[str]]
    description_factory: Callable[[date], str | None] = lambda _month_start: None

    def __post_init__(self) -> None:
        REGISTRY.append(self)

    def applies_to(self, month_start: date) -> bool:
        return self.include_month(month_start.month)

    def month_key(self, month_start: date) -> str:
        return f"{month_start.year:04d}-{month_start.month:02d}"

    def matches_name(self, value: str, month_start: date) -> bool:
        return bool(self.name_matcher_factory(month_start).search(value))


@dataclass
class CreationPlan:
    to_create: list[PlannedDiscordEvent] = field(default_factory=list)


class RecurringDiscordEventCreator:
    def __init__(
        self,
        settings: Settings,
        series: list[MonthlyEventSeries] | None = None,
    ):
        self.settings = settings
        self.timeout_seconds = 20.0
        self._resolved_discord_guild_id: int | None = settings.discord_guild_id
        self.series: list[MonthlyEventSeries] = (
            list(series) if series is not None else list(REGISTRY)
        )

    def run(self, months_ahead: int = 6) -> dict[str, int | str]:
        month_starts = self._target_months(months_ahead)
        existing_discord_events = self._load_discord_events()
        apply_changes = not self.settings.is_staging

        plan = self._build_creation_plan(month_starts, existing_discord_events)
        summary = {
            "mode": "apply" if apply_changes else "dry-run",
            "target_months": len(month_starts),
            "existing_events_total": len(existing_discord_events),
            "to_create": len(plan.to_create),
        }
        logger.info(
            "Recurring Discord event creation summary: "
            f"{json.dumps(summary, sort_keys=True)}"
        )

        if not apply_changes:
            return summary

        for event in plan.to_create:
            self._create_discord_event(event)

        logger.info("Created {} recurring Discord events.", len(plan.to_create))
        return summary

    def _build_creation_plan(
        self,
        month_starts: list[date],
        existing_discord_events: list[dict[str, Any]],
    ) -> CreationPlan:
        plan = CreationPlan()

        for month_start in month_starts:
            for series in self.series:
                if not series.applies_to(month_start):
                    continue
                if self._has_matching_existing_event(
                    existing_discord_events,
                    month_start,
                    series,
                ):
                    continue
                plan.to_create.append(self._build_planned_event(series, month_start))

        return plan

    @staticmethod
    def _build_planned_event(
        series: MonthlyEventSeries,
        month_start: date,
    ) -> PlannedDiscordEvent:
        start_at = series.recurrence.occurrence(
            month_start.year, month_start.month, MOUNTAIN
        )
        return PlannedDiscordEvent(
            series_id=series.series_id,
            month_key=series.month_key(month_start),
            name=series.title_factory(month_start),
            start_at=start_at,
            end_at=start_at + series.duration,
            location=series.location_factory(month_start),
            description=series.description_factory(month_start),
        )

    def _has_matching_existing_event(
        self,
        existing_discord_events: list[dict[str, Any]],
        month_start: date,
        series: MonthlyEventSeries,
    ) -> bool:
        for event in existing_discord_events:
            if event.get("status") == DISCORD_STATUS_CANCELED:
                continue

            start_at = self._parse_datetime(event.get("scheduled_start_time"))
            if start_at is None:
                continue
            start_local = start_at.astimezone(MOUNTAIN)
            if (
                start_local.year != month_start.year
                or start_local.month != month_start.month
            ):
                continue

            event_name = str(event.get("name") or "")
            if series.matches_name(event_name, month_start):
                return True

        return False

    def _target_months(self, months_ahead: int) -> list[date]:
        if months_ahead <= 0:
            return []

        now_local = utcnow().astimezone(MOUNTAIN)
        year = now_local.year
        month = now_local.month
        out: list[date] = []
        for _ in range(months_ahead):
            out.append(date(year, month, 1))
            month += 1
            if month > 12:
                month = 1
                year += 1
        return out

    def _load_discord_events(self) -> list[dict[str, Any]]:
        url = (
            f"{DISCORD_API_BASE_URL}/guilds/"
            f"{self._discord_guild_id()}/scheduled-events"
        )
        headers = {
            "Authorization": f"Bot {self.settings.discord_token}",
            "Content-Type": "application/json",
        }
        params = {"with_user_count": "false"}

        with httpx.Client(timeout=self.timeout_seconds) as client:
            response = client.get(url, headers=headers, params=params)
            response.raise_for_status()
            payload = response.json()

        if not isinstance(payload, list):
            raise RecurringEventError(
                "Discord API returned an unexpected payload type."
            )
        return payload

    def _create_discord_event(
        self, planned_event: PlannedDiscordEvent
    ) -> dict[str, Any]:
        url = (
            f"{DISCORD_API_BASE_URL}/guilds/"
            f"{self._discord_guild_id()}/scheduled-events"
        )
        headers = {
            "Authorization": f"Bot {self.settings.discord_token}",
            "Content-Type": "application/json",
        }
        body: dict[str, Any] = {
            "name": planned_event.name,
            "privacy_level": DISCORD_PRIVACY_LEVEL_GUILD_ONLY,
            "scheduled_start_time": self._datetime_to_rfc3339(planned_event.start_at),
            "scheduled_end_time": self._datetime_to_rfc3339(planned_event.end_at),
            "entity_type": DISCORD_ENTITY_TYPE_EXTERNAL,
            "entity_metadata": {"location": planned_event.location},
        }
        if planned_event.description:
            body["description"] = planned_event.description

        with httpx.Client(timeout=self.timeout_seconds) as client:
            response = client.post(url, headers=headers, json=body)
            response.raise_for_status()
            return response.json()

    @staticmethod
    def _parse_datetime(value: str | None) -> datetime | None:
        if not value:
            return None
        normalized = value.replace("Z", "+00:00")
        parsed = datetime.fromisoformat(normalized)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=UTC)
        return parsed.astimezone(UTC)

    @staticmethod
    def _datetime_to_rfc3339(value: datetime) -> str:
        return value.astimezone(UTC).isoformat().replace("+00:00", "Z")

    def _discord_guild_id(self) -> int:
        if self._resolved_discord_guild_id is not None:
            return self._resolved_discord_guild_id

        channel_url = (
            f"{DISCORD_API_BASE_URL}/channels/{self.settings.bookclub_channel_id}"
        )
        headers = {
            "Authorization": f"Bot {self.settings.discord_token}",
            "Content-Type": "application/json",
        }
        with httpx.Client(timeout=self.timeout_seconds) as client:
            response = client.get(channel_url, headers=headers)
            response.raise_for_status()
            payload = response.json()

        guild_id = payload.get("guild_id")
        if guild_id is None:
            raise RecurringEventError(
                "Unable to resolve Discord guild ID from BOOKCLUB_CHANNEL_ID."
            )

        self._resolved_discord_guild_id = int(guild_id)
        return self._resolved_discord_guild_id


def _month_name(month_start: date) -> str:
    return calendar.month_name[month_start.month]


MEETUP_SERIES = MonthlyEventSeries(
    series_id="meetup",
    recurrence=MonthlyRecurrenceRule(
        byweekday=calendar.SATURDAY,
        bysetpos=2,
        at_time=time(hour=15, minute=0),
    ),
    duration=timedelta(hours=2),
    include_month=lambda month: month % 2 == 0,
    title_factory=lambda month_start: f"{_month_name(month_start)} Meetup",
    location_factory=lambda month_start: (
        "Liberty Park" if 4 <= month_start.month <= 8 else "TBD"
    ),
    name_matcher_factory=lambda month_start: re.compile(
        rf"(?i)\b{re.escape(_month_name(month_start))}\b.*\bmeetup\b"
    ),
)

BOOK_CLUB_SERIES = MonthlyEventSeries(
    series_id="book_club",
    recurrence=MonthlyRecurrenceRule(
        byweekday=calendar.TUESDAY,
        bysetpos=2,
        at_time=time(hour=19, minute=0),
    ),
    duration=timedelta(hours=1, minutes=30),
    include_month=lambda month: month % 2 == 1,
    title_factory=lambda _month_start: "Book Club",
    location_factory=lambda _month_start: "TBD",
    name_matcher_factory=lambda _month_start: re.compile(r"(?i)^book\s*club(?:\b|:)"),
)


if __name__ == "__main__":
    RecurringDiscordEventCreator(get_settings()).run()
