from sqlite3 import connect
from typing import Generator
from pathlib import Path

users_db = Path(__file__).resolve().parent / "users.db"

class User:
    def __init__(self, user_UUID, username, discord_username, refresh_token, discord_id=None, access_token=None):
        self.user_UUID = user_UUID
        self.username = username
        self.discord_username = discord_username
        self.refresh_token = refresh_token
        self.access_token = access_token
        self.discord_id = discord_id
        
    def __str__(self):
        return f"User(user_UUID={self.user_UUID}, username={self.username}, discord_username={self.discord_username}, refresh_token={self.refresh_token}, access_token={self.access_token}, discord_id={self.discord_id})"

def init_db() -> None:
    conn = connect(users_db)
    cursor = conn.cursor()
    cursor.execute("CREATE TABLE IF NOT EXISTS users (user_UUID TEXT, username TEXT, discord_username TEXT, refresh_token TEXT, discord_id TEXT)")
    conn.commit()
    conn.close()

def add_user(user: User) -> None:
    conn = connect(users_db)
    cursor = conn.cursor()
    cursor.execute("INSERT INTO users (user_UUID, username, discord_username, refresh_token, discord_id) VALUES (?, ?, ?, ?, ?)", (user.user_UUID, user.username, user.discord_username, user.refresh_token, user.discord_id))
    conn.commit()
    conn.close()

def get_all_users() -> list[User]:
    conn = connect(users_db)
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM users")
    users = cursor.fetchall()
    conn.close()
    return [User(user[0], user[1], user[2], user[3], user[4]) for user in users]

def iterate_users_one_by_one() -> Generator[User, None, None]:
    conn = connect(users_db)
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM users")
    
    while True:
        user = cursor.fetchone()
        if user is None:
            conn.close()
            return
        yield User(user[0], user[1], user[2], user[3], user[4])

def get_user_by_uuid(user_UUID: str) -> User:
    conn = connect(users_db)
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM users WHERE user_UUID = ?", (user_UUID,))
    user = cursor.fetchone()
    conn.close()
    return User(user[0], user[1], user[2], user[3], user[4])

def update_user_refresh_token(user: User, refresh_token: str) -> None:
    conn = connect(users_db)
    cursor = conn.cursor()
    cursor.execute("UPDATE users SET refresh_token = ? WHERE user_UUID = ?", (refresh_token, user.user_UUID))
    conn.commit()
    conn.close()

def update_user_discord_id(user: User, discord_id: str) -> None:
    conn = connect(users_db)
    cursor = conn.cursor()
    cursor.execute("UPDATE users SET discord_id = ? WHERE user_UUID = ?", (discord_id, user.user_UUID))
    conn.commit()
    conn.close()
    
def get_user_by_name(username: str) -> User:
    conn = connect(users_db)
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM users WHERE discord_username = ?", (username,))
    user = cursor.fetchone()
    conn.close()
    return User(user[0], user[1], user[2], user[3], user[4])