import sql
import OAuth2
import aiohttp
import requests
from typing import Any
from datetime import datetime
import discord
from dotenv import load_dotenv
import os
import time
import asyncio
import sys

load_dotenv()

DISCORD_TOKEN = os.getenv("discord_token")
bot = discord.Client(intents=discord.Intents.all())
OWNER_DISCORD_USERNAME = os.getenv("owner_discord_username")
SPOTIFY_SEMAPHORE = asyncio.Semaphore(1)

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

async def spotify_request(user: sql.User, url: str, session: aiohttp.ClientSession, params: dict[str, str] = {}) -> dict[str, Any]:
    headers = {"Authorization": f"Bearer {user.access_token}"}
    attempts = 3
    while attempts > 0:
        if attempts != 3:
            print(f"Attempt {3 - attempts + 1} of 3 for {url}")
        try:
            async with session.get(url, params=params, headers=headers) as response:
                response.raise_for_status()
                
                return await response.json()
        except aiohttp.ClientResponseError as e:
            if e.status == 429:
                seconds_to_wait = int(e.headers.get('Retry-After'))
                print(f"Rate limited (429). Waiting {seconds_to_wait} seconds before retry...")
                if seconds_to_wait > 20:
                    await error_message(f"Rate limited (429). Waiting {seconds_to_wait} seconds before retry... for user {user.safe_str()}")
                    sys.exit(1)
                await asyncio.sleep(seconds_to_wait)
                continue
            else:
                raise e

def spotify_request_sync(user: sql.User, url: str, params: dict[str, str] = {}, body: dict[str, Any] = {}, method: str = "GET") -> dict[str, Any]:
    headers = {"Authorization": f"Bearer {user.access_token}"}
    attempts = 3
    while attempts > 0:
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
            print(f"Error requesting {url}: {e}")
            if hasattr(e, 'response') and e.response and e.response.status_code == 429:
                print(f"Rate limited (429). Waiting {e.response.headers.get('Retry-After')} seconds before retry...")
                time.sleep(int(e.response.headers.get('Retry-After')))
                continue
            if e.response.status_code == 403:
                print(e.response.raw.read().decode('utf-8'))
            else:
                raise e

def get_all_artists(user: sql.User) -> list[dict]:
    artists = []
    next = None
    
    while True:
        try:
            response = spotify_request_sync(user, FOLLOWING_ARTISTS_URL, {
            "type": "artist",
            "limit": "50",
            "after": next
            })['artists']
            
            artists.extend(response['items'])
            next = response['cursors']['after']
        except requests.exceptions.RequestException as e:
            print(f"Error requesting {FOLLOWING_ARTISTS_URL}: {e}")
            return artists
        if not next:
            break
    
    return artists

async def recent_5_for_each_category_album(user: sql.User, artist_id: str, session: aiohttp.ClientSession, semaphore: asyncio.Semaphore) -> list[str]:
    albums = []
    for category in ["album", "single", "appears_on", "compilation"]:
        async with semaphore:
            response = await spotify_request(user, ARTIST_ALBUMS_URL.format(artist_id=artist_id), session, {
            "limit": 5,
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
        response = spotify_request_sync(user, link, params={"limit": 50})
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
    response = spotify_request_sync(user, ME_URL)
    id = response['id']
    
    body = {
        "name": "SpotiNotif",
        "description": "New Releases from your followed artists",
        "public": True
    }
    
    response = spotify_request_sync(user, CREATE_PLAYLIST_URL.format(user_id=id), body=body, method="POST")
    playlist_id = response['id']
    return playlist_id

async def add_to_playlist(user: sql.User, new_releases) -> None:
    if not user.playlist_id:
        return 
    
    if not await check_playlist_exists(user):
        user.playlist_id = await create_playlist(user)
        sql.update_user_playlist_id(user, user.playlist_id)
    
    uris = []
    try:
        for _, songs in new_releases.items():
            for song in songs.values():
                link = song['id']
                response = spotify_request_sync(user, ALBUM_URL.format(album_id=link))
                
                items = response['tracks']['items']
                next = response['tracks']['next']
                while True:
                    if next:
                        response = spotify_request_sync(user, next)
                        items.extend(response['items'])
                        next = response['next']
                    else:
                        break
                uris.extend([item['uri'] for item in items])
        
        body = {
            "uris": uris
        }
        
        spotify_request_sync(user, ADD_TO_PLAYLIST_URL.format(playlist_id=user.playlist_id), body=body, method="POST")
    except Exception as e:
        await error_message(f"Error adding to playlist: {e}")

async def new_releases(user: sql.User) -> str:
    token_info = OAuth2.refresh_access_token(user.refresh_token)
    
    access_token = token_info['access_token']
    user.access_token = access_token
    
    try:
        artists = get_all_artists(user)
    except Exception as e:
        await error_message(f"Error requesting artists: {e}")
        return "Error requesting artists"
    
    artists_ids = [(artist['id'], artist['name']) for artist in artists]

    new_releases = {}
    
    async with aiohttp.ClientSession() as session:
        async def process_single_artist(artist_id, artist_name):
            albums = await recent_5_for_each_category_album(user, artist_id, session, SPOTIFY_SEMAPHORE)
            
            new_songs = {}
            for album in albums:
                current_time = datetime.now().strftime("%Y-%m-%d")
                
                if album['release_date'] == current_time:
                    new_songs[album['id']] = album
            
            return artist_name, new_songs if new_songs else None
        
        tasks = [process_single_artist(artist_id, artist_name) for artist_id, artist_name in artists_ids]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        
        for result in results:
            if isinstance(result, Exception):
                print(f"Error processing artist: {result}")
                await error_message(f"Error processing artist: {result}")
                continue
            artist_name, new_songs = result
            if new_songs:
                new_releases[artist_name] = new_songs
    
    str = ""
    if len(new_releases) > 0:
        str += f"New Releases! {datetime.now().strftime("%m/%d")}\n\n"
    
        for artist, songs in new_releases.items():
            str += f"**{artist}**\n"
            for song in songs.values():
                str += f"* [{song['name']}]({song['external_urls']['spotify']})\n"
            str += "\n"
        
        await add_to_playlist(user, new_releases)
    else:
        str += f"No new releases today! {datetime.now().strftime("%m/%d")}\n\n"

    return str

async def process_user(user: sql.User):
    await send_message(user, "Finding new releases for the day")
    str = await new_releases(user)
    await send_message(user, str)
    
@bot.event
async def send_message(user: sql.User, message: str):
    if user.discord_id:
        try:
            discord_user = await bot.fetch_user(user.discord_id)
            await discord_user.send(message)
        except discord.NotFound:
            print(f"User with ID {user.discord_id} not found")
        except discord.Forbidden:
            print(f"Cannot send message to user {user.discord_id} (DMs closed)")
        except Exception as e:
            print(f"Error sending message to user {user.discord_id}: {e}")
    else:
        for client in bot.guilds:
            for member in client.members:
                if member.name == user.discord_username:
                    try:
                        sql.update_user_discord_id(user, member.id)
                    except Exception as e:
                        await error_message(f"Error updating user discord ID: {e}")
                    await member.send(message)

@bot.event
async def error_message(error: Exception):
    if OWNER_DISCORD_USERNAME:
        try:
            owner_user = sql.get_user_by_discord_username(OWNER_DISCORD_USERNAME)
            await send_message(owner_user, f"Error: {error}")
        except Exception as e:
            print(f"Error getting owner user: {e}")

@bot.event
async def on_ready():
    print("Starting the day loop")

    # tasks = []
    for user in sql.iterate_users_one_by_one():        
        print("Starting task for user", user.username)
        await process_user(user)
        # task = asyncio.create_task(process_user(user))
        # tasks.append(task)

    # await asyncio.gather(*tasks)
    print("Finished the day loop")
    await bot.close()

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
        print(f"Error in on_ready: {e}")
        await bot.close()

if __name__ == "__main__":
    bot.run(DISCORD_TOKEN)
