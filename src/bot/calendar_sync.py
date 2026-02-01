from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

import discord
from google.oauth2 import service_account
from googleapiclient.discovery import build
from loguru import logger

from bot.config import get_settings
from bot.utils import utcnow


GOOGLE_CALENDAR_SCOPES = ["https://www.googleapis.com/auth/calendar"]
SYNC_CUTOFF = datetime(2026, 1, 31, tzinfo=timezone.utc)


@dataclass(frozen=True)
class DiscordEventSnapshot:
    event_id: int
    name: str
    description: str
    location: str
    start_time: datetime
    end_time: datetime


@dataclass(frozen=True)
class GoogleEventSnapshot:
    summary: str
    description: str
    location: str
    start_time: datetime
    end_time: datetime
    discord_event_id: str


def _normalize_private_key(value: str) -> str:
    return value.replace("\\n", "\n")


def _build_calendar_service():
    settings = get_settings()
    credentials = service_account.Credentials.from_service_account_info(
        {
            "type": "service_account",
            "client_email": settings.google_service_account_email,
            "private_key": _normalize_private_key(
                settings.google_service_account_private_key
            ),
            "token_uri": "https://oauth2.googleapis.com/token",
        },
        scopes=GOOGLE_CALENDAR_SCOPES,
    )
    return build("calendar", "v3", credentials=credentials, cache_discovery=False)


def _coerce_aware(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value


def _event_start_time(event: discord.ScheduledEvent) -> datetime | None:
    for attr in ("scheduled_start_time", "start_time", "start"):
        value = getattr(event, attr, None)
        if value is not None:
            return value
    return None


def _event_end_time(
    event: discord.ScheduledEvent, start_time: datetime
) -> datetime | None:
    for attr in ("scheduled_end_time", "end_time", "end"):
        value = getattr(event, attr, None)
        if value is not None:
            return value
    return start_time + timedelta(hours=1)


def _event_snapshot(event: discord.ScheduledEvent) -> DiscordEventSnapshot | None:
    start_time = _event_start_time(event)
    if start_time is None:
        return None
    start_time = _coerce_aware(start_time)
    end_time = _coerce_aware(_event_end_time(event, start_time))
    return DiscordEventSnapshot(
        event_id=int(event.id),
        name=str(getattr(event, "name", "")),
        description=str(getattr(event, "description", "") or ""),
        location=str(getattr(event, "location", "") or ""),
        start_time=start_time,
        end_time=end_time,
    )


def _rfc3339(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _discord_event_body(snapshot: DiscordEventSnapshot) -> dict:
    return {
        "summary": snapshot.name,
        "description": snapshot.description,
        "location": snapshot.location,
        "start": {"dateTime": _rfc3339(snapshot.start_time)},
        "end": {"dateTime": _rfc3339(snapshot.end_time)},
        "extendedProperties": {"private": {"discord_event_id": str(snapshot.event_id)}},
    }


def _parse_google_datetime(value: dict) -> datetime | None:
    if "dateTime" in value:
        raw = value["dateTime"]
        if raw.endswith("Z"):
            raw = raw.replace("Z", "+00:00")
        return _coerce_aware(datetime.fromisoformat(raw))
    return None


def _google_event_start(value: dict) -> datetime | None:
    start = value.get("start", {})
    parsed = _parse_google_datetime(start)
    if parsed is None:
        return None
    return _coerce_aware(parsed)


def _google_event_end(value: dict) -> datetime | None:
    end = value.get("end", {})
    parsed = _parse_google_datetime(end)
    if parsed is None:
        return None
    return _coerce_aware(parsed)


def _clean_text(value: str | None) -> str:
    return (value or "").strip()


def _google_event_snapshot(existing: dict) -> GoogleEventSnapshot | None:
    start_time = _google_event_start(existing)
    end_time = _google_event_end(existing)
    if start_time is None or end_time is None:
        return None
    existing_props = existing.get("extendedProperties", {}).get("private", {})
    return GoogleEventSnapshot(
        summary=_clean_text(existing.get("summary")),
        description=_clean_text(existing.get("description")),
        location=_clean_text(existing.get("location")),
        start_time=start_time,
        end_time=end_time,
        discord_event_id=str(existing_props.get("discord_event_id", "")).strip(),
    )


def _desired_google_snapshot(snapshot: DiscordEventSnapshot) -> GoogleEventSnapshot:
    return GoogleEventSnapshot(
        summary=_clean_text(snapshot.name),
        description=_clean_text(snapshot.description),
        location=_clean_text(snapshot.location),
        start_time=_coerce_aware(snapshot.start_time),
        end_time=_coerce_aware(snapshot.end_time),
        discord_event_id=str(snapshot.event_id),
    )


def _snapshot_is_future(snapshot: DiscordEventSnapshot) -> bool:
    return snapshot.start_time >= utcnow()


def _snapshot_is_syncable(snapshot: DiscordEventSnapshot) -> bool:
    return snapshot.start_time >= SYNC_CUTOFF and _snapshot_is_future(snapshot)


def _sync_google_events(settings, snapshots: dict[str, DiscordEventSnapshot]) -> None:
    service = _build_calendar_service()
    time_min = max(utcnow(), SYNC_CUTOFF)
    page_token: str | None = None
    google_events: dict[str, dict] = {}
    delete_candidates: list[dict] = []
    while True:
        response = (
            service.events()
            .list(
                calendarId=settings.google_calendar_id,
                timeMin=_rfc3339(time_min),
                singleEvents=True,
                orderBy="startTime",
                pageToken=page_token,
            )
            .execute()
        )
        for item in response.get("items", []):
            start_time = _google_event_start(item)
            if start_time is None or start_time < SYNC_CUTOFF:
                continue
            props = item.get("extendedProperties", {}).get("private", {})
            discord_event_id = str(props.get("discord_event_id", "")).strip()
            if not discord_event_id:
                delete_candidates.append(item)
            elif discord_event_id in google_events:
                delete_candidates.append(item)
            else:
                google_events[discord_event_id] = item
        page_token = response.get("nextPageToken")
        if not page_token:
            break

    for google_event in delete_candidates:
        logger.info(
            "Deleting Google event {} (no matching Discord event).",
            google_event.get("id"),
        )
        service.events().delete(
            calendarId=settings.google_calendar_id, eventId=google_event["id"]
        ).execute()

    for discord_event_id, google_event in google_events.items():
        if discord_event_id not in snapshots:
            logger.info(
                "Deleting Google event {} (no matching Discord event).",
                google_event.get("id"),
            )
            service.events().delete(
                calendarId=settings.google_calendar_id, eventId=google_event["id"]
            ).execute()
            continue

        snapshot = snapshots[discord_event_id]
        existing_snapshot = _google_event_snapshot(google_event)
        if existing_snapshot is None:
            logger.info(
                "Updating Google event {} from Discord event {}.",
                google_event.get("id"),
                discord_event_id,
            )
            service.events().update(
                calendarId=settings.google_calendar_id,
                eventId=google_event["id"],
                body=_discord_event_body(snapshot),
            ).execute()
        elif existing_snapshot != _desired_google_snapshot(snapshot):
            logger.info(
                "Updating Google event {} from Discord event {}.",
                google_event.get("id"),
                discord_event_id,
            )
            service.events().update(
                calendarId=settings.google_calendar_id,
                eventId=google_event["id"],
                body=_discord_event_body(snapshot),
            ).execute()

    for discord_event_id, snapshot in snapshots.items():
        if discord_event_id in google_events:
            continue
        logger.info("Creating Google event for Discord event {}.", discord_event_id)
        service.events().insert(
            calendarId=settings.google_calendar_id,
            body=_discord_event_body(snapshot),
        ).execute()


async def sync_discord_events_to_google(bot: discord.Client) -> None:
    settings = get_settings()
    guild = bot.get_guild(settings.discord_guild_id)
    if guild is None:
        guild = await bot.fetch_guild(settings.discord_guild_id)

    scheduled_events = await guild.fetch_scheduled_events()
    snapshots: dict[str, DiscordEventSnapshot] = {}
    for event in scheduled_events:
        snapshot = _event_snapshot(event)
        if snapshot is None:
            continue
        if _snapshot_is_syncable(snapshot):
            snapshots[str(snapshot.event_id)] = snapshot

    await asyncio.to_thread(_sync_google_events, settings, snapshots)
