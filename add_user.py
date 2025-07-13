from dotenv import load_dotenv
from flask import Flask, redirect, request
import uuid
import sql
import OAuth2

app = Flask(__name__)
load_dotenv()

users = {}

sql.init_db()

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
    if not username or not discord_username:
        return redirect('/')
    
    user_UUID = str(uuid.uuid4())
    users[user_UUID] = {'username': username, 'discord_username': discord_username.lower()}

    auth_url = OAuth2.create_authorization_url(state=user_UUID)
    return redirect(auth_url)

@app.route('/callback')
def callback():
    authCode = request.args.get('code')
    user_UUID = request.args.get('state')
    error = request.args.get('error')
    
    if error:
        return f"Error: {error}"
    
    if user_UUID not in users:
        return f"User not found"
    
    user_data = users[user_UUID]
    del users[user_UUID]
    username = user_data['username']
    discord_username = user_data['discord_username']
    
    response = OAuth2.get_access_token(authCode)
    refresh_token = response['refresh_token']
    
    sql.add_user(sql.User(user_UUID, username, discord_username, refresh_token))
    
    return f"Successfully authenticated user: {username} with Discord: {discord_username}"

if __name__ == '__main__':
    app.run(debug=True, port=5000)
