import argparse
import asyncio
import json
import os
import sys
import time
from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import Any
from urllib.parse import urlparse

import aiohttp
import discord
import requests
from dotenv import load_dotenv

import anchor
import OAuth2
import sql
from anchor_credentials import (
    CredentialConfigurationError,
    CredentialDecryptionError,
    decrypt_api_key,
)
from logging_config import configure_logging, get_logger

logger = get_logger(__name__)

BREAKPOINT = 100


@dataclass(frozen=True)
class RunOptions:
    today: date
    is_new_day: bool
    start_date: date | None = None
    end_date: date | None = None

    @property
    def catchup(self) -> bool:
        return self.start_date is not None

    @property
    def mode(self) -> str:
        return "catchup" if self.catchup else "daily"

    @property
    def date_range(self) -> str:
        return f"{self.start_date} through {self.end_date}"

    def includes(self, release_date: Any) -> bool:
        if not isinstance(release_date, str) or len(release_date) != 10:
            return False
        try:
            released = date.fromisoformat(release_date)
        except ValueError:
            return False
        if released.isoformat() != release_date:
            return False
        if self.start_date is not None and self.end_date is not None:
            return self.start_date <= released <= self.end_date
        return released == self.today

FOLLOWING_ARTISTS_URL  = "https://api.spotify.com/v1/me/following"
ARTIST_ALBUMS_URL      = "https://api.spotify.com/v1/artists/{artist_id}/albums"
ME_URL                 = "https://api.spotify.com/v1/me"
ME_PLAYLISTS_URL       = "https://api.spotify.com/v1/me/playlists"
ME_FOLLOW_PLAYLIST_URL = "https://api.spotify.com/v1/playlists/{playlist_id}/followers"
ALBUM_URL              = "https://api.spotify.com/v1/albums/{album_id}"
CREATE_PLAYLIST_URL    = "https://api.spotify.com/v1/users/{user_id}/playlists"
GET_PLAYLIST_URL       = "https://api.spotify.com/v1/playlists/{playlist_id}"
ADD_TO_PLAYLIST_URL    = "https://api.spotify.com/v1/playlists/{playlist_id}/tracks"

def user_log_context(user: sql.User) -> dict[str, str | None]:
    return user.log_context()

def endpoint_name(url: str) -> str:
    path = urlparse(url).path
    if path == "/v1/me/following":
        return "spotify_following_artists"
    if path == "/v1/me":
        return "spotify_current_user"
    if path == "/v1/me/playlists":
        return "spotify_playlists"
    if "/albums" in path:
        return "spotify_albums"
    if "/playlists" in path and "/tracks" in path:
        return "spotify_playlist_tracks"
    if "/playlists" in path:
        return "spotify_playlist"
    return "spotify_api"

async def spotify_request(user: sql.User, url: str, session: aiohttp.ClientSession, params: dict[str, str] | None = None) -> dict[str, Any]:
    params = params or {}
    headers = {"Authorization": f"Bearer {user.access_token}"}
    attempts = 3
    while attempts > 0:
        attempt_number = 4 - attempts
        if attempts != 3:
            logger.info(
                "Retrying Spotify request",
                extra={
                    "event": "spotify_request_retry",
                    "endpoint": endpoint_name(url),
                    "attempt": attempt_number,
                    "max_attempts": 3,
                    **user_log_context(user),
                },
            )
        try:
            async with session.get(url, params=params, headers=headers) as response:
                response.raise_for_status()
                return await response.json()
        except aiohttp.ClientResponseError as e:
            if e.status == 429:
                seconds_to_wait = max(0, int((e.headers or {}).get('Retry-After', '0')))
                
                logger.warning(
                    "Spotify request rate limited",
                    extra={
                        "event": "spotify_request_rate_limited",
                        "endpoint": endpoint_name(url),
                        "status_code": e.status,
                        "retry_after_seconds": seconds_to_wait,
                        "attempt": attempt_number,
                        **user_log_context(user),
                    },
                )
                if seconds_to_wait > 60:
                    raise RuntimeError(f"Spotify rate limit requires waiting {seconds_to_wait} seconds") from e
                
                await asyncio.sleep(seconds_to_wait)
            elif e.status == 403:
                logger.error(
                    "Spotify request forbidden",
                    extra={
                        "event": "spotify_request_forbidden",
                        "endpoint": endpoint_name(url),
                        "status_code": e.status,
                        **user_log_context(user),
                    },
                )
                raise
            elif 500 <= e.status < 600:
                # Handle 500-level server errors with exponential backoff
                wait_time = (3 - attempts) * 2  # Exponential backoff: 2, 4 seconds
                logger.warning(
                    "Spotify request returned server error",
                    extra={
                        "event": "spotify_request_server_error",
                        "endpoint": endpoint_name(url),
                        "status_code": e.status,
                        "retry_after_seconds": wait_time,
                        "attempt": attempt_number,
                        **user_log_context(user),
                    },
                )
                await asyncio.sleep(wait_time)
                attempts -= 1
                continue
            else:
                logger.exception(
                    "Spotify request failed",
                    extra={
                        "event": "spotify_request_failed",
                        "endpoint": endpoint_name(url),
                        "status_code": e.status,
                        "attempt": attempt_number,
                        **user_log_context(user),
                    },
                )
                raise 
        attempts -= 1
    logger.error(
        "Spotify request exhausted retries",
        extra={"event": "spotify_request_retries_exhausted", "endpoint": endpoint_name(url), **user_log_context(user)},
    )
    raise RuntimeError("Spotify request exhausted retries")

def spotify_request_sync(user: sql.User, url: str, params: dict[str, str] | None = None, body: dict[str, Any] | None = None, method: str = "GET") -> dict[str, Any]:
    params = params or {}
    body = body or {}
    headers = {"Authorization": f"Bearer {user.access_token}"}
    attempts = 3
    while attempts > 0:
        attempt_number = 4 - attempts
        if attempts != 3:
            logger.info(
                "Retrying Spotify request",
                extra={
                    "event": "spotify_request_retry",
                    "endpoint": endpoint_name(url),
                    "method": method,
                    "attempt": attempt_number,
                    "max_attempts": 3,
                    **user_log_context(user),
                },
            )
        try:
            if method == "GET":
                response = requests.get(url, params=params, headers=headers)
            elif method == "POST":
                response = requests.post(url, params=params, headers=headers, json=body)
            else:
                raise ValueError(f"Unsupported HTTP method: {method}")
    
            response.raise_for_status()
            return response.json()
        except requests.exceptions.RequestException as e:
            if e.response is not None and e.response.status_code == 429:
                retry_after = e.response.headers.get('Retry-After')
                seconds_to_wait = max(0, int(retry_after)) if retry_after else 0
                
                logger.warning(
                    "Spotify request rate limited",
                    extra={
                        "event": "spotify_request_rate_limited",
                        "endpoint": endpoint_name(url),
                        "method": method,
                        "status_code": e.response.status_code,
                        "retry_after_seconds": seconds_to_wait,
                        "attempt": attempt_number,
                        **user_log_context(user),
                    },
                )
                if seconds_to_wait > 60:
                    raise RuntimeError(f"Spotify rate limit requires waiting {seconds_to_wait} seconds") from e
                
                time.sleep(seconds_to_wait)
            elif e.response is not None and e.response.status_code == 403:
                logger.error(
                    "Spotify request forbidden",
                    extra={
                        "event": "spotify_request_forbidden",
                        "endpoint": endpoint_name(url),
                        "method": method,
                        "status_code": e.response.status_code,
                        **user_log_context(user),
                    },
                )
                raise
            elif e.response is not None and 500 <= e.response.status_code < 600:
                # Handle 500-level server errors with exponential backoff
                wait_time = (3 - attempts) * 2  # Exponential backoff: 2, 4 seconds
                logger.warning(
                    "Spotify request returned server error",
                    extra={
                        "event": "spotify_request_server_error",
                        "endpoint": endpoint_name(url),
                        "method": method,
                        "status_code": e.response.status_code,
                        "retry_after_seconds": wait_time,
                        "attempt": attempt_number,
                        **user_log_context(user),
                    },
                )
                time.sleep(wait_time)
                attempts -= 1
                continue
            else:
                status_code = e.response.status_code if getattr(e, "response", None) is not None else None
                logger.exception(
                    "Spotify request failed",
                    extra={
                        "event": "spotify_request_failed",
                        "endpoint": endpoint_name(url),
                        "method": method,
                        "status_code": status_code,
                        "attempt": attempt_number,
                        **user_log_context(user),
                    },
                )
                raise
        attempts -= 1
    logger.error(
        "Spotify request exhausted retries",
        extra={"event": "spotify_request_retries_exhausted", "endpoint": endpoint_name(url), "method": method, **user_log_context(user)},
    )
    raise RuntimeError("Spotify request exhausted retries")

def get_all_artists(user: sql.User) -> list[dict]:
    artists = []
    next_cursor = None
    
    while True:
        try:
            params = {
                "type": "artist",
                "limit": "50",
                "after": next_cursor or ""
            }
            response = spotify_request_sync(user, FOLLOWING_ARTISTS_URL, params)['artists']
            artists.extend(response['items'])
            next_cursor = response['cursors']['after']
        except requests.exceptions.RequestException:
            logger.exception("Error requesting followed artists", extra={"event": "spotify_followed_artists_failed", **user_log_context(user)})
            raise
        if not next_cursor:
            break
    
    logger.info("Fetched followed artists", extra={"event": "spotify_followed_artists_succeeded", "artist_count": len(artists), **user_log_context(user)})
    return artists

async def get_all_albums(user: sql.User, artist_id: str, session: aiohttp.ClientSession, semaphore: asyncio.Semaphore) -> list[dict[str, Any]]:
    albums = []
    
    next_url = None
    while True:
        if next_url:
            async with semaphore:
                response = await spotify_request(user, next_url, session)
        else:
            async with semaphore:
                response = await spotify_request(user, ARTIST_ALBUMS_URL.format(artist_id=artist_id), session, {
                    "limit": "50",
                    "include_groups": "album,single,appears_on",
                    "market": "US",
                })
        
        for item in response['items']:
            if item['album_type'] == "compilation":
                continue
            albums.append(item)
        
        next_url = response['next']
        
        if not next_url:
            break

    return albums

async def recent_20_for_each_category_album(user: sql.User, artist_id: str, session: aiohttp.ClientSession, semaphore: asyncio.Semaphore) -> list[dict[str, Any]]:
    albums = []
    for category in ["album", "single", "appears_on"]:
        async with semaphore:
            response = await spotify_request(user, ARTIST_ALBUMS_URL.format(artist_id=artist_id), session, {
                "limit": "20",
                "include_groups": category,
                "market": "US"
            })
    
        albums.extend(response['items'])
    return albums

async def check_playlist_exists(user: sql.User) -> bool:
    items = []
    next = None
    link = ME_PLAYLISTS_URL
    
    while True:
        response = spotify_request_sync(user, link, params={"limit": "50"})
        items.extend(response['items'])
        next = response['next']
        link = next
        if not next:
            break
    
    for item in items:
        if item['id'] == user.playlist_id:
            return True
    return False

async def create_playlist(user: sql.User) -> str:
    logger.info("Creating Spotify playlist", extra={"event": "spotify_playlist_create_started", **user_log_context(user)})
    response = spotify_request_sync(user, ME_URL)
    id = response['id']
    
    body = {
        "name": "SpotiNotif",
        "description": "New Releases from your followed artists",
        "public": True
    }
    
    response = spotify_request_sync(user, CREATE_PLAYLIST_URL.format(user_id=id), body=body, method="POST")
    playlist_id = response['id']
    logger.info("Created Spotify playlist", extra={"event": "spotify_playlist_create_succeeded", "playlist_id": playlist_id, **user_log_context(user)})
    return playlist_id

async def add_to_playlist(user: sql.User, new_releases) -> None:
    if not user.playlist_id:
        logger.info("Playlist update skipped", extra={"event": "playlist_update_skipped", "reason": "user_has_no_playlist", **user_log_context(user)})
        return
    release_count = sum(len(songs) for songs in new_releases.values())
    logger.info("Playlist update started", extra={"event": "playlist_update_started", "release_count": release_count, **user_log_context(user)})
    try:
        if not await check_playlist_exists(user):
            logger.info("Configured playlist was not found", extra={"event": "playlist_missing", **user_log_context(user)})
            user.playlist_id = await create_playlist(user)
            sql.update_user_playlist_id(user, user.playlist_id)

        uris = []
        for songs in new_releases.values():
            for song in songs.values():
                link = song['id']
                response = spotify_request_sync(user, ALBUM_URL.format(album_id=link))
    
                items = response['tracks']['items']
                next_url = response['tracks']['next']
                while next_url:
                    response = spotify_request_sync(user, next_url)
                    items.extend(response['items'])
                    next_url = response['next']
                uris.extend([item['uri'] for item in items])
    
        for offset in range(0, len(uris), BREAKPOINT):
            body = {"uris": uris[offset : offset + BREAKPOINT]}
            spotify_request_sync(user, ADD_TO_PLAYLIST_URL.format(playlist_id=user.playlist_id), body=body, method="POST")
        logger.info(
            "Playlist update succeeded",
            extra={"event": "playlist_update_succeeded", "release_count": release_count, "track_count": len(uris), **user_log_context(user)},
        )
    except Exception:
        logger.exception("Playlist update failed", extra={"event": "playlist_update_failed", "release_count": release_count, **user_log_context(user)})
        raise

async def new_releases(user: sql.User, options: RunOptions) -> tuple[str, int, anchor.AnchorNotification]:
    logger.info("Refreshing Spotify token for user", extra={"event": "spotify_refresh_token_started", **user_log_context(user)})
    try:
        token_info = OAuth2.refresh_access_token(user.refresh_token)
    except Exception:
        logger.exception("Spotify token refresh failed", extra={"event": "spotify_refresh_token_failed", **user_log_context(user)})
        raise
    
    access_token = token_info['access_token']
    user.access_token = access_token
    logger.info("Spotify token refreshed for user", extra={"event": "spotify_refresh_token_succeeded", **user_log_context(user)})
    
    try:
        artists = get_all_artists(user)
    except Exception:
        logger.exception("Error requesting artists", extra={"event": "spotify_artists_request_failed", **user_log_context(user)})
        raise
    
    artists_ids = [(artist['id'], artist['name']) for artist in artists]
    logger.info("Starting artist processing", extra={"event": "artist_processing_started", "artist_count": len(artists_ids), **user_log_context(user)})
    
    new_releases = {}
    songs_already_added = user.get_items()
    if not options.catchup and options.is_new_day:
        user.reset_items()
    
    semaphore = asyncio.Semaphore(1)
    async with aiohttp.ClientSession() as session:
        async def process_single_artist(artist_id, artist_name):
            if not options.catchup:
                albums = await recent_20_for_each_category_album(user, artist_id, session, semaphore)
            else:
                albums = await get_all_albums(user, artist_id, session, semaphore)

            new_songs = {}
            
            for album in albums:
                album_id = album.get('id')
                if not album_id or not options.includes(album.get('release_date')):
                    continue
                if options.catchup:
                    new_songs[album_id] = album
                elif album_id not in songs_already_added:
                    user.add_item(album_id)
                    new_songs[album_id] = album
                            
            return artist_name, new_songs if new_songs else None
        
        tasks = [process_single_artist(artist_id, artist_name) for artist_id, artist_name in artists_ids]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        failed_artists = 0
        for result in results:
            if isinstance(result, Exception):
                logger.exception("Error processing artist", exc_info=(type(result), result, result.__traceback__), extra={"event": "artist_processing_failed", **user_log_context(user)})
                failed_artists += 1
                continue
            if isinstance(result, tuple) and len(result) == 2:
                artist_name, new_songs = result
            else:
                logger.warning("Unexpected artist result format", extra={"event": "artist_processing_unexpected_result", **user_log_context(user)})
                failed_artists += 1
                continue
            if new_songs:
                new_releases[artist_name] = new_songs
        if failed_artists:
            raise RuntimeError(f"Release scan incomplete: {failed_artists} artist(s) failed")
        if not options.catchup:
            sql.update_user_items(user)
    
    release_count = sum(len(songs) for songs in new_releases.values())
    logger.info(
        "Finished release scan",
        extra={
            "event": "release_scan_finished",
            "artist_count": len(artists_ids),
            "artist_with_release_count": len(new_releases),
            "new_release_count": release_count,
            **user_log_context(user),
        },
    )
    message = ""
    if len(new_releases) > 0:
        if options.catchup:
            message += f"New Releases! {options.date_range}\n\n"
        else:
            message += f"New Releases! {options.today.strftime('%m/%d')}\n\n"
            if not options.is_new_day:
                message += "Strays from today:\n"
        for artist, songs in new_releases.items():
            message += f"**{artist}**\n"
            for song in songs.values():
                message += f"* [{song['name']}]({song['external_urls']['spotify']})\n"
            message += "\n"
        
        await add_to_playlist(user, new_releases)
    else:
        if options.catchup:
            message += f"No new releases from {options.date_range}!\n\n"
        elif options.is_new_day:
            message += f"No new releases today! {options.today.strftime('%m/%d')}\n\n"
        else:
            message += f"No strays today! {options.today.strftime('%m/%d')}\n\n"
    return message, release_count, build_anchor_notification(user, new_releases, release_count, options)

def build_anchor_notification(
    user: sql.User,
    releases: dict[str, dict[str, dict[str, Any]]],
    release_count: int,
    options: RunOptions,
) -> anchor.AnchorNotification:
    today = options.today.strftime('%m/%d')
    if options.catchup:
        title = f"Spotify New Releases {options.date_range}"
    elif options.is_new_day:
        title = f"Spotify New Releases {today}"
    else:
        title = f"Spotify Strays {today}"

    if not releases:
        if options.catchup:
            message = f"No new releases from {options.date_range}!"
        elif options.is_new_day:
            message = f"No new releases today! {today}"
        else:
            message = f"No strays today! {today}"
        return anchor.AnchorNotification(title=title, message=message)

    full_lines: list[str] = []
    compact_rows: list[str] = []
    for artist, songs in releases.items():
        full_lines.append(artist)
        for song in songs.values():
            song_name = str(song['name'])
            spotify_url = str(song['external_urls']['spotify'])
            full_lines.append(f"- {song_name}: {spotify_url}")
            compact_rows.append(f"{artist} - {song_name}")
        full_lines.append("")
    full_message = "\n".join(full_lines).rstrip()

    if len(full_message) <= anchor.MAX_ANCHOR_MESSAGE_CHARACTERS:
        message = full_message
    else:
        message = compact_anchor_summary(release_count, len(releases), compact_rows)

    source_url = None
    source_label = None
    if user.playlist_id:
        source_url = f"https://open.spotify.com/playlist/{user.playlist_id}"
        source_label = "Open SpotiNotif playlist"
    return anchor.AnchorNotification(
        title=title,
        message=message,
        source_url=source_url,
        source_label=source_label,
    )


def compact_anchor_summary(release_count: int, artist_count: int, rows: list[str]) -> str:
    summary = f"{release_count} new releases from {artist_count} artists."
    included_rows: list[str] = []
    for index, row in enumerate(rows):
        remaining = len(rows) - index - 1
        suffix = f"\n+{remaining} more. Full list was sent on Discord." if remaining else ""
        candidate = "\n".join([summary, *included_rows, row]) + suffix
        if len(candidate) > anchor.MAX_ANCHOR_MESSAGE_CHARACTERS:
            break
        included_rows.append(row)

    omitted = len(rows) - len(included_rows)
    result = "\n".join([summary, *included_rows])
    if omitted:
        result += f"\n+{omitted} more. Full list was sent on Discord."
    return result[:anchor.MAX_ANCHOR_MESSAGE_CHARACTERS]


async def send_anchor_notification(user: sql.User, notification: anchor.AnchorNotification) -> str:
    if not user.anchor_api_key_ciphertext:
        logger.info(
            "Anchor notification skipped",
            extra={"event": "anchor_notification_skipped", "reason": "not_configured", **user_log_context(user)},
        )
        return "skipped"

    try:
        api_key = decrypt_api_key(user.anchor_api_key_ciphertext)
        await anchor.create_notification(user.user_UUID, api_key, notification)
        logger.info(
            "Anchor notification created",
            extra={"event": "anchor_notification_succeeded", **user_log_context(user)},
        )
        return "succeeded"
    except (CredentialConfigurationError, CredentialDecryptionError) as error:
        logger.error(
            "Anchor credential is unavailable",
            extra={
                "event": "anchor_notification_failed",
                "reason": type(error).__name__,
                **user_log_context(user),
            },
        )
    except anchor.AnchorDeliveryError as error:
        logger.warning(
            "Anchor notification delivery failed",
            extra={
                "event": "anchor_notification_failed",
                "reason": "request_failed",
                "status_code": error.status_code,
                **user_log_context(user),
            },
        )
    except Exception:
        logger.exception(
            "Unexpected Anchor notification failure",
            extra={"event": "anchor_notification_failed", "reason": "unexpected_error", **user_log_context(user)},
        )
    return "failed"


async def process_user(user: sql.User, options: RunOptions, bot: discord.Client) -> tuple[bool, int, str]:
    user_started_at = time.monotonic()
    logger.info("User processing started", extra={"event": "user_processing_started", **user_log_context(user)})
    try:
        if options.catchup:
            await send_message(user, f"Catching up on releases from {options.date_range}!", bot)
        elif options.is_new_day:
            await send_message(user, "Finding new releases for the day!", bot)
        else:
            await send_message(user, "Catching up on any strays from today!", bot)

        message, release_count, anchor_notification = await new_releases(user, options)
        await send_message(user, message, bot)
        anchor_status = await send_anchor_notification(user, anchor_notification)
        logger.info(
            "User processing finished",
            extra={
                "event": "user_processing_finished",
                "status": "succeeded",
                "duration_seconds": round(time.monotonic() - user_started_at, 3),
                "new_release_count": release_count,
                "anchor_status": anchor_status,
                **user_log_context(user),
            },
        )
        return True, release_count, anchor_status
    except Exception:
        logger.exception(
            "User processing failed",
            extra={
                "event": "user_processing_finished",
                "status": "failed",
                "duration_seconds": round(time.monotonic() - user_started_at, 3),
                **user_log_context(user),
            },
        )
        await error_message(Exception(f"Error processing user: {user.safe_str()}"), bot)
        return False, 0, "not_attempted"

async def send_message(user: sql.User, message: str, bot: discord.Client):
    # Split message if it's too long
    messages = split_long_message(message)
    logger.info(
        "Discord message send started",
        extra={"event": "discord_message_send_started", "message_part_count": len(messages), **user_log_context(user)},
    )
    
    if user.discord_id:
        try:
            discord_user = await bot.fetch_user(user.discord_id)
            for msg in messages:
                await discord_user.send(msg)
            logger.info(
                "Discord message sent by user ID",
                extra={"event": "discord_message_send_succeeded", "delivery_method": "discord_id", "message_part_count": len(messages), **user_log_context(user)},
            )
        except discord.NotFound:
            logger.warning("Discord user ID not found", extra={"event": "discord_message_send_failed", "reason": "user_not_found", **user_log_context(user)})
            raise
        except discord.Forbidden:
            logger.warning("Discord user DMs are closed", extra={"event": "discord_message_send_failed", "reason": "dms_closed", **user_log_context(user)})
            raise
        except Exception:
            logger.exception("Discord message send failed", extra={"event": "discord_message_send_failed", "reason": "unexpected_error", **user_log_context(user)})
            raise
    else:
        for client in bot.guilds:
            for member in client.members:
                if member.name == user.discord_username:
                    sql.update_user_discord_id(user, str(member.id))
                    user.discord_id = str(member.id)
                    for msg in messages:
                        await member.send(msg)
                    logger.info(
                        "Discord message sent by username lookup",
                        extra={
                            "event": "discord_message_send_succeeded",
                            "delivery_method": "guild_member_lookup",
                            "message_part_count": len(messages),
                            **user_log_context(user),
                        },
                    )
                    return
        logger.warning("Discord member was not found", extra={"event": "discord_message_send_failed", "reason": "member_not_found", **user_log_context(user)})
        raise RuntimeError("Discord member was not found")

def split_long_message(message: str, max_length: int = 1900) -> list[str]:
    """Split a message that's too long by looking for \n delimiters"""
    if len(message) <= max_length:
        return [message]
    
    messages = []
    current_message = ""
    
    # Split by lines
    lines = message.split('\n')
    
    for line in lines:
        # Check if adding this line would exceed the limit
        if len(current_message + line + '\n') > max_length:
            if current_message:
                messages.append(current_message.rstrip())
                current_message = line + '\n'
            else:
                # If a single line is too long, we have to truncate it
                messages.append(line[:max_length-3] + "...")
                current_message = ""
        else:
            current_message += line + '\n'
    
    # Add the last message if it has content
    if current_message.strip():
        messages.append(current_message.rstrip())
    
    return messages

async def error_message(error: Exception, bot: discord.Client):
    logger.error("Sending owner error notification", extra={"event": "owner_error_notification_started", "error_type": type(error).__name__, "error_message": str(error)})
    owner_username = os.getenv("owner_discord_username")
    if owner_username:
        try:
            owner_user = sql.get_user_by_discord_username(owner_username)
            if not owner_user:
                logger.warning(
                    "Owner user was not found for error notification",
                    extra={"event": "owner_error_notification_failed", "reason": "owner_user_not_found", "owner_discord_username": owner_username},
                )
                return
            await send_message(owner_user, f"Error: {error}", bot)
            logger.info("Owner error notification sent", extra={"event": "owner_error_notification_succeeded"})
        except Exception:
            logger.exception("Owner error notification failed", extra={"event": "owner_error_notification_failed", "reason": "unexpected_error"})
    else:
        logger.warning("Owner error notification skipped", extra={"event": "owner_error_notification_skipped", "reason": "owner_discord_username_missing"})


def run_notifier(users: list[sql.User], options: RunOptions, token: str) -> int:
    # SpotiNotifs only sends text DMs and never initializes Discord voice support.
    discord.VoiceClient.warn_nacl = False
    bot = discord.Client(intents=discord.Intents.all())
    started = False
    exit_code = 1
    notifier_started_at = time.monotonic()

    @bot.event
    async def on_ready():
        nonlocal started, exit_code
        if started:
            return
        started = True
        try:
            logger.info(
                "Notifier Discord bot ready",
                extra={
                    "event": "discord_bot_ready",
                    "bot_user": str(bot.user) if bot.user else None,
                    "guild_count": len(bot.guilds),
                },
            )
            logger.info(
                "Starting notifier user loop",
                extra={
                    "event": "notifier_user_loop_started",
                    "user_count": len(users),
                    "mode": options.mode,
                    "is_new_day": options.is_new_day,
                    "catchup_start_date": options.start_date,
                    "catchup_end_date": options.end_date,
                },
            )
            successful_users = 0
            failed_users = 0
            total_new_releases = 0
            anchor_succeeded_users = 0
            anchor_failed_users = 0
            for user in users:
                succeeded, release_count, anchor_status = await process_user(user, options, bot)
                if succeeded:
                    successful_users += 1
                else:
                    failed_users += 1
                if anchor_status == "succeeded":
                    anchor_succeeded_users += 1
                elif anchor_status == "failed":
                    anchor_failed_users += 1
                total_new_releases += release_count
            logger.info(
                "Finished notifier user loop",
                extra={
                    "event": "notifier_user_loop_finished",
                    "user_count": len(users),
                    "successful_user_count": successful_users,
                    "failed_user_count": failed_users,
                    "new_release_count": total_new_releases,
                    "anchor_succeeded_user_count": anchor_succeeded_users,
                    "anchor_failed_user_count": anchor_failed_users,
                    "duration_seconds": round(time.monotonic() - notifier_started_at, 3),
                },
            )
            exit_code = 1 if failed_users else 0
        except Exception:
            logger.exception("Notifier user loop failed", extra={"event": "notifier_user_loop_failed"})
            exit_code = 1
        finally:
            await bot.close()

    try:
        bot.run(token, log_handler=None)
    except Exception:
        logger.exception("Notifier failed", extra={"event": "notifier_failed"})
        return 1
    return exit_code


def parse_date(value: str) -> date:
    parts = value.split("-")
    if len(parts) == 3:
        if len(parts[0]) == 2:
            value = f"{parts[2]}-{parts[0]}-{parts[1]}"
        try:
            parsed = date.fromisoformat(value)
            if parsed.isoformat() == value:
                return parsed
        except ValueError:
            pass
    raise argparse.ArgumentTypeError("expected a valid YYYY-MM-DD or MM-DD-YYYY date")


def create_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Notify all users of today's releases, or run an explicit catch-up.",
        allow_abbrev=False,
    )
    commands = parser.add_subparsers(dest="command")
    users = commands.add_parser("users", help="Inspect registered users", allow_abbrev=False)
    user_commands = users.add_subparsers(dest="users_command", required=True)
    listing = user_commands.add_parser("list", help="List users without credentials", allow_abbrev=False)
    listing.add_argument("--format", choices=("table", "json", "ids"), default="table")

    catchup = commands.add_parser(
        "catchup",
        help="Replay releases for explicitly selected users",
        description="Replay an inclusive date range. Repeated runs can duplicate messages and playlist tracks.",
        allow_abbrev=False,
    )
    catchup.add_argument("start_date", type=parse_date)
    catchup.add_argument("end_date", type=parse_date)
    selection = catchup.add_mutually_exclusive_group(required=True)
    selection.add_argument("--user", action="append", metavar="UUID", help="Select a UUID; repeat for multiple users")
    selection.add_argument("--users-from-stdin", action="store_true", help="Read one UUID per line from stdin")
    selection.add_argument("--all-users", action="store_true", help="Explicitly select every registered user")
    return parser


def print_users(output_format: str) -> None:
    users = sql.list_user_summaries()
    if output_format == "json":
        print(json.dumps(users, ensure_ascii=False))
    elif output_format == "ids":
        for user in users:
            print(user["user_uuid"])
    else:
        rows = [("UUID", "USERNAME", "DISCORD USERNAME")]
        rows.extend(
            tuple(str(user[field] or "").replace("\n", "\\n").replace("\r", "\\r").replace("\t", "\\t")
                  for field in ("user_uuid", "username", "discord_username"))
            for user in users
        )
        widths = [max(len(row[index]) for row in rows) for index in range(3)]
        for row in rows:
            print("  ".join(value.ljust(width) for value, width in zip(row, widths)).rstrip())


def select_users(args: argparse.Namespace) -> list[sql.User]:
    requested = None
    if not args.all_users:
        source = sys.stdin if args.users_from_stdin else args.user
        requested = list(dict.fromkeys(value.strip() for value in source if value.strip()))
        if not requested:
            raise ValueError("no users selected; provide at least one UUID")

    users = sorted(sql.get_all_users(read_only=True), key=lambda user: (user.username or "", user.user_UUID))
    if requested is not None:
        known = {user.user_UUID for user in users}
        unknown = [user_uuid for user_uuid in requested if user_uuid not in known]
        if unknown:
            raise ValueError(f"unknown user UUID(s): {', '.join(unknown)}")
        selected = set(requested)
        users = [user for user in users if user.user_UUID in selected]
    if not users:
        raise ValueError("no users selected; the database contains no registered users")
    return users


def main(argv: list[str] | None = None) -> int:
    parser = create_parser()
    args = parser.parse_args(argv)
    listing = args.command == "users"
    configure_logging(service=os.getenv("SERVICE_NAME", "notifier"), stream=sys.stderr if listing else sys.stdout)

    if listing:
        try:
            print_users(args.format)
        except Exception:
            logger.exception("Unable to list users", extra={"event": "notifier_user_list_failed"})
            return 1
        return 0

    now = datetime.now(UTC).astimezone()
    options = RunOptions(today=now.date(), is_new_day=now.hour < 12)
    if args.command == "catchup":
        if args.start_date > args.end_date:
            parser.error("start date must be on or before end date")
        if args.end_date > options.today:
            parser.error("catch-up dates cannot be in the future")
        options = RunOptions(options.today, options.is_new_day, args.start_date, args.end_date)
        try:
            users = select_users(args)
        except ValueError as error:
            parser.error(str(error))
        except Exception:
            logger.exception("Unable to load selected users", extra={"event": "notifier_user_selection_failed"})
            return 1
    else:
        try:
            sql.init_db()
            users = sql.get_all_users()
        except Exception:
            logger.exception("Unable to load users", extra={"event": "notifier_users_load_failed"})
            return 1

    load_dotenv()
    token = os.getenv("discord_token")
    if not token:
        logger.error("Discord token is not set", extra={"event": "notifier_missing_discord_token"})
        return 1
    logger.info(
        "Notifier starting",
        extra={
            "event": "notifier_started",
            "mode": options.mode,
            "is_new_day": options.is_new_day,
            "user_count": len(users),
            "catchup_start_date": options.start_date,
            "catchup_end_date": options.end_date,
        },
    )
    if options.catchup:
        logger.warning(
            "Catch-up replays the entire range and can duplicate notifications and playlist tracks",
            extra={"event": "notifier_catchup_replay"},
        )
    return run_notifier(users, options, token)


if __name__ == "__main__":
    sys.exit(main())
