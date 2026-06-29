from dotenv import load_dotenv
from flask import Flask, redirect, request
import os
import uuid
import sql
import OAuth2
import spotify
import asyncio
from logging_config import configure_logging, get_logger

app = Flask(__name__)
load_dotenv()
configure_logging(service="server")
logger = get_logger(__name__)

users = {}

sql.init_db()

@app.route('/health')
def health():
    return "ok\n", 200, {"Content-Type": "text/plain; charset=utf-8"}

@app.route('/')
def index():
    return '''
    <!DOCTYPE html>
    <html>
    <head>
        <title>Spotify New Music</title>
        <style>
            body {
                font-family: Arial, sans-serif;
                max-width: 400px;
                margin: 50px auto;
                padding: 20px;
                background-color: #f5f5f5;
            }
            .container {
                background: white;
                padding: 30px;
                border-radius: 10px;
                box-shadow: 0 2px 10px rgba(0,0,0,0.1);
            }
            h1 {
                color: #1DB954;
                text-align: center;
                margin-bottom: 30px;
            }
            .form-group {
                margin-bottom: 20px;
            }
            label {
                display: block;
                margin-bottom: 5px;
                font-weight: bold;
                color: #333;
            }
            input[type="text"] {
                width: 100%;
                padding: 10px;
                border: 1px solid #ddd;
                border-radius: 5px;
                font-size: 16px;
                box-sizing: border-box;
            }
            button {
                width: 100%;
                padding: 12px;
                background-color: #1DB954;
                color: white;
                border: none;
                border-radius: 5px;
                font-size: 16px;
                cursor: pointer;
                transition: background-color 0.3s;
            }
            button:hover {
                background-color: #1ed760;
            }
        </style>
    </head>
    <body>
        <div class="container">
            <h1>🎵 Spotify New Music</h1>
            <form action="/auth" method="POST">
                <div class="form-group">
                    <label for="username">Enter your username:</label>
                    <input type="text" id="username" name="username" required placeholder="Your username">
                </div>
                <div class="form-group">
                    <label for="discord_username">Enter your Discord username:</label>
                    <input type="text" id="discord_username" name="discord_username" required placeholder="Your Discord username">
                </div>
                <div class="form-group">
                    <label for="want_playlist">Automatically create a playlist for all newly released songs from your followed artists</label>
                    <input type="checkbox" id="want_playlist" name="want_playlist" required>
                </div>
                <button type="submit">Continue to Spotify</button>
            </form>
        </div>
    </body>
    </html>
    '''

@app.route('/auth', methods=['POST'])
def auth():
    username = request.form.get('username')
    discord_username = request.form.get('discord_username')
    want_playlist = request.form.get('want_playlist')
    if not username or not discord_username:
        logger.info("Auth form missing required fields", extra={"event": "web_auth_form_invalid"})
        return redirect('/')
    
    user_UUID = str(uuid.uuid4())
    users[user_UUID] = {'username': username, 'discord_username': discord_username.lower(), 'want_playlist': want_playlist}
    
    if want_playlist == 'on':
        want_playlist = True
    else:
        want_playlist = False

    logger.info(
        "Auth form submitted",
        extra={
            "event": "web_auth_form_submitted",
            "user_uuid": user_UUID,
            "username": username,
            "discord_username": discord_username.lower(),
            "want_playlist": want_playlist,
        },
    )
    auth_url = OAuth2.create_authorization_url(state=user_UUID)
    logger.info(
        "Redirecting user to Spotify OAuth",
        extra={"event": "web_oauth_redirect_created", "user_uuid": user_UUID, "username": username},
    )
    return redirect(auth_url)

@app.route('/callback')
def callback():
    authCode = request.args.get('code')
    user_UUID = request.args.get('state')
    error = request.args.get('error')
    
    if error:
        logger.info("OAuth callback returned an error", extra={"event": "web_oauth_callback_error", "oauth_error": error})
        return f"Error: {error}"
    
    if user_UUID not in users:
        logger.info("OAuth callback state was not found", extra={"event": "web_oauth_callback_state_missing", "user_uuid": user_UUID})
        return f"User not found"
    
    user_data = users[user_UUID]
    del users[user_UUID]
    username = user_data['username']
    discord_username = user_data['discord_username']
    want_playlist = user_data['want_playlist']
    
    response = OAuth2.get_access_token(authCode)
    refresh_token = response['refresh_token']
    
    user = sql.User(user_UUID, username, discord_username, refresh_token)
    if want_playlist:
        logger.info("Creating signup playlist", extra={"event": "web_signup_playlist_create_started", **user.log_context()})
        user.access_token = OAuth2.refresh_access_token(refresh_token)['access_token']
        playlist_id = asyncio.run(spotify.create_playlist(user))
        user.playlist_id = playlist_id
        logger.info("Created signup playlist", extra={"event": "web_signup_playlist_create_succeeded", **user.log_context()})
    
    if sql.add_user(user):
        logger.info("OAuth callback completed", extra={"event": "web_oauth_callback_succeeded", **user.log_context()})
        return f"Successfully authenticated user: {username} with Discord: {discord_username}"
    else:
        logger.info("OAuth callback found duplicate user", extra={"event": "web_oauth_callback_duplicate_user", **user.log_context()})
        return f"User {username} already exists"

if __name__ == '__main__':
    app.run(debug=False, host='0.0.0.0', port=int(os.getenv("PORT", "5000")))
