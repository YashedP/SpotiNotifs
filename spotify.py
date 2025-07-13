import sql
import OAuth2
import requests
from typing import Any
from datetime import datetime
import discord
from dotenv import load_dotenv
import os

load_dotenv()

discord_token = os.getenv("discord_token")
bot = discord.Client(intents=discord.Intents.all())

following_artists_url = "https://api.spotify.com/v1/me/following"
artist_albums_url = "https://api.spotify.com/v1/artists/{artist_id}/albums"

sql.init_db()

def spotify_request(user: sql.User, url: str, params: dict[str, str] = {}) -> dict[str, Any]:
    response = requests.get(url, params=params, headers={"Authorization": f"Bearer {user.access_token}"})
    return response.json()

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
    

async def new_releases():
    for user in sql.iterate_users_one_by_one():
        token_info = OAuth2.refresh_access_token(user.refresh_token)
        
        user.access_token = token_info['access_token']
        sql.update_user_access_token(user, user.access_token)

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
                
            new_releases[artist_name] = new_songs
        await new_releases_loop(user.discord_username, new_releases)


@bot.event
async def on_ready():
    await new_releases()
    print("Done")
    
@bot.event            
async def new_releases_loop(username: str, new_releases: dict[str, Any]):
    print("new_releases_loop")
    for client in bot.guilds:
        for member in client.members:
            if member.name == username:
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
                    
                await member.send(str)
                break

bot.run(discord_token)