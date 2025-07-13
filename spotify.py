import sql
import OAuth2
import requests
from typing import Any
from datetime import datetime
import discord
from dotenv import load_dotenv
import os
import time
import asyncio

load_dotenv()

discord_token = os.getenv("discord_token")
bot = discord.Client(intents=discord.Intents.all())

following_artists_url = "https://api.spotify.com/v1/me/following"
artist_albums_url = "https://api.spotify.com/v1/artists/{artist_id}/albums"

sql.init_db()

def spotify_request(user: sql.User, url: str, params: dict[str, str] = {}) -> dict[str, Any]:
    attempts = 3
    while attempts > 0:
        try:
            response = requests.get(url, params=params, headers={"Authorization": f"Bearer {user.access_token}"})
            return response.json()
        except Exception as e:
            print(e)
            attempts -= 1
            if attempts == 0:
                raise e
            time.sleep(1 + (3 - attempts) * 2)

def get_all_artists(user: sql.User) -> list[str]:
    artists = []
    next = None
    
    while True:
        response = spotify_request(user, following_artists_url, {
            "type": "artist",
            "limit": "50",
            "after": next
        })['artists']
        artists.extend(response['items'])
        next = response['cursors']['after']
        if not next:
            break
    
    return artists

def recent_5_for_each_category_album(user: sql.User, artist_id: str) -> list[str]:
    albums = []
    for category in ["album", "single", "appears_on", "compilation"]:
        response = spotify_request(user, artist_albums_url.format(artist_id=artist_id), {
            "limit": 5,
            "album_type": category,
            "market": "US"
        })
        albums.extend(response['items'])
    return albums
    

async def new_releases(user: sql.User) -> str:
    token_info = OAuth2.refresh_access_token(user.refresh_token)
    
    access_token = token_info['access_token']
    user.access_token = access_token
    
    artists = get_all_artists(user)
    artists_ids = [(artist['id'], artist['name']) for artist in artists]

    new_releases = {}
    for artist_id, artist_name in artists_ids:
        albums = recent_5_for_each_category_album(user, artist_id)
        new_songs = {}
        for album in albums:
            current_time = datetime.now().strftime("%Y-%m-%d")
            
            if album['release_date'] == current_time:
                new_songs[album['id']] = album
        
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
    else:
        str += f"No new releases today! {datetime.now().strftime("%m/%d")}\n\n"

    return str


@bot.event
async def on_ready():
    print("Starting the day loop")
    semaphore = asyncio.Semaphore(5)

    tasks = []
    for user in sql.iterate_users_one_by_one():
        print("Starting task for user", user.username)
        task = asyncio.create_task(process_user_with_semaphore(user, semaphore))
        tasks.append(task)

    await asyncio.gather(*tasks)
    print("Finished the day loop")
    await bot.close()

async def process_user_with_semaphore(user: sql.User, semaphore: asyncio.Semaphore):
    async with semaphore:
        await process_user(user)

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
                    sql.update_user_discord_id(user, member.id)
                    await member.send(message)

bot.run(discord_token)