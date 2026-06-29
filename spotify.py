import sql
import OAuth2
import aiohttp
import requests
from typing import Any
from datetime import datetime, timedelta
import discord
from dotenv import load_dotenv
import os
import time
import asyncio
import sys
from urllib.parse import urlparse

from observability import configure_logging, get_logger

load_dotenv()
RUN_ID = configure_logging(service=os.getenv("SERVICE_NAME", "notifier"))
logger = get_logger(__name__)

DISCORD_TOKEN = os.getenv("discord_token")
bot = discord.Client(intents=discord.Intents.all())
OWNER_DISCORD_USERNAME = os.getenv("owner_discord_username")
SPOTIFY_SEMAPHORE = asyncio.Semaphore(1)
BREAKPOINT = 100
is_new_day = True if datetime.now().hour < 12 else False
catchup = False
catchup_days = []
notifier_started_at = time.monotonic()

FOLLOWING_ARTISTS_URL  = "https://api.spotify.com/v1/me/following"
ARTIST_ALBUMS_URL      = "https://api.spotify.com/v1/artists/{artist_id}/albums"
ME_URL                 = "https://api.spotify.com/v1/me"
ME_PLAYLISTS_URL       = "https://api.spotify.com/v1/me/playlists"
ME_FOLLOW_PLAYLIST_URL = "https://api.spotify.com/v1/playlists/{playlist_id}/followers"
ALBUM_URL              = "https://api.spotify.com/v1/albums/{album_id}"
CREATE_PLAYLIST_URL    = "https://api.spotify.com/v1/users/{user_id}/playlists"
GET_PLAYLIST_URL       = "https://api.spotify.com/v1/playlists/{playlist_id}"
ADD_TO_PLAYLIST_URL    = "https://api.spotify.com/v1/playlists/{playlist_id}/tracks"

sql.init_db()

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
                seconds_to_wait = int(e.headers.get('Retry-After'))  # pyright: ignore[reportArgumentType, reportOptionalMemberAccess]
                
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
                    await error_message(Exception(f"Rate limited (429). Waiting {seconds_to_wait} seconds before retry... for user {user.safe_str()}"))
                    sys.exit(1)
                
                await asyncio.sleep(seconds_to_wait)
            elif e.status == 403:
                error_msg = f"API call returned 403 Forbidden (Unauthorized) for user {user.safe_str()} at URL: {url}"
                logger.error(
                    "Spotify request forbidden",
                    extra={
                        "event": "spotify_request_forbidden",
                        "endpoint": endpoint_name(url),
                        "status_code": e.status,
                        **user_log_context(user),
                    },
                )
                await error_message(Exception(error_msg))
                sys.exit(1)
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
    return {}

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
            if hasattr(e, 'response') and e.response and e.response.status_code == 429:
                retry_after = e.response.headers.get('Retry-After')
                seconds_to_wait = int(retry_after) if retry_after else 0
                
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
                    asyncio.run(error_message(Exception(f"Rate limited (429). Waiting {seconds_to_wait} seconds before retry... for user {user.safe_str()}")))
                    sys.exit(1)
                
                time.sleep(seconds_to_wait)
            elif hasattr(e, 'response') and e.response and e.response.status_code == 403:
                error_msg = f"API call returned 403 Forbidden (Unauthorized) for user {user.safe_str()} at URL: {url}"
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
                asyncio.run(error_message(Exception(error_msg)))
                sys.exit(1)
            elif hasattr(e, 'response') and e.response and 500 <= e.response.status_code < 600:
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
                raise e
        attempts -= 1
    logger.error(
        "Spotify request exhausted retries",
        extra={"event": "spotify_request_retries_exhausted", "endpoint": endpoint_name(url), "method": method, **user_log_context(user)},
    )
    return {}

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
        except requests.exceptions.RequestException as e:
            logger.exception("Error requesting followed artists", extra={"event": "spotify_followed_artists_failed", **user_log_context(user)})
            return artists
        if not next_cursor:
            break
    
    logger.info("Fetched followed artists", extra={"event": "spotify_followed_artists_succeeded", "artist_count": len(artists), **user_log_context(user)})
    return artists

async def get_all_albums(user: sql.User, artist_id: str, session: aiohttp.ClientSession, semaphore: asyncio.Semaphore) -> list[str]:
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

async def recent_20_for_each_category_album(user: sql.User, artist_id: str, session: aiohttp.ClientSession, semaphore: asyncio.Semaphore) -> list[str]:
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
        for _, songs in new_releases.items():
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
    
        num_requests_required = len(uris) // BREAKPOINT + 1
        for i in range(num_requests_required):
            body = {"uris": uris[i * BREAKPOINT : (i + 1) * BREAKPOINT]}
            spotify_request_sync(user, ADD_TO_PLAYLIST_URL.format(playlist_id=user.playlist_id), body=body, method="POST")
        logger.info(
            "Playlist update succeeded",
            extra={"event": "playlist_update_succeeded", "release_count": release_count, "track_count": len(uris), **user_log_context(user)},
        )
    except Exception as e:
        logger.exception("Playlist update failed", extra={"event": "playlist_update_failed", "release_count": release_count, **user_log_context(user)})
        await error_message(Exception(f"Error adding to playlist: {e}"))

async def new_releases(user: sql.User) -> tuple[str, int]:
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
    except Exception as e:
        logger.exception("Error requesting artists", extra={"event": "spotify_artists_request_failed", **user_log_context(user)})
        await error_message(Exception(f"Error requesting artists: {e}"))
        return "Error requesting artists", 0
    
    artists_ids = [(artist['id'], artist['name']) for artist in artists]
    logger.info("Starting artist processing", extra={"event": "artist_processing_started", "artist_count": len(artists_ids), **user_log_context(user)})
    
    new_releases = {}
    songs_already_added = user.get_items()
    if is_new_day:
        user.reset_items()
    
    async with aiohttp.ClientSession() as session:
        async def process_single_artist(artist_id, artist_name):
            if not catchup:
                albums = await recent_20_for_each_category_album(user, artist_id, session, SPOTIFY_SEMAPHORE)
            else:
                albums = await get_all_albums(user, artist_id, session, SPOTIFY_SEMAPHORE)

            new_songs = {}
            
            for album in albums:
                if not catchup:
                    current_time = datetime.now().strftime("%Y-%m-%d")
                
                    if album.get('release_date') == current_time: # type: ignore
                        album_id = album.get('id') # type: ignore
                
                        if album_id not in songs_already_added:
                            user.add_item(album_id)
                            new_songs[album_id] = album
                else:
                    days = [day.strftime("%Y-%m-%d") for day in catchup_days]

                    if album.get('release_date') in days: # type: ignore
                        album_id = album.get('id') # type: ignore
                        
                        if album_id:
                            new_songs[album_id] = album
                            
            return artist_name, new_songs if new_songs else None
        
        tasks = [process_single_artist(artist_id, artist_name) for artist_id, artist_name in artists_ids]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        sql.update_user_items(user)
        for result in results:
            if isinstance(result, Exception):
                logger.exception("Error processing artist", exc_info=(type(result), result, result.__traceback__), extra={"event": "artist_processing_failed", **user_log_context(user)})
                await error_message(Exception(f"Error processing artist: {result}"))
                continue
            if isinstance(result, tuple) and len(result) == 2:
                artist_name, new_songs = result
            else:
                logger.warning("Unexpected artist result format", extra={"event": "artist_processing_unexpected_result", **user_log_context(user)})
                continue
            if new_songs:
                new_releases[artist_name] = new_songs
    
    
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
        if catchup:
            message += f"New Releases! {catchup_days[0].strftime('%m/%d')}-{catchup_days[-1].strftime('%m/%d')}\n\n"
            for artist, songs in new_releases.items():
                message += f"**{artist}**\n"
                for song in songs.values():
                    message += f"* [{song['name']}]({song['external_urls']['spotify']})\n"
                message += "\n"
        else:    
            if is_new_day:
                message += f"New Releases! {datetime.now().strftime('%m/%d')}\n\n"
            else:
                message += f"New Releases! {datetime.now().strftime('%m/%d')}\n\n" + "Strays from today:\n"
            for artist, songs in new_releases.items():
                message += f"**{artist}**\n"
                for song in songs.values():
                    message += f"* [{song['name']}]({song['external_urls']['spotify']})\n"
                message += "\n"
        
        await add_to_playlist(user, new_releases)
    else:
        if is_new_day:
            message += f"No new releases today! {datetime.now().strftime('%m/%d')}\n\n"
        else:
            message += f"No strays today! {datetime.now().strftime('%m/%d')}\n\n"
    return message, release_count

async def process_user(user: sql.User) -> tuple[bool, int]:
    user_started_at = time.monotonic()
    logger.info("User processing started", extra={"event": "user_processing_started", **user_log_context(user)})
    if catchup:
        await send_message(user, "Catching up on all songs missed due to the bot outage! Apologies for the delay.")
    else:
        if is_new_day:
            await send_message(user, "Finding new releases for the day!")
        else:
            await send_message(user, "Catching up on any strays from today!")
    
    try:
        message, release_count = await new_releases(user)
        await send_message(user, message)
        logger.info(
            "User processing finished",
            extra={
                "event": "user_processing_finished",
                "status": "succeeded",
                "duration_seconds": round(time.monotonic() - user_started_at, 3),
                "new_release_count": release_count,
                **user_log_context(user),
            },
        )
        return True, release_count
    except Exception as e:
        logger.exception(
            "User processing failed",
            extra={
                "event": "user_processing_finished",
                "status": "failed",
                "duration_seconds": round(time.monotonic() - user_started_at, 3),
                **user_log_context(user),
            },
        )
        await error_message(Exception(f"Error processing user: {user.safe_str()}"))
        return False, 0

@bot.event
async def send_message(user: sql.User, message: str):
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
        except discord.Forbidden:
            logger.warning("Discord user DMs are closed", extra={"event": "discord_message_send_failed", "reason": "dms_closed", **user_log_context(user)})
        except Exception as e:
            logger.exception("Discord message send failed", extra={"event": "discord_message_send_failed", "reason": "unexpected_error", **user_log_context(user)})
    else:
        for client in bot.guilds:
            for member in client.members:
                if member.name == user.discord_username:
                    try:
                        sql.update_user_discord_id(user, str(member.id))
                    except Exception as e:
                        logger.exception("Failed to cache Discord ID", extra={"event": "discord_id_cache_failed", **user_log_context(user)})
                        await error_message(Exception(f"Error updating user discord ID: {e}"))
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

@bot.event
async def error_message(error: Exception):
    logger.error("Sending owner error notification", extra={"event": "owner_error_notification_started", "error_type": type(error).__name__, "error_message": str(error)})
    if OWNER_DISCORD_USERNAME:
        try:
            owner_user = sql.get_user_by_discord_username(OWNER_DISCORD_USERNAME)
            if not owner_user:
                logger.warning(
                    "Owner user was not found for error notification",
                    extra={"event": "owner_error_notification_failed", "reason": "owner_user_not_found", "owner_discord_username": OWNER_DISCORD_USERNAME},
                )
                return
            await send_message(owner_user, f"Error: {error}")
            logger.info("Owner error notification sent", extra={"event": "owner_error_notification_succeeded"})
        except Exception as e:
            logger.exception("Owner error notification failed", extra={"event": "owner_error_notification_failed", "reason": "unexpected_error"})
    else:
        logger.warning("Owner error notification skipped", extra={"event": "owner_error_notification_skipped", "reason": "owner_discord_username_missing"})

@bot.event
async def delete_messages():
    for client in bot.guilds:
        for member in client.members:
            if member.name == OWNER_DISCORD_USERNAME:
                OWNER_DISCORD_ID = member.id
                break

    try:
        user = await bot.fetch_user(OWNER_DISCORD_ID)
        channel = await user.create_dm()

        async for message in channel.history(limit=100):
            if message.author == bot.user:
                await message.delete()
    except Exception as e:
        logger.exception("Error deleting Discord messages", extra={"event": "discord_delete_messages_failed"})
        await bot.close()

@bot.event
async def on_ready():
    logger.info(
        "Notifier Discord bot ready",
        extra={
            "event": "discord_bot_ready",
            "bot_user": str(bot.user) if bot.user else None,
            "guild_count": len(bot.guilds),
        },
    )
    users = list(sql.iterate_users_one_by_one())
    logger.info(
        "Starting notifier user loop",
        extra={
            "event": "notifier_user_loop_started",
            "user_count": len(users),
            "mode": "catchup" if catchup else "daily",
            "is_new_day": is_new_day,
        },
    )

    successful_users = 0
    failed_users = 0
    total_new_releases = 0
    for user in users:
        succeeded, release_count = await process_user(user)
        if succeeded:
            successful_users += 1
        else:
            failed_users += 1
        total_new_releases += release_count

    logger.info(
        "Finished notifier user loop",
        extra={
            "event": "notifier_user_loop_finished",
            "user_count": len(users),
            "successful_user_count": successful_users,
            "failed_user_count": failed_users,
            "new_release_count": total_new_releases,
            "duration_seconds": round(time.monotonic() - notifier_started_at, 3),
        },
    )
    await bot.close()

if __name__ == "__main__":
    mode = "daily"
    if len(sys.argv) > 1:
        mode = sys.argv[1]
            
        if mode == "catchup":
            catchup = True
            
            if len(sys.argv) == 4:
                try:
                    start_day = datetime.strptime(sys.argv[2], "%m-%d-%Y")
                    end_day = datetime.strptime(sys.argv[3], "%m-%d-%Y")
                    if end_day == datetime.now().date():
                        end_day = end_day - timedelta(days=1)
                except ValueError:
                    logger.error("Invalid catchup date format", extra={"event": "notifier_cli_invalid_date_format"})
                    sys.exit(1)
                delta = end_day - start_day
                catchup_days.append(start_day)
                for i in range(delta.days + 1):
                    day = start_day + timedelta(days=i)
                    catchup_days.append(day)
                catchup_days.append(end_day)
            else:
                logger.error("Invalid catchup arguments", extra={"event": "notifier_cli_invalid_arguments"})
                sys.exit(1)
        else:
            logger.error("Invalid notifier mode", extra={"event": "notifier_cli_invalid_mode", "mode": mode})
            sys.exit(1)

    logger.info(
        "Notifier starting",
        extra={
            "event": "notifier_started",
            "mode": "catchup" if catchup else "daily",
            "is_new_day": is_new_day,
            "catchup_start_date": catchup_days[0].strftime("%Y-%m-%d") if catchup_days else None,
            "catchup_end_date": catchup_days[-1].strftime("%Y-%m-%d") if catchup_days else None,
        },
    )

    if DISCORD_TOKEN:
        bot.run(DISCORD_TOKEN)
    else:
        logger.error("Discord token is not set", extra={"event": "notifier_missing_discord_token"})
        sys.exit(1)
