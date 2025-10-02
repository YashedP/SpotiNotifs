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

# Debug: Check if environment variables are loaded
print("Environment variables check:")
print(f"clientId: {'✓' if os.getenv('clientId') else '✗'}")
print(f"redirectUri: {'✓' if os.getenv('redirectUri') else '✗'}")
print(f"clientSecret: {'✓' if os.getenv('clientSecret') else '✗'}")
print(f"authorizationUrl: {'✓' if os.getenv('authorizationUrl') else '✗'}")
print(f"tokenUrl: {'✓' if os.getenv('tokenUrl') else '✗'}")

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
        'is_relogin': True,
        'existing_user_id': existing_user.user_UUID
    }
    
    try:
        auth_url = OAuth2.create_authorization_url(state=user_UUID)
        print(f"Generated auth URL: {auth_url}")  # Debug print
        return redirect(auth_url)
    except Exception as e:
        print(f"Error creating authorization URL: {e}")  # Debug print
        return serve_html_with_error(f"Error creating authorization URL: {str(e)}")

@app.route('/callback')
def callback():
    print("=== CALLBACK FUNCTION STARTED ===")
    
    authCode = request.args.get('code')
    user_UUID = request.args.get('state')
    error = request.args.get('error')
    
    print(f"DEBUG - authCode: {authCode}")
    print(f"DEBUG - user_UUID: {user_UUID}")
    print(f"DEBUG - error: {error}")
    print(f"DEBUG - All request args: {dict(request.args)}")
    
    if error:
        print(f"ERROR in callback: {error}")
        return serve_html_with_error(error)
    
    if user_UUID not in users:
        print(f"ERROR - User UUID {user_UUID} not found in users dict")
        print(f"DEBUG - Available users: {list(users.keys())}")
        return serve_html_with_error("User not found")
    
    print(f"DEBUG - Found user in users dict: {user_UUID}")
    user_data = users[user_UUID]
    print(f"DEBUG - User data: {user_data}")
    del users[user_UUID]
    username = user_data['username']
    is_relogin = user_data.get('is_relogin', False)
    existing_user_id = user_data.get('existing_user_id')
    
    print(f"DEBUG - username: {username}")
    print(f"DEBUG - is_relogin: {is_relogin}")
    print(f"DEBUG - existing_user_id: {existing_user_id}")
    
    print("DEBUG - About to call OAuth2.get_access_token")
    print(f"DEBUG - authCode length: {len(authCode) if authCode else 'None'}")
    print(f"DEBUG - authCode preview: {authCode[:20] if authCode else 'None'}...")
    
    try:
        print("DEBUG - Calling OAuth2.get_access_token now...")
        response = OAuth2.get_access_token(authCode)
        print(f"DEBUG - OAuth2 response received: {type(response)}")
        print(f"DEBUG - OAuth2 response keys: {list(response.keys()) if isinstance(response, dict) else 'Not a dict'}")
        refresh_token = response['refresh_token']
        print(f"DEBUG - refresh_token extracted: {refresh_token[:20] if refresh_token else 'None'}...")
    except Exception as e:
        print(f"ERROR - Failed to get access token: {e}")
        print(f"ERROR - Exception type: {type(e)}")
        import traceback
        print(f"ERROR - Full traceback: {traceback.format_exc()}")
        return serve_html_with_error(f"Failed to get access token: {str(e)}")
    
    if is_relogin:
        print("DEBUG - Processing relogin flow")
        # Update existing user's refresh token
        print(f"DEBUG - About to update refresh token for user ID: {existing_user_id}")
        if sql.update_user_refresh_token_by_id(existing_user_id, refresh_token):
            print(f"DEBUG - Successfully updated refresh token for user: {username}")
            return serve_html_with_error(f"Successfully re-authenticated user: {username}")
        else:
            print(f"ERROR - Failed to update refresh token for user: {username}")
            return serve_html_with_error(f"Failed to update user {username}")
    else:
        print("DEBUG - Processing new user flow")
        discord_username = user_data['discord_username']
        want_playlist = user_data['want_playlist']
        
        print(f"DEBUG - discord_username: {discord_username}")
        print(f"DEBUG - want_playlist: {want_playlist}")
        
        user = sql.User(user_UUID, username, discord_username, refresh_token)
        print(f"DEBUG - Created user object: {user}")
        
        if want_playlist:
            print("DEBUG - User wants playlist, getting access token and creating playlist")
            try:
                user.access_token = OAuth2.refresh_access_token(refresh_token)['access_token']
                print(f"DEBUG - Got access token: {user.access_token[:20]}...")
                playlist_id = asyncio.run(spotify.create_playlist(user))
                print(f"DEBUG - Created playlist with ID: {playlist_id}")
                user.playlist_id = playlist_id
            except Exception as e:
                print(f"ERROR - Failed to create playlist: {e}")
                return serve_html_with_error(f"Failed to create playlist: {str(e)}")
        
        print("DEBUG - About to add user to database")
        if sql.add_user(user):
            print(f"DEBUG - Successfully added user: {username}")
            return serve_html_with_error(f"Successfully authenticated user: {username} with Discord: {discord_username}")
        else:
            print(f"ERROR - User {username} already exists")
            return serve_html_with_error(f"User {username} already exists")

if __name__ == '__main__':
    app.run(debug=True, port=5000)
