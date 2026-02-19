from __future__ import annotations

import base64
import hashlib
import json
import subprocess
import tempfile
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
from loguru import logger

from bot.config import Settings, get_settings

DISCORD_API_BASE_URL = "https://discord.com/api/v10"
GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
GOOGLE_CALENDAR_API_BASE_URL = "https://www.googleapis.com/calendar/v3"
GOOGLE_CALENDAR_SCOPE = "https://www.googleapis.com/auth/calendar"

DISCORD_STATUS_CANCELED = 4
SYNC_SOURCE = "discord_scheduled_event"
DEFAULT_SAFETY_CUTOFF = datetime(2026, 1, 1, tzinfo=UTC)


class SyncError(RuntimeError):
    """Raised when sync configuration or API interactions fail."""


@dataclass
class SyncPlan:
    to_create: list[dict[str, Any]] = field(default_factory=list)
    to_update: list[dict[str, Any]] = field(default_factory=list)
    to_cancel: list[dict[str, Any]] = field(default_factory=list)
    skipped_before_cutoff: int = 0


class DiscordGoogleCalendarSync:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.timeout_seconds = 20.0
        self.safety_cutoff = DEFAULT_SAFETY_CUTOFF
        self._resolved_discord_guild_id: int | None = settings.discord_guild_id

        private_key = settings.google_service_account_private_key.replace("\\n", "\n")
        self.google_service_account_private_key = private_key
        self._cached_access_token: str | None = None
        self._access_token_expires_at: int = 0

    def run(self) -> dict[str, int | str]:
        discord_events = self._load_discord_events()
        google_events = self._load_google_events()
        apply_changes = not self.settings.is_staging

        plan = self._build_sync_plan(discord_events, google_events)
        summary = {
            "mode": "apply" if apply_changes else "dry-run",
            "discord_events_total": len(discord_events),
            "google_managed_events_total": len(google_events),
            "skipped_before_safety_cutoff": plan.skipped_before_cutoff,
            "to_create": len(plan.to_create),
            "to_update": len(plan.to_update),
            "to_cancel": len(plan.to_cancel),
        }
        logger.info(f"Calendar sync summary: {json.dumps(summary, sort_keys=True)}")

        if not apply_changes:
            return summary

        for event_body in plan.to_create:
            self._create_google_event(event_body)

        for update in plan.to_update:
            self._update_google_event(update["google_event_id"], update["body"])

        for cancel in plan.to_cancel:
            self._cancel_google_event(cancel["google_event_id"])

        logger.info(
            "Applied calendar sync changes: "
            f"created={len(plan.to_create)} "
            f"updated={len(plan.to_update)} "
            f"canceled={len(plan.to_cancel)}"
        )
        return summary

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
            raise SyncError("Discord API returned an unexpected payload type.")
        return payload

    def _load_google_events(self) -> list[dict[str, Any]]:
        url = (
            f"{GOOGLE_CALENDAR_API_BASE_URL}/calendars/"
            f"{self.settings.google_calendar_id}/events"
        )
        params: dict[str, Any] = {
            "singleEvents": "true",
            "showDeleted": "false",
            "maxResults": 2500,
            "timeMin": self._datetime_to_rfc3339(self.safety_cutoff),
        }

        managed: list[dict[str, Any]] = []
        page_token: str | None = None
        with httpx.Client(timeout=self.timeout_seconds) as client:
            while True:
                if page_token:
                    params["pageToken"] = page_token
                elif "pageToken" in params:
                    del params["pageToken"]

                response = client.get(
                    url,
                    headers=self._google_headers(),
                    params=params,
                )
                response.raise_for_status()
                payload = response.json()

                for item in payload.get("items", []):
                    private_props = (item.get("extendedProperties") or {}).get(
                        "private"
                    ) or {}
                    if private_props.get("sync_source") != SYNC_SOURCE:
                        continue
                    if private_props.get("discord_event_id"):
                        managed.append(item)

                page_token = payload.get("nextPageToken")
                if not page_token:
                    break

        return managed

    def _build_sync_plan(
        self,
        discord_events: list[dict[str, Any]],
        google_events: list[dict[str, Any]],
    ) -> SyncPlan:
        plan = SyncPlan()
        existing = self._existing_google_by_discord_id(google_events)
        seen_discord_event_ids: set[str] = set()

        for discord_event in discord_events:
            event_body, skipped_before_cutoff = self._build_google_event_body(
                discord_event
            )
            if skipped_before_cutoff:
                plan.skipped_before_cutoff += 1
                continue
            if event_body is None:
                continue

            discord_event_id = event_body["extendedProperties"]["private"][
                "discord_event_id"
            ]
            seen_discord_event_ids.add(discord_event_id)
            existing_event = existing.get(discord_event_id)

            if existing_event is None:
                plan.to_create.append(event_body)
                continue

            existing_private = (existing_event.get("extendedProperties") or {}).get(
                "private"
            ) or {}
            existing_hash = existing_private.get("sync_hash")
            if (
                existing_hash
                == event_body["extendedProperties"]["private"]["sync_hash"]
            ):
                continue

            plan.to_update.append(
                {
                    "google_event_id": existing_event["id"],
                    "discord_event_id": discord_event_id,
                    "body": event_body,
                }
            )

        for discord_event_id, existing_event in existing.items():
            if discord_event_id in seen_discord_event_ids:
                continue

            google_event_id = existing_event.get("id")
            if not google_event_id:
                continue

            plan.to_cancel.append(
                {
                    "google_event_id": google_event_id,
                    "discord_event_id": discord_event_id,
                }
            )

        return plan

    def _build_google_event_body(
        self,
        discord_event: dict[str, Any],
    ) -> tuple[dict[str, Any] | None, bool]:
        start_at = self._parse_datetime(discord_event.get("scheduled_start_time"))
        if start_at is None:
            return None, False
        if start_at < self.safety_cutoff:
            return None, True

        end_at = self._parse_datetime(discord_event.get("scheduled_end_time"))
        if end_at is None or end_at <= start_at:
            end_at = start_at + timedelta(hours=1)

        description_parts: list[str] = []
        description = discord_event.get("description")
        if description:
            description_parts.append(description)

        body: dict[str, Any] = {
            "summary": discord_event.get("name")
            or f"Discord Event {discord_event['id']}",
            "description": "\n".join(description_parts).strip(),
            "start": {"dateTime": self._datetime_to_rfc3339(start_at)},
            "end": {"dateTime": self._datetime_to_rfc3339(end_at)},
            "status": self._discord_status_to_google(discord_event.get("status")),
            "extendedProperties": {
                "private": {
                    "discord_event_id": str(discord_event["id"]),
                    "discord_guild_id": str(self._discord_guild_id()),
                    "sync_source": SYNC_SOURCE,
                }
            },
        }

        location = self._event_location(discord_event)
        if location:
            body["location"] = location

        body["extendedProperties"]["private"]["sync_hash"] = self._compute_sync_hash(
            body
        )
        return body, False

    def _create_google_event(self, event_body: dict[str, Any]) -> dict[str, Any]:
        url = (
            f"{GOOGLE_CALENDAR_API_BASE_URL}/calendars/"
            f"{self.settings.google_calendar_id}/events"
        )
        with httpx.Client(timeout=self.timeout_seconds) as client:
            response = client.post(url, headers=self._google_headers(), json=event_body)
            response.raise_for_status()
            return response.json()

    def _update_google_event(
        self,
        google_event_id: str,
        event_body: dict[str, Any],
    ) -> dict[str, Any]:
        url = (
            f"{GOOGLE_CALENDAR_API_BASE_URL}/calendars/"
            f"{self.settings.google_calendar_id}/events/{google_event_id}"
        )
        with httpx.Client(timeout=self.timeout_seconds) as client:
            response = client.patch(
                url, headers=self._google_headers(), json=event_body
            )
            response.raise_for_status()
            return response.json()

    def _cancel_google_event(self, google_event_id: str) -> dict[str, Any]:
        url = (
            f"{GOOGLE_CALENDAR_API_BASE_URL}/calendars/"
            f"{self.settings.google_calendar_id}/events/{google_event_id}"
        )
        with httpx.Client(timeout=self.timeout_seconds) as client:
            response = client.patch(
                url,
                headers=self._google_headers(),
                json={"status": "cancelled"},
            )
            response.raise_for_status()
            return response.json()

    def _google_headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self._google_access_token()}",
            "Content-Type": "application/json",
        }

    def _google_access_token(self) -> str:
        now = int(time.time())
        if self._cached_access_token and now < self._access_token_expires_at - 60:
            return self._cached_access_token

        assertion = self._build_service_account_jwt(now)
        form_data = {
            "grant_type": "urn:ietf:params:oauth:grant-type:jwt-bearer",
            "assertion": assertion,
        }

        with httpx.Client(timeout=self.timeout_seconds) as client:
            response = client.post(GOOGLE_TOKEN_URL, data=form_data)
            response.raise_for_status()
            payload = response.json()

        access_token = payload.get("access_token")
        expires_in = payload.get("expires_in", 3600)
        if not access_token:
            raise SyncError(
                "Google token endpoint response did not include access_token."
            )

        self._cached_access_token = str(access_token)
        self._access_token_expires_at = now + int(expires_in)
        return self._cached_access_token

    def _build_service_account_jwt(self, now_epoch: int) -> str:
        header = {"alg": "RS256", "typ": "JWT"}
        payload = {
            "iss": self.settings.google_service_account_email,
            "scope": GOOGLE_CALENDAR_SCOPE,
            "aud": GOOGLE_TOKEN_URL,
            "iat": now_epoch,
            "exp": now_epoch + 3600,
        }

        encoded_header = self._urlsafe_b64(
            json.dumps(header, separators=(",", ":"), sort_keys=True).encode("utf-8")
        )
        encoded_payload = self._urlsafe_b64(
            json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")
        )
        signing_input = f"{encoded_header}.{encoded_payload}".encode("utf-8")

        with tempfile.NamedTemporaryFile(
            "w", delete=True, encoding="utf-8"
        ) as key_file:
            key_file.write(self.google_service_account_private_key)
            key_file.flush()
            sign_proc = subprocess.run(
                ["openssl", "dgst", "-sha256", "-sign", key_file.name],
                input=signing_input,
                capture_output=True,
                check=False,
            )
        if sign_proc.returncode != 0:
            raise SyncError(
                "Failed to sign Google JWT with openssl. Ensure openssl is installed and key is valid."
            )

        signature = self._urlsafe_b64(sign_proc.stdout)
        return f"{encoded_header}.{encoded_payload}.{signature}"

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

    @staticmethod
    def _urlsafe_b64(data: bytes) -> str:
        return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")

    @staticmethod
    def _discord_status_to_google(discord_status: int | None) -> str:
        if discord_status == DISCORD_STATUS_CANCELED:
            return "cancelled"
        return "confirmed"

    @staticmethod
    def _event_location(discord_event: dict[str, Any]) -> str | None:
        metadata = discord_event.get("entity_metadata") or {}
        location = metadata.get("location")
        if location:
            return location

        channel_id = discord_event.get("channel_id")
        if channel_id:
            return f"Discord channel ID: {channel_id}"
        return None

    @staticmethod
    def _compute_sync_hash(event_body: dict[str, Any]) -> str:
        relevant = {
            "summary": event_body.get("summary"),
            "description": event_body.get("description"),
            "location": event_body.get("location"),
            "start": event_body.get("start"),
            "end": event_body.get("end"),
            "status": event_body.get("status", "confirmed"),
        }
        digest = hashlib.sha256(
            json.dumps(relevant, sort_keys=True, separators=(",", ":")).encode("utf-8")
        )
        return digest.hexdigest()

    @staticmethod
    def _existing_google_by_discord_id(
        google_events: list[dict[str, Any]],
    ) -> dict[str, dict[str, Any]]:
        mapped: dict[str, dict[str, Any]] = {}
        for event in google_events:
            private_props = (event.get("extendedProperties") or {}).get("private") or {}
            event_id = private_props.get("discord_event_id")
            if not event_id:
                continue
            mapped[str(event_id)] = event
        return mapped

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
            raise SyncError(
                "Unable to resolve Discord guild ID from BOOKCLUB_CHANNEL_ID."
            )

        self._resolved_discord_guild_id = int(guild_id)
        return self._resolved_discord_guild_id


if __name__ == "__main__":
    DiscordGoogleCalendarSync(get_settings()).run()
