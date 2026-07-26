import asyncio
import hashlib
import json
import os
from dataclasses import dataclass
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime

import aiohttp

DEFAULT_ANCHOR_API_BASE_URL = "https://anchor-api.yashjani.com"
MAX_ANCHOR_MESSAGE_CHARACTERS = 1024
MAX_ATTEMPTS = 3


class AnchorDeliveryError(RuntimeError):
    def __init__(self, message: str, status_code: int | None = None):
        super().__init__(message)
        self.status_code = status_code


@dataclass(frozen=True)
class AnchorNotification:
    title: str
    message: str
    source_url: str | None = None
    source_label: str | None = None


def notification_payload(user_uuid: str, notification: AnchorNotification) -> dict[str, object]:
    canonical_content = {
        "title": notification.title,
        "message": notification.message,
        "source_url": notification.source_url,
        "source_label": notification.source_label,
        "response_requirement": "inform",
        "initial_urgency": "routine",
        "maximum_urgency": "routine",
    }
    encoded = json.dumps(canonical_content, sort_keys=True, separators=(",", ":")).encode("utf-8")
    digest = hashlib.sha256(encoded).hexdigest()
    payload: dict[str, object] = {
        "idempotency_key": f"spotinotifs:{user_uuid}:{digest}",
        "title": notification.title,
        "message": notification.message,
        "response_requirement": "inform",
        "initial_urgency": "routine",
        "maximum_urgency": "routine",
    }
    if notification.source_url:
        payload["source_url"] = notification.source_url
    if notification.source_label:
        payload["source_label"] = notification.source_label
    return payload


async def create_notification(
    user_uuid: str,
    api_key: str,
    notification: AnchorNotification,
    *,
    base_url: str | None = None,
    session: aiohttp.ClientSession | None = None,
) -> None:
    endpoint = f"{(base_url or os.getenv('ANCHOR_API_BASE_URL') or DEFAULT_ANCHOR_API_BASE_URL).rstrip('/')}/v1/notifications"
    headers = {
        "Accept": "application/json",
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "User-Agent": "SpotiNotifs/0.1",
    }
    payload = notification_payload(user_uuid, notification)
    owns_session = session is None
    if session is None:
        session = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=15))

    try:
        for attempt in range(1, MAX_ATTEMPTS + 1):
            try:
                async with session.post(endpoint, headers=headers, json=payload) as response:
                    if response.status in {200, 201}:
                        return
                    if (response.status == 429 or 500 <= response.status < 600) and attempt < MAX_ATTEMPTS:
                        await asyncio.sleep(retry_delay(response.headers.get("Retry-After"), attempt))
                        continue
                    raise AnchorDeliveryError("Anchor rejected the notification", response.status)
            except (TimeoutError, aiohttp.ClientError) as error:
                if attempt == MAX_ATTEMPTS:
                    raise AnchorDeliveryError("Anchor notification request failed") from error
                await asyncio.sleep(float(attempt))
    finally:
        if owns_session:
            await session.close()


def retry_delay(retry_after: str | None, attempt: int) -> float:
    if retry_after:
        try:
            return min(max(float(retry_after), 0.0), 30.0)
        except ValueError:
            try:
                retry_at = parsedate_to_datetime(retry_after)
                if retry_at.tzinfo is None:
                    retry_at = retry_at.replace(tzinfo=UTC)
                return min(max((retry_at - datetime.now(UTC)).total_seconds(), 0.0), 30.0)
            except (TypeError, ValueError, OverflowError):
                pass
    return float(attempt)
