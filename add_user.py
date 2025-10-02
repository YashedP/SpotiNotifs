from dotenv import load_dotenv
from flask import Flask, redirect, request, send_file, url_for
import uuid
import sql
import OAuth2
import spotify
import asyncio
import os

app = Flask(__name__)
load_dotenv()

users = {}

sql.init_db()

def serve_html_with_error(error=None):
    """Serve index.html with optional error message"""
    try:
        with open('index.html', 'r', encoding='utf-8') as f:
            html_content = f.read()
        
        if error:
            # Add error message to the HTML
            error_html = f'<div style="color: red; margin-bottom: 20px; padding: 10px; background-color: #ffe6e6; border: 1px solid #ff9999; border-radius: 5px;">{error}</div>'
            # Insert error message after the h1 tag
            html_content = html_content.replace('<h1>🎵 Spotify New Music</h1>', f'<h1>🎵 Spotify New Music</h1>\n{error_html}')
        
        return html_content
    except FileNotFoundError:
        return "HTML file not found", 404

@app.route('/')
def index():
    return serve_html_with_error()

@app.route('/auth', methods=['POST'])
def auth():
    username = request.form.get('username')
    discord_username = request.form.get('discord_username')
    want_playlist = request.form.get('want_playlist')
    if not username or not discord_username:
        return serve_html_with_error("Username and Discord username are required")
    
    user_UUID = str(uuid.uuid4())
    users[user_UUID] = {'username': username, 'discord_username': discord_username.lower(), 'want_playlist': want_playlist}
    
    if want_playlist == 'on':
        want_playlist = True
    else:
        want_playlist = False

    auth_url = OAuth2.create_authorization_url(state=user_UUID)
    return redirect(auth_url)

@app.route('/relogin', methods=['POST'])
def relogin():
    username = request.form.get('username')
    if not username:
        return serve_html_with_error("Username is required")
    
    # Check if user exists in database
    existing_user = sql.get_user_by_username(username)
    if not existing_user:
        return serve_html_with_error(f"User '{username}' not found. Please register first using the form above.")
    
    # Create a new UUID for this re-authentication session
    user_UUID = str(uuid.uuid4())
    users[user_UUID] = {
        'username': username, 
        'discord_username': existing_user.discord_username, 
        'want_playlist': existing_user.playlist_id is not None,
        'is_relogin': True,
        'existing_user_id': existing_user.id
    }
    
    auth_url = OAuth2.create_authorization_url(state=user_UUID)
    return redirect(auth_url)

@app.route('/callback')
def callback():
    authCode = request.args.get('code')
    user_UUID = request.args.get('state')
    error = request.args.get('error')
    
    if error:
        return serve_html_with_error(error)
    
    if user_UUID not in users:
        return serve_html_with_error("User not found")
    
    user_data = users[user_UUID]
    del users[user_UUID]
    username = user_data['username']
    discord_username = user_data['discord_username']
    want_playlist = user_data['want_playlist']
    is_relogin = user_data.get('is_relogin', False)
    existing_user_id = user_data.get('existing_user_id')
    
    response = OAuth2.get_access_token(authCode)
    refresh_token = response['refresh_token']
    
    if is_relogin:
        # Update existing user's refresh token
        if sql.update_user_refresh_token_by_id(existing_user_id, refresh_token):
            return serve_html_with_error(f"Successfully re-authenticated user: {username}")
        else:
            return serve_html_with_error(f"Failed to update user {username}")
    else:
        # Create new user
        user = sql.User(user_UUID, username, discord_username, refresh_token)
        if want_playlist:
            user.access_token = OAuth2.refresh_access_token(refresh_token)['access_token']
            playlist_id = asyncio.run(spotify.create_playlist(user))
            user.playlist_id = playlist_id
        
        if sql.add_user(user):
            return serve_html_with_error(f"Successfully authenticated user: {username} with Discord: {discord_username}")
        else:
            return serve_html_with_error(f"User {username} already exists")

if __name__ == '__main__':
    app.run(debug=True, port=5000)
