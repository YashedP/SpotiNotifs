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
        <meta name="viewport" content="width=device-width, initial-scale=1">
        <link rel="icon" href="data:image/svg+xml,<svg xmlns=%22http://www.w3.org/2000/svg%22 viewBox=%220 0 100 100%22><text y=%22.9em%22 font-size=%2290%22>🎵</text></svg>">
        <style>
            body {
                font-family: Arial, sans-serif;
                max-width: 440px;
                margin: 0 auto;
                padding: 40px 20px 56px;
                background-color: #f5f5f5;
                color: #242424;
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
                margin: 0 0 12px;
            }
            .intro {
                margin: 0 0 30px;
                color: #555;
                line-height: 1.5;
                text-align: center;
            }
            .form-section + .form-section {
                margin-top: 30px;
                padding-top: 30px;
                border-top: 1px solid #e5e5e5;
            }
            h2 {
                margin: 0 0 8px;
                font-size: 20px;
            }
            .section-description {
                margin: 0 0 20px;
                color: #666;
                line-height: 1.5;
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
            input[type="text"]:focus-visible,
            input[type="checkbox"]:focus-visible,
            button:focus-visible {
                outline: 3px solid rgba(29, 185, 84, 0.3);
                outline-offset: 2px;
            }
            .checkbox-label {
                display: flex;
                align-items: flex-start;
                gap: 10px;
                font-weight: normal;
                line-height: 1.4;
            }
            .checkbox-label input {
                margin-top: 3px;
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
                transition: background-color 0.2s, transform 0.2s;
            }
            button:hover {
                background-color: #1ed760;
            }
            button:active {
                transform: translateY(1px);
            }
        </style>
    </head>
    <body>
        <main class="container">
            <header>
                <h1>🎵 Spotify New Music</h1>
                <p class="intro">Get new-release notifications from the artists you follow.</p>
            </header>

            <section class="form-section" aria-labelledby="signup-heading">
                <h2 id="signup-heading">Connect a new user</h2>
                <p class="section-description">Set up notifications and choose whether to maintain a Spotify playlist.</p>
                <form action="/auth" method="POST">
                    <div class="form-group">
                        <label for="signup-username">Username</label>
                        <input type="text" id="signup-username" name="username" required placeholder="Your username">
                    </div>
                    <div class="form-group">
                        <label for="signup-discord-username">Discord username</label>
                        <input type="text" id="signup-discord-username" name="discord_username" required placeholder="Your Discord username">
                    </div>
                    <div class="form-group">
                        <label class="checkbox-label" for="signup-want-playlist">
                            <input type="checkbox" id="signup-want-playlist" name="want_playlist">
                            <span>Create a playlist for new releases from followed artists</span>
                        </label>
                    </div>
                    <button type="submit">Connect Spotify</button>
                </form>
            </section>

            <section class="form-section" aria-labelledby="reauth-heading">
                <h2 id="reauth-heading">Reconnect an existing user</h2>
                <p class="section-description">Refresh your Spotify access. Your Discord, playlist, and release settings stay unchanged.</p>
                <form action="/reauth" method="POST">
                    <div class="form-group">
                        <label for="reauth-username">Existing username</label>
                        <input type="text" id="reauth-username" name="username" required placeholder="Your existing username">
                    </div>
                    <button type="submit">Reconnect Spotify</button>
                </form>
            </section>
        </main>
    </body>
    </html>
    '''

@app.route('/auth', methods=['POST'])
def auth():
    username = request.form.get('username')
    discord_username = request.form.get('discord_username')
    want_playlist = request.form.get('want_playlist') == 'on'
    if not username or not discord_username:
        logger.info("Auth form missing required fields", extra={"event": "web_auth_form_invalid"})
        return redirect('/')

    if sql.get_user_by_username(username):
        logger.info("Signup username already exists", extra={"event": "web_signup_user_duplicate", "username": username})
        return f"User {username} already exists. Use Reconnect Spotify instead.", 409
    
    user_UUID = str(uuid.uuid4())
    users[user_UUID] = {
        'flow': 'signup',
        'username': username,
        'discord_username': discord_username.lower(),
        'want_playlist': want_playlist,
    }

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

@app.route('/reauth', methods=['POST'])
def reauth():
    username = request.form.get('username')
    if not username:
        logger.info("Reauthorization form missing username", extra={"event": "web_reauthorization_form_invalid"})
        return redirect('/')

    existing_user = sql.get_user_by_username(username)
    if not existing_user:
        logger.info("Reauthorization user not found", extra={"event": "web_reauthorization_user_not_found", "username": username})
        return f"User {username} was not found. Connect a new user instead.", 404

    user_UUID = str(uuid.uuid4())
    users[user_UUID] = {'flow': 'reauth', 'username': username}

    logger.info("OAuth reauthorization started", extra={"event": "web_oauth_reauthorization_started", **existing_user.log_context()})
    auth_url = OAuth2.create_authorization_url(state=user_UUID)
    logger.info(
        "Redirecting user to Spotify OAuth",
        extra={"event": "web_oauth_redirect_created", "flow": "reauth", "user_uuid": existing_user.user_UUID, "username": username},
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
    flow = user_data['flow']
    username = user_data['username']
    
    response = OAuth2.get_access_token(authCode)
    refresh_token = response['refresh_token']

    if flow == 'reauth':
        existing_user = sql.get_user_by_username(username)
        if not existing_user:
            logger.info("Reauthorization user no longer exists", extra={"event": "web_oauth_reauthorization_user_not_found", "username": username})
            return f"User {username} was not found. Connect a new user instead.", 404
        sql.update_user_refresh_token(existing_user, refresh_token)
        logger.info("OAuth reauthorization completed", extra={"event": "web_oauth_reauthorization_succeeded", **existing_user.log_context()})
        return f"Spotify reconnected for {username}. Your existing settings were preserved."

    if flow != 'signup':
        logger.error("OAuth callback has invalid flow", extra={"event": "web_oauth_callback_invalid_flow", "flow": flow})
        return "Invalid authorization flow. Return home and try again.", 400

    discord_username = user_data['discord_username']
    want_playlist = user_data['want_playlist']
    
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
